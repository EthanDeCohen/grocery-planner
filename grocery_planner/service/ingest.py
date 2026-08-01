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


def available_scrapers() -> list[str]:
    """Sorted list of store keys that can be scraped."""
    return sorted(SCRAPERS)


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
) -> dict[str, Any]:
    """Scrape a store's fresh deals and persist them, replacing prior scrape rows.

    Replacement (the ``DELETE`` below) is scoped to this store, this source,
    *and* this postal code (GFP-55) so scraping one ZIP never destroys another
    ZIP's rows for the same store. Every row also carries the ``postal_code``
    it was scraped for (GFP-54).

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
