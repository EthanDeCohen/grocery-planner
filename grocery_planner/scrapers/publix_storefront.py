# ######### decohen-partners ##########
# Protein Ledger
"""Publix shelf prices, through the Instacart storefront platform (GFP-293).

In:  a ZIP.  Out: deal rows with prices, plus nutrition where Publix publishes it.

Publix does not put grocery prices on publix.com -- that site prices only what
you can order online, so searching "chicken breast" there returns subs and
platters. Grocery e-commerce is handed to Instacart, and the logo next to
"Delivery & curbside" in Publix's own nav is Instacart's.

So this is a third TENANT of the platform already running Sprouts and ALDI, not
a new client. Everything here is a tenant record; the behaviour lives in
instacart_storefront. (GFP-288 chased publix.com's own endpoints first; the one
that answers keys on a curated carousel, not a product. Superseded by this.)

THE CANDIDATE PROBLEM -- READ THIS BEFORE CHANGING scrape(). Publix's catalogue
dwarfs the other two tenants', and `limit` does not save you: it bounds the
PRICING pass only. The nutrition pass makes one GraphQL call per slug it is
handed. Measured 2026-08-14:

    171,249 slugs in the sitemap
    108,978 unique                  <- the sitemap repeats about 36%
      7,725 plausible protein buys  <- 7.1%, a 14x cut

Unfiltered, that is ~109k calls to learn the protein content of shampoo, and
the first spike slept 1,558 seconds doing exactly that. So the filter is the
DEFAULT, not an option, and it is free: it reads the name already sitting in
the slug and asks protein_kind. No network.

The filter is deliberately PERMISSIVE -- it keeps "fish oil 1200 mg" because a
name alone cannot tell that from fish, and dropping a real protein to dodge a
supplement is the worse error. Downstream already rejects an implausible
density. Tightening belongs in protein_kind, where every source benefits, not
in a per-tenant blocklist here.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from typing import Any, Iterable

import httpx

from .. import protein_kind
from . import instacart_storefront as _platform

#: The CLI/registry name. `publix` is the Flipp weekly-ad banner and
#: `publix-catalog` was the Parse.bot feed -- three names for one shop. The
#: registry is last-write-wins and `SCRAPERS.update(flipp_banners.MODULES)` runs
#: last, so a module whose key collides with a banner is silently shadowed and
#: unreachable from the CLI. That happened to sprouts and cost a live debugging
#: session.
SCRAPER_KEY = "publix-storefront"
STORE_KEY = "publix"

#: Distinct from the banner's source so the two feeds do not delete each other:
#: service/ingest.run_scrape scopes its replace to (store, source, postal_code).
SOURCE = "instacart-storefront"

MERCHANT = "Publix (storefront)"
DEFAULT_POSTAL_CODE = "27401"
DEAL_TYPE = "Storefront Price"
PRODUCT_IDENTIFIER_NS = "publix.product_id"

BASE_URL = "https://delivery.publix.com"
RETAILER_SLUG = "publix"

#: Instacart's ids for this tenant, read back from the live storefront rather
#: than assumed. These are the 27401 values and are cold-start defaults only;
#: `serves`/`scrape` re-resolve them for whatever ZIP they are given. Confirmed
#: 2026-08-14 from the storefront's Apollo cache:
#:     {"retailerId":"57","retailerSlug":"publix","zoneId":"430"}
#: and, alongside it, {"postalCode":"27401","shopId":"3548"}.
DEFAULT_SHOP_ID = "3548"
DEFAULT_ZONE_ID = "430"
RETAILER_ID = "57"

#: Cold-start defaults only. The spike that proved this tenant reported
#: `hashes_discovered: True`, i.e. discovery worked and these were never used --
#: they exist for the run where it does not. Written out here rather than
#: imported from sprouts.py even though the values match today: the platform's
#: PINNING note explains why sharing them would couple two tenants whose
#: rotations are independent.
FALLBACK_HASHES = {
    "ProductNutritionalInfo":
        "9bc43a13c48e633ba4c8016118f101942a44603c5d10f913e9e471ffb730185a",
    "SimpleShopCollection":
        "d438f50ce0c6b59526c922754c7908bfbfa073c8893f466f0276f40a0074501a",
}

#: No canary yet. Unlike Sprouts, no Publix product has been confirmed to carry
#: a stable, well-populated panel, and inventing one would make
#: `verify_pinned_hashes` report success against a product that might simply be
#: missing. None is the honest value until one is chosen deliberately.
CANARY_PRODUCT_ID = None

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
    source_label="publix_storefront",
    priceless_description="Publix storefront listing",
)

#: A product slug is ``<numeric id>-<name with hyphens>``, e.g.
#: ``54638-lundberg-family-farms-organic-california-brown-jasmine-rice-32-oz``.
_SLUG_ID_PREFIX = re.compile(r"^[0-9]+-")

#: protein_kind's two non-answers.
_NOT_A_PROTEIN = frozenset({"unknown", "other"})

def name_from_slug(slug: str) -> str:
    """The product name a slug carries, for offline classification.

    ``54638-just-bare-natural-fresh-chicken-tenders-14-0-oz`` ->
    ``just bare natural fresh chicken tenders 14 0 oz``. Lossy (sizes come back
    as digits, apostrophes are gone) but ``protein_kind`` reads words, not
    punctuation, so it is good enough for the only question asked of it.
    """
    return _SLUG_ID_PREFIX.sub("", slug).replace("-", " ").strip()


def protein_candidate_slugs(slugs: Iterable[str]) -> list[str]:
    """The subset worth asking the nutrition API about, in the order given.

    Publix's catalogue is big enough that this is the difference between a
    viable scrape and an impossible one -- `limit` only bounds the PRICING
    pass, while the nutrition pass walks every slug it is handed, one call
    each. Measured: 108,978 unique slugs -> 15,669 candidates.

    Order is preserved rather than sorted, so successive bounded runs walk the
    catalogue the same way twice.

    Two things this deliberately does NOT do any more:
    de-duplication belongs to the platform, which does it for every tenant
    (GFP-294); and the ~40-term non-meat vocabulary that used to live here is
    gone, because `protein_kind` names dairy, eggs and plant protein itself
    (GFP-295). One question, asked once.
    """
    kept: list[str] = []
    for slug in slugs:
        name = name_from_slug(slug)
        if protein_kind.is_disqualified(name):
            continue
        if protein_kind.classify(name) not in _NOT_A_PROTEIN:
            kept.append(slug)   # meat, seafood, dairy, egg or plant
    return kept


def readiness() -> tuple[bool, str]:
    """Always ready -- a guest session, no credential of any kind.

    Kept so this module satisfies the same duck-typed surface as wholefoods and
    kroger, both of which can be *un*ready. Callers branch on the flag, not on
    whether the attribute exists.
    """
    return True, "no credentials required (guest session)"


def serves(postal_code: str) -> bool | None:
    """Is there a Publix serving ``postal_code``? (GFP-257)

    ``None`` when the question cannot be put -- a network error, a rotated
    hash. Unknown is not absent, and availability.py treats it permissively.
    """
    return _platform.serves(TENANT, postal_code)


def verify_pinned_hashes(
    client: _platform.StorefrontClient | None = None,
) -> tuple[bool, str]:
    """Prove the pinned nutrition query still runs. ``(ok, message)``.

    Reports honestly that it cannot check while :data:`CANARY_PRODUCT_ID` is
    None -- the platform helper handles that case, and a fabricated canary would
    turn "no product to test" into a false pass.
    """
    return _platform.verify_pinned_hashes(TENANT, client)


def product_page_url(slug: str | None) -> str | None:
    return TENANT.product_page_url(slug)


def scrape(
    postal_code: str | None = None,
    limit: int | None = None,
    conn: sqlite3.Connection | None = None,
    client: _platform.StorefrontClient | None = None,
    now: datetime | None = None,
    slugs: Iterable[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Scrape Publix shelf prices and return ``(rows, meta, stats)``.

    Matches the contract ``service/ingest.run_scrape`` expects of every scraper.

    Unlike the other two tenants this narrows the catalogue **before** the
    nutrition pass, via :func:`protein_candidate_slugs` -- see the module
    docstring for why ``limit`` alone is not enough. Passing ``slugs``
    explicitly overrides that entirely, filter included, which is what a
    reproduction of one product should do.
    """
    owned_client = client is None
    active = client or _platform.StorefrontClient(TENANT)
    try:
        if slugs is None:
            if owned_client:
                active.discover()
            slugs = protein_candidate_slugs(active.product_slugs())
        return _platform.scrape(
            TENANT, postal_code=postal_code, limit=limit, conn=conn,
            client=active, now=now, slugs=slugs,
        )
    finally:
        if owned_client:
            active.close()
