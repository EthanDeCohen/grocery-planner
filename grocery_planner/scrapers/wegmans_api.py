# ######### decohen-partners ##########
# Protein Ledger
"""Wegmans' own JSON API: the best source found outside Kroger (GFP-165).

Discovered 2026-08-10 by pointing the renderer container at a product page and
reading which calls it made. **The browser's value was reconnaissance, not
production** -- everything below runs on plain ``httpx``, because the API the
page uses answers directly.

What it gives, which nothing else does
--------------------------------------
``GET /api/products/{store_number}/{sku}`` returns, verified live:

============================  =========================================
``price_inStore.amount``      a REAL PER-STORE price
``price_delivery.amount``     and a separate delivery price
``price_inStore.unitPrice``   "$9.99/lb." -- the retailer's own per-unit
``packSize``                  "1 lb.", "12 ounce" -- machine-readable size
``upc``                       a UPC, for FoodData Central (GFP-24)
``nutrition``                 protein grams AND the serving size in grams
============================  =========================================

Per-store pricing is the headline. PRISM serves a default-store figure and
Albertsons does too (GFP-246, GFP-260); Wegmans varies genuinely -- the same SKU
was $1.69 at store 140 (Chapel Hill NC) and $1.79 at store 48 (King of Prussia
PA). That makes this the only non-Kroger source that can price a client's actual
shop.

And the protein arrives as ``22g per 112 grams``, which converts to per-100g
directly. No name matching, no USDA lookup, no confidence score -- the figure is
the manufacturer's own, for this exact product. On the sample measured it was
present on every item, against Kroger's 82%.

``GET /api/stores`` returns 114 stores with ``storeNumber``, ``zip``,
``latitude`` and ``longitude``. That single call answers GFP-257's availability
question exactly (no declared prefixes, no guessing) and hands GFP-210 real
store coordinates for proximity.

Access
------
``robots.txt`` is ``User-Agent: *`` / ``Allow: /`` with **no Disallow lines at
all** -- the most permissive of any candidate probed. No credential is required
and no quota is documented, so unlike Kroger this draws on no budget. Be polite
anyway: a delay between fetches, and only the protein departments.

Why SKUs ship as data
---------------------
The API prices a SKU but does not enumerate them; the site's own product listing
is rendered client-side (its search is Algolia). So SKU discovery is a browser
job, run occasionally by ``server/renderer``, and its output ships as package
data the way the USDA snapshot does for GFP-24. Refreshing the list is an
operation, not a release.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import resources
from typing import Any, Iterable

import httpx

from .. import db, matching
from . import foodfacts, base

STORE_KEY = "wegmans"
SCRAPER_KEY = "wegmans-api"
#: A SECOND source for a store that already has one -- the Flipp weekly ad is
#: `wegmans`. Distinct SOURCE keeps run_scrape's (store, source, postal_code)
#: replace scope from letting either evict the other.
SOURCE = "wegmans-api"
MERCHANT = "Wegmans (API)"
DEFAULT_POSTAL_CODE = "27514"          # Chapel Hill NC, store 140

API = "https://www.wegmans.com/api"
#: Seconds between product fetches. No published quota, which is a reason for
#: restraint rather than against it.
REQUEST_DELAY = 0.4
#: Cap one run. The shipped SKU list is protein-only and already bounded, but a
#: refreshed list should never silently become an unbounded crawl.
DEFAULT_MAX_PRODUCTS = 600

#: Package data: SKUs discovered from the protein departments.
SKU_RESOURCE = "wegmans_skus.json"


@dataclass(frozen=True)
class FoodFact:
    """The retailer's own protein figure for one exact product.

    Stored as a DENSITY -- grams per 100g -- rather than per serving, because a
    per-serving figure is meaningless without the serving, and the whole point
    of this source is that it states both.
    """

    sku: str
    item_name: str
    category: str
    protein_per_100g: float


@dataclass(frozen=True)
class Store:
    number: str
    name: str
    city: str
    state: str
    zip: str
    latitude: float | None
    longitude: float | None


def _client(timeout: float = 30.0) -> httpx.Client:
    return httpx.Client(timeout=timeout, follow_redirects=True,
                        headers={"User-Agent": base.user_agent()})


def stores(client: httpx.Client | None = None) -> list[Store]:
    """Every Wegmans store, with its ZIP and coordinates."""
    own = client or _client()
    try:
        payload = own.get(f"{API}/stores").json()
    finally:
        if client is None:
            own.close()
    out: list[Store] = []
    for row in payload if isinstance(payload, list) else []:
        number = row.get("storeNumber")
        if number is None:
            continue
        out.append(Store(
            number=str(number),
            name=str(row.get("name") or ""),
            city=str(row.get("city") or ""),
            state=str(row.get("stateAbbreviation") or ""),
            # ZIPs arrive as "18976-2492" sometimes; keep the five-digit head.
            zip=str(row.get("zip") or row.get("iwsZip") or "")[:5],
            latitude=row.get("latitude"),
            longitude=row.get("longitude"),
        ))
    return out


def store_for(postal_code: str, client: httpx.Client | None = None) -> Store | None:
    """The store serving a ZIP: exact match first, then the same ZIP3.

    ZIP3 rather than a distance calculation on purpose. Real coordinates are
    available and GFP-166 will use them, but availability and proximity are
    different questions (GFP-257) and this one only needs "is there a store
    around here". A haversine here would quietly become the app's second,
    divergent distance implementation.
    """
    found = stores(client)
    exact = [s for s in found if s.zip == postal_code]
    if exact:
        return exact[0]
    near = [s for s in found if s.zip[:3] == postal_code[:3]]
    return near[0] if near else None


def serves(postal_code: str) -> bool | None:
    """GFP-257: asked, and answered exactly by the store list."""
    try:
        return store_for(postal_code) is not None
    except (httpx.HTTPError, ValueError):
        return None                     # unknown, never "does not serve"


def load_skus() -> list[str]:
    """The discovered protein SKUs shipped as package data."""
    try:
        text = (resources.files("grocery_planner.data")
                .joinpath(SKU_RESOURCE).read_text(encoding="utf-8"))
    except (FileNotFoundError, ModuleNotFoundError):
        return []
    try:
        return list(json.loads(text).get("skus") or [])
    except (ValueError, AttributeError):
        return []


def protein_per_100g(product: dict[str, Any]) -> float | None:
    """Grams of protein per 100g, from the retailer's own nutrition panel.

    ``None`` unless BOTH the protein figure and a serving size in grams are
    present. A protein-per-serving with an unknown serving is not a
    per-100g figure, and guessing the serving would be the dangerous direction
    -- it makes an item look cheaper per gram than it is (savings.py rule 4).
    """
    nutrition = product.get("nutrition") or {}
    serving = nutrition.get("serving") or {}
    if str(serving.get("servingSizeUom") or "").lower() not in {"grams", "g", "gram"}:
        return None
    try:
        grams = float(serving.get("servingSize"))
    except (TypeError, ValueError):
        return None
    if grams <= 0:
        return None
    for block in nutrition.get("nutritions") or []:
        if not isinstance(block, dict):
            continue
        for items in block.values():
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict):
                    continue
                if str(item.get("name") or "").strip().lower() != "protein":
                    continue
                try:
                    quantity = float(item.get("quantity"))
                except (TypeError, ValueError):
                    return None
                return quantity / grams * 100.0
    return None


#: Where this figure came from, and the banner it is sold under. Passed
#: separately because they are not always equal -- see foodfacts.
FOOD_SOURCE = "wegmans"
MATCH_METHOD = "wegmans_api_direct"


def upsert_food_fact(conn: sqlite3.Connection, fact: FoodFact) -> None:
    """Record this retailer's own protein figure (GFP-302: one shared write).

    Wegmans' feed carries no separate product title, so name and item_name are the same string here -- the fallback the other sources take conditionally, taken unconditionally. See foodfacts' asymmetry note.
    """
    foodfacts.upsert_food_fact(
        conn, FOOD_SOURCE, STORE_KEY, MATCH_METHOD,
        foodfacts.FoodFact(
            source_ref=fact.sku,
            name=fact.item_name,
            category=fact.category,
            protein_per_100g=fact.protein_per_100g,
            item_name=fact.item_name,
        ),
    )


def _display_name(product: dict[str, Any]) -> str:
    """A name a nutritionist can read, with the size in it.

    The size goes IN the name because ``savings.parse_size`` reads sizes from
    item names -- the same reason ``prism.py`` appends its slug size. Without
    it this feed's machine-readable ``packSize`` never reaches the optimiser.
    """
    #: productName is the full retail name ("Wegmans Choice Top Round Cutlets,
    #: 4 Slices"). `name` and `consumerBrandName` are the BRAND -- reading
    #: those produced rows called "Wegmans, 1 lb", which identifies nothing and
    #: matches nothing.
    name = str(product.get("productName") or "").strip()
    if not name:
        brand = str(product.get("consumerBrandName") or "").strip()
        desc = str(product.get("webProductDescription") or "").strip()
        name = " ".join(p for p in (brand, desc) if p).strip()
    pack = str(product.get("packSize") or "").strip().rstrip(".")
    return f"{name}, {pack}" if pack and pack.lower() not in name.lower() else name


def to_row(
    product: dict[str, Any], store: Store,
) -> tuple[dict[str, Any] | None, FoodFact | None]:
    """One API product into a ``deals`` row and the protein fact it states.

    ``(None, None)`` when there is no usable price. ``(row, None)`` when there
    is a price but no usable nutrition panel -- absent stays absent, and a row
    without protein is still a real shelf price worth having.
    """
    from .. import importers

    price_block = product.get("price_inStore") or product.get("price_delivery") or {}
    try:
        price = float(price_block.get("amount"))
    except (TypeError, ValueError):
        return None, None
    if price <= 0:
        return None, None
    name = _display_name(product)
    if not name:
        return None, None

    unit_price = str(price_block.get("unitPrice") or "")
    uom = unit_price.rsplit("/", 1)[-1].strip(". ") if "/" in unit_price else None
    protein = protein_per_100g(product)
    upc = (product.get("upc") or [None])[0]

    row = {c: None for c in importers.DEAL_COLUMNS}
    row.update(
        item_name=name,
        sub_category=base.infer_sub_category(
            name, str(product.get("consumerBrandName") or ""), True),
        deal_type="Shelf price",
        dollar_price=price,
        sale_price=price,
        sold_by="WEIGHT" if product.get("isSoldByWeight") else "UNIT",
        price_per_unit_uom=uom or None,
        product_identifier=str(product.get("skuId") or "") or None,
        product_identifier_ns="wegmans.sku",
        source_url=f"https://www.wegmans.com/shop/product/{product.get('skuId')}",
        notes=(f"Store {store.number} ({store.city}, {store.state}). "
               + (f"UPC {upc}. " if upc else "")
               + (f"Protein {protein:.1f}g/100g." if protein is not None else
                  "No usable nutrition panel.")),
    )
    fact = None
    if protein is not None and product.get("skuId"):
        crumbs = product.get("breadcrumbs") or []
        category = next(
            (str(b.get("text")) for b in reversed(crumbs[:-1])
             if isinstance(b, dict) and b.get("text")), "Meat")
        fact = FoodFact(sku=str(product["skuId"]), item_name=name,
                        category=category, protein_per_100g=protein)
    return row, fact


def scrape(
    postal_code: str | None = None,
    include_coupons: bool = True,
    max_products: int = DEFAULT_MAX_PRODUCTS,
    delay: float = REQUEST_DELAY,
    skus: Iterable[str] | None = None,
    conn: sqlite3.Connection | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Fetch the protein catalogue priced at the store serving ``postal_code``.

    Also records each product's stated protein density (see
    :func:`upsert_food_fact`), which is what lets the optimiser price these
    rows without matching a name to a USDA food.
    """
    zip_code = postal_code or DEFAULT_POSTAL_CODE
    own = _client()
    try:
        store = store_for(zip_code, own)
        if store is None:
            raise RuntimeError(
                f"No Wegmans store found for postal code {zip_code}. "
                "Wegmans operates in NY, PA, VA, NJ, MD, MA, NC, DE, DC and CT.")
        wanted = list(skus if skus is not None else load_skus())[:max_products or None]
        rows: list[dict[str, Any]] = []
        facts: dict[str, FoodFact] = {}
        failed = no_price = 0
        for i, sku in enumerate(wanted):
            if delay and i:
                time.sleep(delay)
            try:
                response = own.get(f"{API}/products/{store.number}/{sku}")
                if response.status_code != 200:
                    failed += 1
                    continue
                product = response.json()
            except (httpx.HTTPError, ValueError):
                failed += 1
                continue
            row, fact = to_row(product, store)
            if row is None:
                no_price += 1
                continue
            rows.append(row)
            if fact is not None:
                facts[fact.sku] = fact
    finally:
        own.close()

    # Written after the fetch loop, so a mid-run failure leaves no partial
    # facts; and through the caller's connection when there is one, so the
    # facts land in the same transaction as the deals they describe.
    target = conn or db.connect()
    try:
        for fact in facts.values():
            upsert_food_fact(target, fact)
        if conn is None:
            target.commit()
    finally:
        if conn is None:
            target.close()

    meta = {"id": store.number, "store": store.name,
            "city": store.city, "state": store.state}
    stats = {"total": len(rows), "considered": len(wanted),
             "failed": failed, "no_price": no_price,
             "protein_facts": len(facts),
             "store_number": store.number}
    return rows, meta, stats
