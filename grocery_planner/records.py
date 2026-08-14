# ######### decohen-partners ##########
# Protein Ledger
"""Durable record low/high per item (GFP-75).

The problem this solves
-----------------------
``price_history`` (GFP-39) accumulates raw observations and GFP-42 will prune
it. An all-time low computed by scanning that history vanishes the moment
retention runs, and cannot be recomputed afterwards -- the observation it came
from is gone. So records are **maintained on write and stored**, not derived
on read. That is also why GFP-75 has to land before or with GFP-42.

What a record is keyed on
-------------------------
``(store, postal_code, item_name)``. Not ``deals.id``, which does not survive
a rescrape (see ``db_script/migration/0013_GFP-75.ddl`` for the full
reasoning, and GFP-25 for the same fallback made for the same reason).

Price *and* cost-per-gram-of-protein
------------------------------------
Both are tracked, because they can move in opposite directions: a price can
fall while $/g protein rises if the package shrank. Tracking price alone would
make shrinkflation invisible in the tool built to catch it.

Rolling windows are derived, not stored
---------------------------------------
A rolling window cannot be maintained incrementally -- as the window slides,
the current minimum can drop out of it, and finding the new one means
re-reading the observations still inside. Anything stored would be a cache
that goes stale between scrapes, and a stale "30-day low" is worse than none.
:func:`rolling_window` computes them from ``price_history`` on demand, which
stays correct only while retention keeps at least :data:`RETENTION_FLOOR_DAYS`
of history. GFP-42 must honour that floor.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable, Sequence

from . import savings

# The rolling windows this module offers, longest last.
ROLLING_WINDOWS_DAYS: tuple[int, ...] = (30, 90)

# The minimum history GFP-42's retention policy may keep without breaking
# rolling windows. Stated here, in code, so the coupling is enforceable rather
# than remembered: retention that prunes below this silently starts returning
# a "90-day low" computed from less than 90 days.
RETENTION_FLOOR_DAYS: int = max(ROLLING_WINDOWS_DAYS)


@dataclass(frozen=True)
class Record:
    """One item's durable extremes at one store and ZIP."""

    store: str
    postal_code: str
    item_name: str
    record_low_price: float | None
    record_low_price_at: str | None
    record_high_price: float | None
    record_high_price_at: str | None
    record_low_cpgp: float | None
    record_low_cpgp_at: str | None
    record_high_cpgp: float | None
    record_high_cpgp_at: str | None
    first_seen_at: str
    last_seen_at: str
    observations: int

    @property
    def is_thin(self) -> bool:
        """True when this record rests on too few observations to mean much.

        An "all-time low" from a single sighting is just today's price wearing
        a hat. Callers that display records should say so rather than let a
        one-observation record read with the same authority as a year of them.
        """
        return self.observations < 3


@dataclass(frozen=True)
class RollingWindow:
    """Min/max inside a recent window, computed from ``price_history``."""

    days: int
    low_price: float | None
    high_price: float | None
    observations: int

    @property
    def is_complete(self) -> bool:
        """Whether the window had any data at all to summarise."""
        return self.observations > 0


def observed_price(row: dict[str, Any]) -> float | None:
    """The price to record for a scraped row, or ``None`` if it carries none.

    Mirrors what the rest of the codebase treats as "the price of this deal":
    ``dollar_price`` (the numeric extraction) first, then ``sale_price``, then
    ``regular_price``. Per savings.py's rule 1, absent stays absent -- a row
    with no readable price contributes no observation rather than a zero,
    which would otherwise become an unbeatable and entirely fictional record
    low.
    """
    for key in ("dollar_price", "sale_price", "regular_price"):
        value = row.get(key)
        if value is None:
            continue
        try:
            price = float(value)
        except (TypeError, ValueError):
            continue
        # A non-positive price is a parse artefact, not a free lunch.
        if price > 0:
            return price
    return None


_SELECT = (
    "SELECT store, postal_code, item_name, record_low_price, record_low_price_at, "
    "record_high_price, record_high_price_at, record_low_cpgp, record_low_cpgp_at, "
    "record_high_cpgp, record_high_cpgp_at, first_seen_at, last_seen_at, observations "
    "FROM price_records"
)


def _to_record(row: sqlite3.Row) -> Record:
    return Record(**{k: row[k] for k in row.keys()})


def update_records(
    conn: sqlite3.Connection,
    store: str,
    postal_code: str,
    rows: Iterable[dict[str, Any]],
    observed_on: str,
) -> dict[str, int]:
    """Fold one scrape's rows into the durable records. Returns a summary.

    Called from ``service/ingest.run_scrape`` *after* the replace-guards have
    passed, so a scrape that gets refused never moves a record. Does not
    commit -- the caller owns the transaction, so records and the history rows
    they summarise land together or not at all.

    Within a single scrape the same ``item_name`` can appear more than once
    (a weekly-ad row and a coupon row for the same product). Each is folded in
    turn, so the best of them wins the day rather than the last one seen.

    A new extreme is recorded only when it strictly beats the stored one, so
    ``record_low_price_at`` keeps the date the price was FIRST achieved rather
    than creeping forward every time it is matched again.
    """
    seen: dict[str, dict[str, float | None]] = {}

    # Collapse this scrape's rows to one best/worst pair per item first, so
    # the database sees one write per item no matter how many rows mention it.
    for row in rows:
        name = (row.get("item_name") or "").strip()
        if not name:
            continue
        price = observed_price(row)
        if price is None:
            # No price means no observation -- not a record, and not even a
            # "seen today". A row we cannot price tells us nothing about
            # what this item costs.
            continue

        cpgp = None
        cost = savings.cost_per_gram_protein(price, name, store, conn=conn)
        if cost is not None:
            cpgp = cost.cost_per_gram_protein

        agg = seen.setdefault(
            name, {"low": price, "high": price, "cpgp_low": cpgp, "cpgp_high": cpgp}
        )
        agg["low"] = min(agg["low"], price)
        agg["high"] = max(agg["high"], price)
        if cpgp is not None:
            agg["cpgp_low"] = cpgp if agg["cpgp_low"] is None else min(agg["cpgp_low"], cpgp)
            agg["cpgp_high"] = cpgp if agg["cpgp_high"] is None else max(agg["cpgp_high"], cpgp)

    created = updated = new_lows = 0
    for name, agg in seen.items():
        existing = conn.execute(
            _SELECT + " WHERE store=? AND postal_code=? AND item_name=?",
            (store, postal_code, name),
        ).fetchone()

        if existing is None:
            conn.execute(
                "INSERT INTO price_records("
                "store, postal_code, item_name, record_low_price, record_low_price_at, "
                "record_high_price, record_high_price_at, record_low_cpgp, "
                "record_low_cpgp_at, record_high_cpgp, record_high_cpgp_at, "
                "first_seen_at, last_seen_at, observations, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)",
                (
                    store, postal_code, name,
                    agg["low"], observed_on,
                    agg["high"], observed_on,
                    agg["cpgp_low"], observed_on if agg["cpgp_low"] is not None else None,
                    agg["cpgp_high"], observed_on if agg["cpgp_high"] is not None else None,
                    observed_on, observed_on, observed_on,
                ),
            )
            created += 1
            continue

        record = _to_record(existing)
        low, low_at = _keep_lower(record.record_low_price, record.record_low_price_at,
                                  agg["low"], observed_on)
        high, high_at = _keep_higher(record.record_high_price, record.record_high_price_at,
                                     agg["high"], observed_on)
        c_low, c_low_at = _keep_lower(record.record_low_cpgp, record.record_low_cpgp_at,
                                      agg["cpgp_low"], observed_on)
        c_high, c_high_at = _keep_higher(record.record_high_cpgp, record.record_high_cpgp_at,
                                         agg["cpgp_high"], observed_on)

        if low_at == observed_on and record.record_low_price is not None:
            new_lows += 1

        # An observation is a DAY, not a call. Re-running a scrape twice in
        # one day, or replaying the backfill, must not inflate the count --
        # otherwise `is_thin` silently stops flagging a record that really is
        # built on a single sighting, and a re-run would launder a thin record
        # into an authoritative-looking one. This mirrors price_history's own
        # per-calendar-day grain (GFP-39).
        counts = record.last_seen_at != observed_on

        # Never let a replay drag the bounds the wrong way: an out-of-order
        # call with an older date must extend first_seen_at backwards, not
        # move last_seen_at backwards.
        first_seen = min(record.first_seen_at, observed_on)
        last_seen = max(record.last_seen_at, observed_on)

        conn.execute(
            "UPDATE price_records SET record_low_price=?, record_low_price_at=?, "
            "record_high_price=?, record_high_price_at=?, record_low_cpgp=?, "
            "record_low_cpgp_at=?, record_high_cpgp=?, record_high_cpgp_at=?, "
            "first_seen_at=?, last_seen_at=?, observations=observations+?, updated_at=? "
            "WHERE store=? AND postal_code=? AND item_name=?",
            (low, low_at, high, high_at, c_low, c_low_at, c_high, c_high_at,
             first_seen, last_seen, 1 if counts else 0, observed_on,
             store, postal_code, name),
        )
        updated += 1

    return {"created": created, "updated": updated, "new_lows": new_lows}


def _keep_lower(
    stored: float | None, stored_at: str | None, candidate: float | None, on: str
) -> tuple[float | None, str | None]:
    """Strictly-better wins; ties keep the ORIGINAL date.

    Strictness is the point: if matching the record moved the date forward, an
    item sitting at its usual price would look like it set a new low every
    week, and "record low, first seen today" would stop meaning anything.
    """
    if candidate is None:
        return stored, stored_at
    if stored is None or candidate < stored:
        return candidate, on
    return stored, stored_at


def _keep_higher(
    stored: float | None, stored_at: str | None, candidate: float | None, on: str
) -> tuple[float | None, str | None]:
    if candidate is None:
        return stored, stored_at
    if stored is None or candidate > stored:
        return candidate, on
    return stored, stored_at


def backfill_from_history(conn: sqlite3.Connection) -> dict[str, int]:
    """Seed records from whatever ``price_history`` still holds.

    Run once when the feature lands. Without it, every record starts empty and
    the history already captured -- which is the only history that will ever
    exist for those days -- is wasted.

    Fully idempotent, including the observation count: only strictly-better
    values are kept, and ``observations`` counts distinct days rather than
    calls, so replaying cannot inflate it and launder a one-sighting record
    into an authoritative-looking one.

    This is a best-effort catch-up, not a substitute for recording on write.
    It can only see history that has not already been pruned, which is exactly
    the limitation that makes GFP-75-before-GFP-42 non-negotiable.
    """
    rows = conn.execute(
        "SELECT store, postal_code, item_name, sub_category, deal_type, "
        "regular_price, sale_price, dollar_price, captured_at "
        "FROM price_history ORDER BY captured_at ASC"
    ).fetchall()

    by_day: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (row["store"], row["postal_code"], row["captured_at"])
        by_day.setdefault(key, []).append(dict(row))

    totals = {"created": 0, "updated": 0, "new_lows": 0, "days": 0}
    # Chronological order matters: replaying days out of order would attribute
    # a record to the wrong date even though the value itself would be right.
    for (store, postal_code, captured_at) in sorted(by_day):
        summary = update_records(
            conn, store, postal_code, by_day[(store, postal_code, captured_at)], captured_at
        )
        totals["created"] += summary["created"]
        totals["updated"] += summary["updated"]
        totals["new_lows"] += summary["new_lows"]
        totals["days"] += 1
    conn.commit()
    return totals


def rolling_window(
    conn: sqlite3.Connection,
    store: str,
    postal_code: str,
    item_name: str,
    days: int = 30,
    today: date | None = None,
) -> RollingWindow:
    """Min/max price within the last ``days``, read from ``price_history``.

    Derived rather than stored -- see the module docstring. Answers a
    different question from the all-time record: "has this got more expensive
    lately?" Price creep and shrinkflation are invisible against an all-time
    low, which a slowly-rising price never disturbs.
    """
    anchor = today or date.today()
    since = (anchor - timedelta(days=days)).isoformat()
    row = conn.execute(
        "SELECT COUNT(*) AS n, "
        "MIN(COALESCE(dollar_price, sale_price, regular_price)) AS lo, "
        "MAX(COALESCE(dollar_price, sale_price, regular_price)) AS hi "
        "FROM price_history WHERE store=? AND postal_code=? AND item_name=? "
        "AND captured_at >= ? "
        "AND COALESCE(dollar_price, sale_price, regular_price) > 0",
        (store, postal_code, item_name, since),
    ).fetchone()
    return RollingWindow(
        days=days,
        low_price=row["lo"],
        high_price=row["hi"],
        observations=int(row["n"] or 0),
    )


def fetch_records(
    conn: sqlite3.Connection,
    store: str | None = None,
    postal_code: str | None = None,
    search: str | None = None,
    order_by: str = "cpgp",
    limit: int = 20,
) -> list[Record]:
    """Stored records, cheapest-protein first by default.

    ``order_by='cpgp'`` ranks by record low $/g protein and drops items that
    have none -- there is no honest ordering for an item whose protein cost
    was never computable. ``order_by='price'`` ranks by record low price over
    everything priced.
    """
    where: list[str] = []
    params: list[Any] = []
    if store:
        where.append("store=?")
        params.append(store)
    if postal_code:
        where.append("postal_code=?")
        params.append(postal_code)
    if search:
        where.append("item_name LIKE ?")
        params.append(f"%{search}%")

    if order_by == "cpgp":
        where.append("record_low_cpgp IS NOT NULL")
        order = "record_low_cpgp ASC"
    elif order_by == "price":
        where.append("record_low_price IS NOT NULL")
        order = "record_low_price ASC"
    else:
        raise ValueError(f"unknown order_by {order_by!r} (use 'cpgp' or 'price')")

    sql = _SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {order}"
    if limit and limit > 0:
        sql += " LIMIT ?"
        params.append(limit)
    return [_to_record(r) for r in conn.execute(sql, params)]


def count_records(conn: sqlite3.Connection) -> dict[str, int]:
    """How much the record table actually knows -- for CLI/GUI summaries."""
    row = conn.execute(
        "SELECT COUNT(*) AS items, "
        "SUM(CASE WHEN record_low_cpgp IS NOT NULL THEN 1 ELSE 0 END) AS with_cpgp, "
        "SUM(CASE WHEN observations >= 3 THEN 1 ELSE 0 END) AS established "
        "FROM price_records"
    ).fetchone()
    return {
        "items": int(row["items"] or 0),
        "with_cpgp": int(row["with_cpgp"] or 0),
        "established": int(row["established"] or 0),
    }


def prune_history(
    conn: sqlite3.Connection,
    days: int | None = None,
    today: date | None = None,
) -> int:
    """Delete price history older than the retention window (GFP-42).

    Returns how many rows went. Safe to call repeatedly -- a second call with
    nothing to do deletes nothing and costs one indexed comparison.

    **Records survive this, and that is the point.** GFP-75's record lows are
    stored, not recomputed, precisely so that "cheapest ever seen" outlives the
    observation that produced it. Rolling windows are the opposite: they are
    recomputed on demand and therefore only as good as the history still here,
    which is why :data:`RETENTION_FLOOR_DAYS` is a floor the caller may not go
    below (``config._retention_days`` enforces it).

    Measured before choosing the default: price history costs about 411 bytes
    per row including indexes, at roughly 1,600 rows per day for two stores.
    That is ~239 MB at 365 days, against a ~500 MB budget which would be
    reached somewhere near 763 days. Adding stores scales it linearly, so the
    figure to re-measure is rows per day, not the byte count.
    """
    if days is None:
        from . import config
        days = config.history_retention_days()

    cutoff = ((today or date.today()) - timedelta(days=days)).isoformat()
    cursor = conn.execute(
        "DELETE FROM price_history WHERE captured_at < ?", (cutoff,)
    )
    conn.commit()
    return cursor.rowcount or 0
