"""Front-end-agnostic operations shared by every UI (GFP-14).

The CLI (``cli``) and the PySide6 GUI (``gui``) both drive the app through this
package so scrape + query logic lives in exactly one place. Functions here
return plain data and never print — the front end owns all presentation.

Split into submodules under GFP-43 as this layer grows to carry customers,
nutrition and ingest:

- :mod:`.deals` — deal query, ranking, filtering and export (GFP-16/GFP-17/GFP-8).
- :mod:`.ingest` — pulling fresh deals from a store scraper into SQLite.
- :mod:`.trends` — price and $/g protein over time, by store or by food, from
  ``price_history`` (GFP-36, generalised to both metrics and both dimensions
  by GFP-40 so the chart and ``gplan trends`` share one definition).

This module re-exports the full public API so existing call sites
(``service.fetch_deals``, ``service.run_scrape``, ``service.UnknownStoreError``,
``service.DEAL_TYPE_GROUPS``, ...) keep working unchanged.

GFP-71: ``ScrapeGuardError`` (and its two subclasses, ``EmptyScrapeError`` /
``ImplausibleCollapseError``) are re-exported here too, same as
``UnknownStoreError`` already was -- so a front end can catch
``service.ScrapeGuardError`` without reaching into ``service.ingest``
directly, which was the inconsistency this ticket flagged.
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
    EmptyScrapeError,
    ImplausibleCollapseError,
    ScraperStatus,
    ScrapeGuardError,
    UnknownStoreError,
    all_scrapers,
    available_scrapers,
    run_scrape,
    scraper_status,
)
from .trends import (
    DEFAULT_WINDOW_DAYS,
    MIN_POINTS_TO_PLOT,
    Dimension,
    Metric,
    PriceTrend,
    TrendPoint,
    TrendSeries,
    UnknownFoodError,
    UnscopedPriceTrendError,
    has_price_history,
    price_trend,
    protein_price_trend,
    trend_stores,
)

__all__ = [
    "DEAL_TYPE_GROUPS",
    "DEFAULT_WINDOW_DAYS",
    "EXPORT_COLUMNS",
    "MIN_POINTS_TO_PLOT",
    "Dimension",
    "Metric",
    "PriceTrend",
    "TrendPoint",
    "TrendSeries",
    "UnknownFoodError",
    "UnscopedPriceTrendError",
    "EmptyScrapeError",
    "ImplausibleCollapseError",
    "ScrapeGuardError",
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
    "has_price_history",
    "is_expired",
    "price_trend",
    "protein_price_trend",
    "run_scrape",
    "scraper_status",
    "stores_with_deals",
    "today_iso",
    "trend_stores",
]
