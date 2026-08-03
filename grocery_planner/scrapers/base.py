"""Shared Flipp/Wishabi flyer client — the GFP-6 "shared scraper library".

This is the ONE place that owns the Flipp dependency. Flipp's endpoints are
undocumented and unauthenticated: there's no license/key, but expect ToS,
breakage, and rate-limit risk. If Flipp changes or blocks us, swap it here.

It provides everything a store scraper needs so a new store is *data*, not code:
  - a store registry (:class:`StoreConfig`),
  - the httpx Flipp client (weekly flyers **and** digital coupons),
  - shared classification (sub-category, unit) and best-effort price extraction,
  - row builders that emit DB-ready ``deals`` rows for weekly-ad items and coupons,
  - :func:`scrape_store`, which orchestrates a full store scrape.

Store modules (e.g. ``foodlion``) supply a :class:`StoreConfig` and call
:func:`scrape_store`; the CLI inserts the returned rows into SQLite.
"""
from __future__ import annotations

import random
import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

FLIPP_DATA_URL = "https://flyers-ng.flippback.com/api/flipp/data"
FLIPP_ITEMS_URL = "https://flyers-ng.flippback.com/api/flipp/flyers/{flyer_id}/flyer_items"
# GFP-87: the value now lives in config so a debugging session can change it
# without editing source. The NAME stays here because callers import it, and a
# function call at use-time means a config change takes effect without a
# restart.
def user_agent() -> str:
    from .. import config

    return config.user_agent()


#: Back-compat for anything importing the constant directly. Resolved at import
#: time, so prefer user_agent() in new code.
USER_AGENT = "grocery-planner/0.1 (+local personal use)"

# GFP-15 — where a deal's "View ad" link points.
#
# Flipp's *data* API (fetch_data / fetch_flyer_items, above) never returns a
# browsable URL -- every "url"-shaped field it hands back is an image asset
# (cutout_image_url, coupon_image_url, thumbnail_url, ...), never a page to
# click through to. flipp.com's public web app is a separate product from
# that data API, and its URL scheme is undocumented, so these were confirmed
# by hand (browsing flipp.com and reading the resulting URL/page title) as
# part of GFP-15, not guessed at:
#   - a weekly-ad item: https://flipp.com/en-us/flyer/{flyer_id}
#     ?postal_code={postal_code}&item_id={item_id}
#     -> loads that store's real weekly-ad flyer, scrolled to the item's page.
#   - a digital coupon: https://flipp.com/en-us/coupon/{coupon_id}
#     ?postal_code={postal_code}
#     -> loads that specific coupon's page (verified against a live Borden
#     string-cheese coupon; the rendered page matched the coupon's own
#     promotion_text exactly).
# Undocumented means Flipp is free to change this scheme without notice --
# same risk as the rest of this module (see the module docstring).
#
# IMPORTANT (honesty requirement, GFP-15): Flipp is a flyer AGGREGATOR, not a
# storefront. Both URL shapes above resolve to a weekly ad or a coupon page,
# never a cart or a "buy" flow. Any UI built on `source_url` (GFP-38/GFP-52)
# MUST label the control "View ad", never "Buy now" -- and must render plain
# text (not a dead link/button) when source_url is empty, since a link isn't
# always derivable (see the row builders below).
FLIPP_WEB_FLYER_URL = "https://flipp.com/en-us/flyer/{flyer_id}"
FLIPP_WEB_COUPON_URL = "https://flipp.com/en-us/coupon/{coupon_id}"

# GFP-111 -- the namespaces this module's rows stamp on `product_identifier_ns`.
#
# `product_identifier` alone is not self-describing: '1018281137' as a Flipp
# item id and '1018281137' as a Kroger productId denote two entirely unrelated
# products, so the value is meaningless without the vocabulary it belongs to
# and the two columns are always written together. Every consumer (the GFP-112
# grocery list's SKU column, the GFP-113 online-order file) must read the pair.
#
# Namespaced by SOURCE, not by store, which is what keeps this store-agnostic
# (GFP-32): one Flipp namespace covers Food Lion, Harris Teeter and any store
# added to the registry later, because what identifies the id's vocabulary is
# the feed it came from, never which shop the flyer happens to be for.
#
# A weekly-ad row and a coupon row are different vocabularies, not one: Flipp
# numbers its flyer items and its digital coupons independently, and a deal is
# always exactly one or the other (see the flipp_coupon_id note in
# db_script/migration/0012_GFP-15.ddl).
PRODUCT_IDENTIFIER_NS_ITEM = "flipp.item_id"
PRODUCT_IDENTIFIER_NS_COUPON = "flipp.coupon_id"


def product_identifier(value: Any, namespace: str) -> tuple[str | None, str | None]:
    """Return the ``(product_identifier, product_identifier_ns)`` pair to store.

    Both or neither, always. An absent id yields ``(None, None)`` rather than a
    namespace labelling nothing -- and never a stand-in derived from the item
    name or a row id, which would look exactly like a real SKU to the ordering
    API it would eventually be handed to (savings.py rule 1: absent stays
    absent, never a guess).

    The value is kept verbatim as TEXT rather than coerced to a number: Kroger
    productIds are zero-padded ('0020895500000') and an ASIN is alphanumeric
    ('B09439SKW3'), so neither survives being read as an integer.
    """
    if value is None:
        return None, None
    text = str(value).strip()
    if not text:
        return None, None
    return text, namespace


@dataclass(frozen=True)
class StoreConfig:
    """Per-store scraping config. ``key`` matches the stores registry / data folder."""

    key: str
    merchant_name: str   # exactly as Flipp labels the merchant
    loyalty_name: str    # e.g. "MVP", "VIC" — recorded in notes
    default_postal_code: str


# Registry of scrapable stores. Add a store here + a thin module and it works.
FOOD_LION = StoreConfig("foodlion", "Food Lion", "MVP", "27401")
HARRIS_TEETER = StoreConfig("harristeeter", "Harris Teeter", "VIC", "27401")


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def local_now() -> datetime:
    """Local-now in US Eastern, tolerant of a missing IANA tz db (Windows)."""
    try:
        return datetime.now(ZoneInfo("America/New_York"))
    except ZoneInfoNotFoundError:
        return datetime.now().astimezone()


def generate_sid() -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(16))


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def format_date(value: str | None) -> str:
    parsed = parse_dt(value)
    return parsed.date().isoformat() if parsed else ""


def _align_tz(value: datetime, reference: datetime) -> datetime:
    """Make ``value`` comparable to ``reference`` (both aware or both naive).

    Flipp normally returns offset-aware timestamps and :func:`local_now` is
    aware, but a date-only or offset-less value would otherwise raise
    ``TypeError`` mid-scrape. Assume such a value is in the reference's zone.
    """
    if (value.tzinfo is None) == (reference.tzinfo is None):
        return value
    if reference.tzinfo is None:
        return value.replace(tzinfo=None)
    return value.replace(tzinfo=reference.tzinfo)


def is_active(value_from: str | None, value_to: str | None, now: datetime) -> bool:
    start, end = parse_dt(value_from), parse_dt(value_to)
    if not start or not end:
        return False
    return _align_tz(start, now) <= now <= _align_tz(end, now)


def is_expired(value_to: str | None, now: datetime) -> bool:
    """True only when ``value_to`` parses to a moment already past.

    An absent or unparseable end date is *unknown*, not expired — we never drop
    a deal just because the source omitted a date.
    """
    end = parse_dt(value_to)
    return end is not None and _align_tz(end, now) < now


def normalize_price(value: Any) -> str:
    """Return a compact price *string* ("1.990" -> "1.99", "2.00" -> "2").

    Non-numeric text (e.g. "BOGO") passes through unchanged.
    """
    if value is None or value == "":
        return ""
    text = str(value).strip()
    if not text:
        return ""
    try:
        return f"{float(text.replace('$', '').replace(',', '')):.2f}".rstrip("0").rstrip(".")
    except ValueError:
        return text


def price_to_float(value: Any) -> float | None:
    """Coerce a price-ish value to ``float`` for DB storage, or ``None``."""
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Best-effort dollar-price extraction: one comparable numeric price per deal,
# from a structured field or dug out of the free-text ad copy.
# --------------------------------------------------------------------------- #
DOLLAR_AMOUNT = re.compile(r"\$\s*(\d{1,4}(?:\.\d{1,2})?)")
SAVE_AMOUNT = re.compile(r"save\s+\$?\s*(\d+(?:\.\d{1,2})?)", re.IGNORECASE)
OFF_AMOUNT = re.compile(r"\$\s*(\d+(?:\.\d{1,2})?)\s+off", re.IGNORECASE)


def _first_dollar_in_text(text: str) -> str:
    if not text:
        return ""
    for pattern in (SAVE_AMOUNT, OFF_AMOUNT, DOLLAR_AMOUNT):
        match = pattern.search(text)
        if match:
            return normalize_price(match.group(1))
    return ""


def extract_dollar_price(
    *,
    sale_price: str = "",
    regular_price: str = "",
    discount_amount: str = "",
    deal_description: str = "",
    item_name: str = "",
) -> str:
    """Return one comparable dollar value (string) for filtering/sorting deals.

    Prefers a structured price field; otherwise digs a "$x" out of the free text.
    """
    for field in (sale_price, discount_amount, regular_price):
        normalized = normalize_price(field)
        if normalized:
            return normalized

    for separator in ("—", " - "):
        if deal_description and separator in deal_description:
            parsed = _first_dollar_in_text(deal_description.split(separator, 1)[1])
            if parsed:
                return parsed

    return _first_dollar_in_text(deal_description) or _first_dollar_in_text(item_name)


# --------------------------------------------------------------------------- #
# Item classification (shared across stores)
# --------------------------------------------------------------------------- #
# (sub_category, keyword tuples). First match wins; order matters.
SUB_CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Store Promotion Banner", ("mvp summer", "shop & earn", "summer savings")),
    ("Loyalty Program Feature", ("shop & earn", "p6_v2", "mvp ")),
    ("Pantry & Seasoning", ("seasoning", "spice", "sauce", "pasta", "rice", "beans")),
    ("Meat & Seafood", (
        "beef", "pork", "poultry", "seafood", "chicken", "steak", "sausage",
        "lunchmeat", "london broil", "wings", "drumstick", "fish", "salmon",
        "turkey", "ham", "bacon", "burger", "meatball", "ribeye", "ribs",
    )),
    ("Dairy & Cheese", (
        "cheese", "feta", "cheddar", "milk", "yogurt", "butter", "cream",
        "sour cream", "cottage cheese",
    )),
    ("Frozen Foods", (
        "pizza", "frozen", "dinners", "marie callender", "healthy choice",
        "lean cuisine", "ice cream",
    )),
    ("Snacks & Chips", (
        "chips", "fritos", "cheetos", "crackers", "club", "town house",
        "tortilla chips", "pretzel", "popcorn", "cookies", "snack",
    )),
    ("Beverages", (
        "coca-cola", "coke", "pepsi", "water", "propel", "juice", "tea",
        "coffee", "soda", "drink", "lemonade", "sparkling",
    )),
    ("Bakery", ("bagel", "croissant", "bread", "roll", "muffin", "donut", "cake")),
    ("Produce", (
        "apple", "banana", "grape", "melon", "watermelon", "cantaloupe",
        "lettuce", "tomato", "potato", "onion", "pepper", "salad", "fruit",
        "vegetable", "produce",
    )),
    ("Household & Personal Care", (
        "shampoo", "soap", "detergent", "cleaner", "paper towel", "tissue",
    )),
    ("Baby & Kids", ("beech-nut", "pouch", "diaper", "baby", "infant")),
]


def infer_sub_category(item_name: str, brand: str, has_price: bool) -> str:
    haystack = f"{item_name} {brand}".lower()
    for category, keywords in SUB_CATEGORY_RULES:
        if any(k in haystack for k in keywords):
            return category
    if not has_price:
        if re.search(r"\b(or|and)\b", item_name.lower()) and len(item_name.split()) >= 4:
            return "Multi-Product Promo (price not listed)"
        if brand:
            return f"{brand} Brand Feature (price not listed)"
        return "Weekly Ad Feature (price not listed)"
    return "General Grocery"


def infer_unit(item_name: str, price: str) -> str:
    name = item_name.lower()
    if any(t in name for t in ("lb", "pound", "per lb")):
        return "lb"
    if "dozen" in name or re.search(r"\beggs?\b", name):
        return "dozen"
    if "gallon" in name:
        return "gallon"
    return "each" if price else ""


def infer_coupon_deal_type(sale_story: str, coupon_type: str) -> str:
    story = sale_story.lower()
    if "bogo" in story or "buy 1 get 1" in story or "buy one get one" in story:
        return "Bogo"
    if "buy" in story and "free" in story:
        return "Bogo"
    # Percent-off must be checked before the generic "off" text match below,
    # else "20% off" would be mislabeled a flat Digital Coupon.
    if coupon_type == "percentoff" or "%" in story:
        return "Percent Off Coupon"
    if coupon_type == "amountoff" or "save $" in story or "off" in story:
        return "Digital Coupon"
    return "Digital Coupon"


# --------------------------------------------------------------------------- #
# Flipp HTTP client
# --------------------------------------------------------------------------- #
class FlippClient:
    """Thin wrapper over the Flipp data + flyer-items endpoints."""

    def __init__(self, timeout: float = 30.0):
        self._client = httpx.Client(timeout=timeout, headers={"User-Agent": user_agent()})

    def __enter__(self) -> "FlippClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def fetch_data(self, postal_code: str) -> dict[str, Any]:
        """Return the full Flipp payload (contains both ``flyers`` and ``coupons``)."""
        resp = self._client.get(
            FLIPP_DATA_URL,
            params={"locale": "en", "postal_code": postal_code, "sid": generate_sid()},
        )
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, dict):
            raise ValueError("Unexpected Flipp data response")
        return payload

    def fetch_flyer_items(self, flyer_id: int | str) -> list[dict[str, Any]]:
        resp = self._client.get(
            FLIPP_ITEMS_URL.format(flyer_id=flyer_id),
            params={"locale": "en", "sid": generate_sid()},
        )
        resp.raise_for_status()
        items = resp.json()
        if not isinstance(items, list):
            raise ValueError(f"Unexpected Flipp response for flyer {flyer_id}")
        return items


def weekly_flyer_candidates(
    flyers: list[dict[str, Any]], merchant: str
) -> list[dict[str, Any]]:
    """Every 'weekly' flyer a merchant publishes, regardless of validity dates."""
    return [
        f for f in flyers
        if (f.get("merchant") or "").strip().lower() == merchant.lower()
        and "weekly" in (f.get("name") or "").lower()
    ]


def flyer_status(flyer: dict[str, Any], now: datetime) -> str:
    """Classify a flyer as ``active``, ``upcoming``, ``expired`` or ``unknown``."""
    valid_from, valid_to = flyer.get("valid_from"), flyer.get("valid_to")
    if is_active(valid_from, valid_to, now):
        return "active"
    if is_expired(valid_to, now):
        return "expired"
    start = parse_dt(valid_from)
    if start and _align_tz(start, now) > now:
        return "upcoming"
    return "unknown"


def pick_weekly_flyer(
    flyers: list[dict[str, Any]], merchant: str, now: datetime
) -> dict[str, Any] | None:
    """Pick the weekly flyer to scrape: the active one, else the next to start.

    Never returns an expired flyer. The old ``pool = active or candidates``
    fallback silently served last week's ad as current, and every weekly-ad row
    inherited its dates — the GFP-16 bug. ``None`` means "nothing usable"; the
    caller must say so rather than store stale rows.
    """
    candidates = weekly_flyer_candidates(flyers, merchant)
    by_status: dict[str, list[dict[str, Any]]] = {}
    for flyer in candidates:
        by_status.setdefault(flyer_status(flyer, now), []).append(flyer)

    active = by_status.get("active") or []
    if active:  # most recently started active flyer wins
        return max(active, key=lambda f: f.get("valid_from") or "")

    # Next best: an ad that hasn't started yet (or carries no usable dates).
    pending = (by_status.get("upcoming") or []) + (by_status.get("unknown") or [])
    if pending:  # the one starting soonest
        return min(pending, key=lambda f: f.get("valid_from") or "")
    return None


# --------------------------------------------------------------------------- #
# Digital coupons
# --------------------------------------------------------------------------- #
def coupon_item_name(coupon: dict[str, Any]) -> str:
    brand = (coupon.get("brand") or "").strip()
    if brand:
        return brand
    text = (coupon.get("promotion_text") or coupon.get("sale_story") or "").strip()
    if len(text) > 80:
        text = text[:77] + "..."
    return text or "Digital coupon"


def is_grocery_coupon(coupon: dict[str, Any]) -> bool:
    categories = [str(cat).lower() for cat in (coupon.get("categories") or [])]
    if "grocery" in categories:
        return True
    text = f"{coupon.get('promotion_text', '')} {coupon.get('sale_story', '')}".lower()
    food_keywords = (
        "meat", "chicken", "beef", "pork", "egg", "milk", "cheese", "yogurt",
        "bread", "cereal", "snack", "grocery", "produce", "deli", "seafood",
    )
    return any(word in text for word in food_keywords)


def filter_grocery_coupons(
    coupons: list[dict[str, Any]], now: datetime
) -> list[dict[str, Any]]:
    return [
        c for c in coupons
        if is_grocery_coupon(c) and is_active(c.get("valid_from"), c.get("valid_to"), now)
    ]


# --------------------------------------------------------------------------- #
# Row builders (emit DB-ready `deals` rows)
# --------------------------------------------------------------------------- #
def _flyer_item_source_url(flyer_id: Any, item_id: Any, postal_code: str | None) -> str | None:
    """Build a "View ad" link for a weekly-ad item, or ``None`` if it can't be.

    See the FLIPP_WEB_FLYER_URL comment above for how this URL shape was
    verified. ``flyer_id``/``item_id`` are effectively always present (every
    weekly-ad item and flyer Flipp has returned so far carries an ``id``) but
    are not a *guaranteed* field, so this degrades to ``None`` rather than
    emitting a URL with a missing/"None" segment -- callers (the GUI, GFP-38/
    GFP-52) must render plain text, not a dead link, when this is ``None``.
    """
    if not flyer_id or not item_id:
        return None
    url = FLIPP_WEB_FLYER_URL.format(flyer_id=flyer_id)
    query = {"item_id": str(item_id)}
    if postal_code:
        query["postal_code"] = str(postal_code)
    return f"{url}?{urllib.parse.urlencode(query)}"


def _coupon_source_url(coupon_id: Any, postal_code: str | None) -> str | None:
    """Build a "View ad" link for a digital coupon, or ``None`` if it can't be."""
    if not coupon_id:
        return None
    url = FLIPP_WEB_COUPON_URL.format(coupon_id=coupon_id)
    if postal_code:
        return f"{url}?{urllib.parse.urlencode({'postal_code': str(postal_code)})}"
    return url


def flyer_item_to_row(
    item: dict[str, Any],
    flyer: dict[str, Any],
    store: StoreConfig,
    postal_code: str | None = None,
) -> dict[str, Any]:
    """Map a weekly-ad flyer item to a `deals` row (numeric fields as float|None).

    ``postal_code`` defaults to the store's own default when omitted (kept
    optional so existing callers/tests that only pass ``(item, flyer, store)``
    keep working) and only affects the ``source_url`` query string.
    """
    postal_code = postal_code or store.default_postal_code
    item_name = (item.get("name") or "").strip()
    brand = (item.get("brand") or "").strip()
    sale_price = normalize_price(item.get("price"))
    has_price = bool(sale_price)
    unit = infer_unit(item_name, sale_price)
    sub_category = infer_sub_category(item_name, brand, has_price)
    flyer_id = flyer.get("id")
    item_id = item.get("id")

    parts = []
    if brand:
        parts.append(brand)
    if sale_price:
        parts.append(f"${sale_price}" + (f"/{unit}" if unit else ""))
    elif sub_category:
        parts.append(sub_category)
    deal_description = " — ".join(parts) if parts else "Weekly ad item"
    identifier, identifier_ns = product_identifier(item_id, PRODUCT_IDENTIFIER_NS_ITEM)

    # flipp_flyer_id/flipp_item_id are kept in `notes` here for provenance
    # (unchanged, existing behavior) *and* promoted to real `deals` columns
    # below (GFP-15) so they're queryable without parsing this free-text
    # blob -- e.g. `WHERE flipp_flyer_id = ?` instead of a `notes LIKE`.
    notes = [
        "source=weekly_ad",
        f"flipp_flyer_id={flyer_id}",
        f"flipp_item_id={item_id}",
        f"loyalty={store.loyalty_name}",
    ]
    if brand:
        notes.append(f"brand={brand}")
    if not has_price:
        notes.append("price_missing=true")

    return {
        "item_name": item_name,
        "sub_category": sub_category,
        "deal_type": "Weekly Ad" if has_price else "Weekly Ad (price not listed)",
        "deal_description": deal_description,
        "regular_price": None,
        "sale_price": price_to_float(sale_price),
        "dollar_price": price_to_float(
            extract_dollar_price(
                sale_price=sale_price,
                deal_description=deal_description,
                item_name=item_name,
            )
        ),
        "discount_amount": None,
        "discount_percent": None,
        "valid_from": format_date(item.get("valid_from") or flyer.get("valid_from")),
        "valid_to": format_date(item.get("valid_to") or flyer.get("valid_to")),
        "loyalty_required": "Y",
        "notes": "; ".join(notes),
        # GFP-15: a "View ad" link (never "Buy now" -- see module header) and
        # the ad-clipping image. cutout_image_url is not a guaranteed field
        # (Flipp's schema is undocumented) but was present on every item
        # observed while building this ticket -- degrade to None, not a
        # broken <img>, when it's missing.
        "source_url": _flyer_item_source_url(flyer_id, item_id, postal_code),
        "image_url": (item.get("cutout_image_url") or "").strip() or None,
        "flipp_flyer_id": flyer_id,
        "flipp_item_id": item_id,
        "flipp_coupon_id": None,
        # GFP-98: Flipp ad copy never states how a price is denominated,
        # nor a per-unit price. NULL is the honest value -- "not stated by
        # the source" -- and a guess here would be worse than nothing.
        "sold_by": None,
        "price_per_unit": None,
        "price_per_unit_uom": None,
        # GFP-111: the same Flipp item id as `flipp_item_id` above, restated in
        # the source-agnostic pair so a reader has ONE place to look for "this
        # row's product identifier" whatever produced it. flipp_item_id is
        # untouched -- this adds a column, it does not replace one. NULL/NULL
        # when Flipp omitted the item's id, since a weekly-ad row with no id is
        # simply not orderable and there is nothing honest to put here.
        "product_identifier": identifier,
        "product_identifier_ns": identifier_ns,
    }


def coupon_to_row(
    coupon: dict[str, Any], store: StoreConfig, postal_code: str | None = None
) -> dict[str, Any]:
    """Map a digital coupon to a `deals` row (discount fields as float|None).

    ``postal_code`` defaults to the store's own default when omitted, same as
    :func:`flyer_item_to_row`.
    """
    postal_code = postal_code or store.default_postal_code
    sale_story = (coupon.get("sale_story") or "").strip()
    promotion_text = (coupon.get("promotion_text") or "").strip()
    deal_type = infer_coupon_deal_type(sale_story, str(coupon.get("coupon_type") or ""))
    coupon_id = coupon.get("coupon_id")

    discount_amount = normalize_price(coupon.get("dollars_off"))
    discount_percent: float | None = None
    percent_off = coupon.get("percent_off")
    if percent_off not in (None, ""):
        try:
            discount_percent = float(percent_off) * 100
        except (TypeError, ValueError):
            discount_percent = None

    deal_description = promotion_text or sale_story
    item_name = coupon_item_name(coupon)
    identifier, identifier_ns = product_identifier(coupon_id, PRODUCT_IDENTIFIER_NS_COUPON)

    notes = [
        "source=digital_coupon",
        f"coupon_id={coupon_id}",
        f"coupon_type={coupon.get('coupon_type')}",
        f"loyalty={store.loyalty_name}",
        f"redemption={coupon.get('redemption_method', 'savetocard')}",
    ]

    return {
        "item_name": item_name,
        "sub_category": "Digital Coupon",
        "deal_type": deal_type,
        "deal_description": deal_description,
        "regular_price": None,
        "sale_price": None,
        "dollar_price": price_to_float(
            extract_dollar_price(
                discount_amount=discount_amount,
                deal_description=deal_description,
                item_name=item_name,
            )
        ),
        "discount_amount": price_to_float(discount_amount),
        "discount_percent": discount_percent,
        "valid_from": format_date(coupon.get("valid_from")),
        "valid_to": format_date(coupon.get("valid_to")),
        "loyalty_required": "Y",
        "notes": "; ".join(notes),
        # GFP-15: same "View ad" contract as flyer_item_to_row -- a coupon
        # link resolves to the coupon's page, never a checkout/cart.
        "source_url": _coupon_source_url(coupon_id, postal_code),
        "image_url": (coupon.get("coupon_image_url") or "").strip() or None,
        "flipp_flyer_id": None,
        "flipp_item_id": None,
        "flipp_coupon_id": coupon_id,
        # GFP-98: Flipp ad copy never states how a price is denominated,
        # nor a per-unit price. NULL is the honest value -- "not stated by
        # the source" -- and a guess here would be worse than nothing.
        "sold_by": None,
        "price_per_unit": None,
        "price_per_unit_uom": None,
        # GFP-111: a coupon id, in its OWN namespace -- Flipp numbers coupons
        # separately from flyer items, so the same digits can appear as both
        # and mean different things. Deliberately not folded in with
        # flipp.item_id for that reason.
        "product_identifier": identifier,
        "product_identifier_ns": identifier_ns,
    }


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def _no_flyer_message(
    flyers: list[dict[str, Any]], store: StoreConfig, postal_code: str, now: datetime
) -> str:
    """Explain *why* there is nothing to scrape — expired ad vs. no ad at all."""
    candidates = weekly_flyer_candidates(flyers, store.merchant_name)
    if not candidates:
        return (
            f"No {store.merchant_name} weekly flyer found for postal code {postal_code}. "
            f"Flipp listed {len(flyers)} flyers; none matched that merchant + 'weekly'."
        )
    newest = max(candidates, key=lambda f: f.get("valid_to") or "")
    return (
        f"No current {store.merchant_name} weekly flyer for postal code {postal_code} — "
        f"the newest one ({newest.get('name')!r}, {format_date(newest.get('valid_from'))} to "
        f"{format_date(newest.get('valid_to'))}) has expired. Refusing to store stale deals; "
        "try again once the new ad is published."
    )


def scrape_store(
    store: StoreConfig,
    postal_code: str | None = None,
    include_coupons: bool = True,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Fetch a store's active weekly ad (+ digital coupons).

    Returns ``(deal_rows, flyer_meta, stats)``.
    """
    postal_code = postal_code or store.default_postal_code
    now = now or local_now()

    with FlippClient() as client:
        payload = client.fetch_data(postal_code)
        flyers = payload.get("flyers", [])
        if not isinstance(flyers, list):
            raise ValueError("Unexpected Flipp response: missing flyers list")

        flyer = pick_weekly_flyer(flyers, store.merchant_name, now)
        if flyer is None:
            raise RuntimeError(_no_flyer_message(flyers, store, postal_code, now))

        items = client.fetch_flyer_items(flyer["id"])
        weekly_rows, expired_items = [], 0
        for item in items:
            if not (item.get("name") or "").strip():
                continue
            # An item may carry its own dates and lapse before the flyer does.
            if is_expired(item.get("valid_to") or flyer.get("valid_to"), now):
                expired_items += 1
                continue
            weekly_rows.append(flyer_item_to_row(item, flyer, store, postal_code))

        coupon_rows: list[dict[str, Any]] = []
        if include_coupons:
            coupons = payload.get("coupons", [])
            if isinstance(coupons, list):
                seen: set[Any] = set()
                for coupon in filter_grocery_coupons(coupons, now):
                    coupon_id = coupon.get("coupon_id")
                    if coupon_id in seen:
                        continue
                    seen.add(coupon_id)
                    coupon_rows.append(coupon_to_row(coupon, store, postal_code))

    rows = weekly_rows + coupon_rows
    rows.sort(key=lambda r: (r["sub_category"] or "", (r["item_name"] or "").lower()))

    stats = {
        "weekly_ad": len(weekly_rows),
        "digital_coupons": len(coupon_rows),
        "no_price": sum(1 for r in weekly_rows if r["sale_price"] is None),
        "bogo": sum(1 for r in coupon_rows if r["deal_type"] == "Bogo"),
        "expired_items": expired_items,
        "total": len(rows),
        "flyer_id": flyer.get("id"),
        "flyer_name": flyer.get("name"),
        "flyer_status": flyer_status(flyer, now),
        "valid_from": format_date(flyer.get("valid_from")),
        "valid_to": format_date(flyer.get("valid_to")),
    }
    return rows, flyer, stats
