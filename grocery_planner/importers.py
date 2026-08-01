"""Import the existing CSV layout (data/<store>/{prices,deals}.csv) into SQLite.

Loss-less mapping of the README schema. Re-importing a store replaces its prior
csv-import rows, so the command is idempotent.
"""
from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .stores import STORES, Store

SOURCE_CSV = "csv-import"

DEAL_COLUMNS = [
    "item_name", "sub_category", "deal_type", "deal_description",
    "regular_price", "sale_price", "dollar_price", "discount_amount", "discount_percent",
    "valid_from", "valid_to", "loyalty_required", "notes",
    # GFP-15: per-deal "View ad" link + ad-clipping image, plus the Flipp
    # identifiers promoted out of `notes` into queryable columns. Always
    # NULL for csv-import rows (the legacy Excel export never had these) --
    # only grocery_planner/scrapers/base.py's row builders populate them.
    "source_url", "image_url", "flipp_flyer_id", "flipp_item_id", "flipp_coupon_id",
]
PRICE_COLUMNS = [
    "item_name", "brand", "category", "regular_price", "sale_price", "unit",
    "price_per_unit", "on_sale", "loyalty_required", "date_collected", "notes",
]
NUMERIC = {
    "regular_price", "sale_price", "dollar_price", "discount_amount",
    "discount_percent", "price_per_unit",
    "flipp_flyer_id", "flipp_item_id", "flipp_coupon_id",
}


@dataclass
class ImportResult:
    store: str
    deals: int
    prices: int
    skipped: list[str]


def _to_float(value: str | None):
    if value is None:
        return None
    text = str(value).strip().lstrip("$").replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _read_rows(path: Path, columns: list[str]) -> list[dict]:
    rows: list[dict] = []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        for raw in csv.DictReader(fh):
            row = {c: (raw.get(c) or "").strip() for c in columns}
            for c in columns:
                if c in NUMERIC:
                    row[c] = _to_float(row[c])
            rows.append(row)
    return rows


def import_store(conn: sqlite3.Connection, store: Store, data_dir: Path) -> ImportResult:
    folder = data_dir / store.data_folder
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    skipped: list[str] = []
    deals = prices = 0

    deals_csv = folder / "deals.csv"
    if deals_csv.exists():
        rows = _read_rows(deals_csv, DEAL_COLUMNS)
        conn.execute("DELETE FROM deals WHERE store=? AND source=?", (store.key, SOURCE_CSV))
        conn.executemany(
            f"INSERT INTO deals(store, {', '.join(DEAL_COLUMNS)}, source, imported_at) "
            f"VALUES (:store, {', '.join(':' + c for c in DEAL_COLUMNS)}, :source, :imported_at)",
            [{**r, "store": store.key, "source": SOURCE_CSV, "imported_at": now} for r in rows],
        )
        deals = len(rows)
    else:
        skipped.append(f"{store.data_folder}/deals.csv")

    prices_csv = folder / "prices.csv"
    if prices_csv.exists():
        rows = _read_rows(prices_csv, PRICE_COLUMNS)
        conn.execute("DELETE FROM prices WHERE store=? AND source=?", (store.key, SOURCE_CSV))
        conn.executemany(
            f"INSERT INTO prices(store, {', '.join(PRICE_COLUMNS)}, source, imported_at) "
            f"VALUES (:store, {', '.join(':' + c for c in PRICE_COLUMNS)}, :source, :imported_at)",
            [{**r, "store": store.key, "source": SOURCE_CSV, "imported_at": now} for r in rows],
        )
        prices = len(rows)
    else:
        skipped.append(f"{store.data_folder}/prices.csv")

    conn.commit()
    return ImportResult(store.key, deals, prices, skipped)


def import_dir(conn: sqlite3.Connection, data_dir: Path) -> list[ImportResult]:
    """Import every known store found under data_dir."""
    results = []
    for store in STORES:
        if (data_dir / store.data_folder).is_dir():
            results.append(import_store(conn, store, data_dir))
    return results
