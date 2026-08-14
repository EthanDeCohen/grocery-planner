# ######### decohen-partners ##########
# Protein Ledger
"""Walmart shelf prices, via Parse.bot (GFP-270).

GFP-197 filed Walmart as technically unreachable: product URLs redirect to a
challenge page. That verdict was about *our* client, not about the data, and
:mod:`grocery_planner.scrapers.parsebot` is a client that gets through. Read
that module first -- it owns the credential, the pacing and the caveats about
depending on a third party for a whole store.

WHAT WALMART GIVES, MEASURED 2026-08-12 (27401, "boneless skinless chicken breast")
-----------------------------------------------------------------------------------
53 products on page 1 of 73 results. Every row carried a name, a price and a
``package_size``; most carried a retailer-stated ``price_per_unit`` ("$2.23/lb").

**That per-unit figure is the reason this source is worth having.** Flipp gives
a size on 0-5% of rows; Walmart gives one on nearly all of them, plus the
denomination in the retailer's own words rather than inferred from prose.

THE ZIP IS REAL, AND WAS CHECKED RATHER THAN ASSUMED
-----------------------------------------------------
``search_grocery_products`` takes a ``zip_code`` and echoes it back, which
proves nothing on its own -- Trader Joe's echoes a store code while serving a
national price. So the same query was run at 27401 and at 94110: 43 products
appeared in both, and **3 were priced differently** (Great Value chicken $9.47
vs $9.97). Most of Walmart's catalogue is nationally priced with regional
exceptions, which is how Walmart actually prices, so a small diff is the
correct result -- a zero diff would have meant the ZIP was decoration.

WHAT IT DOES NOT GIVE: PROTEIN
-------------------------------
Checked on ``get_product_details``: the only occurrence of "protein" in the
payload is marketing prose in the description ("each serving offers 25 grams of
lean protein"), with no serving size, so no density is computable from it. The
real panel exists solely as an IMAGE (``Nutrition facts label image`` -> a PNG).
There is no UPC and no GTIN.

So this module writes **no** ``foods``/``food_nutrients`` rows -- it is a price
and size source, and its protein comes from the same name-matching every Flipp
store uses. That places its rows in the low-confidence tier, which is exactly
why `bill.py` needs a confidence floor before this source is switched on for
customers.

THE DENOMINATION, AND WHY WALMART IS NOT MARKED ‡
--------------------------------------------------
Walmart quotes a real package price *and* a rate: ``$12.41`` with
``$4.58/lb`` on a "1.50-4.30 lb Tray". 12.41 / 4.58 = 2.71 lb, inside that
range -- so the headline figure is a genuine basket price for an assumed
weight, not a rate. That is the existing PREPACKAGED case (GFP-152's ``**``),
and ``specifications`` confirms it in the retailer's own words:
``Sales unit: Weight``, ``Net content statement: Random Weight``.

Publix is the opposite and does get the double dagger -- see ``publix.py``.
The distinction is the whole point of :data:`weight_basis.RATE`: one is a
price that may shift a little, the other is not a price yet.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

from .. import importers, savings, weight_basis
from . import base, parsebot

SCRAPER_KEY = "walmart"
STORE_KEY = "walmart"
#: Distinct from "scrape" so this can never overwrite, or be overwritten by, a
#: Flipp Walmart banner. One does exist for 27401 as of 2026-08-12 -- 318 items,
#: **zero priced**, general merchandise -- so it is not registered, but the
#: source value is kept distinct anyway because that is what stops the two
#: colliding if it ever is.
SOURCE = "parsebot"
MERCHANT = "Walmart"
DEAL_TYPE = "Shelf Price"
DEFAULT_POSTAL_CODE = "27401"
PRODUCT_IDENTIFIER_NS = "walmart.item_id"

#: The generated API, pinned. See parsebot.verify_pinned_ids for why this is a
#: canary rather than a constant nobody checks.
SCRAPER_ID = "e7d6b110-c61f-466e-99f3-a85efdcb2820"
ENDPOINTS = {
    "grocery": parsebot.Endpoint(SCRAPER_ID, "search_grocery_products"),
    "details": parsebot.Endpoint(SCRAPER_ID, "get_product_details"),
}

#: The bounded work list. Parse.bot is METERED, so this never walks a
#: catalogue -- it asks a fixed set of protein questions. Reported in
#: ``stats['queries']`` so the coverage this costs is visible rather than
#: silent; a reader can see that "Walmart has 40 products" means "we asked 8
#: questions", not "Walmart sells 40 things".
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

#: "$4.58/lb" -- a rate, in the retailer's own words.
_RATE = re.compile(r"\$\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*(lb|pound|oz|ounce)\b", re.I)
#: "1.50-4.30 lb", "2.75 - 7.0 lb" -- a package whose weight is a range.
_WEIGHT_RANGE = re.compile(
    r"[0-9]+(?:\.[0-9]+)?\s*-\s*[0-9]+(?:\.[0-9]+)?\s*(lb|lbs|pound|pounds|oz)\b", re.I
)


def readiness() -> tuple[bool, str]:
    """Walmart is ready exactly when the vendor is."""
    return parsebot.readiness()


def serves(postal_code: str) -> bool | None:
    """Unknown rather than True. Walmart is nearly everywhere, but "nearly" is
    not a fact about THIS postal code, and GFP-257's whole point is that a
    hand-written footprint is how Food Lion came to claim all of Kentucky."""
    return None


def is_rate(price_text: object) -> float | None:
    """The dollars-per-pound figure if ``price_text`` is a rate, else ``None``.

    An oz rate is converted to a pound rate so every RATE row in the database
    is denominated the same way; mixing $/lb and $/oz silently would make the
    cheapest-looking rows the ones quoted in the smaller unit.
    """
    match = _RATE.search(str(price_text or ""))
    if not match:
        return None
    amount = float(match.group(1))
    unit = match.group(2).lower()
    return amount * 16.0 if unit.startswith("o") else amount


#: Weight phrases that describe the PACKAGE, not the priced quantity:
#: "Less Than 4 Lbs.", "4 Lbs. or More Pkg", "2.75 - 7.0 lb", a bare "5 lb".
_PACKAGE_WEIGHT_PHRASE = re.compile(
    r"(?:less\s+than\s+|under\s+|up\s+to\s+)?"
    r"\d+(?:\.\d+)?\s*(?:-\s*\d+(?:\.\d+)?\s*)?"
    r"(?:lbs?|pounds?|oz|ounces?)\b\.?"
    r"(?:\s*or\s+(?:more|less))?(?:\s*pkg\.?)?",
    re.I,
)


def priced_pound_name(name: str) -> str:
    """``name`` rewritten so its only size is the pound the price refers to.

    THE BUG THIS EXISTS FOR, found live on 2026-08-12 and worth stating because
    it is invisible in the output:

    ``savings.parse_size`` takes the FIRST size in a string. Publix names
    qualify the package -- "Publix Chicken Thighs 4 Lbs. or More" -- so simply
    appending ", 1 lb" left ``4.0 lbs`` as the parsed size while the price was
    $2.39 **per pound**. The engine then priced four pounds of chicken at
    $2.39: a 4x understatement, on fresh meat, which is the highest-protein and
    therefore highest-ranking category. Cheapest-first ranking means an
    understatement does not sit harmlessly in the middle of the list -- it
    goes straight to the top.

    So on a rate row every weight phrase in the name is stripped, because on a
    rate row every weight phrase IS a package qualifier: the priced quantity is
    definitionally one pound and cannot be anything else.
    """
    stripped = _PACKAGE_WEIGHT_PHRASE.sub(" ", name)
    # Tidy the punctuation the removal leaves behind ("Thighs , USDA" ->
    # "Thighs, USDA") so the name still reads like a product.
    stripped = re.sub(r"\s+,", ",", stripped)
    stripped = re.sub(r",\s*,+", ",", stripped)
    stripped = re.sub(r"\s{2,}", " ", stripped).strip(" ,.")
    return f"{stripped}, 1 lb"


def package_price(product: dict[str, Any]) -> float | None:
    """The price of the thing, when the row states one.

    Walmart's ``price`` is numeric and IS a package total (verified: 12.41 at
    $4.58/lb on a 1.50-4.30 lb tray = 2.71 lb, inside the range). A string
    price would be a rate, which this refuses rather than coercing -- see
    ``publix.py`` for the source where that is the normal case.
    """
    value = product.get("price")
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    return None


def denomination(product: dict[str, Any], item_name: str) -> tuple[str | None, str | None]:
    """``(sold_by, weight_basis)`` for one row.

    Three outcomes, and the middle one is the one that matters:

    * a package price on a fixed-size package -> no denomination question
    * a package price on a RANGE-weighted package -> ``WEIGHT`` + PREPACKAGED,
      which is GFP-152's ``**``: real price, weight varies a little
    * only a rate -> ``WEIGHT`` + RATE, GFP-270's ``‡``: not a price yet
    """
    size_text = f"{product.get('package_size') or ''} {item_name}"
    has_rate = is_rate(product.get("price_per_unit")) is not None
    if package_price(product) is None and has_rate:
        return weight_basis.SOLD_BY_WEIGHT, weight_basis.RATE
    if _WEIGHT_RANGE.search(size_text):
        # Walmart states this itself in `specifications` as
        # "Net content statement: Random Weight". The range in the name is the
        # same fact, available without a second metered call per product.
        return weight_basis.SOLD_BY_WEIGHT, weight_basis.PREPACKAGED
    if has_rate and re.search(r"\bper\s*lb\b|/\s*lb\b", size_text, re.I):
        return weight_basis.SOLD_BY_WEIGHT, weight_basis.UNKNOWN
    return "UNIT", None


def priced_quantity(product: dict[str, Any], basis: str | None) -> str | None:
    """The size the PRICE refers to, ready to fold into ``item_name``.

    The kroger.py convention, and the reason this module needs no branch in
    ``savings``: price and size must describe the same quantity. For a RATE row
    that quantity is one pound -- never the package, which is exactly the GFP-98
    mistake that understated whole pork loin about 7x.

    Returns ``"1 lb"`` for rates, the stated package size otherwise, and
    ``None`` when there is nothing trustworthy to state.
    """
    if basis == weight_basis.RATE:
        return "1 lb"
    size = (product.get("package_size") or "").strip()
    if not size:
        return None
    # A range is not a size the optimiser can use -- "1.50-4.30 lb" parses to
    # nothing sensible -- so give it the midpoint's unit-bearing half only if
    # the row also carries a rate to anchor it. Otherwise say nothing.
    if _WEIGHT_RANGE.search(size):
        return None
    return size


def to_row(product: dict[str, Any], postal_code: str, moment: datetime) -> dict[str, Any] | None:
    """One Walmart product -> one ``deals`` row, or ``None`` if unusable."""
    name = (product.get("name") or "").strip()
    if not name:
        return None

    basis_pair = denomination(product, name)
    sold_by, basis = basis_pair
    rate = is_rate(product.get("price_per_unit")) or is_rate(product.get("price"))
    price = package_price(product)
    if price is None and basis == weight_basis.RATE:
        price = rate
    if price is None:
        return None

    if basis == weight_basis.RATE:
        # Never trust a size already in the name here -- see priced_pound_name.
        item_name = priced_pound_name(name)
    else:
        size_text = priced_quantity(product, basis)
        item_name = name
        if size_text and not savings.parse_size(item_name):
            item_name = f"{name}, {size_text}"

    row = {column: None for column in importers.DEAL_COLUMNS}
    row.update(
        item_name=item_name,
        sub_category=base.infer_sub_category(item_name, product.get("brand") or "", True),
        deal_type=DEAL_TYPE,
        deal_description=(product.get("price_per_unit") or None),
        dollar_price=price,
        sale_price=price,
        sold_by=sold_by,
        weight_basis=basis,
        price_per_unit=rate,
        price_per_unit_uom="lb" if rate is not None else None,
        source_url=product.get("url") or product.get("product_url"),
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
    """Scrape Walmart shelf prices for ``postal_code``. ``(rows, meta, stats)``.

    ``limit`` bounds the number of QUERIES, not products -- the meter is per
    call, so that is the axis that costs money. ``conn`` is accepted for
    interface parity and unused: this source writes no food facts, because it
    publishes no machine-readable protein (see the module docstring).
    """
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
    unpriced = 0
    try:
        for query in asked:
            try:
                payload = active.call(ENDPOINTS["grocery"], query=query, zip_code=zip_code)
            except parsebot.OutOfCreditsError as exc:
                # Every remaining query would fail the same way, so stop asking
                # -- but KEEP what is already in hand. Nothing is written until
                # this function returns, so raising here would discard a run
                # that is merely short, which is the asymmetry retry.py exists
                # to complain about.
                credits_exhausted = str(exc)
                break
            except parsebot.ParseBotError as exc:
                # One dead query must not lose the other nine. The whole run is
                # held in memory until it returns (see service/ingest), so a
                # raise here costs everything already fetched.
                failed.append(f"{query}: {type(exc).__name__}")
                continue
            for product in payload.get("products") or []:
                row = to_row(product, zip_code, moment)
                if row is None:
                    unpriced += 1
                    continue
                key = row["product_identifier"] or row["item_name"]
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)

        by_weight = sum(1 for r in rows if r["sold_by"] == weight_basis.SOLD_BY_WEIGHT)
        rated = sum(1 for r in rows if r["weight_basis"] == weight_basis.RATE)
        meta = {
            "name": f"{MERCHANT} shelf prices ({zip_code})",
            "id": STORE_KEY,
            "store_name": MERCHANT,
        }
        stats = {
            "weekly_ad": len(rows),
            "digital_coupons": 0,
            "no_price": 0,
            "bogo": 0,
            "expired_items": 0,
            "total": len(rows),
            "flyer_id": STORE_KEY,
            "flyer_name": meta["name"],
            "flyer_status": "active",
            "valid_from": moment.date().isoformat(),
            "valid_to": moment.date().isoformat(),
            "priced": len(rows),
            "with_protein": 0,
            "sold_by_weight": by_weight,
            # No silent caps: this is a keyword sample, not a catalogue.
            "queries": len(asked),
            # A run cut short by billing, not by the retailer. Loud, because
            # the remedy is a decision rather than a retry.
            "credits_exhausted": credits_exhausted,
            "queries_failed": len(failed),
            "query_errors": "; ".join(failed[:3]) or None,
            "products_dropped_unpriced": unpriced,
            # How many rows carry the double dagger, so a run that is mostly
            # rates is legible as such rather than looking like cheap meat.
            "priced_by_rate": rated,
            **active.pace.stats(),
            **active.stats(),
        }
        return rows, meta, stats
    finally:
        if owned:
            active.close()
