"""Food Lion weekly-ad + digital-coupon scraper.

Thin store module: it supplies the :class:`~grocery_planner.scrapers.base.StoreConfig`
and delegates the actual work to the shared Flipp client in ``base`` (GFP-6).
Returns DB-ready ``deals`` rows; the CLI inserts them into SQLite.
"""
from __future__ import annotations

from typing import Any

from . import base

STORE = base.FOOD_LION
STORE_KEY = STORE.key
MERCHANT = STORE.merchant_name
DEFAULT_POSTAL_CODE = STORE.default_postal_code


def scrape(
    postal_code: str = DEFAULT_POSTAL_CODE, include_coupons: bool = True
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Scrape the active Food Lion ad. Returns ``(deal_rows, flyer_meta, stats)``."""
    return base.scrape_store(STORE, postal_code=postal_code, include_coupons=include_coupons)
