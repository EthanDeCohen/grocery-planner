"""Reset store CSVs to a clean baseline and refresh scraped deals."""
from __future__ import annotations

import argparse
import csv
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

STORES = ("wholefoods", "foodlion", "harristeeter")

PRICES_HEADERS = [
    "item_name",
    "brand",
    "category",
    "regular_price",
    "sale_price",
    "unit",
    "price_per_unit",
    "on_sale",
    "loyalty_required",
    "date_collected",
    "notes",
]

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


def write_header_only(path: Path, headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.DictWriter(handle, fieldnames=headers).writeheader()


def reset_csv_baselines() -> None:
    for store in STORES:
        write_header_only(DATA / store / "prices.csv", PRICES_HEADERS)
        write_header_only(DATA / store / "deals.csv", DEALS_HEADERS)
        print(f"Reset {store}/prices.csv and {store}/deals.csv (headers only)")


def remove_stale_artifacts() -> None:
    captures = DATA / "captures"
    if not captures.exists():
        return

    def on_rm_error(_func, _path, _exc_info) -> None:
        pass

    shutil.rmtree(captures, onerror=on_rm_error)
    if captures.exists():
        print(f"Note: could not fully remove {captures} (permission denied); skipped.")
    else:
        print(f"Removed stale folder: {captures}")


def refresh_scraped_deals(postal_code: str, include_digital_coupons: bool) -> int:
    from scrape_all_deals import main as scrape_all_main

    argv = ["--postal-code", postal_code]
    if not include_digital_coupons:
        argv.append("--no-digital-coupons")
    return scrape_all_main(argv)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reset grocery CSV files and optionally re-scrape store deals."
    )
    parser.add_argument(
        "--postal-code",
        default="27401",
        help="ZIP code used by deal scrapers (default: 27401)",
    )
    parser.add_argument(
        "--no-scrape",
        action="store_true",
        help="Only reset CSV headers; do not fetch fresh deals",
    )
    parser.add_argument(
        "--no-digital-coupons",
        action="store_true",
        help="Skip Harris Teeter / Food Lion digital coupons when scraping",
    )
    args = parser.parse_args(argv)

    remove_stale_artifacts()
    reset_csv_baselines()

    if args.no_scrape:
        print("Skipped deal scraping (--no-scrape).")
        return 0

    scripts_dir = str(Path(__file__).resolve().parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    return refresh_scraped_deals(args.postal_code, not args.no_digital_coupons)


if __name__ == "__main__":
    raise SystemExit(main())