# ######### decohen-partners ##########
# Protein Ledger
"""The GIANT Company's weekly ad, via Flipp (GFP-247).

The per-ZIP price half of the Philadelphia banner. GIANT shipped first as a
PRISM catalogue with a *hand-declared* footprint, because its store locator is
DataDome-protected (GFP-246) and nothing could be asked. Then a Flipp survey of
19103 on 2026-08-09 found GIANT publishes a weekly ad after all, listed as
"Giant Food Stores".

That matters twice over:

* *A real per-ZIP price.* The catalogue serves a default-store figure; the ad is
  scraped per postal code, as Food Lion's is. Paired through GFP-248's join
  (price from the ad, size and protein from the catalogue) GIANT gets the same
  treatment as Food Lion instead of being the one banner priced nationally.
* *A real footprint.* ``serves()`` asks Flipp instead of trusting the prefix
  list in ``giant.py``, which was never verified and is the same kind of guess
  that turned out to claim Food Lion served all of Kentucky.

Distinct from ``giant.py``, which is the catalogue, exactly as ``foodlion`` and
``foodlion_catalog`` are distinct: same STORE_KEY, different SOURCE, so
run_scrape's (store, source, postal_code) replace scope keeps them apart and
neither evicts the other.
"""
from __future__ import annotations

from typing import Any

from . import base

STORE = base.GIANT
SCRAPER_KEY = "giant-ad"
STORE_KEY = STORE.key
MERCHANT = STORE.merchant_name
DEFAULT_POSTAL_CODE = STORE.default_postal_code


def scrape(
    postal_code: str | None = None, include_coupons: bool = True
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Scrape the active GIANT weekly ad. Returns ``(deal_rows, flyer_meta, stats)``."""
    return base.scrape_store(STORE, postal_code=postal_code, include_coupons=include_coupons)


def serves(postal_code: str) -> bool | None:
    """Does GIANT publish a weekly ad here? (GFP-257) See ``base``."""
    return base.serves_postal_code(STORE, postal_code)
