"""Protein-price trends over time (GFP-36): the read side of ``price_history``.

The GUI's trends pane asks one question: **is protein getting cheaper or dearer,
and where?** This module answers it front-end-agnostically, like the rest of
:mod:`grocery_planner.service` — it returns plain data and never draws anything,
so the same numbers are available to a CLI or a test without a Qt event loop.

The series plotted is the *cheapest* $/g protein each store offered on each day,
not an average. A nutritionist buying for a client buys the best available
option, so the minimum is the number that actually reaches the shopping list;
an average is dragged around by how many expensive items happened to be in that
week's ad, which says nothing about what the client will pay.

**Why the probe price.** ``savings.cost_per_gram_protein`` costs up to two
queries per call, and a 90-day window over a full ad is six figures of rows —
far too slow to open a window with. But the expensive half of that chain
(size → matched food → protein per 100 g → grams of protein in the package)
depends only on ``(store, item_name)``, never on the price; only the final
division does. So grams of protein is resolved **once per distinct item** with
a probe price of 1.0 and cached, and each day's rows are then a division. Same
arithmetic as calling the function per row, two orders of magnitude fewer
queries.

**Honesty.** Same rule as ``grocery_planner/savings.py``: a number we cannot
compute is absent, never guessed. An item whose protein content cannot be
resolved contributes no point rather than a zero (which would plot as a
free lunch and win every day it appeared). A day with no resolvable item
contributes no point rather than a gap filled by its neighbours. And a
series with fewer than :data:`MIN_POINTS_TO_PLOT` points is reported as
unplottable with a reason, because two dots joined by a line look exactly
like a trend while carrying none — see :attr:`ProteinTrend.is_plottable`.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

from .. import db, savings

#: A line needs at least this many days before it is a trend rather than a dot.
MIN_POINTS_TO_PLOT = 2

#: Default window. Matches ``records.RETENTION_FLOOR_DAYS`` (GFP-75/GFP-42):
#: asking for more history than retention promises to keep would quietly
#: produce a shorter chart than the axis claims.
DEFAULT_WINDOW_DAYS = 90


@dataclass(frozen=True)
class TrendPoint:
    """The cheapest protein one store sold on one day."""

    day: str                      # ISO YYYY-MM-DD, as stored in price_history
    cost_per_gram_protein: float
    item_name: str                # which item was cheapest, for the tooltip
    price: float


@dataclass(frozen=True)
class StoreTrend:
    """One store's series, oldest first."""

    store: str
    points: list[TrendPoint] = field(default_factory=list)

    @property
    def is_plottable(self) -> bool:
        return len(self.points) >= MIN_POINTS_TO_PLOT

    @property
    def latest(self) -> TrendPoint | None:
        return self.points[-1] if self.points else None


@dataclass(frozen=True)
class ProteinTrend:
    """Every store's series over one window, plus why it may not be plottable."""

    days: int
    series: list[StoreTrend] = field(default_factory=list)

    @property
    def plottable(self) -> list[StoreTrend]:
        return [s for s in self.series if s.is_plottable]

    @property
    def is_plottable(self) -> bool:
        return bool(self.plottable)

    @property
    def observed_days(self) -> int:
        """Distinct days any store produced a point on."""
        return len({point.day for s in self.series for point in s.points})

    @property
    def reason(self) -> str:
        """Why there is no chart, in words a user can act on. Empty if there is one.

        Deliberately distinguishes "no data at all" from "one day so far" —
        the first means something is wrong (nothing has ever been scraped, or
        nothing resolves to a protein figure), the second means the tool is
        working and simply needs to run again tomorrow. Telling a user to wait
        when the pipeline is broken, or that it is broken when they just need
        to wait, are different failures.
        """
        if self.is_plottable:
            return ""
        if self.observed_days == 0:
            return (
                "No protein prices on record yet. Run a scrape "
                "(Data ▸ Run scrape…) — a chart needs at least "
                f"{MIN_POINTS_TO_PLOT} days of history."
            )
        return (
            f"Only {self.observed_days} day of protein prices so far. "
            f"A trend needs at least {MIN_POINTS_TO_PLOT} days — this will "
            "start plotting once tomorrow's scrape lands."
        )


def _protein_grams_resolver(conn: sqlite3.Connection):
    """Memoised ``(store, item_name) -> grams of protein per package, or None``.

    See the module docstring for why this is separated from the price. The
    probe price of 1.0 is arbitrary and never leaves this function: it exists
    only because ``cost_per_gram_protein`` refuses a non-positive price, and
    ``ProteinCost.protein_grams`` — the part actually wanted here — is
    computed from size and food match alone.
    """
    cache: dict[tuple[str, str], float | None] = {}

    def resolve(store: str, item_name: str) -> float | None:
        key = (store, item_name)
        if key not in cache:
            cost = savings.cost_per_gram_protein(1.0, item_name, store, conn=conn)
            cache[key] = cost.protein_grams if cost and cost.protein_grams > 0 else None
        return cache[key]

    return resolve


def protein_price_trend(
    days: int = DEFAULT_WINDOW_DAYS,
    store: str | None = None,
    today: date | None = None,
    conn: sqlite3.Connection | None = None,
) -> ProteinTrend:
    """Cheapest $/g protein per store per day over the last ``days``.

    Reads ``price_history`` (GFP-39), which a scrape appends to rather than
    replacing, so this survives the ``deals`` table being rewritten every run.
    Stores are returned cheapest-latest first, so the store currently winning
    on protein is the first series a caller draws.
    """
    own = conn or db.connect()
    anchor = today or date.today()
    since = (anchor - timedelta(days=days)).isoformat()

    where = ["captured_at >= ?", "COALESCE(dollar_price, sale_price, regular_price) > 0"]
    params: list[object] = [since]
    if store:
        where.append("store=?")
        params.append(store)

    rows = own.execute(
        "SELECT store, item_name, captured_at, "
        "COALESCE(dollar_price, sale_price, regular_price) AS price "
        f"FROM price_history WHERE {' AND '.join(where)} "
        "ORDER BY captured_at",
        params,
    ).fetchall()

    protein_grams = _protein_grams_resolver(own)
    # (store, day) -> the cheapest point seen so far for it.
    best: dict[tuple[str, str], TrendPoint] = {}
    for row in rows:
        grams = protein_grams(row["store"], row["item_name"])
        if grams is None:
            continue  # no protein figure -> no point, never a zero
        cpgp = row["price"] / grams
        key = (row["store"], row["captured_at"])
        current = best.get(key)
        if current is None or cpgp < current.cost_per_gram_protein:
            best[key] = TrendPoint(
                day=row["captured_at"],
                cost_per_gram_protein=cpgp,
                item_name=row["item_name"],
                price=row["price"],
            )

    by_store: dict[str, list[TrendPoint]] = {}
    for (store_key, _day), point in best.items():
        by_store.setdefault(store_key, []).append(point)

    series = [
        StoreTrend(store=store_key, points=sorted(points, key=lambda p: p.day))
        for store_key, points in by_store.items()
    ]
    # Cheapest most-recent price first: the store currently winning leads.
    series.sort(key=lambda s: (
        s.latest.cost_per_gram_protein if s.latest else float("inf"), s.store
    ))
    return ProteinTrend(days=days, series=series)
