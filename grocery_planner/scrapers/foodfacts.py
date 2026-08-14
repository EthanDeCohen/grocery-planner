# ######### decohen-partners ##########
# Protein Ledger
"""Save a retailer's own protein number into the database.

In:  a FoodFact (product id, name, protein per 100g) from any scraper.
Out: three rows -- the food, its protein density, and a match linking the
     store's deal name to that food.

Every scraper that reads nutrition off a label used to do this itself. There
were six near-identical copies; GFP-302 made it one.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from .. import matching


def now_iso() -> str:
    """UTC timestamp, to the second."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class FoodFact:
    """One product's protein figure, as the retailer published it."""

    #: The retailer's own product id. Called product_id / sku / asin depending
    #: on who you ask, which is half the reason this was six functions.
    source_ref: str
    #: The retailer's product title, for the foods table.
    name: str
    category: str
    #: Grams per 100g, not per serving -- a serving figure is useless without
    #: the serving size.
    protein_per_100g: float
    #: The name as it appears in `deals`. May differ from `name` (a scraper
    #: often appends the size). This is what the match keys on.
    item_name: str


def upsert_food_fact(
    conn: sqlite3.Connection, source: str, store: str, method: str, fact: FoodFact
) -> None:
    """Write the food, its protein density, and the match. Safe to re-run.

    source = where the number came from (the API).
    store  = the banner it's sold under. NOT always the same thing: Kroger's API
             supplies Harris Teeter, so source='kroger' but store='harristeeter'.
             Get this wrong and the match points at a store with no deals.
    method = how we know (kroger_api_direct, wholefoods_direct, ...).
    """
    now = now_iso()

    # Keyed on (source, source_ref) so '123' at Kroger and '123' at Trader Joe's
    # stay two different foods.
    conn.execute(
        "INSERT INTO foods(name, category, source, source_ref, slug, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(source, source_ref) DO UPDATE SET "
        "name=excluded.name, category=excluded.category, slug=excluded.slug, "
        "updated_at=excluded.updated_at",
        (fact.name, fact.category, source, fact.source_ref,
         f"{source}-{fact.source_ref}", now),
    )
    food_id = conn.execute(
        "SELECT id FROM foods WHERE source=? AND source_ref=?",
        (source, fact.source_ref),
    ).fetchone()["id"]

    conn.execute(
        "INSERT INTO food_nutrients(food_id, nutrient, amount_per_100g, unit) "
        "VALUES (?, 'protein', ?, 'g') "
        "ON CONFLICT(food_id, nutrient) DO UPDATE SET "
        "amount_per_100g=excluded.amount_per_100g, unit=excluded.unit",
        (food_id, fact.protein_per_100g),
    )

    # MANUAL matters: it stops the keyword matcher overwriting a number we read
    # off the actual label with a guess about a similarly-named product.
    conn.execute(
        "INSERT INTO deal_food_match"
        "(store, item_name, food_id, confidence, method, match_source, updated_at) "
        "VALUES (?, ?, ?, 1.0, ?, ?, ?) "
        "ON CONFLICT(store, item_name) DO UPDATE SET "
        "food_id=excluded.food_id, confidence=1.0, method=excluded.method, "
        "match_source=excluded.match_source, updated_at=excluded.updated_at",
        (store, fact.item_name, food_id, method, matching.MANUAL, now),
    )
