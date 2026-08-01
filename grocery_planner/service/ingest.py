"""Scrape ingestion (GFP-14): pulling fresh deals from a store scraper into SQLite.

Split out of the former ``service.py`` module (GFP-43) as the front-end-agnostic
service layer grows to cover customers, nutrition and ingest. The CLI (``cli``)
and the PySide6 GUI (``gui``) both drive scraping through :func:`run_scrape` so
the scrape + persist logic lives in exactly one place.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from .. import db, importers
from ..scrapers import SCRAPERS


class UnknownStoreError(ValueError):
    """Raised when a store key has no registered scraper."""


class ScrapeGuardError(RuntimeError):
    """Base class for run_scrape's replace-guards (GFP-67).

    The whole product's data comes from one undocumented, unauthenticated
    Flipp endpoint (see ``scrapers/base.py``). If that endpoint changes shape
    and a parse silently yields nothing (or next to nothing) *without*
    raising, the old code would still DELETE this store+ZIP's rows and have
    nothing real to put back — total silent data loss. Raising one of these
    instead means the DELETE never happens: existing ``deals`` and
    ``price_history`` rows for this (store, postal_code) are left exactly as
    they were. Pass ``force=True`` to :func:`run_scrape` to replace anyway,
    once you've independently confirmed the low count is real and not
    upstream breakage.
    """


class EmptyScrapeError(ScrapeGuardError):
    """A scrape parsed to zero deal rows.

    Distinct from GFP-16's "no current weekly flyer" ``RuntimeError`` (raised
    earlier, inside the scraper, before any rows exist): this covers a
    well-formed response that *found* a flyer/coupons but the parse produced
    nothing usable from it — the more dangerous case because it looks like a
    successful run.
    """


class ImplausibleCollapseError(ScrapeGuardError):
    """A scrape returned far fewer rows than the last known-good capture.

    E.g. 900 rows yesterday and 3 today. That is a parser/response-shape
    problem, not a quiet ad week — see :data:`_COLLAPSE_RATIO` for the exact
    rule and why it was chosen.
    """


def available_scrapers() -> list[str]:
    """Sorted list of store keys that can be scraped."""
    return sorted(SCRAPERS)


# --------------------------------------------------------------------------- #
# GFP-67 replace-guard: refuse to DELETE this store+ZIP's rows unless the new
# scrape looks like a real replacement for them.
# --------------------------------------------------------------------------- #
# Trip the collapse guard only on a drop of more than 90% (current count under
# 10% of the last capture). Ordinary week-to-week variation — even a much
# smaller ad — rarely wipes out nine in ten rows; a Flipp response-shape
# change routinely wipes out ~100%. 90% is comfortably below "real" noise and
# comfortably above "the parser fell through empty-handed", so it separates
# the two cases without needing any store-specific tuning.
_COLLAPSE_RATIO = 0.1

# Only apply the collapse check when the previous capture was itself
# reasonably sized. A store whose last capture was 5 rows dropping to 1 row
# is not evidence of upstream breakage the way 900 -> 3 is — it's just a
# small store getting smaller — so below this floor the ratio check is
# skipped entirely (only the zero-row guard still applies).
_COLLAPSE_MIN_PREVIOUS = 20


def _previous_capture_count(
    conn: sqlite3.Connection, store_key: str, zip_code: str
) -> int | None:
    """Row count of this (store, postal_code)'s most recent price_history capture.

    ``price_history`` (GFP-39) records every observation and is keyed one row
    per (store, postal_code, item_name, deal_type) per calendar day, so the
    most recent ``captured_at`` group *is* the last successful scrape's row
    count — a real baseline instead of a hard-coded number. ``None`` means
    there is no prior capture at all (first-ever scrape for this store+ZIP),
    in which case the collapse guard has nothing to compare against.
    """
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM price_history WHERE store=? AND postal_code=? "
        "AND captured_at=(SELECT MAX(captured_at) FROM price_history "
        "WHERE store=? AND postal_code=?)",
        (store_key, zip_code, store_key, zip_code),
    ).fetchone()
    if row is None or row["n"] == 0:
        return None
    return int(row["n"])


def _guard_replacement(
    conn: sqlite3.Connection, store_key: str, zip_code: str, current: int, force: bool
) -> None:
    """Raise a :class:`ScrapeGuardError` rather than let a bad scrape replace good data.

    Called after the scraper has returned rows but *before* the existing
    ``deals``/``price_history`` rows are touched, so an error here leaves the
    database exactly as it was.
    """
    if force:
        return

    if current == 0:
        raise EmptyScrapeError(
            f"Scrape of {store_key!r} for postal code {zip_code} returned 0 deals. "
            "This almost always means Flipp's response shape changed under the "
            "parser (an unauthenticated, undocumented endpoint), not that the "
            "store genuinely has no ad this week. Refusing to replace existing "
            "data — the deals and price_history rows already stored for this "
            "store and ZIP were left untouched. If you have independently "
            "confirmed (e.g. via flipp.com) that this store truly has nothing "
            "active right now, call run_scrape(..., force=True) to accept the "
            "empty result and replace anyway."
        )

    previous = _previous_capture_count(conn, store_key, zip_code)
    if (
        previous is not None
        and previous >= _COLLAPSE_MIN_PREVIOUS
        and current < previous * _COLLAPSE_RATIO
    ):
        raise ImplausibleCollapseError(
            f"Scrape of {store_key!r} for postal code {zip_code} returned only "
            f"{current} deals, down from {previous} at the last capture (a drop "
            f"of over {round((1 - _COLLAPSE_RATIO) * 100)}%). That is far more "
            "likely to be Flipp response-shape breakage than a genuinely quiet "
            "ad week. Refusing to replace existing data — the deals and "
            "price_history rows already stored for this store and ZIP were left "
            "untouched. If this really is a small ad week, call run_scrape(..., "
            "force=True) to accept it and replace anyway."
        )


_HISTORY_UPSERT = (
    "INSERT INTO price_history("
    "store, postal_code, item_name, sub_category, deal_type, regular_price, "
    "sale_price, dollar_price, discount_amount, discount_percent, source, "
    "captured_at, updated_at) "
    "VALUES (:store, :postal_code, :item_name, :sub_category, :deal_type, "
    ":regular_price, :sale_price, :dollar_price, :discount_amount, :discount_percent, "
    ":source, :captured_at, :updated_at) "
    "ON CONFLICT(store, postal_code, item_name, deal_type, captured_at) DO UPDATE SET "
    "regular_price=excluded.regular_price, sale_price=excluded.sale_price, "
    "dollar_price=excluded.dollar_price, discount_amount=excluded.discount_amount, "
    "discount_percent=excluded.discount_percent, source=excluded.source, "
    "updated_at=excluded.updated_at"
)


def run_scrape(
    store_key: str,
    postal_code: str | None = None,
    conn: sqlite3.Connection | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Scrape a store's fresh deals and persist them, replacing prior scrape rows.

    Replacement (the ``DELETE`` below) is scoped to this store, this source,
    *and* this postal code (GFP-55) so scraping one ZIP never destroys another
    ZIP's rows for the same store. Every row also carries the ``postal_code``
    it was scraped for (GFP-54).

    Before that DELETE runs, the new rows are checked against two guards
    (GFP-67) so a broken parse can never wipe out good data:

    - :class:`EmptyScrapeError` if the scrape returned zero rows.
    - :class:`ImplausibleCollapseError` if it returned far fewer rows than
      the last successful capture recorded in ``price_history`` (see
      :data:`_COLLAPSE_RATIO`/:data:`_COLLAPSE_MIN_PREVIOUS` for the exact
      rule). This does *not* replace GFP-16's guard, which already raises
      inside the scraper when there is no current weekly flyer at all — that
      covers "nothing to scrape"; this covers "scraped, but the result looks
      broken."

    Both guards are skipped when ``force=True``, the deliberate escape hatch
    for a genuinely tiny (or empty) ad week — without it, a real quiet week
    would make a store permanently unscrapeable.

    Each scraped row is additionally appended to ``price_history`` (GFP-39) so
    price movement over time survives even though ``deals`` itself is a
    current-snapshot table that gets overwritten on every scrape. The append
    is an upsert keyed by calendar day, so re-running a scrape twice in one
    day updates today's history row rather than fabricating a second data
    point.

    Returns ``{"flyer": ..., "stats": ..., "postal_code": ...}``. Raises
    :class:`UnknownStoreError` for an unregistered store. When called from a
    worker thread, pass no ``conn`` so a thread-local connection is opened.
    """
    scraper = SCRAPERS.get(store_key)
    if scraper is None:
        raise UnknownStoreError(store_key)

    zip_code = postal_code or scraper.DEFAULT_POSTAL_CODE
    rows, flyer, stats = scraper.scrape(postal_code=postal_code)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    today = now[:10]
    own = conn or db.connect()
    _guard_replacement(own, store_key, zip_code, len(rows), force)
    cols = importers.DEAL_COLUMNS
    own.execute(
        "DELETE FROM deals WHERE store=? AND source=? AND postal_code=?",
        (store_key, "scrape", zip_code),
    )
    own.executemany(
        f"INSERT INTO deals(store, postal_code, {', '.join(cols)}, source, imported_at) "
        f"VALUES (:store, :postal_code, {', '.join(':' + c for c in cols)}, :source, :imported_at)",
        [{**r, "store": store_key, "postal_code": zip_code, "source": "scrape", "imported_at": now}
         for r in rows],
    )
    own.executemany(
        _HISTORY_UPSERT,
        [{**r, "store": store_key, "postal_code": zip_code, "source": "scrape",
          "captured_at": today, "updated_at": now} for r in rows],
    )
    own.commit()
    return {"flyer": flyer, "stats": stats, "postal_code": zip_code}
