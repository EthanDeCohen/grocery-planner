# ######### decohen-partners ##########
# Protein Ledger
"""Harris Teeter weekly-ad + digital-coupon scraper.

Thin store module (mirrors ``foodlion``): supplies the
:class:`~grocery_planner.scrapers.base.StoreConfig` and delegates to the shared
Flipp client in ``base`` (GFP-6). Returns DB-ready ``deals`` rows.
"""
from __future__ import annotations

from typing import Any

from . import base

STORE = base.HARRIS_TEETER
STORE_KEY = STORE.key
MERCHANT = STORE.merchant_name
DEFAULT_POSTAL_CODE = STORE.default_postal_code


def scrape(
    postal_code: str | None = None, include_coupons: bool = True
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Scrape the active Harris Teeter ad. Returns ``(deal_rows, flyer_meta, stats)``."""
    return base.scrape_store(STORE, postal_code=postal_code, include_coupons=include_coupons)


def serves(postal_code: str) -> bool | None:
    """Does this store publish a weekly ad here? (GFP-257) See ``base``."""
    return base.serves_postal_code(STORE, postal_code)
