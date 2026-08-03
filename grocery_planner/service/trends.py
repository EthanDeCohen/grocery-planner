"""Price and $/g-protein trends over time (GFP-36, generalised by GFP-40).

The read side of ``price_history``. GFP-36 built the one question the main
window's right pane asks — **is protein getting cheaper, and where?** — and
GFP-40 widens that into the two axes the ticket names: two **metrics**
(``$/g protein`` and plain ``price``) over two **dimensions** (by store and
by food), from a single definition. That single definition is the point: the
chart and ``gplan trends`` must not be able to disagree about what "the price
of chicken last month" means, which is exactly what two parallel
implementations would eventually do.

Everything here is front-end-agnostic like the rest of
:mod:`grocery_planner.service` — plain data out, nothing drawn, so the numbers
behind the chart are testable without a Qt event loop.

**The daily value is a minimum, not an average.** A nutritionist buying for a
client buys the best available option, so the cheapest offer is the number that
actually reaches the shopping list. An average is dragged around by how many
expensive items happened to be in that week's ad, which says nothing about what
the client will pay.

**Why the probe price.** ``savings.cost_per_gram_protein`` costs up to two
queries per call, and a 90-day window over a full ad is six figures of rows —
far too slow to open a window with. But the expensive half of that chain
(size → matched food → protein per 100 g → grams of protein in the package)
depends only on ``(store, item_name)``, never on the price; only the final
division does. So grams of protein is resolved **once per distinct item** with
a probe price of 1.0 and cached, and each day's rows are then a division. Same
arithmetic as calling the function per row, two orders of magnitude fewer
queries.

**Why a price series must be scoped to a food.** "Food Lion's price today" is
not a quantity — the minimum over a whole ad is whichever small item happened
to be cheapest, which measures package size, not price. "Food Lion's chicken
price" is a quantity. So :data:`Metric.PRICE` is refused unless the caller
either filters to a food or groups by food; see :func:`price_trend`. $/g
protein carries no such restriction because dividing by grams of protein is
precisely what makes items of different sizes comparable.

**Honesty.** Same rule as ``grocery_planner/savings.py``: a number we cannot
compute is absent, never guessed.

- An item whose protein content cannot be resolved contributes no point rather
  than a zero (which would plot as a free lunch and win every day it appeared).
- A day with no resolvable item contributes no point rather than a gap filled
  by its neighbours — **a week with no scrape is a gap, not a zero**. Callers
  get missing days as missing points; the chart spaces points by real date
  (``gui/trends.py``), so a gap reads as a gap.
- A series with fewer than :data:`MIN_POINTS_TO_PLOT` points is reported as
  unplottable with a reason, because two dots joined by a line look exactly
  like a trend while carrying none — see :attr:`PriceTrend.is_plottable`.

**Store-agnostic (GFP-32).** Nothing here branches on store identity. A store
key is a grouping key and half of a ``deal_food_match`` lookup, never a
condition; adding store #3 changes no line of this module.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum

from .. import db, matching, savings
from ..stores import BY_KEY

#: A line needs at least this many days before it is a trend rather than a dot.
MIN_POINTS_TO_PLOT = 2

#: Default window. Matches ``records.RETENTION_FLOOR_DAYS`` (GFP-75/GFP-42):
#: asking for more history than retention promises to keep would quietly
#: produce a shorter chart than the axis claims.
DEFAULT_WINDOW_DAYS = 90


class Metric(str, Enum):
    """What the series measures. The unit a caller formats in follows from this."""

    #: Dollars per gram of protein — comparable across package sizes.
    PROTEIN = "protein"
    #: The observed package price in dollars. Only meaningful scoped to a food;
    #: see the module docstring and :func:`price_trend`.
    PRICE = "price"


class Dimension(str, Enum):
    """What one series *is* — the identity its ``key`` carries."""

    STORE = "store"
    FOOD = "food"


class UnknownFoodError(ValueError):
    """``food=`` matched nothing in the catalog.

    Raised rather than returning an empty trend: "this food has no prices yet"
    and "you typed a food that does not exist" are different answers, and
    silently showing the first for the second is how a typo becomes a
    conclusion about the market.
    """


class UnscopedPriceTrendError(ValueError):
    """A plain-price series was asked for without a food to scope it to."""


@dataclass(frozen=True)
class TrendPoint:
    """The best offer one series saw on one day."""

    day: str                 # ISO YYYY-MM-DD, as stored in price_history
    value: float             # in the units of the trend's Metric
    item_name: str           # which item produced it, for the tooltip
    price: float             # the observed package price (== value for Metric.PRICE)
    store: str               # which store it came from -- the interesting half
    #: when grouping by food, and a self-check when grouping by store
    size_grams: float | None = None   # package weight where one is known


@dataclass(frozen=True)
class TrendSeries:
    """One line: a store's series, or a food's series. Oldest point first."""

    key: str                 # store key, or food slug -- stable, machine-readable
    label: str               # the human name for it; presentation-ready, not drawn here
    points: list[TrendPoint] = field(default_factory=list)

    @property
    def is_plottable(self) -> bool:
        return len(self.points) >= MIN_POINTS_TO_PLOT

    @property
    def latest(self) -> TrendPoint | None:
        return self.points[-1] if self.points else None


@dataclass(frozen=True)
class PriceTrend:
    """Every series over one window, plus why there may not be a chart."""

    days: int
    metric: Metric = Metric.PROTEIN
    dimension: Dimension = Dimension.STORE
    series: list[TrendSeries] = field(default_factory=list)

    @property
    def plottable(self) -> list[TrendSeries]:
        return [s for s in self.series if s.is_plottable]

    @property
    def is_plottable(self) -> bool:
        return bool(self.plottable)

    @property
    def observed_days(self) -> int:
        """Distinct days any series produced a point on."""
        return len({point.day for s in self.series for point in s.points})

    @property
    def noun(self) -> str:
        """What this trend is *of*, for messages a user reads."""
        return "protein prices" if self.metric is Metric.PROTEIN else "prices"

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
                f"No {self.noun} on record yet. Run a scrape "
                "(Data ▸ Run scrape…) — a chart needs at least "
                f"{MIN_POINTS_TO_PLOT} days of history."
            )
        return (
            f"Only {self.observed_days} day of {self.noun} so far. "
            f"A trend needs at least {MIN_POINTS_TO_PLOT} days — this will "
            "start plotting once tomorrow's scrape lands."
        )


# --------------------------------------------------------------------------- #
# Resolution: item name -> the food and the grams behind it
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _Resolved:
    """What ``(store, item_name)`` resolves to, independent of any price."""

    protein_grams: float | None   # None when the protein chain does not close
    food_id: int | None           # None on the GFP-69 label-claim path (no matched food)
    size_grams: float | None


def _resolver(conn: sqlite3.Connection, *, food_fallback: bool):
    """Memoised ``(store, item_name) -> _Resolved``.

    See the module docstring for why this is separated from the price. The
    probe price of 1.0 is arbitrary and never leaves this function: it exists
    only because ``cost_per_gram_protein`` refuses a non-positive price, and
    the parts actually wanted here are computed from size and food match alone.

    ``food_fallback`` costs one extra query per *distinct unresolved item* and
    buys food attribution for rows whose protein chain does not close — a food
    matched with no protein figure on record still has a price worth plotting.
    Only a price series needs it, so a $/g-protein trend does not pay for it.
    """
    cache: dict[tuple[str, str], _Resolved] = {}

    def resolve(store: str, item_name: str) -> _Resolved:
        key = (store, item_name)
        if key in cache:
            return cache[key]

        cost = savings.cost_per_gram_protein(1.0, item_name, store, conn=conn)
        if cost is not None:
            resolved = _Resolved(
                protein_grams=cost.protein_grams if cost.protein_grams > 0 else None,
                food_id=cost.food_id,
                size_grams=cost.size_grams,
            )
        elif food_fallback:
            match = matching.get_match(store, item_name, conn=conn)
            resolved = _Resolved(
                protein_grams=None,
                food_id=match["food_id"] if match else None,
                size_grams=None,
            )
        else:
            resolved = _Resolved(None, None, None)

        cache[key] = resolved
        return resolved

    return resolve


def _food_ids(conn: sqlite3.Connection, food: str) -> list[int]:
    """Every catalog id ``food`` names, by slug or by name, case-insensitively."""
    rows = conn.execute(
        "SELECT id FROM foods WHERE lower(slug) = lower(?) OR lower(name) = lower(?)",
        (food, food),
    ).fetchall()
    if not rows:
        near = conn.execute(
            "SELECT name FROM foods WHERE name LIKE ? ORDER BY name LIMIT 5",
            (f"%{food}%",),
        ).fetchall()
        hint = (
            " — did you mean: " + ", ".join(row["name"] for row in near)
            if near else ""
        )
        raise UnknownFoodError(
            f"no food in the catalog is called {food!r}{hint}"
        )
    return [int(row["id"]) for row in rows]


def _food_names(conn: sqlite3.Connection, ids: set[int]) -> dict[int, tuple[str, str]]:
    """``food_id -> (slug, name)`` for the ids a trend actually produced."""
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT id, slug, name FROM foods WHERE id IN ({placeholders})",
        tuple(ids),
    ).fetchall()
    return {int(row["id"]): (row["slug"], row["name"]) for row in rows}


def _store_label(store_key: str) -> str:
    store = BY_KEY.get(store_key)
    return store.display_name if store else store_key


def has_price_history(conn: sqlite3.Connection | None = None) -> bool:
    """Is there ANY captured price at all, of any age, for any store?

    Deliberately unwindowed, unlike :func:`trend_stores`. This answers a
    different question — "has this install ever collected anything?" — and the
    GUI needs it to tell a genuinely empty database (GFP-104: show one plain
    message, not controls governing nothing) apart from a database that simply
    has nothing inside the selected window.

    ``LIMIT 1`` rather than a count: the answer is a yes/no and the table grows
    without bound (until GFP-42), so counting rows to learn "at least one" would
    get slower every week for no gain.
    """
    own = conn or db.connect()
    return own.execute("SELECT 1 FROM price_history LIMIT 1").fetchone() is not None


def trend_stores(
    days: int = DEFAULT_WINDOW_DAYS,
    today: date | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[tuple[str, str]]:
    """``(key, label)`` for every store with price history in the window.

    Feeds the chart's store selector (GFP-41). Deliberately derived from the
    data rather than from the store registry: a registry-driven list offers
    stores that have never been scraped, and picking one shows an empty chart
    that looks like a bug. Equally deliberately it does NOT resolve protein —
    that costs a query per distinct item and this only needs to fill a dropdown.

    Sorted by label so the list does not reshuffle under the user's cursor when
    prices move, unlike the series order, which is ranked on purpose.
    """
    own = conn or db.connect()
    anchor = today or date.today()
    since = (anchor - timedelta(days=days)).isoformat()
    rows = own.execute(
        "SELECT DISTINCT store FROM price_history WHERE captured_at >= ?", (since,)
    ).fetchall()
    return sorted(
        ((row["store"], _store_label(row["store"])) for row in rows),
        key=lambda pair: pair[1],
    )


# --------------------------------------------------------------------------- #
# The query
# --------------------------------------------------------------------------- #
def price_trend(
    *,
    metric: Metric = Metric.PROTEIN,
    dimension: Dimension = Dimension.STORE,
    days: int = DEFAULT_WINDOW_DAYS,
    store: str | None = None,
    food: str | None = None,
    postal_code: str | None = None,
    today: date | None = None,
    conn: sqlite3.Connection | None = None,
) -> PriceTrend:
    """The best daily offer per series over the last ``days``, as a time series.

    Reads ``price_history`` (GFP-39), which a scrape appends to rather than
    replacing, so this survives the ``deals`` table being rewritten every run.
    Series are returned cheapest-latest first, so whichever store or food is
    currently winning is the first line a caller draws.

    ``metric`` picks what the value means, ``dimension`` picks what one series
    is. ``store`` / ``food`` / ``postal_code`` narrow the rows considered; a
    ``food`` filter is what makes :data:`Metric.PRICE` legal (see the module
    docstring), and ``postal_code`` matters once clients have their own ZIPs
    (GFP-53) — history from two ZIPs is two markets, not one series.

    Raises :class:`UnscopedPriceTrendError` for an unscoped price series and
    :class:`UnknownFoodError` for a ``food`` the catalog does not know.
    """
    if metric is Metric.PRICE and food is None and dimension is not Dimension.FOOD:
        raise UnscopedPriceTrendError(
            "a price series needs a food to be about: pass food=... or "
            "dimension=Dimension.FOOD. The cheapest item in a whole weekly ad "
            "measures package size, not price — use Metric.PROTEIN to compare "
            "across foods."
        )

    own = conn or db.connect()
    anchor = today or date.today()
    since = (anchor - timedelta(days=days)).isoformat()

    where = ["captured_at >= ?", "COALESCE(dollar_price, sale_price, regular_price) > 0"]
    params: list[object] = [since]
    if store:
        where.append("store = ?")
        params.append(store)
    if postal_code:
        where.append("postal_code = ?")
        params.append(postal_code)

    rows = own.execute(
        "SELECT store, item_name, captured_at, "
        "COALESCE(dollar_price, sale_price, regular_price) AS price "
        f"FROM price_history WHERE {' AND '.join(where)} "
        "ORDER BY captured_at",
        params,
    ).fetchall()

    # A food scope and a food dimension both need each row attributed to a food,
    # which the protein chain alone cannot always do -- see `_resolver`.
    by_food = dimension is Dimension.FOOD or food is not None
    resolve = _resolver(own, food_fallback=by_food)
    wanted_foods = set(_food_ids(own, food)) if food else None

    # (series key, day) -> the best point seen for it so far.
    best: dict[tuple[str, str], TrendPoint] = {}
    food_ids_seen: set[int] = set()

    for row in rows:
        resolved = resolve(row["store"], row["item_name"])

        if wanted_foods is not None and resolved.food_id not in wanted_foods:
            continue

        if metric is Metric.PROTEIN:
            if resolved.protein_grams is None:
                continue  # no protein figure -> no point, never a zero
            value = row["price"] / resolved.protein_grams
        else:
            value = row["price"]

        if dimension is Dimension.FOOD:
            if resolved.food_id is None:
                continue  # a food series needs a food; a label claim has none
            series_key: str | int = resolved.food_id
            food_ids_seen.add(resolved.food_id)
        else:
            series_key = row["store"]

        key = (str(series_key), row["captured_at"])
        current = best.get(key)
        if current is None or value < current.value:
            best[key] = TrendPoint(
                day=row["captured_at"],
                value=value,
                item_name=row["item_name"],
                price=row["price"],
                store=row["store"],
                size_grams=resolved.size_grams,
            )

    grouped: dict[str, list[TrendPoint]] = {}
    for (series_key, _day), point in best.items():
        grouped.setdefault(series_key, []).append(point)

    if dimension is Dimension.FOOD:
        names = _food_names(own, food_ids_seen)
        labelled = {
            key: names.get(int(key), (key, key)) for key in grouped
        }
        series = [
            TrendSeries(
                key=labelled[key][0],
                label=labelled[key][1],
                points=sorted(points, key=lambda p: p.day),
            )
            for key, points in grouped.items()
        ]
    else:
        series = [
            TrendSeries(
                key=key,
                label=_store_label(key),
                points=sorted(points, key=lambda p: p.day),
            )
            for key, points in grouped.items()
        ]

    # Cheapest most-recent value first: whoever is currently winning leads.
    series.sort(key=lambda s: (s.latest.value if s.latest else float("inf"), s.key))
    return PriceTrend(days=days, metric=metric, dimension=dimension, series=series)


def protein_price_trend(
    days: int = DEFAULT_WINDOW_DAYS,
    store: str | None = None,
    today: date | None = None,
    conn: sqlite3.Connection | None = None,
    *,
    postal_code: str | None = None,
) -> PriceTrend:
    """Cheapest $/g protein per store per day — the main window's question.

    A named shorthand for the :func:`price_trend` defaults, kept because this
    one combination is the headline of the whole product and reads better at
    its call sites than four keyword arguments would.
    """
    return price_trend(
        metric=Metric.PROTEIN,
        dimension=Dimension.STORE,
        days=days,
        store=store,
        postal_code=postal_code,
        today=today,
        conn=conn,
    )
