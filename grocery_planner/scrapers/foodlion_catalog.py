"""Food Lion's PRISM product catalogue (GFP-247).

A SECOND source for a store that already has one, exactly as ``kroger`` is for
``harristeeter``: ``foodlion`` is the Flipp weekly ad, ``foodlion-catalog`` is
the catalogue behind ``/groceries/product/``. They complement each other -- the
ad carries a per-ZIP promotional price the catalogue cannot, the catalogue
carries sizes and protein the ad never has -- so neither may evict the other,
which is what the distinct ``SOURCE`` below guarantees.

All the work is in ``prism``; this module is the banner's configuration.
"""
from __future__ import annotations

from typing import Any

from . import prism

STORE = prism.FOOD_LION
SCRAPER_KEY = "foodlion-catalog"
STORE_KEY = STORE.key
SOURCE = "prism"
#: Human label for the registry surface, as kroger.py does -- these modules
#: are not Flipp-sourced, so there is no Flipp merchant name to carry.
MERCHANT = "Food Lion (PRISM catalogue)"
DEFAULT_POSTAL_CODE = STORE.default_postal_code


def scrape(
    postal_code: str | None = None, include_coupons: bool = True
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Scrape a bounded slice of the Food Lion catalogue."""
    return prism.scrape_store(STORE, postal_code=postal_code)
