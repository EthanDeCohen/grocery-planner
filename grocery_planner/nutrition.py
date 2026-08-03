"""Nutrition catalog reads (GFP-23): the foundation for cost-per-gram-of-protein.

``foods``/``food_nutrients`` (db_script/migration/0005_GFP-23.ddl) store one
row per food and one row per (food, nutrient) fact respectively -- protein is
just the first nutrient populated (db_script/migration/0006_GFP-23.dml seeds
a curated starter catalog); fibre, carbs, fat etc. scaffold in later as more
food_nutrients rows, not as a schema change.

Like ``grocery_planner/service/deals.py``, every function here takes an
optional ``conn`` that defaults to ``db.connect()`` rather than holding a
connection on an object: SQLite connections cannot cross threads, and the GUI
scrapes on a background thread.

Deal-to-food matching (GFP-25) and the USDA ingest (GFP-24) are out of scope
here; this module only reads what's already in the tables.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from . import db

PROTEIN = "protein"


@dataclass(frozen=True)
class FoodItem:
    """One ``foods`` row, with its protein-per-100g pulled in from ``food_nutrients``.

    ``protein_per_100g`` is ``None`` when the food has no protein row yet --
    the tables are deliberately allowed to be incomplete (GFP-23 is the
    foundation, not a fully populated catalog).
    """

    id: int
    name: str
    category: str
    source: str
    source_ref: str | None
    protein_per_100g: float | None
    #: Which animal protein this is (GFP-106): a kind, ``protein_kind.OTHER``,
    #: ``protein_kind.UNKNOWN``, or ``None`` when nothing has classified it yet.
    #: Deliberately NOT derived from ``category`` -- see protein_kind's docstring.
    protein_kind: str | None = None


def _row_to_food(row: sqlite3.Row) -> FoodItem:
    return FoodItem(
        id=row["id"],
        name=row["name"],
        category=row["category"],
        source=row["source"],
        source_ref=row["source_ref"],
        protein_per_100g=row["protein_per_100g"],
        protein_kind=row["protein_kind"],
    )


# Every function below LEFT JOINs in the protein row (rather than requiring
# one) so a food with no nutrient data yet still shows up, just with
# protein_per_100g=None -- the schema doesn't force nutrients to exist.
_FOOD_SELECT = (
    "SELECT f.id, f.name, f.category, f.source, f.source_ref, f.protein_kind, "
    "n.amount_per_100g AS protein_per_100g "
    "FROM foods f "
    "LEFT JOIN food_nutrients n ON n.food_id = f.id AND n.nutrient = ?"
)


def list_foods(
    category: str | None = None,
    conn: sqlite3.Connection | None = None,
    *,
    kind: str | None = None,
    meat_only: bool = False,
) -> list[FoodItem]:
    """All foods, optionally filtered, ordered by name.

    ``kind`` and ``meat_only`` (GFP-106) filter on ``protein_kind`` rather than
    ``category``, which is the only way to ask "chicken" of the catalog rows a
    deal actually matches to -- those say only 'Meat'. Both classify anything
    unclassified first, so a food added by this morning's scrape is not silently
    missing from the answer.
    """
    own = conn or db.connect()
    if kind or meat_only:
        from . import protein_kind as pk

        pk.ensure_classified(own)

    sql = _FOOD_SELECT
    params: list[str] = [PROTEIN]
    clauses = []
    if category:
        clauses.append("f.category = ?")
        params.append(category)
    if kind:
        clauses.append("f.protein_kind = ?")
        params.append(kind)
    if meat_only:
        from . import protein_kind as pk

        kinds = sorted(pk.MEAT_KINDS)
        clauses.append(f"f.protein_kind IN ({','.join('?' * len(kinds))})")
        params.extend(kinds)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY f.name"
    return [_row_to_food(r) for r in own.execute(sql, params)]


def get_food(name: str, conn: sqlite3.Connection | None = None) -> FoodItem | None:
    """A single food by exact name, or ``None`` if there isn't one."""
    own = conn or db.connect()
    row = own.execute(
        _FOOD_SELECT + " WHERE f.name = ?", [PROTEIN, name]
    ).fetchone()
    return _row_to_food(row) if row else None


def list_categories(conn: sqlite3.Connection | None = None) -> list[str]:
    """Distinct food categories present in the data.

    Sourced from the data rather than hard-coded, so the client-facing v1
    checkbox list (beef, pork, chicken, fish, tofu, whey) -- and any category
    added after v1 -- always reflects what's actually in ``foods``.
    """
    own = conn or db.connect()
    return [r[0] for r in own.execute(
        "SELECT DISTINCT category FROM foods ORDER BY category"
    )]


def protein_per_100g(name: str, conn: sqlite3.Connection | None = None) -> float | None:
    """Grams of protein per 100g for a food by name, or ``None`` if unknown."""
    food = get_food(name, conn=conn)
    return food.protein_per_100g if food else None
