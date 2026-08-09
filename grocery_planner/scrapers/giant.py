"""GIANT's PRISM product catalogue -- the Philadelphia banner (GFP-247).

The GIANT Company (``giantfoodstores.com``, Carlisle PA) is the Philadelphia
banner, and a different company from Giant Food (``giantfood.com``, Landover
MD) despite the shared name. Both run PRISM; this is the one in a market
GFP-165 targets.

Unlike Food Lion, GIANT has no Flipp ad configured here, so today this is the
banner's only feed -- and per GFP-246 its price is a default-store figure. Say
so wherever it is shown until a per-ZIP source for this market exists.

All the work is in ``prism``; this module is the banner's configuration.
"""
from __future__ import annotations

from typing import Any

from . import prism

STORE = prism.GIANT
SCRAPER_KEY = "giant"
STORE_KEY = STORE.key
SOURCE = "prism"
#: Human label for the registry surface, as kroger.py does -- these modules
#: are not Flipp-sourced, so there is no Flipp merchant name to carry.
MERCHANT = "GIANT (PRISM catalogue)"
DEFAULT_POSTAL_CODE = STORE.default_postal_code


def scrape(
    postal_code: str | None = None, include_coupons: bool = True
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Scrape a bounded slice of the GIANT catalogue."""
    return prism.scrape_store(STORE, postal_code=postal_code)
