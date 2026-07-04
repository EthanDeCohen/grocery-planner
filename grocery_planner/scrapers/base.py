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
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

FLIPP_DATA_URL = "https://flyers-ng.flippback.com/api/flipp/data"
FLIPP_ITEMS_URL = "https://flyers-ng.flippback.com/api/flipp/flyers/{flyer_id}/flyer_items"
USER_AGENT = "grocery-planner/0.1 (+local personal use)"


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


def is_active(value_from: str | None, value_to: str | None, now: datetime) -> bool:
    start, end = parse_dt(value_from), parse_dt(value_to)
    if not start or not end:
        return False
    return start <= now <= end


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
        self._client = httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT})

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


def pick_weekly_flyer(
    flyers: list[dict[str, Any]], merchant: str, now: datetime
) -> dict[str, Any] | None:
    """Pick the active 'weekly' flyer for a merchant; fall back to newest."""
    candidates = [
        f for f in flyers
        if (f.get("merchant") or "").strip().lower() == merchant.lower()
        and "weekly" in (f.get("name") or "").lower()
    ]
    if not candidates:
        return None

    active = [f for f in candidates if is_active(f.get("valid_from"), f.get("valid_to"), now)]
    pool = active or candidates
    pool.sort(key=lambda f: f.get("valid_from", ""), reverse=True)
    return pool[0]


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
def flyer_item_to_row(
    item: dict[str, Any], flyer: dict[str, Any], store: StoreConfig
) -> dict[str, Any]:
    """Map a weekly-ad flyer item to a `deals` row (numeric fields as float|None)."""
    item_name = (item.get("name") or "").strip()
    brand = (item.get("brand") or "").strip()
    sale_price = normalize_price(item.get("price"))
    has_price = bool(sale_price)
    unit = infer_unit(item_name, sale_price)
    sub_category = infer_sub_category(item_name, brand, has_price)

    parts = []
    if brand:
        parts.append(brand)
    if sale_price:
        parts.append(f"${sale_price}" + (f"/{unit}" if unit else ""))
    elif sub_category:
        parts.append(sub_category)
    deal_description = " — ".join(parts) if parts else "Weekly ad item"

    notes = [
        "source=weekly_ad",
        f"flipp_flyer_id={flyer.get('id')}",
        f"flipp_item_id={item.get('id')}",
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
    }


def coupon_to_row(coupon: dict[str, Any], store: StoreConfig) -> dict[str, Any]:
    """Map a digital coupon to a `deals` row (discount fields as float|None)."""
    sale_story = (coupon.get("sale_story") or "").strip()
    promotion_text = (coupon.get("promotion_text") or "").strip()
    deal_type = infer_coupon_deal_type(sale_story, str(coupon.get("coupon_type") or ""))

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

    notes = [
        "source=digital_coupon",
        f"coupon_id={coupon.get('coupon_id')}",
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
    }


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
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
            raise RuntimeError(
                f"No {store.merchant_name} weekly flyer found for postal code {postal_code}"
            )

        items = client.fetch_flyer_items(flyer["id"])
        weekly_rows = [
            flyer_item_to_row(i, flyer, store)
            for i in items
            if (i.get("name") or "").strip()
        ]

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
                    coupon_rows.append(coupon_to_row(coupon, store))

    rows = weekly_rows + coupon_rows
    rows.sort(key=lambda r: (r["sub_category"] or "", (r["item_name"] or "").lower()))

    stats = {
        "weekly_ad": len(weekly_rows),
        "digital_coupons": len(coupon_rows),
        "no_price": sum(1 for r in weekly_rows if r["sale_price"] is None),
        "bogo": sum(1 for r in coupon_rows if r["deal_type"] == "Bogo"),
        "total": len(rows),
        "flyer_id": flyer.get("id"),
        "flyer_name": flyer.get("name"),
        "valid_from": format_date(flyer.get("valid_from")),
        "valid_to": format_date(flyer.get("valid_to")),
    }
    return rows, flyer, stats
