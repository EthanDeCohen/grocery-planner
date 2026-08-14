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

from . import base, prism
from .base import FOOD_LION as STORE_CONFIG

STORE = prism.FOOD_LION
SCRAPER_KEY = "foodlion-catalog"
STORE_KEY = STORE.key
SOURCE = "prism"
#: Human label for the registry surface, as kroger.py does -- these modules
#: are not Flipp-sourced, so there is no Flipp merchant name to carry.
MERCHANT = "Food Lion (PRISM catalogue)"

#: GFP-257: this banner's footprint is asked, not declared.
#:
#: It WAS a hand-written ZIP-prefix list, and that list was measurably wrong --
#: it claimed Food Lion served all of Georgia's 30xxx, when Food Lion does not
#: operate in Atlanta at all. A wrong "serves" is worse than an unknown: it
#: sends a scrape at a market that does not exist and quietly asserts coverage
#: the client does not have.
#:
#: The catalogue and the weekly ad are the same chain with the same stores, so
#: the ad's own answer is the right one for both. base.serves_postal_code asks
#: Flipp whether this merchant publishes here, which is free, empirical, and
#: self-correcting as the footprint changes.
def serves(postal_code: str) -> bool | None:
    """Delegates to the weekly ad's answer -- same chain, same stores."""
    return base.serves_postal_code(STORE_CONFIG, postal_code)
DEFAULT_POSTAL_CODE = STORE.default_postal_code


def scrape(
    postal_code: str | None = None, include_coupons: bool = True
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Scrape a bounded slice of the Food Lion catalogue."""
    return prism.scrape_store(STORE, postal_code=postal_code)
