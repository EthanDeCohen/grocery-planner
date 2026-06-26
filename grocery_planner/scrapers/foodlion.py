"""Food Lion weekly-ad scraper (ported from scripts/scrape_foodlion.py).

Returns normalized deal rows (matching the `deals` table columns) instead of
writing a CSV — the CLI inserts them into SQLite.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .base import FlippClient, format_date, normalize_price, pick_weekly_flyer

MERCHANT = "Food Lion"
STORE_KEY = "foodlion"
DEFAULT_POSTAL_CODE = "27401"


def _now() -> datetime:
    """Local-now in US Eastern, tolerant of a missing IANA tz db (Windows)."""
    try:
        return datetime.now(ZoneInfo("America/New_York"))
    except ZoneInfoNotFoundError:
        return datetime.now().astimezone()

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


def _item_to_row(item: dict[str, Any], flyer: dict[str, Any]) -> dict[str, Any]:
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

    notes = [f"flipp_flyer_id={flyer.get('id')}", f"flipp_item_id={item.get('id')}"]
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
        "sale_price": float(sale_price) if has_price else None,
        "discount_amount": None,
        "discount_percent": None,
        "valid_from": format_date(item.get("valid_from") or flyer.get("valid_from")),
        "valid_to": format_date(item.get("valid_to") or flyer.get("valid_to")),
        "loyalty_required": "Y",
        "notes": "; ".join(notes),
    }


def scrape(postal_code: str = DEFAULT_POSTAL_CODE) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch the active Food Lion weekly ad. Returns (deal_rows, flyer_meta)."""
    now = _now()
    with FlippClient() as client:
        flyers = client.fetch_flyers(postal_code)
        flyer = pick_weekly_flyer(flyers, MERCHANT, now)
        if flyer is None:
            raise RuntimeError(f"No Food Lion weekly flyer found for postal code {postal_code}")
        items = client.fetch_flyer_items(flyer["id"])

    rows = [_item_to_row(i, flyer) for i in items if (i.get("name") or "").strip()]
    rows.sort(key=lambda r: (r["item_name"] or "").lower())
    return rows, flyer
