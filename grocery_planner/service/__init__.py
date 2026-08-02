"""Front-end-agnostic operations shared by every UI (GFP-14).

The CLI (``cli``) and the PySide6 GUI (``gui``) both drive the app through this
package so scrape + query logic lives in exactly one place. Functions here
return plain data and never print — the front end owns all presentation.

Split into submodules under GFP-43 as this layer grows to carry customers,
nutrition and ingest:

- :mod:`.deals` — deal query, ranking, filtering and export (GFP-16/GFP-17/GFP-8).
- :mod:`.ingest` — pulling fresh deals from a store scraper into SQLite.

This module re-exports the full public API so existing call sites
(``service.fetch_deals``, ``service.run_scrape``, ``service.UnknownStoreError``,
``service.DEAL_TYPE_GROUPS``, ...) keep working unchanged.
"""
from __future__ import annotations

from .deals import (
    DEAL_TYPE_GROUPS,
    EXPORT_COLUMNS,
    UnknownDealTypeError,
    best_deals,
    count_deals,
    deal_categories,
    export_deals,
    fetch_deals,
    is_expired,
    stores_with_deals,
    today_iso,
)
from .ingest import (
    ScraperStatus,
    UnknownStoreError,
    all_scrapers,
    available_scrapers,
    run_scrape,
    scraper_status,
)

__all__ = [
    "DEAL_TYPE_GROUPS",
    "EXPORT_COLUMNS",
    "ScraperStatus",
    "UnknownDealTypeError",
    "UnknownStoreError",
    "all_scrapers",
    "available_scrapers",
    "best_deals",
    "count_deals",
    "deal_categories",
    "export_deals",
    "fetch_deals",
    "is_expired",
    "run_scrape",
    "scraper_status",
    "stores_with_deals",
    "today_iso",
]
