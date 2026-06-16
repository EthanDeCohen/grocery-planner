"""Run all store deal scrapers (Food Lion, Harris Teeter)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

from flipp_common import FOOD_LION, HARRIS_TEETER, scrape_store_deals

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POSTAL_CODE = "27401"

STORES = [
    ("Food Lion", FOOD_LION),
    ("Harris Teeter", HARRIS_TEETER),
]


def run_store(
    label: str,
    store,
    postal_code: str,
    include_digital_coupons: bool,
) -> bool:
    print(f"\n=== {label} ===")
    try:
        path, stats = scrape_store_deals(
            store,
            ROOT,
            postal_code,
            include_digital_coupons=include_digital_coupons,
        )
    except httpx.HTTPError as exc:
        print(f"HTTP error: {exc}", file=sys.stderr)
        return False
    except Exception as exc:
        print(f"Scrape failed: {exc}", file=sys.stderr)
        return False

    print(f"Flyer: {stats['flyer_name']} ({stats['flyer_id']})")
    print(f"Valid: {stats['valid_from']} to {stats['valid_to']}")
    print(
        f"Wrote {stats['total']} deals to {path} "
        f"({stats['weekly_ad']} weekly ad, {stats['digital_coupons']} digital coupons, "
        f"{stats['bogo']} BOGO, {stats['no_price']} weekly items without price)"
    )
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape deals for all configured stores.")
    parser.add_argument("--postal-code", default=DEFAULT_POSTAL_CODE)
    parser.add_argument(
        "--no-digital-coupons",
        action="store_true",
        help="Skip clip-to-card grocery coupons from Flipp",
    )
    args = parser.parse_args(argv)

    ok = True
    for label, store in STORES:
        if not run_store(label, store, args.postal_code, not args.no_digital_coupons):
            ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())