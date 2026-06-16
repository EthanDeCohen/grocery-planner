"""Shared Flipp API helpers for grocery store deal scrapers."""
from __future__ import annotations

import csv
import random
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

FLIPP_DATA_URL = "https://flyers-ng.flippback.com/api/flipp/data"
FLIPP_ITEMS_URL = "https://flyers-ng.flippback.com/api/flipp/flyers/{flyer_id}/flyer_items"

TIMEZONE = ZoneInfo("America/New_York")

DEALS_HEADERS = [
    "item_name",
    "sub_category",
    "deal_type",
    "deal_description",
    "regular_price",
    "sale_price",
    "discount_amount",
    "discount_percent",
    "valid_from",
    "valid_to",
    "loyalty_required",
    "notes",
]

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


@dataclass(frozen=True)
class StoreConfig:
    slug: str
    merchant_name: str
    loyalty_name: str
    default_store_code: str
    data_dir_name: str


FOOD_LION = StoreConfig("foodlion", "Food Lion", "MVP", "1473", "foodlion")
HARRIS_TEETER = StoreConfig("harristeeter", "Harris Teeter", "VIC", "09700313", "harristeeter")


def generate_sid() -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(16))


def parse_flipp_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def format_date(value: str | None) -> str:
    parsed = parse_flipp_datetime(value)
    return parsed.date().isoformat() if parsed else ""


def is_active(value_from: str | None, value_to: str | None, now: datetime) -> bool:
    start = parse_flipp_datetime(value_from)
    end = parse_flipp_datetime(value_to)
    if not start or not end:
        return False
    return start <= now <= end


def fetch_flipp_data(client: httpx.Client, postal_code: str) -> dict[str, Any]:
    response = client.get(
        FLIPP_DATA_URL,
        params={"locale": "en", "postal_code": postal_code, "sid": generate_sid()},
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Unexpected Flipp data response")
    return payload


def fetch_flyer_items(client: httpx.Client, flyer_id: int | str) -> list[dict[str, Any]]:
    response = client.get(
        FLIPP_ITEMS_URL.format(flyer_id=flyer_id),
        params={"locale": "en", "sid": generate_sid()},
    )
    response.raise_for_status()
    items = response.json()
    if not isinstance(items, list):
        raise ValueError(f"Unexpected Flipp items response for flyer {flyer_id}")
    return items


def pick_weekly_flyer(
    flyers: list[dict[str, Any]], merchant_name: str, now: datetime
) -> dict[str, Any] | None:
    candidates = [
        flyer
        for flyer in flyers
        if (flyer.get("merchant") or "").strip().lower() == merchant_name.lower()
        and "weekly" in (flyer.get("name") or "").lower()
    ]
    if not candidates:
        return None

    active = []
    for flyer in candidates:
        if is_active(flyer.get("valid_from"), flyer.get("valid_to"), now):
            active.append(flyer)

    if active:
        active.sort(key=lambda flyer: flyer.get("valid_from", ""), reverse=True)
        return active[0]

    candidates.sort(key=lambda flyer: flyer.get("valid_from", ""), reverse=True)
    return candidates[0]


def normalize_price(value: Any) -> str:
    if value is None or value == "":
        return ""
    text = str(value).strip()
    if not text:
        return ""
    try:
        number = float(text)
        return f"{number:.2f}".rstrip("0").rstrip(".")
    except ValueError:
        return text


def infer_sub_category(item_name: str, brand: str, has_price: bool) -> str:
    haystack = f"{item_name} {brand}".lower()

    for category, keywords in SUB_CATEGORY_RULES:
        if any(keyword in haystack for keyword in keywords):
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
    if any(token in name for token in ("lb", "pound", "per lb")):
        return "lb"
    if "dozen" in name or re.search(r"\beggs?\b", name):
        return "dozen"
    if "gallon" in name:
        return "gallon"
    if price:
        return "each"
    return ""


def infer_coupon_deal_type(sale_story: str, coupon_type: str) -> str:
    story = sale_story.lower()
    if "bogo" in story or "buy 1 get 1" in story or "buy one get one" in story:
        return "Bogo"
    if "buy" in story and "free" in story:
        return "Bogo"
    if coupon_type == "amountoff" or "save $" in story or "off" in story:
        return "Digital Coupon"
    if coupon_type == "percentoff" or "%" in story:
        return "Percent Off Coupon"
    return "Digital Coupon"


def flyer_item_to_deal_row(
    item: dict[str, Any], flyer: dict[str, Any], store: StoreConfig
) -> dict[str, str]:
    item_name = (item.get("name") or "").strip()
    brand = (item.get("brand") or "").strip()
    sale_price = normalize_price(item.get("price"))
    has_price = bool(sale_price)
    unit = infer_unit(item_name, sale_price)
    sub_category = infer_sub_category(item_name, brand, has_price)

    description_parts = []
    if brand:
        description_parts.append(brand)
    if sale_price:
        description_parts.append(f"${sale_price}" + (f"/{unit}" if unit else ""))
    elif sub_category:
        description_parts.append(sub_category)
    deal_description = " — ".join(description_parts) if description_parts else "Weekly ad item"
    deal_type = "Weekly Ad" if has_price else "Weekly Ad (price not listed)"

    notes = [
        f"source=weekly_ad",
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
        "deal_type": deal_type,
        "deal_description": deal_description,
        "regular_price": "",
        "sale_price": sale_price,
        "discount_amount": "",
        "discount_percent": "",
        "valid_from": format_date(item.get("valid_from") or flyer.get("valid_from")),
        "valid_to": format_date(item.get("valid_to") or flyer.get("valid_to")),
        "loyalty_required": "Y",
        "notes": "; ".join(notes),
    }


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


def filter_grocery_coupons(coupons: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    selected = []
    for coupon in coupons:
        if not is_grocery_coupon(coupon):
            continue
        if not is_active(coupon.get("valid_from"), coupon.get("valid_to"), now):
            continue
        selected.append(coupon)
    return selected


def coupon_to_deal_row(coupon: dict[str, Any], store: StoreConfig) -> dict[str, str]:
    sale_story = (coupon.get("sale_story") or "").strip()
    promotion_text = (coupon.get("promotion_text") or "").strip()
    dollars_off = coupon.get("dollars_off")
    percent_off = coupon.get("percent_off")
    deal_type = infer_coupon_deal_type(sale_story, str(coupon.get("coupon_type") or ""))

    discount_amount = normalize_price(dollars_off) if dollars_off not in (None, "") else ""
    discount_percent = ""
    if percent_off not in (None, ""):
        try:
            discount_percent = str(int(float(percent_off) * 100))
        except (TypeError, ValueError):
            discount_percent = str(percent_off)

    notes = [
        "source=digital_coupon",
        f"coupon_id={coupon.get('coupon_id')}",
        f"coupon_type={coupon.get('coupon_type')}",
        f"loyalty={store.loyalty_name}",
        f"redemption={coupon.get('redemption_method', 'savetocard')}",
    ]

    return {
        "item_name": coupon_item_name(coupon),
        "sub_category": "Digital Coupon",
        "deal_type": deal_type,
        "deal_description": promotion_text or sale_story,
        "regular_price": "",
        "sale_price": "",
        "discount_amount": discount_amount,
        "discount_percent": discount_percent,
        "valid_from": format_date(coupon.get("valid_from")),
        "valid_to": format_date(coupon.get("valid_to")),
        "loyalty_required": "Y",
        "notes": "; ".join(notes),
    }


def write_deals_csv(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=DEALS_HEADERS)
        writer.writeheader()
        writer.writerows(rows)


def scrape_store_deals(
    store: StoreConfig,
    project_root: Path,
    postal_code: str,
    include_digital_coupons: bool = True,
) -> tuple[Path, dict[str, Any]]:
    now = datetime.now(TIMEZONE)
    deals_path = project_root / "data" / store.data_dir_name / "deals.csv"

    with httpx.Client(
        timeout=30,
        headers={"User-Agent": "grocery-planner/1.0 (+local personal use)"},
    ) as client:
        payload = fetch_flipp_data(client, postal_code)
        flyers = payload.get("flyers", [])
        if not isinstance(flyers, list):
            raise ValueError("Unexpected Flipp response: missing flyers list")

        flyer = pick_weekly_flyer(flyers, store.merchant_name, now)
        if flyer is None:
            raise RuntimeError(
                f"No {store.merchant_name} weekly flyer found for postal code {postal_code}"
            )

        items = fetch_flyer_items(client, flyer["id"])
        weekly_rows = [
            flyer_item_to_deal_row(item, flyer, store)
            for item in items
            if (item.get("name") or "").strip()
        ]

        coupon_rows: list[dict[str, str]] = []
        if include_digital_coupons:
            coupons = payload.get("coupons", [])
            if isinstance(coupons, list):
                grocery_coupons = filter_grocery_coupons(coupons, now)
                seen_ids: set[Any] = set()
                for coupon in grocery_coupons:
                    coupon_id = coupon.get("coupon_id")
                    if coupon_id in seen_ids:
                        continue
                    seen_ids.add(coupon_id)
                    coupon_rows.append(coupon_to_deal_row(coupon, store))

    rows = weekly_rows + coupon_rows
    rows.sort(key=lambda row: (row["sub_category"], row["item_name"].lower()))
    write_deals_csv(rows, deals_path)

    stats: dict[str, Any] = {
        "weekly_ad": len(weekly_rows),
        "digital_coupons": len(coupon_rows),
        "no_price": sum(1 for row in weekly_rows if not row["sale_price"]),
        "bogo": sum(1 for row in coupon_rows if row["deal_type"] == "Bogo"),
        "total": len(rows),
        "flyer_id": flyer.get("id"),
        "flyer_name": flyer.get("name"),
        "valid_from": format_date(flyer.get("valid_from")),
        "valid_to": format_date(flyer.get("valid_to")),
    }
    return deals_path, stats