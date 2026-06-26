"""Fetch Food Lion weekly ad + grocery digital coupons via the Flipp API."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

from flipp_common import FOOD_LION, scrape_store_deals

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POSTAL_CODE = "27401"
DEFAULT_STORE_CODE = FOOD_LION.default_store_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scrape Food Lion weekly ad and digital grocery coupons."
    )
    parser.add_argument("--postal-code", default=DEFAULT_POSTAL_CODE)
    parser.add_argument("--store-code", default=DEFAULT_STORE_CODE)
    parser.add_argument(
        "--no-digital-coupons",
        action="store_true",
        help="Skip clip-to-card grocery coupons from Flipp",
    )
    args = parser.parse_args(argv)

    try:
        path, stats = scrape_store_deals(
            FOOD_LION,
            ROOT,
            args.postal_code,
            include_digital_coupons=not args.no_digital_coupons,
        )
    except httpx.HTTPError as exc:
        print(f"HTTP error while fetching Food Lion data: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Food Lion scrape failed: {exc}", file=sys.stderr)
        return 1

    print(f"Flyer: {stats['flyer_name']} ({stats['flyer_id']})")
    print(f"Valid: {stats['valid_from']} to {stats['valid_to']}")
    print(
        f"Wrote {stats['total']} deals to {path} "
        f"({stats['weekly_ad']} weekly ad, {stats['digital_coupons']} digital coupons, "
        f"{stats['bogo']} BOGO, {stats['no_price']} weekly items without price)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())