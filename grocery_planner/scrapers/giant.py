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

from . import base, prism

STORE = prism.GIANT
SCRAPER_KEY = "giant"
STORE_KEY = STORE.key
SOURCE = "prism"
#: Human label for the registry surface, as kroger.py does -- these modules
#: are not Flipp-sourced, so there is no Flipp merchant name to carry.
MERCHANT = "GIANT (PRISM catalogue)"

#: GFP-257: asked, not declared. This WAS a hand-written ZIP-prefix list, until
#: a Flipp survey found GIANT publishes a weekly ad ("Giant Food Stores") and
#: the same list-based approach was measured wrong for Food Lion -- it claimed
#: all of Kentucky when Food Lion is in one Kentucky metro. The catalogue and
#: the ad are the same chain with the same stores, so the ad's answer is right
#: for both.
def serves(postal_code: str) -> bool | None:
    """Delegates to the weekly ad's answer -- same chain, same stores."""
    return base.serves_postal_code(base.GIANT, postal_code)
DEFAULT_POSTAL_CODE = STORE.default_postal_code


def scrape(
    postal_code: str | None = None, include_coupons: bool = True
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Scrape a bounded slice of the GIANT catalogue."""
    return prism.scrape_store(STORE, postal_code=postal_code)
