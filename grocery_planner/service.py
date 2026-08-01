"""Front-end-agnostic operations shared by every UI (GFP-14).

The CLI (``cli``) and the PySide6 GUI (``gui``) both drive the app through this
module so scrape + query logic lives in exactly one place. Functions here return
plain data and never print — the front end owns all presentation.
"""
from __future__ import annotations

import csv
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from . import db, importers, savings
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


# Deal-type groups (GFP-17). The stored deal_type is fine-grained ("Weekly Ad
# (price not listed)", "Percent Off Coupon", ...); users think in these buckets,
# so one key maps to one SQL predicate for both the CLI flag and the GUI radio.
DEAL_TYPE_GROUPS: dict[str, tuple[str, str]] = {
    "all": ("All", ""),
    "weekly": ("Weekly Ad", "deal_type LIKE 'Weekly Ad%'"),
    "coupon": ("Coupon", "deal_type LIKE '%Coupon%'"),
    "bogo": ("BOGO", "deal_type = 'Bogo'"),
}


class UnknownDealTypeError(ValueError):
    """Raised for a deal-type group key that is not in :data:`DEAL_TYPE_GROUPS`."""


def _like(term: str) -> str:
    """Wrap a search term for LIKE, neutralizing the wildcards a user may type."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _deal_filters(
    store: str | None = None,
    category: str | None = None,
    deal_type: str | None = None,
    on_sale: bool = False,
    hide_expired: bool = False,
    loyalty_only: bool = False,
    search: str = "",
    valid_on: str | None = None,
    today: str = "",
) -> tuple[str, list[Any]]:
    """Build the shared WHERE clause so every front end filters identically.

    This is the single definition of what each filter *means*; ``fetch_deals``
    and ``count_deals`` both route through it, and every GUI control maps 1:1
    onto one of these arguments (GFP-17).
    """
    clauses: list[str] = []
    params: list[Any] = []
    if store:
        clauses.append("store=?")
        params.append(store)
    if category:
        clauses.append("sub_category=?")
        params.append(category)
    if deal_type and deal_type != "all":
        if deal_type not in DEAL_TYPE_GROUPS:
            raise UnknownDealTypeError(deal_type)
        clauses.append(DEAL_TYPE_GROUPS[deal_type][1])
    if on_sale:
        clauses.append("sale_price IS NOT NULL")
    if loyalty_only:
        clauses.append("loyalty_required='Y'")
    if search:
        clauses.append(
            "(item_name LIKE ? ESCAPE '\\' OR deal_description LIKE ? ESCAPE '\\')"
        )
        params += [_like(search), _like(search)]
    if hide_expired:
        clauses.append(f"NOT ({_HAS_END_DATE} AND valid_to < ?)")
        params.append(today)
    if valid_on:
        # Undated ends are open-ended, so an absent bound never excludes a row.
        clauses.append(
            "(valid_from IS NULL OR valid_from='' OR valid_from<=?) "
            "AND (valid_to IS NULL OR valid_to='' OR valid_to>=?)"
        )
        params += [valid_on, valid_on]
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def fetch_deals(
    store: str | None = None,
    limit: int = 0,
    on_sale: bool = False,
    hide_expired: bool = False,
    today: str | None = None,
    category: str | None = None,
    deal_type: str | None = None,
    loyalty_only: bool = False,
    search: str = "",
    valid_on: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[sqlite3.Row]:
    """Return stored deal rows (``limit`` 0 = all).

    Every argument except ``limit``/``conn`` is a filter shared verbatim with
    the CLI flags and the GUI controls (GFP-17). Each row carries an ``expired``
    flag (1/0) so a front end can grey out stale deals; ``hide_expired`` drops
    them instead. ``today`` overrides the freshness cutoff, ``valid_on`` asks
    "what was on offer on this date".
    """
    own = conn or db.connect()
    day = today or today_iso()
    where, params = _deal_filters(
        store=store, category=category, deal_type=deal_type, on_sale=on_sale,
        hide_expired=hide_expired, loyalty_only=loyalty_only, search=search,
        valid_on=valid_on, today=day,
    )
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
    category: str | None = None,
    deal_type: str | None = None,
    loyalty_only: bool = False,
    search: str = "",
    valid_on: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Count deals matching the same filters as :func:`fetch_deals`, ignoring ``limit``."""
    own = conn or db.connect()
    day = today or today_iso()
    where, params = _deal_filters(
        store=store, category=category, deal_type=deal_type, on_sale=on_sale,
        hide_expired=hide_expired, loyalty_only=loyalty_only, search=search,
        valid_on=valid_on, today=day,
    )
    return own.execute(f"SELECT COUNT(*) FROM deals{where}", params).fetchone()[0]


# --------------------------------------------------------------------------- #
# Choices for the filter controls — sourced from the data, not hard-coded, so
# CSV-only stores (e.g. Whole Foods) show up alongside the scrapable ones.
# --------------------------------------------------------------------------- #
def best_deals(
    limit: int = 20,
    score_with: str | None = None,
    conn: sqlite3.Connection | None = None,
    **filters: Any,
) -> list[dict[str, Any]]:
    """Rank the deals matching ``filters`` (GFP-8).

    Ranked by cost per normalized unit, or by a user formula when
    ``score_with`` names one. ``filters`` are the same keywords
    :func:`fetch_deals` accepts, so "best deals" and "list deals" always agree
    on which rows are in play. Defaults to current deals only — ranking stale
    prices would be actively misleading.
    """
    own = conn or db.connect()
    filters.setdefault("hide_expired", True)
    rows = fetch_deals(conn=own, **filters)
    if score_with:
        return savings.score_deals(own, score_with, rows, limit=limit)
    return savings.rank_by_unit_price(rows, limit=limit)


EXPORT_COLUMNS = [
    "store", "item_name", "sub_category", "deal_type", "size", "price",
    "unit_price", "unit", "sale_price", "dollar_price", "valid_from", "valid_to",
    "expired",
]


def export_deals(
    path: str | Path,
    conn: sqlite3.Connection | None = None,
    **filters: Any,
) -> int:
    """Write the deals matching ``filters`` to a CSV file; returns the row count.

    CSV rather than .xlsx on purpose: GFP-13 retired the Excel runtime and its
    pandas/openpyxl dependency, and a CSV opens in Excel, Sheets and Numbers
    without dragging that back in. Rows carry the GFP-8 size/unit-price columns
    so a spreadsheet can sort on value directly.
    """
    own = conn or db.connect()
    rows = savings.annotate(fetch_deals(conn=own, **filters))
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=EXPORT_COLUMNS, extrasaction="ignore"
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in EXPORT_COLUMNS})
    return len(rows)


def stores_with_deals(conn: sqlite3.Connection | None = None) -> list[str]:
    """Store keys that actually have deal rows, scraped or imported."""
    own = conn or db.connect()
    return [r[0] for r in own.execute(
        "SELECT DISTINCT store FROM deals WHERE store IS NOT NULL ORDER BY store"
    )]


def deal_categories(
    store: str | None = None, conn: sqlite3.Connection | None = None
) -> list[str]:
    """Distinct sub-categories, optionally within one store."""
    own = conn or db.connect()
    where, params = ("", [])
    if store:
        where, params = " AND store=?", [store]
    return [r[0] for r in own.execute(
        "SELECT DISTINCT sub_category FROM deals "
        f"WHERE sub_category IS NOT NULL AND sub_category <> ''{where} "
        "ORDER BY sub_category", params
    )]
