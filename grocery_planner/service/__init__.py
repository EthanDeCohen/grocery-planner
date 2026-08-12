"""Front-end-agnostic operations shared by every UI (GFP-14).

The CLI (``cli``) and the PySide6 GUI (``gui``) both drive the app through this
package so scrape + query logic lives in exactly one place. Functions here
return plain data and never print — the front end owns all presentation.

Split into submodules under GFP-43 as this layer grows to carry customers,
nutrition and ingest:

- :mod:`.clients` — client CRUD, the one path both ``gplan client ...`` and the
  GUI roster take (GFP-33), over the GFP-28 customer record. Deliberately NOT
  re-exported flat below, unlike the four modules that follow: those are flat
  because GFP-43 split an already-flat ``service.py`` and could not break the
  call sites it had. ``clients`` is new, both its call sites import
  ``service.clients`` by name, and there are ~20 of them — flattening those
  into this namespace would put ``service.get_client`` next to
  ``service.fetch_deals`` with nothing but the noun to say which is which.
- :mod:`.deals` — deal query, ranking, filtering and export (GFP-16/GFP-17/GFP-8).
- :mod:`.ingest` — pulling fresh deals from a store scraper into SQLite.
- :mod:`.shopping` / :mod:`.shoppingfmt` — a grocery list a client can
  actually shop from, and its printable/CSV/HTML renderings (GFP-112).
- :mod:`.refresh` — whether a refresh is due, so the app's first run and
  GFP-102's background timer cannot both decide to scrape (GFP-105).
- :mod:`.cheapest` — what to buy RIGHT NOW: the cheapest animal protein on
  offer at each store, from `deals` rather than history (GFP-107).
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

from .cheapest import CheapestProtein, cheapest_protein_by_store
from .refresh import (
    NEW_DAY,
    NO_DATA,
    UP_TO_DATE,
    RefreshDecision,
    refresh_decision,
)
from .shopping import (
    DEFAULT_DAYS,
    GroceryItem,
    GroceryList,
    grocery_list_for,
)
from .shoppingfmt import EXTENSIONS, RENDERERS, render as render_grocery_list, write as write_grocery_list
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
    UnsupportedLimitError,
    all_scrapers,
    available_scrapers,
    scrapers_for_postal_code,
    ScrapePlan,
    run_scrape,
    scrapers_supporting_limit,
    supports_limit,
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
    "CheapestProtein",
    "NEW_DAY",
    "NO_DATA",
    "RefreshDecision",
    "UP_TO_DATE",
    "DEFAULT_DAYS",
    "EXTENSIONS",
    "GroceryItem",
    "GroceryList",
    "RENDERERS",
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
    "UnsupportedLimitError",
    "all_scrapers",
    "available_scrapers",
    "scrapers_for_postal_code",
    "ScrapePlan",
    "best_deals",
    "cheapest_protein_by_store",
    "count_deals",
    "deal_categories",
    "export_deals",
    "fetch_deals",
    "has_price_history",
    "is_expired",
    "price_trend",
    "grocery_list_for",
    "protein_price_trend",
    "refresh_decision",
    "render_grocery_list",
    "run_scrape",
    "scrapers_supporting_limit",
    "supports_limit",
    "scraper_status",
    "stores_with_deals",
    "today_iso",
    "trend_stores",
    "write_grocery_list",
]
