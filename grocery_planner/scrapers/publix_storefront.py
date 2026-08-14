"""Publix shelf prices, via the Instacart storefront platform (GFP-293).

Publix does not publish grocery prices on publix.com. Its own site prices only
what you can *order* online -- deli and bakery -- so a search there for "chicken
breast" returns subs and platters with menu prices, and the product page for
Perdue Fresh Ground Chicken Breast shows a title, a size, "Add to list" and no
price at all. Grocery e-commerce is delegated to Instacart, and the carrot next
to "Delivery & curbside" in Publix's own navigation is Instacart's logo.

So this is a third tenant of the platform already running for Sprouts and ALDI,
not a new client. Everything below is a tenant record; the behaviour lives in
:mod:`instacart_storefront`.

GFP-288 chased publix.com's own endpoints and found `productitems`, which does
answer unauthenticated -- but its ``Id`` parameter identifies a curated CAROUSEL
rather than a product (one call returns five products sharing the id passed in),
so the reachable surface there is whatever Publix is merchandising. That ticket
is superseded by this one, and this module is why.

THE CANDIDATE PROBLEM -- READ BEFORE CHANGING ``scrape``
--------------------------------------------------------
Publix's catalogue is far larger than the other two tenants', and ``limit``
does not save you: it bounds the PRICING pass only. The nutrition pass walks
every slug it is given, one GraphQL call each. Measured 2026-08-14:

    171,249 slugs listed in the sitemap
    108,978 unique                        <- the sitemap repeats ~36%
      7,725 plausible protein buys        <- 7.1%, a 14x cut

Handing the raw catalogue to the platform means ~109k GraphQL calls to find
protein figures for shampoo and birthday candles, and the first spike slept
1,558 seconds doing exactly that. :func:`protein_candidate_slugs` is therefore
the DEFAULT rather than an option, and the filter is free -- it reads the name
already embedded in the slug and asks ``protein_kind``, with no network at all.

This is GFP-281's finding applied: the coverage is in what we already have, not
in more volume. Pricing 39,808 items to rank protein spends hours on rice.

The filter is deliberately PERMISSIVE. It keeps "fish oil 1200 mg" and "smokey
cheese bacon mashed potatoes" because a name-level rule cannot tell those from
food, and dropping a real protein to avoid a supplement is the worse error --
the pipeline downstream already rejects an implausible density
(``density_rejected_implausible``) and GFP-281's harness will measure whatever
slips through. Tightening belongs in ``protein_kind``, where every source
benefits, not in a per-tenant list here.
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

#: Non-meat protein, which ``protein_kind`` cannot name.
#:
#: ``protein_kind`` classifies SPECIES -- chicken, pork, beef, fish, turkey,
#: shellfish, lamb -- so it answers 'unknown' for eggs, Greek yogurt, whey,
#: cheese, peanut butter, tofu and beans alike. Filtering on it alone would have
#: made Publix a meat-only source while the GUI offers an "Overall protein" tab,
#: which is a coverage cap the user cannot see: the tab would simply look
#: sparse at Publix and nobody would know why.
#:
#: This list is a STOPGAP and deliberately lives here rather than in
#: ``protein_kind``. Widening that module changes what every existing source
#: classifies, and it feeds ``bill.py``'s eligibility and the optimiser's
#: rankings -- not a change to make as a side effect of adding a scraper. See
#: GFP-295: the vocabulary belongs there, and this constant should be deleted
#: when it moves.
_NON_MEAT_PROTEIN_TERMS: tuple[str, ...] = (
    "egg", "eggs", "yogurt", "yoghurt", "greek yogurt", "skyr", "cottage cheese",
    "cheese", "milk", "kefir", "whey", "casein", "protein powder", "protein shake",
    "protein bar", "tofu", "tempeh", "seitan", "edamame", "lentil", "lentils",
    "bean", "beans", "chickpea", "chickpeas", "garbanzo", "hummus", "peanut butter",
    "almond butter", "peanuts", "almonds", "cashews", "walnuts", "pistachios",
    "quinoa", "soy", "sardine", "sardines", "anchovy", "anchovies",
)


def name_from_slug(slug: str) -> str:
    """The product name a slug carries, for offline classification.

    ``54638-just-bare-natural-fresh-chicken-tenders-14-0-oz`` ->
    ``just bare natural fresh chicken tenders 14 0 oz``. Lossy (sizes come back
    as digits, apostrophes are gone) but ``protein_kind`` reads words, not
    punctuation, so it is good enough for the only question asked of it.
    """
    return _SLUG_ID_PREFIX.sub("", slug).replace("-", " ").strip()


def protein_candidate_slugs(slugs: Iterable[str]) -> list[str]:
    """The subset worth asking the nutrition API about, in stable order.

    Two reductions, both free and both measured on the live catalogue:

    * **De-duplicate.** The sitemap lists 171,249 product URLs but only 108,978
      distinct slugs -- roughly 36% are repeats, and each repeat would otherwise
      cost its own GraphQL call.
    * **Drop what cannot be a protein buy**, by asking ``protein_kind`` about
      the name inside the slug. 108,978 -> 7,725.

    Order is preserved rather than sorted so a bounded run is reproducible and
    successive runs walk the catalogue the same way -- the optimiser's
    same-inputs-same-plan invariant (GFP-224) applied to ingestion.
    """
    kept: list[str] = []
    seen: set[str] = set()
    for slug in slugs:
        if slug in seen:
            continue
        seen.add(slug)
        name = name_from_slug(slug)
        if protein_kind.is_disqualified(name):
            continue
        if protein_kind.classify(name) not in _NOT_A_PROTEIN:
            kept.append(slug)          # a named species
        elif _has_non_meat_protein(name):
            kept.append(slug)          # eggs, dairy, legumes, nuts, whey
    return kept


def _has_non_meat_protein(name: str) -> bool:
    """Whole-word match against :data:`_NON_MEAT_PROTEIN_TERMS`.

    Word-bounded rather than a substring test: ``"bean"`` in a substring test
    matches "beanie" and, worse, ``"egg"`` matches "eggplant" -- which is how a
    vegetable ends up in a protein ranking.
    """
    padded = f" {name} "
    return any(f" {term} " in padded for term in _NON_MEAT_PROTEIN_TERMS)


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
