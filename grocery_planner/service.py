"""Front-end-agnostic operations shared by every UI (GFP-14).

The CLI (``cli``) and the PySide6 GUI (``gui``) both drive the app through this
module so scrape + query logic lives in exactly one place. Functions here return
plain data and never print — the front end owns all presentation.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from typing import Any

from . import db, importers
from .scrapers import SCRAPERS


class UnknownStoreError(ValueError):
    """Raised when a store key has no registered scraper."""


def available_scrapers() -> list[str]:
    """Sorted list of store keys that can be scraped."""
    return sorted(SCRAPERS)


def run_scrape(
    store_key: str,
    postal_code: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Scrape a store's fresh deals and persist them, replacing prior scrape rows.

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
    own = conn or db.connect()
    cols = importers.DEAL_COLUMNS
    own.execute("DELETE FROM deals WHERE store=? AND source=?", (store_key, "scrape"))
    own.executemany(
        f"INSERT INTO deals(store, {', '.join(cols)}, source, imported_at) "
        f"VALUES (:store, {', '.join(':' + c for c in cols)}, :source, :imported_at)",
        [{**r, "store": store_key, "source": "scrape", "imported_at": now} for r in rows],
    )
    own.commit()
    return {"flyer": flyer, "stats": stats, "postal_code": zip_code}


# A deal is expired only when it has an end date that is already past (GFP-16);
# a missing date is unknown, never expired. Dates are stored as ISO YYYY-MM-DD,
# so a plain string comparison orders them correctly.
_HAS_END_DATE = "valid_to IS NOT NULL AND valid_to <> ''"
_EXPIRED_SQL = f"CASE WHEN {_HAS_END_DATE} AND valid_to < ? THEN 1 ELSE 0 END"

_DEAL_COLUMNS = (
    "store, item_name, sub_category, deal_type, sale_price, "
    "dollar_price, valid_from, valid_to"
)


def today_iso() -> str:
    """Today's local date as ``YYYY-MM-DD`` — the default freshness cutoff."""
    return date.today().isoformat()


def is_expired(valid_to: str | None, today: str | None = None) -> bool:
    """True when ``valid_to`` is a date that has already passed."""
    if not valid_to:
        return False
    return valid_to < (today or today_iso())


def _deal_filters(
    store: str | None, on_sale: bool, hide_expired: bool, today: str
) -> tuple[str, list[Any]]:
    """Build the shared WHERE clause so every front end filters identically."""
    clauses: list[str] = []
    params: list[Any] = []
    if store:
        clauses.append("store=?")
        params.append(store)
    if on_sale:
        clauses.append("sale_price IS NOT NULL")
    if hide_expired:
        clauses.append(f"NOT ({_HAS_END_DATE} AND valid_to < ?)")
        params.append(today)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def fetch_deals(
    store: str | None = None,
    limit: int = 0,
    on_sale: bool = False,
    hide_expired: bool = False,
    today: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[sqlite3.Row]:
    """Return stored deal rows (``limit`` 0 = all).

    Each row carries an ``expired`` flag (1/0) so a front end can grey out stale
    deals; pass ``hide_expired=True`` to drop them instead. ``today`` overrides
    the cutoff date (tests, "what was valid on...").
    """
    own = conn or db.connect()
    day = today or today_iso()
    where, params = _deal_filters(store, on_sale, hide_expired, day)
    lim = "" if not limit else f" LIMIT {int(limit)}"
    sql = (
        f"SELECT {_DEAL_COLUMNS}, {_EXPIRED_SQL} AS expired "
        f"FROM deals{where} ORDER BY store, item_name{lim}"
    )
    return own.execute(sql, [day, *params]).fetchall()


def count_deals(
    store: str | None = None,
    on_sale: bool = False,
    hide_expired: bool = False,
    today: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Count deals matching the same filters as :func:`fetch_deals`, ignoring ``limit``."""
    own = conn or db.connect()
    day = today or today_iso()
    where, params = _deal_filters(store, on_sale, hide_expired, day)
    return own.execute(f"SELECT COUNT(*) FROM deals{where}", params).fetchone()[0]
