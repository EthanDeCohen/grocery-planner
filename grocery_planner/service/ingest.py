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
