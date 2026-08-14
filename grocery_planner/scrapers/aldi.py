"""ALDI US -- the second tenant of the Instacart Storefront Pro platform client.

GFP-265. This module is deliberately short. Everything it does lives in
:mod:`grocery_planner.scrapers.instacart_storefront`; ALDI is a
:class:`~grocery_planner.scrapers.instacart_storefront.Tenant` record and four
delegating functions, which is the finding this ticket set out to test.

``www.aldi.us`` is not ALDI's code any more than ``shop.sprouts.com`` is
Sprouts'. It is the same white-label platform: same ``__Host-instacart_sid``
guest cookie, same ``/store/<slug>/`` URL shape, same ``node-apollo-state``
blob, same doubly-URL-encoded performance blob carrying persisted-query hashes,
same ``/graphql`` persisted-query access shape. ``discover_persisted_queries``,
written for Sprouts and **unmodified**, found 59 operation hashes in ALDI's
storefront HTML on its first run.

NO BROWSER. NOT A SLOWER OPTION -- A NON-SHIPPING ONE
------------------------------------------------------
A Playwright DOM scraper was proposed for ALDI: launch chromium, scroll the
category pages, read ``inner_text()``, match product names against a hard-coded
brand list (``kirkwood``, ``clancy``, ``parkview``...), regex the prices out.
It was not built. ``https://www.aldi.us/store/aldi/storefront`` returns **200 to
plain httpx** with no login, no hand-minted cookie and no browser step, so the
browser buys nothing -- and per GFP-4 the distributed desktop app cannot carry a
browser binary, so that scraper could never have left a dev machine. The brand
list would also have silently dropped every product whose brand is not on it: a
coverage cap that never appears in ``stats``, which is the exact failure mode
the no-silent-caps rule exists to prevent. No browser is launched by this module
or by anything it calls; the project's headless-always rule is satisfied
vacuously.

THE HEADLINE: ALDI IS NUTRITION-BLOCKED, AND THE PIN IS NOT WHY
----------------------------------------------------------------
The pinned ``ProductNutritionalInfo`` hash captured for Sprouts **works on
ALDI**. Replayed unchanged against ``www.aldi.us/graphql`` it returns HTTP 200,
no ``PERSISTED_QUERY_NOT_FOUND``, and a well-formed
``ItemsProductNutritionalInfo`` envelope. That is consistent with
``SimpleShopCollection``'s hash being byte-identical on both banners: Apollo
hashes the query *document*, the document ships in the shared platform bundle,
so a hash is a property of the Instacart deploy rather than of the banner.

But the envelope is always empty. ``nutritionalInfo`` came back ``null`` for
every product tried, across all three shop ids and every id form the platform
uses (bare ``21171551``, prefixed ``items_124437-21171551``, and
``124437-21171551``). Measured 2026-08-11 over the **full 15,256-product
catalogue**: see :data:`COVERAGE`.

This is an absence of data, not a broken client, and the distinction is load
bearing. Three things rule out a fault on our side:

1. The same request against Sprouts, same session code, same pinned hash,
   returns a full panel (protein 13.0 g, ``servingSize`` "8 oz (227g)").
2. ALDI's product pages *do* ship the nutrition **layout** -- the strings
   "Nutrition Facts", "Serving Size" and "Servings Per Container" are in the
   HTML, inside a ``ProductNutritionalInfoLayout`` block. The UI is wired for a
   panel; the tenant just publishes no data to put in it.
3. Items that certainly carry a physical label -- chicken breast, ground beef,
   peanut butter, greek yogurt, eggs -- return ``null`` exactly like everything
   else. A partial or category-specific gap would not look like this.

The two other places nutrition could plausibly hide were checked and do not
carry it either: ``ItemDetailData`` and ``ItemDetailSupplementalFields`` (both
hashes discoverable from ALDI's own product page) contain no protein, no
serving size and no ingredients.

**Reported as nutrition-blocked rather than worked around.** There is no
inferred density, no brand-based guess and no USDA lookup pretending to be a
label. :data:`CANARY_PRODUCT_ID` is therefore ``None``, and
``verify_pinned_hashes`` returns ``False`` with that reason rather than a
success it did not earn.

WHAT ALDI IS STILL GOOD FOR: PRICE AND SIZE
--------------------------------------------
The schema.org JSON-LD on the product page is fully populated -- ``name``,
``brand``, ``category``, ``size``, ``offers.price``, ``availability``. So ALDI
is a price source of the same shape as the Flipp-sourced stores: a price with a
parseable size, whose protein has to come from the USDA matching pass rather
than from the retailer's own label.

That is why :func:`scrape` calls
:func:`~grocery_planner.scrapers.instacart_storefront.scrape_prices_only`. The
ordinary two-pass ``scrape`` prices only what the nutrition pass found protein
for, which for ALDI is nothing; weakening that rule would remove the guard that
keeps a Sprouts run off the product-page wall, so ALDI takes the other path
instead and says so in ``stats['nutrition_pass']``.

THE PRICE BOUND IS REAL AND IS NOT OPTIONAL
--------------------------------------------
Price comes from product HTML, and that path is the one that hit a hard 403
after ~2,300 pages on Sprouts. ALDI is assumed to be policed at least as tightly
until measured otherwise -- assuming the generous direction is the mistake that
gets an IP blocked -- so :data:`DEFAULT_PRICE_LIMIT` bounds the run well under
that figure and the bound is reported in ``stats['price_limit']`` next to
``stats['products_seen']``. A short scrape must read as "we bounded this", never
as "the store shrank".

ALDI'S ``/graphql`` IS STRICTER THAN SPROUTS'
----------------------------------------------
Worth recording because it contradicts the platform module's throttle table,
which was measured on Sprouts. Sprouts tolerated 37,500 GraphQL requests at
~36/s with zero 429s. ALDI throttled within the first few hundred at ~12.5/s
(:data:`~grocery_planner.scrapers.retry.GRAPHQL_BUDGET`'s floor), and ran clean
at 4/s. The shipped code needs no change -- :class:`retry.Paced` is AIMD and
converges on a sustainable rate by itself, which is exactly the case it was
built for -- but anyone reading the table should know the numbers in it are
per-tenant, not platform-wide.

REGISTRY: ``aldi`` WAS ALREADY TAKEN
-------------------------------------
``flipp_banners.MODULES`` registers an ``aldi`` weekly-ad banner. This module is
a **second source for the same physical store**, which is the case
``scrapers/__init__.py`` documents for Harris Teeter (``harristeeter`` = the
Flipp weekly ad, ``harristeeter-api`` = Kroger's shelf-price API). So:
``SCRAPER_KEY`` is distinct, ``STORE_KEY`` is shared, and ``SOURCE`` is what
stops the two feeds deleting each other in ``service/ingest.run_scrape``, which
scopes its replace to ``(store, source, postal_code)``. Neither feed may evict
the other: Flipp carries BOGO and coupon promotions the storefront does not, and
the storefront carries sizes the weekly ad never has.

This module is **not** registered in ``scrapers/__init__.py`` by GFP-265 -- that
file was owned by another change in flight. Until it is listed there, ``gplan
scrape aldi-storefront`` will not find it.
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
