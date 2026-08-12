"""Sprouts Farmers Market -- a tenant config over the Instacart platform client.

GFP-262 wrote this module as a Sprouts scraper. GFP-265 turned it into what its
own docstring already said it was: ``shop.sprouts.com`` is not Sprouts' code,
it is an **Instacart Storefront Pro** tenant, and "re-aiming it at another
banner is a slug change, not a rewrite". Everything that was tenant-independent
now lives in :mod:`grocery_planner.scrapers.instacart_storefront` -- the guest
session, persisted-query discovery and its double URL-decode, the pacing split
between ``/graphql`` and product HTML, sitemap enumeration, schema.org parsing,
the nutrition payload mapping, and all four traps (WEIGHT, ``"Varied"``, the
three serving-size shapes, and the ``per lb`` pricing-unit dialect).

**Read that module's docstring.** It carries the evidence for every one of those
traps, and this file no longer repeats it -- duplicating the explanation is how
two copies drift until one of them is wrong.

What is left here is the tenant: a host, a slug, a retailer id, a pin and a
canary. That is the whole point -- adding ALDI (``aldi.py``) was a
:class:`~grocery_planner.scrapers.instacart_storefront.Tenant` record, not a
scraper.

WHAT THIS MODULE PROMISES
-------------------------
Every public name GFP-262 exported is still exported and still means the same
thing. ``tests/test_sprouts.py`` passes **unmodified**, which is the check that
this refactor changed structure and not behaviour.

The names below are deliberate re-exports rather than ``from ... import *``: a
wildcard would let a rename in the platform module silently drop a name this one
is contracted to provide, and the resulting ``AttributeError`` would surface
during a scrape rather than at import.

THE ONE BEHAVIOUR THAT CHANGED, AND WHY IT IS A FIX
----------------------------------------------------
``shop_context`` used to take the **first** shop ``SimpleShopCollection``
returned. Generalising exposed that as a coincidence rather than a rule. That
operation returns bare ids, in an unspecified order, with no indication of what
each one is; measured 2026-08-11 for 27401:

    sprouts   515202 (instore), 5201 (delivery), 5202 (pickup)
    aldi      6823 (delivery),  22443 (pickup),  515201 (instore)

All three of a banner's shops are the same physical store; they differ only in
``serviceType``. So "take the first" was right for Sprouts by luck and would
have priced a **delivery basket** at ALDI. The platform client now asks
``ShopCollectionScoped``, which reports ``serviceType``, and prefers
``instore``.

For Sprouts the answer is **unchanged** -- still shop ``515202`` for 27401 --
which is precisely why the bug was invisible here. It is now also *verified*
rather than lucky: ``stats['shop_service_type']`` says ``instore`` instead of
saying nothing.

WHY THERE IS NO BROWSER, HERE OR IN aldi.py
--------------------------------------------
A Playwright DOM scraper was proposed for the second banner and rejected. The
platform answers plain ``httpx`` with a guest session, so a browser buys
nothing -- and the GFP-4 Whole Foods spike already established that the
distributed desktop app cannot carry a browser binary, so a Playwright scraper
is not a slower version of this, it is one that never reaches a customer. See
the platform module for the full argument.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Iterable

import httpx

from . import instacart_storefront as _platform

# The CLI/registry name. Distinct from STORE_KEY because Sprouts ALREADY had a
# source: `flipp_banners.MODULES` registers a `sprouts` weekly-ad banner. That
# collision is not theoretical -- it silently shadowed this module. `__init__`
# builds the registry as
#
#     SCRAPERS = {SCRAPER_KEY or STORE_KEY: m for m in _MODULES}
#     SCRAPERS.update(flipp_banners.MODULES)          # <-- last write wins
#
# so a hand-written module whose key matches a banner is overwritten *by the
# banner*, with no error. `gplan scrape sprouts` then runs the weekly ad and
# this file becomes unreachable from the CLI while still importing fine and
# still passing every one of its own tests. Caught only by running the app.
# Same shape as kroger.py's `harristeeter-api`, and for the same reason.
SCRAPER_KEY = "sprouts-storefront"

# The `deals.store` value -- deliberately shared with the Flipp banner. A
# nutritionist shops at Sprouts, not at "Sprouts' Instacart storefront", and
# keeping one store identity is what lets GFP-75's records treat an observation
# from either feed as the same item.
STORE_KEY = "sprouts"

# The `deals.source` value. This is what keeps the two feeds from clobbering
# each other: service/ingest.run_scrape scopes its replace to
# (store, source, postal_code). Without it the storefront scrape would delete
# the weekly ad's rows and vice versa, on every run.
SOURCE = "instacart-storefront"

MERCHANT = "Sprouts Farmers Market (storefront)"
DEFAULT_POSTAL_CODE = "27401"
DEAL_TYPE = "Storefront Price"
PRODUCT_IDENTIFIER_NS = "sprouts.product_id"

BASE_URL = "https://shop.sprouts.com"
RETAILER_SLUG = "sprouts"
STOREFRONT_PATH = f"/store/{RETAILER_SLUG}/storefront"
PRODUCT_PATH = f"/store/{RETAILER_SLUG}/products/{{slug}}"
GRAPHQL_PATH = _platform.GRAPHQL_PATH
SITEMAP_INDEX = f"{BASE_URL}/sitemaps/storefront_pro/shop_{RETAILER_SLUG}_com/sitemap.xml"

# Instacart's ids for this tenant, read back from the live storefront rather
# than assumed -- see `discover`. These are the 27401 values and are only
# defaults; `serves`/`scrape` re-resolve them for whatever ZIP they are given.
DEFAULT_SHOP_ID = "515202"
DEFAULT_ZONE_ID = "430"
RETAILER_ID = "279"

HEAD_BYTES = _platform.HEAD_BYTES

# Observed 2026-08-11. `SimpleShopCollection` is re-discovered from the
# storefront on every run and this value is only a cold-start default.
# `ProductNutritionalInfo` is a genuine pin -- it appears in no server-rendered
# HTML and cannot be recomputed from the bundles. See the platform module's ROT
# TRAP, and its PINNING section for why this literal is written out here rather
# than shared with aldi.py even though the two are currently equal.
FALLBACK_HASHES = {
    "ProductNutritionalInfo":
        "9bc43a13c48e633ba4c8016118f101942a44603c5d10f913e9e471ffb730185a",
    "SimpleShopCollection":
        "d438f50ce0c6b59526c922754c7908bfbfa073c8893f466f0276f40a0074501a",
}
PINNED_OPERATIONS = _platform.PINNED_OPERATIONS

# A product with a stable, well-populated nutrition panel, used to prove the pin
# still works. Chosen because it exercises the whole path: a real protein
# figure, a metric serving weight, and a non-numeric servings count.
CANARY_PRODUCT_ID = "70516703"

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
    deal_type=DEAL_TYPE,
    product_identifier_ns=PRODUCT_IDENTIFIER_NS,
    source_label="sprouts_storefront",
    # Kept as the GFP-262 wording rather than derived from MERCHANT, which now
    # ends in "(storefront)" and would read "... (storefront) storefront
    # listing". A user-visible string is not a good place to show off a
    # refactor.
    priceless_description="Sprouts storefront listing",
)

# --------------------------------------------------------------------------- #
# Re-exports -- the GFP-262 public surface, unchanged
# --------------------------------------------------------------------------- #
SproutsError = _platform.StorefrontError
QueryNotAllowedError = _platform.QueryNotAllowedError
ThrottledError = _platform.ThrottledError

ShopContext = _platform.ShopContext
Nutrition = _platform.Nutrition
Listing = _platform.Listing
_FoodFact = _platform.FoodFact

product_id_from_slug = _platform.product_id_from_slug
servings_per_container = _platform.servings_per_container
serving_grams = _platform.serving_grams
size_is_weight = _platform.size_is_weight
package_grams = _platform.package_grams
protein_per_100g = _platform.protein_per_100g
pricing_unit_size = _platform.pricing_unit_size
display_item_name = _platform.display_item_name
parse_listing = _platform.parse_listing
discover_persisted_queries = _platform.discover_persisted_queries
nutrition_from_payload = _platform.nutrition_from_payload


def product_page_url(slug: str | None) -> str | None:
    return TENANT.product_page_url(slug)


def _zip_centroid(postal_code: str) -> dict[str, float]:
    """Kept as a module-level name because GFP-262 exported it."""
    return _platform.zip_centroid(postal_code, DEFAULT_POSTAL_CODE)


class SproutsClient(_platform.StorefrontClient):
    """The platform client, bound to the Sprouts tenant.

    A subclass rather than a factory function so that ``isinstance`` checks and
    the GFP-262 constructor signature both keep working. It adds no behaviour,
    and if it ever needs to, that is the signal Sprouts has stopped being an
    ordinary tenant of this platform.
    """

    def __init__(
        self,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
        graphql_pace=None,
        page_pace=None,
    ):
        super().__init__(
            TENANT, client=client, timeout=timeout,
            graphql_pace=graphql_pace, page_pace=page_pace,
        )


# --------------------------------------------------------------------------- #
# Readiness
# --------------------------------------------------------------------------- #
def readiness() -> tuple[bool, str]:
    """Always ready -- this source needs no credential of any kind.

    Kept so the module satisfies the same duck-typed surface as wholefoods and
    kroger, both of which can be *un*ready. Callers branch on the flag, not on
    whether the attribute exists, so returning a constant here is what lets
    Sprouts drop into the CLI's store table with no special case.
    """
    return True, "no credentials required (guest session)"


# --------------------------------------------------------------------------- #
# Orchestration -- thin delegations, so the tenant record is the only thing
# this module actually contributes
# --------------------------------------------------------------------------- #
def listing_to_row(
    listing: _platform.Listing,
    nutrition: _platform.Nutrition | None,
    zip_code: str,
    now: datetime,
) -> tuple[dict[str, Any], _platform.FoodFact | None]:
    return _platform.listing_to_row(TENANT, listing, nutrition, zip_code, now)


def _upsert_food_fact(conn: sqlite3.Connection, fact: _platform.FoodFact) -> None:
    _platform.upsert_food_fact(conn, TENANT, fact)


def verify_pinned_hashes(client: SproutsClient | None = None) -> tuple[bool, str]:
    """Prove the pinned nutrition query still runs. ``(ok, message)``.

    Cheap enough to run as a health check: one product, one request. It exists
    because the pin cannot self-heal, and a rotation is otherwise invisible
    until a scrape comes back with no protein anywhere.
    """
    return _platform.verify_pinned_hashes(TENANT, client)


def serves(postal_code: str) -> bool | None:
    """Is there a Sprouts serving ``postal_code``? (GFP-257)

    ``None`` when the question cannot be put -- a network error, a rotated
    hash. Unknown is not absent, and availability.py treats it permissively.
    """
    return _platform.serves(TENANT, postal_code)


def scrape(
    postal_code: str | None = None,
    limit: int | None = None,
    conn: sqlite3.Connection | None = None,
    client: SproutsClient | None = None,
    now: datetime | None = None,
    slugs: Iterable[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Scrape Sprouts shelf prices and return ``(rows, meta, stats)``.

    Matches the contract ``service/ingest.run_scrape`` expects of every scraper.
    Nutrition goes through ``/graphql`` (bulk-safe); only the products that came
    back with a protein figure are then priced from HTML, paced and with a 403
    circuit-breaker. ``limit`` bounds that second pass; leaving it ``None`` on
    the full 46k catalogue will trip the product-page wall.
    """
    return _platform.scrape(
        TENANT, postal_code=postal_code, limit=limit, conn=conn,
        client=client, now=now, slugs=slugs,
    )
