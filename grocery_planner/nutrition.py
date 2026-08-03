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
from typing import Iterable

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


# --------------------------------------------------------------------------- #
# Preference matching (GFP-134)
# --------------------------------------------------------------------------- #
#: Broad buckets and the specific kinds that belong to them.
#:
#: ``foods.category`` holds TWO taxonomies at once. Measured on the live
#: database: broad buckets (Meat 208 rows, Dairy 85, Plant Protein 73, Seafood
#: 60, Supplements 44) sitting alongside specific kinds (fish 42, beef 30, pork
#: 29, chicken 28, tofu 24, whey 23). GFP-52 builds a checkbox per distinct
#: value, so a nutritionist sees "chicken" and "Meat" as if they were peers.
#:
#: THE CONSEQUENCE WAS CONCRETE. The cheapest animal protein in the database --
#: Harris Teeter Chicken Drumsticks at $0.0169/g -- has category "Meat". A
#: client who ticked "chicken" did not get it, and was priced against $0.0431/g
#: chicken breast instead: 2.5x, with nothing on screen saying why.
#:
#: This map is the smallest honest fix. It does NOT rewrite the data -- that is
#: a bigger decision about which taxonomy wins, and ingest would have to agree.
#: It makes MATCHING understand the relationship the data already implies, in
#: both directions: ticking "chicken" finds meat that IS chicken, and ticking
#: "Meat" finds the specific kinds beneath it.
CATEGORY_MEMBERS: dict[str, frozenset[str]] = {
    "meat": frozenset({"beef", "pork", "chicken", "turkey", "lamb"}),
    "seafood": frozenset({"fish", "shellfish"}),
}


def _known_kinds() -> frozenset[str]:
    """Every value the classifier can write, folded.

    Imported lazily: protein_kind imports this module, so a module-level
    import would be circular.
    """
    from .protein_kind import KINDS
    return frozenset(_folded(k) for k in KINDS)


def _folded(value: str | None) -> str:
    return (value or "").strip().lower()


def food_matches(
    selected: str, category: str | None, protein_kind: str | None
) -> bool:
    """Does a food belong to the category a nutritionist ticked?

    THE COLUMNS DISAGREE, AND ONE OF THEM IS MORE TRUSTWORTHY. Two real rows
    from the live database make the rule:

    * "Harris Teeter Chicken Drumsticks" -- category "Meat", kind "chicken".
      The cheapest animal protein there is, and ticking "chicken" used to miss
      it, pricing the client against breast at 2.5x.
    * "365 Dark Ground Turkey" -- category "beef", kind "turkey". Ticking
      "beef" used to offer it.

    So category is not merely ambiguous, it is sometimes WRONG, while
    ``protein_kind`` is computed from the product name by explicit, tested
    rules (GFP-107/GFP-135). Hence:

    * A BROAD BUCKET ("Meat", "Seafood") consults both columns -- either can
      place a food inside it.
    * A SPECIFIC KIND ("chicken", "beef") is answered by ``protein_kind``
      ALONE when the classifier has an opinion. Category is not consulted,
      because it is the column that gets this wrong.
    * With no classifier opinion -- Dairy, Plant Protein, Supplements have no
      kind at all -- category is all there is.

    Returns False for a food with neither column set: "cannot confirm this
    belongs to a preferred category" is not "belongs", and a
    category-CONSTRAINED pool must exclude it rather than guess it in, the
    same rule an unpriceable deal already follows.
    """
    want = _folded(selected)
    if not want:
        return False
    have_category = _folded(category)
    have_kind = _folded(protein_kind)

    members = CATEGORY_MEMBERS.get(want)
    if members is not None:
        # A BROAD BUCKET. Either column may place a food inside it, and both
        # are worth consulting: a food filed directly as "beef" belongs under
        # "Meat" just as much as one the classifier read as chicken.
        return (
            want == have_category
            or have_kind in members
            or have_category in members
        )

    if have_kind and want in _known_kinds():
        # A SPECIFIC KIND, and the classifier has an opinion. That opinion
        # WINS OUTRIGHT, and category is not consulted at all.
        #
        # This is not tidiness. foods.category is demonstrably wrong, not
        # merely ambiguous: "365 by Whole Foods Market Dark Ground Turkey" is
        # filed under category "beef" in the live data. Consulting category
        # here offered turkey to a client who ticked beef -- which for a
        # religious or medical restriction is a serious error, and one the
        # user cannot see or correct.
        #
        # protein_kind is computed from the product name by explicit, tested
        # rules (GFP-107/GFP-135). Where it exists it is the better witness,
        # and that is the entire reason it exists.
        return have_kind == want

    # No classifier opinion -- Dairy, Plant Protein and Supplements have no
    # protein_kind at all -- so category is all there is.
    return want == have_category


def food_ids_in(
    categories: Iterable[str], conn: sqlite3.Connection | None = None
) -> set[int]:
    """Every food id belonging to any of ``categories`` (GFP-134).

    Resolved once against both columns rather than per-deal, so the bill, the
    trend chart and anything added later agree by construction instead of by
    each implementing the same rule.
    """
    wanted = [c for c in categories if _folded(c)]
    if not wanted:
        return set()
    own = conn or db.connect()
    return {
        row["id"]
        for row in own.execute("SELECT id, category, protein_kind FROM foods")
        if any(food_matches(c, row["category"], row["protein_kind"]) for c in wanted)
    }
