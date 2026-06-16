"""Fetch Food Lion weekly ad items from the Flipp API and write data/foodlion/deals.csv."""
from __future__ import annotations

import argparse
import csv
import random
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "foodlion"
DEALS_CSV = DATA_DIR / "deals.csv"

FLIPP_DATA_URL = "https://flyers-ng.flippback.com/api/flipp/data"
FLIPP_ITEMS_URL = "https://flyers-ng.flippback.com/api/flipp/flyers/{flyer_id}/flyer_items"

DEFAULT_POSTAL_CODE = "27401"
DEFAULT_STORE_CODE = "1473"
MERCHANT_NAME = "Food Lion"
TIMEZONE = ZoneInfo("America/New_York")

DEALS_HEADERS = [
    "item_name",
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


def generate_sid() -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(16))


def parse_flipp_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def format_date(value: str | None) -> str:
    parsed = parse_flipp_datetime(value)
    return parsed.date().isoformat() if parsed else ""


def pick_weekly_flyer(flyers: list[dict[str, Any]], now: datetime) -> dict[str, Any] | None:
    candidates = [
        flyer
        for flyer in flyers
        if (flyer.get("merchant") or "").strip().lower() == MERCHANT_NAME.lower()
        and "weekly" in (flyer.get("name") or "").lower()
    ]
    if not candidates:
        return None

    active = []
    for flyer in candidates:
        start = parse_flipp_datetime(flyer.get("valid_from"))
        end = parse_flipp_datetime(flyer.get("valid_to"))
        if start and end and start <= now <= end:
            active.append(flyer)

    if active:
        active.sort(key=lambda flyer: flyer.get("valid_from", ""), reverse=True)
        return active[0]

    candidates.sort(key=lambda flyer: flyer.get("valid_from", ""), reverse=True)
    return candidates[0]


def fetch_flyers(client: httpx.Client, postal_code: str) -> list[dict[str, Any]]:
    response = client.get(
        FLIPP_DATA_URL,
        params={"locale": "en", "postal_code": postal_code, "sid": generate_sid()},
    )
    response.raise_for_status()
    payload = response.json()
    flyers = payload.get("flyers", [])
    if not isinstance(flyers, list):
        raise ValueError("Unexpected Flipp response: missing flyers list")
    return flyers


def fetch_flyer_items(client: httpx.Client, flyer_id: int | str) -> list[dict[str, Any]]:
    response = client.get(
        FLIPP_ITEMS_URL.format(flyer_id=flyer_id),
        params={"locale": "en", "sid": generate_sid()},
    )
    response.raise_for_status()
    items = response.json()
    if not isinstance(items, list):
        raise ValueError(f"Unexpected Flipp response for flyer {flyer_id}")
    return items


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


def item_to_deal_row(item: dict[str, Any], flyer: dict[str, Any]) -> dict[str, str]:
    item_name = (item.get("name") or "").strip()
    brand = (item.get("brand") or "").strip()
    sale_price = normalize_price(item.get("price"))
    unit = infer_unit(item_name, sale_price)

    description_parts = []
    if brand:
        description_parts.append(brand)
    if sale_price:
        description_parts.append(f"${sale_price}" + (f"/{unit}" if unit else ""))
    deal_description = " — ".join(description_parts) if description_parts else "Weekly ad item"

    notes = [
        f"flipp_flyer_id={flyer.get('id')}",
        f"flipp_item_id={item.get('id')}",
    ]
    if brand:
        notes.append(f"brand={brand}")

    return {
        "item_name": item_name,
        "deal_type": "Weekly Ad",
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


def write_deals_csv(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=DEALS_HEADERS)
        writer.writeheader()
        writer.writerows(rows)


def scrape(postal_code: str = DEFAULT_POSTAL_CODE) -> Path:
    now = datetime.now(TIMEZONE)

    with httpx.Client(
        timeout=30,
        headers={"User-Agent": "grocery-planner/1.0 (+local personal use)"},
    ) as client:
        flyers = fetch_flyers(client, postal_code)
        flyer = pick_weekly_flyer(flyers, now)
        if flyer is None:
            raise RuntimeError(f"No Food Lion weekly flyer found for postal code {postal_code}")

        items = fetch_flyer_items(client, flyer["id"])

    rows = [item_to_deal_row(item, flyer) for item in items if (item.get("name") or "").strip()]
    rows.sort(key=lambda row: row["item_name"].lower())
    write_deals_csv(rows, DEALS_CSV)

    print(f"Flyer: {flyer.get('name')} ({flyer.get('id')})")
    print(f"Valid: {format_date(flyer.get('valid_from'))} to {format_date(flyer.get('valid_to'))}")
    print(f"Wrote {len(rows)} deals to {DEALS_CSV}")
    return DEALS_CSV


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape Food Lion weekly ad deals via Flipp API.")
    parser.add_argument(
        "--postal-code",
        default=DEFAULT_POSTAL_CODE,
        help=f"US ZIP code (default: {DEFAULT_POSTAL_CODE})",
    )
    parser.add_argument(
        "--store-code",
        default=DEFAULT_STORE_CODE,
        help=f"Food Lion store code for reference/logging (default: {DEFAULT_STORE_CODE})",
    )
    args = parser.parse_args(argv)

    try:
        scrape(postal_code=args.postal_code)
    except httpx.HTTPError as exc:
        print(f"HTTP error while fetching Food Lion flyer data: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Food Lion scrape failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())