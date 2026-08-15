# ######### decohen-partners ##########
# Protein Ledger
"""ALDI, through the shared Instacart storefront client (GFP-265).

In:  a ZIP.
Out: deal rows with prices and sizes. No nutrition -- see below.

ALDI IS NUTRITION-BLOCKED, and the pinned hash is not the reason. Measured: the
nutrition query runs fine and simply returns null for everything, including
chicken breast and eggs that certainly carry a physical label. Two other places
it could hide (ItemDetailData, ItemDetailSupplementalFields) have no protein
either. So this is a tenant that publishes no panels, not a scraper that fails
to read them.

That is why CANARY_PRODUCT_ID is None: there is no product to prove the pin
against, and a hopeful guess would make verify_pinned_hashes report a failure
that looks like a rotated hash. Reported as blocked rather than worked around --
no inferred density, no brand guess, no USDA lookup wearing a label's clothes.

Still worth running for price and size, which sourcelink can lend to the Flipp
banner. Roughly 9% of ALDI rows end up usable, against 72% for Sprouts.

Two more things:

* The price bound is not optional. ALDI's product HTML throttles harder than
  Sprouts', so `limit` is a real constraint rather than a convenience.
* `aldi` was already taken by the Flipp banner, so this registers as
  `aldi-storefront`. The registry is last-write-wins and the banners load last,
  so reusing the key would make this module silently unreachable -- which is
  exactly what happened to sprouts once.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Iterable

import httpx

from . import instacart_storefront as _platform

# See the REGISTRY section: `aldi` already belongs to the Flipp weekly-ad
# banner, and this is a second source for the same store, not a replacement.
SCRAPER_KEY = "aldi-storefront"

# The `deals.store` value -- deliberately shared with the Flipp banner. A
# shopper shops at ALDI, not at "ALDI's Instacart storefront", and keeping one
# store identity is what lets GFP-75's records treat an observation from either
# feed as the same item.
STORE_KEY = "aldi"

# The `deals.source` value. Same string sprouts.py uses, because it names the
# *feed shape* -- one Instacart Storefront Pro tenant -- and ingest scopes its
# replace to (store, source, postal_code), so two different stores sharing a
# source name cannot collide with one another.
SOURCE = "instacart-storefront"

MERCHANT = "ALDI (storefront)"
DEFAULT_POSTAL_CODE = "27401"
DEAL_TYPE = "Storefront Price"
PRODUCT_IDENTIFIER_NS = "aldi.product_id"

BASE_URL = "https://www.aldi.us"
RETAILER_SLUG = "aldi"
# The sitemap path embeds the storefront host, and ALDI's is NOT the
# `shop_<brand>_com` shape Sprouts uses -- `shop_aldi_com` returns 403 and
# `www_aldi_us` returns 200. Derivable from the host, but written out because a
# wrong sitemap URL fails as an empty catalogue rather than as an error.
SITEMAP_HOST_KEY = "www_aldi_us"
SITEMAP_INDEX = f"{BASE_URL}/sitemaps/storefront_pro/{SITEMAP_HOST_KEY}/sitemap.xml"

# Instacart's ids for this tenant, read back from the live storefront on
# 2026-08-11 rather than assumed. ALDI's retailer id is 12 (Sprouts' is 279).
#
# 515201 is the **instore** shop -- see the platform module's SERVICE-TYPE TRAP.
# For 27401 the platform offers three shop ids and they are all the same
# physical store, ALDI - SBY 140 - Greensboro, 2965 Battleground Ave,
# retailerLocationId 124437. They differ only in serviceType:
#
#     6823    delivery
#     22443   pickup
#     515201  instore     <- shelf price, which is what this project compares
#
# `SimpleShopCollection` returns them delivery-first, so the GFP-262 "take the
# first shop" rule would have priced a delivery basket here. Only a default:
# `serves`/`scrape` re-resolve per ZIP and prefer instore explicitly.
DEFAULT_SHOP_ID = "515201"
DEFAULT_ZONE_ID = "430"
RETAILER_ID = "12"

# The same two values sprouts.py pins, written out in full rather than imported
# from it. They are currently equal -- measured, not assumed -- but sharing the
# literal would encode "these are always equal" as a fact when it is only an
# observation: the banners can be moved onto different Instacart deploy trains
# at any time, and the symptom of a shared constant on that day is one tenant
# silently losing all its data. Duplicated literals with independent canaries
# fail loudly instead.
FALLBACK_HASHES = {
    "ProductNutritionalInfo":
        "9bc43a13c48e633ba4c8016118f101942a44603c5d10f913e9e471ffb730185a",
    "SimpleShopCollection":
        "d438f50ce0c6b59526c922754c7908bfbfa073c8893f466f0276f40a0074501a",
}
PINNED_OPERATIONS = _platform.PINNED_OPERATIONS

#: No canary, because there is no panel anywhere in this catalogue to point one
#: at. ``None`` rather than a hopeful product id: ``verify_pinned_hashes`` then
#: reports the real reason instead of reporting a failure that reads like a
#: rotated hash. See the headline section.
CANARY_PRODUCT_ID: str | None = None

#: How many product pages one price run will fetch. Bounded because the
#: product-HTML path is the one that hit a hard 403 after ~2,300 pages on
#: Sprouts. Not a tuning knob to raise casually: raising it is a request to find
#: ALDI's wall the hard way.
DEFAULT_PRICE_LIMIT = 1200

#: Measured 2026-08-11 from ZIP 27401 against shop 515201, reported the way
#: GFP-262 reported Sprouts (46,359 / 15,163 / 11,781 / 11,097). Held here as
#: data so the numbers are quotable and dated rather than buried in prose --
#: and so the day ALDI starts publishing panels, the diff is obvious.
COVERAGE = {
    "measured_on": "2026-08-11",
    "postal_code": "27401",
    "shop_id": "515201",
    "products_in_sitemap": 15256,
    "nutrition_panels": 0,
    "protein_above_zero": 0,
    "computable_protein_density": 0,
}

TENANT = _platform.Tenant(
    store_key=STORE_KEY,
    merchant=MERCHANT,
    base_url=BASE_URL,
    retailer_slug=RETAILER_SLUG,
    retailer_id=RETAILER_ID,
    pinned_hashes=FALLBACK_HASHES,
    canary_product_id=CANARY_PRODUCT_ID,
    default_shop_id=DEFAULT_SHOP_ID,
    default_zone_id=DEFAULT_ZONE_ID,
    default_postal_code=DEFAULT_POSTAL_CODE,
    sitemap_host_key=SITEMAP_HOST_KEY,
    deal_type=DEAL_TYPE,
    product_identifier_ns=PRODUCT_IDENTIFIER_NS,
    source_label="aldi_storefront",
    priceless_description="ALDI storefront listing",
)


def readiness() -> tuple[bool, str]:
    """Ready -- guest session, no credential of any kind.

    The message names the nutrition gap, because "ready" on its own would let a
    user reasonably expect this source to behave like Sprouts and supply protein
    with the price. It does not, and the store table is the first place that
    should say so.
    """
    return True, "no credentials required (guest session); prices only, no nutrition panels"


def nutrition_available() -> tuple[bool, str]:
    """``(False, why)`` -- ALDI publishes no nutrition panels. See the headline.

    A named function rather than a comment so a caller deciding whether to run a
    USDA matching pass over ALDI's rows can ask, instead of inferring it from an
    empty result.
    """
    return False, (
        "ALDI's Instacart tenant returns nutritionalInfo=null for every product "
        f"({COVERAGE['products_in_sitemap']} checked on {COVERAGE['measured_on']}). "
        "The pinned ProductNutritionalInfo hash is accepted and the response is "
        "well formed -- the data is simply not published. Protein for ALDI rows "
        "must come from the USDA matching pass."
    )


def verify_pinned_hashes(client: _platform.StorefrontClient | None = None) -> tuple[bool, str]:
    """``(False, why)`` -- there is no canary to verify against. See the headline."""
    return _platform.verify_pinned_hashes(TENANT, client)


def serves(postal_code: str) -> bool | None:
    """Is there an ALDI serving ``postal_code``? (GFP-257)

    ``None`` when the question cannot be put -- a network error, a rotated
    hash. Unknown is not absent, and availability.py treats it permissively.
    """
    return _platform.serves(TENANT, postal_code)


def listing_to_row(
    listing: _platform.Listing,
    nutrition: _platform.Nutrition | None,
    zip_code: str,
    now: datetime,
) -> tuple[dict[str, Any], _platform.FoodFact | None]:
    return _platform.listing_to_row(TENANT, listing, nutrition, zip_code, now)


def scrape(
    postal_code: str | None = None,
    limit: int | None = None,
    conn: sqlite3.Connection | None = None,
    client: _platform.StorefrontClient | None = None,
    now: datetime | None = None,
    slugs: Iterable[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Scrape ALDI shelf prices and return ``(rows, meta, stats)``.

    Prices only -- there is no nutrition pass to run, because there is no
    nutrition. See the headline section. ``limit`` defaults to
    :data:`DEFAULT_PRICE_LIMIT` rather than to ``None``: unbounded here means
    walking a 15,256-page HTML crawl into a rate-limit wall, so "no limit" is
    not an option this scraper offers. Whatever bound applies is reported in
    ``stats['price_limit']``.
    """
    return _platform.scrape_prices_only(
        TENANT,
        limit=DEFAULT_PRICE_LIMIT if limit is None else limit,
        postal_code=postal_code, conn=conn, client=client, now=now, slugs=slugs,
    )
