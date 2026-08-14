# ######### decohen-partners ##########
# Protein Ledger
"""Lidl US product catalogue -- a second feed for a store that already has one.

GFP-267. ``lidl`` is the Flipp weekly ad. This is Lidl's own product catalogue,
which carries the two things a flyer never does: a printed package size, and --
for some products -- a protein figure. Same relationship as
``foodlion``/``foodlion-catalog`` and ``sprouts``/``sprouts-storefront``, and
the same reason neither may evict the other: the ad has promotions the
catalogue does not, the catalogue has sizes the ad does not.

NO BROWSER, AND THE PROPOSED ONE WOULD HAVE FAILED SILENTLY
------------------------------------------------------------
A Playwright DOM scraper was proposed for this. It was not built, and it is
worth recording *why* beyond the standing rule that a browser does not ship
(GFP-4):

**Its search URL 404s.** ``https://www.lidl.com/search/products/{query}`` does
not exist. Playwright's ``goto`` succeeds against a 404, the selectors then
match nothing, and the script reports "No products extracted -- selectors may
need adjustment". That message points at the selectors, which are fine, and
never at the URL, which is wrong. A DOM scraper cannot tell "the page changed"
from "the page was never there", and that is the failure mode that costs hours.

Everything needed is on plain HTTP with no bot wall:

- **Published gzipped product sitemap** (:data:`PRODUCT_SITEMAP`) -- 4,039
  products, ~950 of them food. The site's own ``/static/sitemap.xml`` is only
  an index pointing at it.
- **schema.org JSON-LD on every product page** -- ``sku``, ``name``,
  ``description``, ``offers.price``.

THE FIND: protein lives in the nutrition image's ALT TEXT
----------------------------------------------------------
Not in the JSON-LD, not in a nutrition block. In the accessibility string on
the Nutrition Facts photograph::

    alt="Nutrition Facts label for a product with 50 calories, 1g total fat,
         10g protein, and 270mg sodium per serving."

That is machine-readable, and it is the only place the figure appears. It is
also **the most fragile nutrition source in this project**, because it is
written for screen readers rather than published as data: the sentence can be
reworded at any time by someone who would not think of it as a breaking change.
:func:`parse_alt_text_protein` therefore matches one narrow shape and returns
``None`` for anything else, and :func:`verify_alt_text_shape` is a canary that
says so out loud rather than letting coverage quietly fall to zero.

THE DECIDING QUESTION, ASKED FIRST: THIS IS A CLAIM, NOT A DENSITY
-------------------------------------------------------------------
The alt text gives protein **per serving**. Measured 2026-08-12, the page
carries **no serving size and no servings-per-container** -- ``"Serving Size"``,
``"servingSize"`` and ``"Servings Per"`` appear zero times in the HTML; the only
occurrence of "serving" at all is inside the alt sentence itself.

So a protein *density* cannot be computed from this source, and **this module
writes no ``foods``/``food_nutrients``/``deal_food_match`` rows at all** --
unlike ``sprouts``, ``kroger`` and ``wholefoods``, which can. Inventing a
density from a per-serving figure with no serving mass is precisely the bug
GFP-73 fixed, and it would be worse here than there: an over-stated density
sorts an item to the *top* of a cheapest-cost-per-gram ranking.

What it does instead is fold the claim into ``item_name``, where
``savings.parse_protein_claim`` already reads exactly this shape (GFP-69's
rule 4) and ``cost_per_gram_protein`` already applies the right, cautious
confidence to it (``LABEL_CLAIM_CONFIDENCE``, and
``LABEL_CLAIM_MULTISERVE_CONFIDENCE`` when the name signals several servings).
Nothing new was needed for that; the engine has handled on-pack claims since
GFP-69. Verified that both parsers read one string without interfering::

    "Premium Chunk Chicken Breast 10G Protein, 12.5 oz"
        parse_size          -> 12.5 oz
        parse_protein_claim -> 10.0

MEASURED COVERAGE
-----------------
See :data:`COVERAGE`. Price is near-universal; protein is opportunistic. That
places Lidl between the two sources built the day before: better than ALDI,
which publishes no nutrition at all, and well short of Sprouts.

robots.txt
----------
``Disallow: /q/search?id=*``. The search path is off limits and this module
never touches it -- enumeration is from the sitemap, which is not disallowed,
and which is the better source anyway because it is complete rather than
query-shaped.
"""
from __future__ import annotations

import gzip
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx

from .. import db, savings, weight_basis
from . import base, retry

# `lidl` is the Flipp banner. A colliding key would be silently overwritten by
# it -- `SCRAPERS.update(flipp_banners.MODULES)` is last-write-wins, which cost
# a live debugging session on `sprouts`. See tests/test_scraper_registry.py.
SCRAPER_KEY = "lidl-catalogue"
STORE_KEY = "lidl"
SOURCE = "lidl-catalogue"

MERCHANT = "Lidl (catalogue)"
DEFAULT_POSTAL_CODE = "27401"
DEAL_TYPE = "Catalogue Price"
PRODUCT_IDENTIFIER_NS = "lidl.sku"

BASE_URL = "https://www.lidl.com"
PRODUCT_SITEMAP = "https://lidl.com/p/export/US/en/product_sitemap.xml.gz"

#: The whole page is read, and that is NOT an oversight.
#:
#: sprouts.py reads only the first 30 KB because its JSON-LD sits ~12 KB in, a
#: 15x bandwidth saving. Copying that here silently produced **zero rows from
#: every product**: on a ~399 KB Lidl page the JSON-LD begins at byte ~232,000
#: and the nutrition alt text at ~322,000, so any partial read that is not
#: essentially the entire page truncates both. Trimming the last 20% would buy
#: little and would break the moment Lidl adds a section, so the optimisation
#: is dropped rather than tuned. The pacing, not the bandwidth, is what bounds
#: this scrape.
READ_WHOLE_PAGE = True

#: Measured 2026-08-12 over a 14-product food sample. Recorded rather than
#: described so a later run can be compared against it rather than against a
#: memory of it.
COVERAGE = {
    "measured_on": "2026-08-12",
    "products_in_sitemap": 4039,
    "food_products_estimated": 950,
    "sample_size": 14,
    "with_price": 14,
    "with_protein_claim": 3,
    "with_serving_size": 0,      # the reason no density is ever written
}

#: The one alt-text shape this parser accepts. Deliberately narrow: a looser
#: pattern would keep returning numbers after the sentence changes, which is
#: worse than returning nothing, because nobody notices a wrong number.
_ALT_PROTEIN = re.compile(
    r"Nutrition Facts label[^\"]*?([0-9]+(?:\.[0-9]+)?)\s*g\s+protein", re.I
)
_LD_JSON = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
_SITEMAP_LOC = re.compile(r"<loc>(.*?)</loc>", re.S)
#: "12.5 oz." in a description bullet. Lidl has no size field of its own.
_SIZE_IN_DESCRIPTION = re.compile(
    r"([0-9]+(?:\.[0-9]+)?)\s*(fl\.?\s*oz|oz|lb|lbs|g|kg|ml|l|ct|count|pk|pack)\b", re.I
)

#: Words that mark a sitemap entry as food. Lidl's sitemap is dominated by the
#: "middle aisle" -- cast iron pans, clothing, power tools -- so enumerating
#: everything would spend most of the scrape on things nobody eats.
FOOD_HINTS = (
    "chicken", "beef", "pork", "turkey", "salmon", "tuna", "fish", "shrimp",
    "egg", "milk", "yogurt", "cheese", "butter", "bean", "lentil", "protein",
    "steak", "sausage", "bacon", "ham", "tofu", "nut", "peanut", "almond",
    "bread", "pasta", "rice", "cereal", "oat", "fruit", "vegetable", "juice",
    "snack", "sauce", "soup", "frozen", "organic", "cream", "chocolate",
)

#: A product whose alt text is known to carry a protein figure. If the shape
#: changes, this stops matching and `verify_alt_text_shape` says so.
CANARY_URL = (
    "https://lidl.com/p/premium-chunk-chicken-breast-with-rib-meat-in-water/p11237179"
)
CANARY_PROTEIN_G = 10.0


class LidlError(RuntimeError):
    """Base for this scraper's failures."""


class ThrottledError(LidlError):
    """Lidl asked us to slow down. See retry.Paced."""


@dataclass(frozen=True)
class Listing:
    """One catalogue product. ``protein_per_serving`` is a CLAIM, not a density."""

    sku: str
    url: str
    name: str | None
    price: float | None
    size_text: str | None
    protein_per_serving: float | None


@dataclass(frozen=True)
class _Nothing:
    """Placeholder so the module's shape matches its siblings.

    This scraper deliberately produces no food facts -- see the docstring's
    "claim, not a density" section -- and this type exists only to make that
    absence explicit to a reader comparing it against kroger.py or sprouts.py,
    where the equivalent dataclass carries real nutrition.
    """


# --------------------------------------------------------------------------- #
# Readiness
# --------------------------------------------------------------------------- #
def readiness() -> tuple[bool, str]:
    """No credential of any kind. Same as the Instacart tenants."""
    return True, "no credentials required; price and size, protein claims only"


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def sku_from_url(url: str) -> str:
    """``.../p11237179`` -> ``"11237179"``. Empty when the URL has no sku."""
    m = re.search(r"/p([0-9]+)\s*$", url.strip())
    return m.group(1) if m else ""


def looks_like_food(url: str) -> bool:
    """Is this sitemap entry plausibly edible?

    A hint list, not a classifier. It is deliberately generous -- a false
    positive costs one wasted request, while a false negative silently drops a
    product from the catalogue, and the second is the expensive direction.
    """
    lowered = url.lower()
    return any(word in lowered for word in FOOD_HINTS)


def parse_alt_text_protein(html: str) -> float | None:
    """Grams of protein per serving from the nutrition image's alt text.

    ``None`` when the page carries no such image, which is the common case --
    only about a fifth of food products have one. That is an absence, not a
    failure: most Lidl products simply do not publish the label as an image
    with a described alt string.
    """
    m = _ALT_PROTEIN.search(html)
    if not m:
        return None
    grams = float(m.group(1))
    return grams if grams > 0 else None


def parse_size_from_description(description: str | None) -> str | None:
    """The package size, dug out of the JSON-LD description's bullet list.

    Lidl publishes no size field; the size is prose, e.g.
    ``"<li>12.5 oz.</li>"``. Returns a normalised ``"12.5 oz"`` that
    ``savings.parse_size`` can read, or ``None``.
    """
    if not description:
        return None
    text = re.sub(r"<[^>]+>", " ", description)
    m = _SIZE_IN_DESCRIPTION.search(text)
    if not m:
        return None
    quantity, unit = m.group(1), m.group(2).lower().replace(".", "").replace(" ", "")
    return f"{quantity} {unit}"


def parse_listing(url: str, html: str) -> Listing | None:
    """Build a :class:`Listing` from a product page's first chunk."""
    product = None
    for block in _LD_JSON.findall(html):
        try:
            candidate = json.loads(block)
        except ValueError:
            continue
        if isinstance(candidate, dict) and candidate.get("@type") == "Product":
            product = candidate
            break
    if product is None:
        return None

    offers = product.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    price = base.price_to_float((offers or {}).get("price"))

    return Listing(
        sku=str(product.get("sku") or sku_from_url(url)),
        url=url,
        name=(product.get("name") or "").strip() or None,
        price=price,
        size_text=parse_size_from_description(product.get("description")),
        protein_per_serving=parse_alt_text_protein(html),
    )


def display_item_name(listing: Listing) -> str:
    """Fold size AND any protein claim into the name, because that is where the
    engine reads both.

    ``savings.parse_size`` and ``savings.parse_protein_claim`` each read
    ``item_name`` and ignore the other's token -- rule 4 exists precisely so a
    "10G Protein" is never mistaken for a 10-gram size. Verified together::

        "Premium Chunk Chicken Breast 10G Protein, 12.5 oz"
            parse_size          -> 12.5 oz
            parse_protein_claim -> 10.0

    The claim goes in only when there IS one, so a product without an alt-text
    label reads exactly as it did before.
    """
    label = (listing.name or "").strip()
    if listing.protein_per_serving:
        grams = listing.protein_per_serving
        rendered = f"{grams:g}G Protein"
        if rendered.lower() not in label.lower():
            label = f"{label} {rendered}".strip()
    if listing.size_text and savings.parse_size(label) is None:
        label = f"{label}, {listing.size_text}".strip(", ")
    return label


def product_page_url(listing: Listing) -> str | None:
    return listing.url or None


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class LidlClient:
    """Plain httpx against the public catalogue. No session, no credential."""

    def __init__(
        self,
        client: httpx.Client | None = None,
        timeout: float = 40.0,
        pace: retry.Paced | None = None,
    ):
        self._owned = client is None
        self._http = client or httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": base.user_agent(),
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        # Lidl's own limits are unmeasured, so it starts on the conservative
        # product-page budget rather than the generous GraphQL one. Assuming
        # the permissive direction is what gets an IP blocked (GFP-263).
        self.pace = pace or retry.Paced(retry.PRODUCT_PAGE_BUDGET)

    def __enter__(self) -> "LidlClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owned:
            self._http.close()

    def product_urls(self, food_only: bool = True) -> list[str]:
        """Every product URL in the published sitemap.

        The sitemap is gzipped and served from the apex host, not ``www``.
        """
        response = self._http.get(PRODUCT_SITEMAP)
        response.raise_for_status()
        xml = gzip.decompress(response.content).decode("utf-8", "replace")
        urls = _SITEMAP_LOC.findall(xml)
        return [u for u in urls if not food_only or looks_like_food(u)]

    def listing(self, url: str) -> Listing | None:
        """Price, size and any protein claim for one product.

        Reads the whole response -- see :data:`READ_WHOLE_PAGE` for why the
        partial-read trick that works on Sprouts silently returns nothing here.
        """
        self.pace.wait()
        response = self._http.get(url)
        if response.status_code in retry.THROTTLE_STATUS:
            if self.pace.record_throttled():
                self.pace.cool_off()
            raise ThrottledError(f"{response.status_code} on {url}")
        self.pace.record_success()
        response.raise_for_status()
        return parse_listing(url, response.text)


def verify_alt_text_shape(client: LidlClient | None = None) -> tuple[bool, str]:
    """Canary: does the nutrition alt-text sentence still parse? ``(ok, message)``

    Exists because this source's protein is an accessibility string rather than
    published data. If Lidl rewords it, :func:`parse_alt_text_protein` starts
    returning ``None`` for everything and coverage silently falls to zero --
    which looks exactly like "Lidl stopped publishing nutrition" and would not
    otherwise raise anything.
    """
    owned = client is None
    active = client or LidlClient()
    try:
        listing = active.listing(CANARY_URL)
    except (LidlError, httpx.HTTPError) as exc:
        return False, f"could not verify (transport): {exc}"
    finally:
        if owned:
            active.close()
    if listing is None:
        return False, f"canary {CANARY_URL} did not parse as a product at all"
    if listing.protein_per_serving is None:
        return False, (
            "canary parsed but its protein claim did not. The alt-text sentence "
            "has probably been reworded -- re-read a product page and update "
            "_ALT_PROTEIN."
        )
    if abs(listing.protein_per_serving - CANARY_PROTEIN_G) > 0.01:
        return False, (
            f"canary protein changed: {listing.protein_per_serving}g, expected "
            f"{CANARY_PROTEIN_G}g. Either the product was reformulated or the "
            "parser is reading the wrong number."
        )
    return True, f"alt-text shape OK (canary {CANARY_PROTEIN_G:g}g protein)"


# --------------------------------------------------------------------------- #
# Row mapping
# --------------------------------------------------------------------------- #
def listing_to_row(listing: Listing, zip_code: str, now: datetime) -> dict[str, Any]:
    """Map one catalogue product to a ``deals`` row.

    Returns a row only -- no food fact. See the module docstring: this source
    cannot compute a protein density and must not pretend to.
    """
    item_name = display_item_name(listing)
    has_price = listing.price is not None
    identifier, identifier_ns = base.product_identifier(
        listing.sku, PRODUCT_IDENTIFIER_NS
    )

    notes = [
        "source=lidl_catalogue",
        f"sku={listing.sku}",
        f"postal_code={zip_code}",
    ]
    if listing.size_text:
        notes.append(f"size={listing.size_text}")
    if listing.protein_per_serving is not None:
        # Recorded as a CLAIM with its provenance, so nothing downstream can
        # mistake it for a measured density.
        notes.append(f"protein_claim_per_serving_g={listing.protein_per_serving:g}")
        notes.append("protein_source=nutrition_label_alt_text")
        notes.append("protein_density=not_computable_no_serving_size")
    if not has_price:
        notes.append("price_missing=true")

    return {
        "item_name": item_name,
        "sub_category": base.infer_sub_category(item_name, "", has_price),
        "deal_type": DEAL_TYPE if has_price else f"{DEAL_TYPE} (price not listed)",
        "deal_description": f"${listing.price:.2f}" if has_price else "Lidl catalogue listing",
        "regular_price": None,
        "sale_price": listing.price,
        "dollar_price": listing.price,
        "discount_amount": None,
        "discount_percent": None,
        "valid_from": now.date().isoformat(),
        # A catalogue price announces no expiry and `deals` is replaced wholesale
        # on re-scrape, so missing valid_to reads as "unknown", never "expired".
        "valid_to": None,
        "loyalty_required": "N",
        "notes": "; ".join(notes),
        "source_url": product_page_url(listing),
        "image_url": None,
        "flipp_flyer_id": None,
        "flipp_item_id": None,
        "flipp_coupon_id": None,
        # Lidl publishes fixed packages; nothing in the response denominates a
        # price per pound, so claiming WEIGHT would be inventing a fact.
        "sold_by": "UNIT",
        "weight_basis": weight_basis.classify("UNIT", None, item_name),
        "price_per_unit": None,
        "price_per_unit_uom": None,
        "product_identifier": identifier,
        "product_identifier_ns": identifier_ns,
    }


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def serves(postal_code: str) -> bool | None:
    """``None`` -- unknown, and honestly so (GFP-257).

    Lidl US publishes no store-locator API this module can ask, and the
    catalogue is national rather than store-scoped, so there is nothing to read
    an answer back from. Returning ``False`` would remove a store the client may
    genuinely have; returning ``True`` would assert a presence nobody checked.
    ``None`` is the third state, and availability.py treats it permissively.

    A declared service area is the intended fix (GFP-184's capability, and
    GFP-257's fallback for platforms that cannot be asked).
    """
    return None


def scrape(
    postal_code: str | None = None,
    limit: int | None = None,
    conn: sqlite3.Connection | None = None,
    client: LidlClient | None = None,
    now: datetime | None = None,
    urls: Iterable[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Scrape the Lidl catalogue and return ``(rows, meta, stats)``.

    Matches the contract ``service/ingest.run_scrape`` expects. Unlike
    ``sprouts``/``kroger``/``wholefoods`` it writes NOTHING to
    ``foods``/``food_nutrients`` -- see the docstring. ``conn`` is accepted for
    interface parity and deliberately unused.
    """
    zip_code = postal_code or DEFAULT_POSTAL_CODE
    moment = now or datetime.now(timezone.utc)

    owned_client = client is None
    active = client or LidlClient()

    try:
        catalogue = list(urls) if urls is not None else active.product_urls()
        targets = catalogue[:limit] if limit is not None else catalogue

        rows: list[dict[str, Any]] = []
        skipped = 0
        unparsed = 0
        for url in targets:
            try:
                listing = active.listing(url)
            except ThrottledError:
                skipped += 1
                continue
            except httpx.HTTPError:
                unparsed += 1
                continue
            if listing is None:
                unparsed += 1
                continue
            rows.append(listing_to_row(listing, zip_code, moment))

        priced = sum(1 for r in rows if r["dollar_price"] is not None)
        claims = sum(
            1 for r in rows if "protein_claim_per_serving_g=" in (r["notes"] or "")
        )
        meta = {
            "name": f"{MERCHANT} ({zip_code})",
            "id": STORE_KEY,
            "store_name": MERCHANT,
        }
        stats = {
            "weekly_ad": len(rows),
            "digital_coupons": 0,
            "no_price": len(rows) - priced,
            "bogo": 0,
            "expired_items": 0,
            "total": len(rows),
            "flyer_id": STORE_KEY,
            "flyer_name": meta["name"],
            "flyer_status": "active",
            "valid_from": moment.date().isoformat(),
            "valid_to": moment.date().isoformat(),
            "priced": priced,
            # Named a CLAIM, not "with_protein", so a reader comparing this
            # against sprouts' stats cannot mistake the two for the same fact.
            "with_protein_claim": claims,
            "with_protein": 0,
            "products_seen": len(targets),
            "catalogue_size": len(catalogue),
            # No silent caps: everything dropped is counted.
            "skipped_throttled": skipped,
            "unparsed": unparsed,
            **active.pace.stats(),
        }
        return rows, meta, stats
    finally:
        if owned_client:
            active.close()
