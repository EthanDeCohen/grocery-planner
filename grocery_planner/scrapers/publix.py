"""Publix shelf prices, via Parse.bot (GFP-270).

The second chain GFP-197 filed as unreachable -- "price endpoint returns 403;
store locator returns no results to an automated client". Both are true of a
direct client and neither is true through :mod:`parsebot`.

Publix is a SECOND source for a store that already has a Flipp banner, so it
carries its own ``SCRAPER_KEY`` and ``SOURCE``. That is not stylistic: the
registry is last-write-wins and ``SCRAPERS.update(flipp_banners.MODULES)``
runs last, so a module whose key collides with a banner is silently shadowed
and unreachable from the CLI. It happened to ``sprouts`` and cost a live
debugging session.

MEASURED 2026-08-12 (27401, "boneless skinless chicken breast")
---------------------------------------------------------------
``search_products_by_zip`` resolved 27401 to **store 1658** by itself and
returned 64 results. The first two:

    Publix Boneless Skinless Chicken Breast, 97% Fat Free, <4 lb    $5.39/lb
    Publix Boneless Skinless Chicken Breast, 97% Fat Free, 4 lb+    $4.99/lb

WHY THIS SOURCE IS WHERE THE DOUBLE DAGGER COMES FROM
------------------------------------------------------
Read those prices again: ``"$5.39/lb"``. Not $5.39. There is no package total
anywhere in the row -- Publix quotes fresh meat as a **rate**, and a rate is
not a price. It cannot be summed into a basket, compared against a weekly
budget, or paid at a till. It is one multiplication short, and the missing
factor is what the shopper happens to pick up.

Storing it as if it were a package price is the GFP-98 trap exactly: that bug
understated whole pork loin about 7x and, because a wrong density can only ever
make something look CHEAPER, such rows do not scatter through a ranking -- they
colonise the top of it, which is the only part the optimiser reads.

So every rate row here is stored as :data:`weight_basis.RATE` and displays a
**‡** wherever it is listed, with the footnote "Price is per pound, not per
package". Two things make that honest rather than decorative:

1. The price is stored **with its quantity**: ``$4.99`` against an item name
   ending ``, 1 lb``. Price and size then describe the same thing, so
   ``savings`` computes cost-per-gram correctly with no branch -- the kroger.py
   convention.
2. The marker is attached to the DATA, not to a Publix-shaped rule in the UI.
   Any source that starts quoting rates inherits it for free.

Partial price coverage is normal here: of the first six results only two
carried a figure at all (the fresh-meat rows); the branded packaged items came
back priceless. Those are dropped and counted in ``stats``.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

from .. import importers, savings, weight_basis
from . import base, parsebot
from .walmart import is_rate, priced_pound_name   # one rate grammar, not two

#: The CLI/registry name. `publix` is already the Flipp weekly-ad banner.
SCRAPER_KEY = "publix-catalog"

#: NOT SCHEDULABLE, on cost (GFP-287, decided by the user 2026-08-14).
#:
#: This source works. It is simply the worst value in the project by a wide
#: margin, and it was the only reason the Parse.bot bill did not fit the free
#: tier. Measured from the vendor's own usage export:
#:
#:     store     credits/scrape   usable rows   credits per usable row
#:     Walmart         30             217              0.14
#:     Publix         100              11              9.10     <- 65x worse
#:
#: `search_products_by_zip` costs 10 credits a call -- more than three times
#: Walmart's -- and most of the Publix catalogue comes back priceless anyway,
#: so the ceiling is low even if the budget were not.
#:
#:     Free tier 200/month:  Walmart + Publix ~563  far over
#:                           Walmart only     ~130  fits, 70 spare
#:
#: So the $30/month Hobby tier was being paid for eleven rows.
#:
#: The scraper is KEPT, not deleted: it is written, tested, and costs nothing
#: while unscheduled. `gplan scrape publix-catalog` still runs it deliberately.
#: What is refused is the recurring cadence that quietly spends money.
#:
#: GFP-288 asked whether Publix's own endpoints could retire this module and its
#: bill. Investigated 2026-08-14: NO, and the reason is worth knowing before
#: anyone tries again.
#:
#: Two pieces genuinely work from a plain client, unauthenticated. Publix's
#: `services.publix.com/search/productdata/productitems?Id=<uuid>&StoreNbr=`
#: returns real prices -- package prices, not the per-pound rates that force the
#: double dagger above -- and `sitemap_products1..7.xml` publishes ~70k products
#: for crawlers, slug and `baseProductId` in each URL.
#:
#: They cannot be joined, and the reason is not the one it first looks like.
#: `Id` is NOT a product identifier -- it identifies a curated CAROUSEL. One
#: call returns five products sharing that single `id`, each with its own
#: `itemCode` and `baseProductId`. There is no product uuid to go looking for.
#:
#: So the reachable surface is whatever Publix is merchandising, not the
#: catalogue: the seed carousel returns a 50-piece wing platter, a charcuterie
#: board and a shrimp platter, $24-70 catering items. Feeding a cost-per-gram
#: optimiser on promoted items is worse than feeding it nothing -- a biased
#: sample does not scatter through a ranking, it colonises the top of it, which
#: is the only part that gets read. The sitemap has the real catalogue and no
#: price; this endpoint has prices and no catalogue.
#:
#: So this module stays the only way to read Publix at all, and the case for
#: unscheduling it is stronger than when it was written: the free replacement
#: does not exist. Do not delete it.
SCHEDULABLE = False

STORE_KEY = "publix"
SOURCE = "parsebot"
MERCHANT = "Publix"
DEAL_TYPE = "Shelf Price"
DEFAULT_POSTAL_CODE = "27401"
PRODUCT_IDENTIFIER_NS = "publix.item_id"

SCRAPER_ID = "02839e68-c3c3-41db-ab74-52a3eb51ce5f"
ENDPOINTS = {
    "search": parsebot.Endpoint(SCRAPER_ID, "search_products_by_zip"),
    "details": parsebot.Endpoint(SCRAPER_ID, "get_product_details"),
    "stores": parsebot.Endpoint(SCRAPER_ID, "find_stores"),
}

#: Bounded, metered, and reported -- same reasoning as walmart.py.
DEFAULT_QUERIES: tuple[str, ...] = (
    "boneless skinless chicken breast",
    "chicken thighs",
    "ground beef",
    "pork chops",
    "salmon fillet",
    "canned tuna",
    "greek yogurt",
    "eggs",
    "block cheese",
    "peanut butter",
)

#: A bare package price: "$4.29" with no denominator.
_PACKAGE_PRICE = re.compile(r"^\s*\$\s*([0-9]+(?:\.[0-9]+)?)\s*$")


def readiness() -> tuple[bool, str]:
    return parsebot.readiness()


def serves(postal_code: str) -> bool | None:
    """Unknown, not guessed. ``find_stores`` could answer this properly and
    should (GFP-257), but it costs a metered call per check, so the permissive
    UNKNOWN is used until that trade is decided deliberately."""
    return None


def parse_price(raw: object) -> tuple[float | None, bool]:
    """``(dollars, is_rate)`` from Publix's price field.

    Publix sends a STRING -- ``"$5.39/lb"`` or ``"$4.29"`` -- or null. The
    boolean is what decides the double dagger, so it is returned rather than
    inferred later from the number, which cannot carry that fact.
    """
    if raw is None:
        return None, False
    if isinstance(raw, (int, float)):
        return (float(raw), False) if raw > 0 else (None, False)
    text = str(raw).strip()
    rate = is_rate(text)
    if rate is not None:
        return rate, True
    match = _PACKAGE_PRICE.match(text)
    if match:
        return float(match.group(1)), False
    return None, False


def to_row(product: dict[str, Any], postal_code: str, moment: datetime) -> dict[str, Any] | None:
    name = (product.get("name") or "").strip()
    if not name:
        return None
    price, rate = parse_price(product.get("price"))
    if price is None:
        return None

    if rate:
        sold_by: str | None = weight_basis.SOLD_BY_WEIGHT
        basis: str | None = weight_basis.RATE
        # The priced quantity is one pound, and the name's own "4 Lbs. or More"
        # is a package qualifier that must not be read as it. See
        # priced_pound_name for the 4x understatement this prevents.
        item_name = priced_pound_name(name)
    else:
        sold_by, basis = "UNIT", None
        size_text = (product.get("package_size") or "").strip() or None
        item_name = name
        if size_text and not savings.parse_size(item_name):
            item_name = f"{name}, {size_text}"

    row = {column: None for column in importers.DEAL_COLUMNS}
    row.update(
        item_name=item_name,
        sub_category=base.infer_sub_category(item_name, product.get("brand") or "", True),
        deal_type=DEAL_TYPE,
        deal_description=str(product.get("price") or "") or None,
        dollar_price=price,
        sale_price=price,
        sold_by=sold_by,
        weight_basis=basis,
        price_per_unit=price if rate else None,
        price_per_unit_uom="lb" if rate else None,
        source_url=product.get("product_url") or product.get("url"),
        product_identifier=str(product.get("item_id") or "") or None,
        product_identifier_ns=PRODUCT_IDENTIFIER_NS,
        valid_from=moment.date().isoformat(),
        valid_to=moment.date().isoformat(),
    )
    return row


def scrape(
    postal_code: str | None = None,
    limit: int | None = None,
    conn: sqlite3.Connection | None = None,
    client: parsebot.ParseBotClient | None = None,
    now: datetime | None = None,
    queries: Iterable[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Scrape Publix shelf prices for ``postal_code``. ``(rows, meta, stats)``."""
    zip_code = postal_code or DEFAULT_POSTAL_CODE
    moment = now or datetime.now(timezone.utc)
    asked = list(queries if queries is not None else DEFAULT_QUERIES)
    if limit is not None:
        asked = asked[:limit]

    owned = client is None
    active = client or parsebot.ParseBotClient()

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    failed: list[str] = []
    credits_exhausted: str | None = None
    priceless = 0
    store_number: str | None = None
    try:
        for query in asked:
            try:
                payload = active.call(ENDPOINTS["search"], keyword=query, zip_code=zip_code)
            except parsebot.OutOfCreditsError as exc:
                # Every remaining query would fail the same way, so stop asking
                # -- but KEEP what is already in hand. Nothing is written until
                # this function returns, so raising here would discard a run
                # that is merely short, which is the asymmetry retry.py exists
                # to complain about.
                credits_exhausted = str(exc)
                break
            except parsebot.ParseBotError as exc:
                failed.append(f"{query}: {type(exc).__name__}")
                continue
            store_number = store_number or (payload.get("store_number") and
                                            str(payload["store_number"]))
            for product in payload.get("products") or []:
                row = to_row(product, zip_code, moment)
                if row is None:
                    priceless += 1
                    continue
                key = row["product_identifier"] or row["item_name"]
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)

        rated = sum(1 for r in rows if r["weight_basis"] == weight_basis.RATE)
        meta = {
            "name": f"{MERCHANT} shelf prices ({zip_code})",
            "id": store_number or STORE_KEY,
            "store_name": MERCHANT,
            "store_number": store_number,
        }
        stats = {
            "weekly_ad": len(rows),
            "digital_coupons": 0,
            "no_price": 0,
            "bogo": 0,
            "expired_items": 0,
            "total": len(rows),
            "flyer_id": store_number or STORE_KEY,
            "flyer_name": meta["name"],
            "flyer_status": "active",
            "valid_from": moment.date().isoformat(),
            "valid_to": moment.date().isoformat(),
            "priced": len(rows),
            "with_protein": 0,
            "sold_by_weight": sum(
                1 for r in rows if r["sold_by"] == weight_basis.SOLD_BY_WEIGHT),
            # Which physical store these prices are, read back from the source
            # rather than assumed -- GFP-77's lesson.
            "store_number": store_number,
            "queries": len(asked),
            # A run cut short by billing, not by the retailer. Loud, because
            # the remedy is a decision rather than a retry.
            "credits_exhausted": credits_exhausted,
            "queries_failed": len(failed),
            "query_errors": "; ".join(failed[:3]) or None,
            # Publix returns many branded rows with no price at all. Counted,
            # because "Publix has 20 products" would otherwise be read as an
            # assortment fact rather than a coverage one.
            "dropped_priceless": priceless,
            "priced_by_rate": rated,
            **active.pace.stats(),
            **active.stats(),
        }
        return rows, meta, stats
    finally:
        if owned:
            active.close()
