# ######### decohen-partners ##########
# Protein Ledger
"""PRISM product catalogue: Food Lion and GIANT (GFP-247).

**Food Lion, Giant Food, GIANT/MARTIN'S, Hannaford and Stop & Shop all run on
PRISM**, Peapod Digital Labs' platform (Ahold Delhaize USA's digital engine).
They serve byte-for-byte the same page structure, and their ``robots.txt``
files are 167 lines differing by exactly one line -- the sitemap hostname. So
this is one scraper for several banners, configured rather than branched
(GFP-32).

What this source is, and what it is NOT
---------------------------------------
**It is a product ATTRIBUTE source, not a price source.** GFP-246 established
that the catalogue serves a *default-store* price: the page is store-bound
(its state carries a ``pickStoreLocationId``) but ``/store-locator`` is
DataDome-protected and a location cookie is ignored, so we cannot ask for the
price at a client's own shop.

That matters far less than it sounds, because **size and protein are
ZIP-invariant**. A 4.5 oz can holds 4.5 oz and 18 g of protein at every Food
Lion in America; only the price varies by store. And a per-ZIP price for these
banners already exists -- the Flipp weekly ad, scraped per postal code. So the
intended shape is: **price from Flipp, size and protein from here**, joined by
GFP-248's ``sourcelink``. This feed's one weakness is the one attribute we do
not need from it.

Rows are therefore written with ``deal_type`` :data:`CATALOGUE_DEAL_TYPE` and a
note saying the price is a default-store reference, so nothing downstream can
mistake it for a promotion at the client's shop.

Access
------
Everything here is inside what ``robots.txt`` permits for ``User-agent: *``:

- ``/groceries/sitemap.xml`` is advertised in ``robots.txt`` itself.
- ``/groceries/product/...`` is **not** in the ``*`` Disallow list. (It *is*
  disallowed for GPTBot/ClaudeBot/PerplexityBot and friends, which are the most
  restricted group in that file -- so this scraper must run under its own
  user-agent and must never claim to be one of them.)
- ``/product-search/`` and ``/browse-aisles/`` ARE disallowed, and ``/`` and
  ``/savings/`` return 403 behind DataDome. Nothing here touches any of them.

Politeness is not optional at this scale. The catalogue is ~30,000 products
across three shards; a full sweep on every scrape would be a load we have no
right to impose. So this filters to protein-relevant products by slug before
fetching anything, caps the run at :data:`DEFAULT_MAX_PRODUCTS`, and sleeps
between requests.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx

from . import base

#: What a catalogue row's ``deal_type`` says. Not "Weekly Ad": this is not an
#: advertised promotion, and a reader must be able to tell them apart.
CATALOGUE_DEAL_TYPE = "Catalogue"

#: Recorded on every row, because the price is a default-store figure and
#: GFP-246 established we cannot ask for a specific store's.
PRICE_CAVEAT = (
    "Catalogue list price for the banner's default store, not the client's "
    "shop -- use the weekly ad for a per-ZIP price (GFP-247)."
)

#: The identifier vocabulary these rows carry (GFP-111). A bare '7134' says
#: nothing about which system minted it, and must never be compared with a
#: Kroger productId or a Flipp item id.
PRODUCT_IDENTIFIER_NS = "prism.product_id"

#: Fetch at most this many product pages in one run. The catalogue is ~30,000
#: products; a bounded, protein-filtered slice is what this source is for.
DEFAULT_MAX_PRODUCTS = 400

#: Seconds between product fetches.
REQUEST_DELAY = 1.0

#: Slugs that contain a protein word but are not a protein food. Found by
#: running the thing: the first live pass returned Milk-Bone "Original BEEF
#: Flavor" dog treats, "STEAK Sauce" and "CHICKEN Gravy", because a keyword
#: cannot tell an ingredient from a flavour label. Negatives are checked first
#: and win, since a false positive here costs a request AND pollutes the
#: catalogue with something no client will ever eat.
EXCLUDE_SLUG_TERMS = (
    "dog", "cat-", "-cat-", "puppy", "kitten", "pet-", "treats", "milk-bone",
    "sauce", "gravy", "seasoning", "marinade", "rub-", "broth", "bouillon",
    "stock-", "soup", "ramen", "flavored", "flavor-", "scented", "bowl-cleaner",
    "shampoo", "litter", "chew", "rawhide", "biscuit",
)

#: Only fetch products whose slug suggests they carry protein worth ranking.
#: Slug-based because the alternative -- fetching everything to find out -- is
#: exactly the load this exists to avoid. Over-inclusive on purpose: a false
#: positive costs one request, a false negative loses a food permanently.
PROTEIN_SLUG_TERMS = (
    "chicken", "beef", "pork", "turkey", "salmon", "tilapia", "cod", "tuna",
    "shrimp", "steak", "bacon", "sausage", "ham", "lamb", "egg", "eggs",
    "yogurt", "cheese", "milk", "tofu", "beans", "lentil", "protein",
    "whey", "jerky", "roast", "loin", "brisket", "ribeye", "sirloin",
)


@dataclass(frozen=True)
class PrismStore:
    """One PRISM banner. Everything that differs between them lives here."""

    key: str            # the deals.store value
    display_name: str
    host: str           # e.g. "foodlion.com"
    default_postal_code: str


FOOD_LION = PrismStore("foodlion", "Food Lion", "foodlion.com", "27401")
GIANT = PrismStore("giant", "GIANT", "giantfoodstores.com", "19103")

_LOC = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.S)
_LD_JSON = re.compile(
    r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
#: Protein grams as the page state carries them.
_PROTEIN = re.compile(
    r'"amount":\s*\[0,\s*([0-9.]+)\][^}]*?"id":\s*\[0,\s*"protein"')
#: ...and as the rendered nutrition panel shows them, for pages shaped
#: differently. Two readings of one fact, so a layout change costs coverage
#: rather than everything.
_PROTEIN_RENDERED = re.compile(r"Protein\s+([0-9.]+)\s*g")
#: A trailing size in a product slug: "...-4-5-oz-can/7134" -> 4.5 oz.
_SLUG_SIZE = re.compile(
    r"-(\d+)(?:-(\d+))?-(oz|lb|lbs|ct|count|g|kg|ml|l|fl-oz|pk)(?:-[a-z]+)?/\d+$")
_SLUG_ID = re.compile(r"/(\d+)$")

_UNIT_LABEL = {
    "oz": "oz", "lb": "lb", "lbs": "lb", "ct": "ct", "count": "ct",
    "g": "g", "kg": "kg", "ml": "ml", "l": "l", "fl-oz": "fl oz", "pk": "ct",
}


def sitemap_url(store: PrismStore) -> str:
    return f"https://{store.host}/groceries/sitemap.xml"


def size_from_slug(url: str) -> str | None:
    """The size a product URL states, as a human unit string, or ``None``.

    The catalogue's ``ld+json`` name carries no size -- "Swanson Premium Chunk
    Chicken Breast in Water" -- but the slug does, as hyphen-separated digits
    with the decimal point dropped: ``...-4-5-oz-can/7134`` is 4.5 oz. That
    size is the entire reason this source is worth having, so it is recovered
    here and appended to the item name, where ``savings.parse_size`` can read
    it exactly as it reads a Kroger name's.
    """
    m = _SLUG_SIZE.search(url)
    if m is None:
        return None
    whole, frac, unit = m.group(1), m.group(2), m.group(3)
    quantity = f"{whole}.{frac}" if frac else whole
    return f"{quantity} {_UNIT_LABEL.get(unit, unit)}"


def product_id_from_url(url: str) -> str | None:
    m = _SLUG_ID.search(url)
    return m.group(1) if m else None


def looks_like_protein(url: str) -> bool:
    """Is this slug worth spending a request on?

    Negatives are checked first and win outright. A keyword cannot tell an
    ingredient from a flavour label -- "Original Beef Flavor" dog treats and
    "Chicken Gravy" both match "beef" and "chicken" -- and the first live run
    returned exactly those.
    """
    slug = url.rsplit("/product/", 1)[-1].lower()
    if any(term in slug for term in EXCLUDE_SLUG_TERMS):
        return False
    return any(term in slug for term in PROTEIN_SLUG_TERMS)


def parse_sitemap(xml: str) -> list[str]:
    """Every ``<loc>`` in a sitemap or sitemap index."""
    return _LOC.findall(xml)


def parse_product(html: str, url: str) -> dict[str, Any] | None:
    """One product page into a ``deals`` row, or ``None`` if it carries no price.

    Absent stays absent (savings.py rule 1): a page without a parseable offer
    produces no row rather than a row with a guessed price.
    """
    blocks = _LD_JSON.findall(html)
    product: dict[str, Any] = {}
    for block in blocks:
        try:
            data = json.loads(block)
        except (ValueError, TypeError):
            continue
        candidate = data.get("product") if isinstance(data, dict) else None
        if isinstance(candidate, dict) and candidate.get("name"):
            product = candidate
            break
    name = (product.get("name") or "").strip()
    if not name:
        return None

    offers = product.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    price = offers.get("price") if isinstance(offers, dict) else None
    try:
        price = float(price) if price is not None else None
    except (TypeError, ValueError):
        price = None
    if price is None or price <= 0:
        return None

    # The size belongs IN the name: that is where savings.parse_size looks, and
    # putting it anywhere else would mean this feed's whole contribution --
    # a machine-readable package weight -- never reached the optimiser.
    size = size_from_slug(url)
    item_name = f"{name}, {size}" if size else name

    protein = _PROTEIN.search(html) or _PROTEIN_RENDERED.search(html)
    brand = product.get("brand") or {}
    brand_name = brand.get("name") if isinstance(brand, dict) else brand

    row = {c: None for c in _DEAL_COLUMNS}
    row.update(
        item_name=item_name,
        sub_category=base.infer_sub_category(item_name, brand_name or "", True),
        deal_type=CATALOGUE_DEAL_TYPE,
        deal_description=(product.get("description") or "")[:500] or None,
        dollar_price=price,
        sale_price=price,
        notes=PRICE_CAVEAT + (
            f" Protein {protein.group(1)}g per serving." if protein else ""),
        source_url=url,
        product_identifier=product_id_from_url(url),
        product_identifier_ns=PRODUCT_IDENTIFIER_NS,
    )
    return row


# Imported lazily-ish to keep this module readable; importers owns the contract.
def _deal_columns() -> list[str]:
    from .. import importers

    return list(importers.DEAL_COLUMNS)


_DEAL_COLUMNS = _deal_columns()


def _client(timeout: float = 40.0) -> httpx.Client:
    return httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": base.user_agent()},
    )


def select_products(
    urls: Iterable[str], max_products: int = DEFAULT_MAX_PRODUCTS,
) -> list[str]:
    """The bounded, protein-relevant slice of a catalogue worth fetching.

    STRIDED, not the first N. The sitemap shards are ordered by product id,
    which is effectively an age ordering, so taking the head of the list
    returns whatever the retailer listed first -- in the live run, shelf-stable
    pantry goods. Striding evenly across the whole filtered pool costs nothing
    and gives a slice that actually represents the catalogue.
    """
    picked = [u for u in urls if looks_like_protein(u)]
    if not max_products or len(picked) <= max_products:
        return picked
    step = len(picked) / max_products
    return [picked[int(i * step)] for i in range(max_products)]


def scrape_store(
    store: PrismStore,
    postal_code: str | None = None,
    max_products: int = DEFAULT_MAX_PRODUCTS,
    delay: float = REQUEST_DELAY,
    client: httpx.Client | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Fetch a bounded slice of one banner's catalogue. Returns ``(rows, meta, stats)``."""
    own = client or _client()
    close = client is None
    try:
        index = parse_sitemap(own.get(sitemap_url(store)).text)
        shards = [u for u in index if "products" in u]
        product_urls: list[str] = []
        for shard in shards:
            # Every shard, always. Four sitemap fetches are trivial beside 400
            # product fetches, and stopping early would re-introduce exactly
            # the head bias select_products strides to remove.
            product_urls.extend(parse_sitemap(own.get(shard).text))

        wanted = select_products(product_urls, max_products)
        rows: list[dict[str, Any]] = []
        failed = 0
        for i, url in enumerate(wanted):
            if delay and i:
                time.sleep(delay)
            try:
                response = own.get(url)
                if response.status_code != 200:
                    failed += 1
                    continue
                row = parse_product(response.text, url)
            except httpx.HTTPError:
                failed += 1
                continue
            if row is not None:
                rows.append(row)
    finally:
        if close:
            own.close()

    meta = {
        "id": store.key,
        "host": store.host,
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    stats = {
        "total": len(rows),
        "catalogue_urls": len(product_urls),
        "considered": len(wanted),
        "failed": failed,
        "shards": len(shards),
    }
    return rows, meta, stats
