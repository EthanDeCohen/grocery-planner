# ######### decohen-partners ##########
# Protein Ledger
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

from . import matching, sourcelink
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
    # GFP-98: how the price is DENOMINATED, and the source's own per-unit
    # price. Only the Kroger API states these today, so they are NULL for
    # Flipp and csv-import rows -- "not stated by the source", which is the
    # honest reading, never a guess. sold_by is 'UNIT' or 'WEIGHT'; a WEIGHT
    # price is per pound, and the UI must tag it as such or a $2.49/lb loin
    # reads as cheaper than a $4.99 packet.
    "sold_by", "price_per_unit", "price_per_unit_uom",
    # GFP-152: which KIND of by-weight item, since sold_by cannot say. Only
    # the Kroger API carries the evidence (its `categories` array), so this is
    # NULL for Flipp and csv rows -- "not applicable", distinct from the
    # 'unknown' value, which means "by weight and we could not tell you".
    "weight_basis",
    # GFP-111: the source's OWN product identifier, plus which vocabulary it
    # belongs to -- always as a pair, since a bare '0020895500000' does not say
    # whether it is a Kroger productId, an ASIN or a Flipp item id, and those
    # must never be compared or joined as if interchangeable. Each scraper
    # module writes its own namespace constant (see PRODUCT_IDENTIFIER_NS in
    # scrapers/kroger.py, scrapers/wholefoods.py and scrapers/base.py); nothing
    # here or downstream parses `notes` to get it. NULL for csv-import rows --
    # the legacy Excel export carries no source identifier and inventing one
    # would be worse than its absence (savings.py rule 1).
    "product_identifier", "product_identifier_ns",
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

# Text columns where a missing or blank CSV cell must land as NULL rather than
# as '' (GFP-111). _read_rows defaults every absent column to the empty string,
# which is harmless for a free-text field but not for an identifier: '' is not
# absent, it is a product identifier that identifies nothing, and it would
# survive into the SKU column and the v2 online-order file looking like a real
# one. The numeric columns above already get this for free (_to_float('') is
# None); these two are TEXT, so they need saying. Absent stays absent
# (savings.py rule 1).
NULL_WHEN_BLANK = {"product_identifier", "product_identifier_ns"}


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
                elif c in NULL_WHEN_BLANK and not row[c]:
                    row[c] = None
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
    """Import every known store found under data_dir, then match what landed.

    GFP-121: this is the second of the two paths that write ``deals``, and it
    had the same hole as the scrape path -- rows arrived and nothing ever
    matched them to a food, so they could not reach a $/g protein figure and
    were invisible to the optimiser.

    Matched once after the whole directory rather than per store:
    ``match_deals`` runs over every distinct ``(store, item_name)`` in the
    table anyway, so calling it per store would repeat the same full sweep for
    each folder present.
    """
    results = []
    for store in STORES:
        if (data_dir / store.data_folder).is_dir():
            results.append(import_store(conn, store, data_dir))
    if results:
        matching.match_deals(conn=conn)
        sourcelink.build_links(conn=conn)   # GFP-248
    return results
