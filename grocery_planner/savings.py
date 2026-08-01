"""Savings & processing engine (GFP-8): unit normalization, ranking, scoring.

The point of the app is not to list deals but to say which ones are *worth
buying*. That needs a comparable number per deal, which means getting a size
out of the ad copy and dividing the price by it.

Three honesty rules run through this module:

1. **A size we cannot read is ``None``, never a guess.** Weekly-ad names are
   free text ("Eggo Frozen Waffles"); a made-up size would produce a confident
   and wrong unit price, which is worse than admitting we don't know.
2. **Only the headline product counts.** Ads bundle alternatives — "11 oz. HT
   Traders Bag Coffee or 8 - 10 Ct. Green Mountain K-Cup" — under one price.
   We read the size before the first "or"; the rest belongs to other products.
3. **Ranges take the low end** ("5.4-5.5 Oz." -> 5.4), so the resulting cost
   per ounce is never flattering by accident.

Note on savings-vs-regular-price: the Flipp scrapers only ever populate
``sale_price``; ``regular_price`` arrives solely from CSV imports. So
:func:`savings_vs_regular` returns ``None`` for scraped rows until shelf-price
capture (GFP-5) lands. Cost-per-unit and ranking below do not depend on it.

:func:`cost_per_gram_protein` (GFP-26) is the number the whole app exists to
compute: it chains a deal's price through its parsed size, its GFP-25 food
match and that food's GFP-23 protein-per-100g figure into dollars per gram of
protein. The same three honesty rules apply, plus one more specific to this
chain: **only a weight-based size carries a gram weight.** A deal priced per
``each`` or ``fl oz`` (see ``COUNT``/``VOLUME`` above) has nothing to convert
to grams, so it has no cost-per-gram-of-protein -- pretending ``each`` means
some fixed weight would be exactly the kind of confident wrong guess rule 1
forbids.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable

from simpleeval import InvalidExpression, simple_eval

from . import db, formulas, matching, nutrition

# Canonical bases: everything comparable reduces to one of these.
WEIGHT = "oz"
VOLUME = "fl oz"
COUNT = "each"

# parse_size() normalizes weight to oz (see _UNITS above); cost-per-gram-of-
# protein needs grams, so this is the one extra conversion that chain adds.
# The exact avoirdupois-ounce figure, not the table's rounded 0.035274, since
# this is the last multiplication in the chain and should not compound the
# table's own rounding.
GRAMS_PER_OZ = 28.349523125

# unit token -> (base, multiplier into that base)
_UNITS: dict[str, tuple[str, float]] = {
    "oz": (WEIGHT, 1.0), "ounce": (WEIGHT, 1.0), "ounces": (WEIGHT, 1.0),
    "lb": (WEIGHT, 16.0), "lbs": (WEIGHT, 16.0), "pound": (WEIGHT, 16.0),
    "pounds": (WEIGHT, 16.0),
    "g": (WEIGHT, 0.035274), "gram": (WEIGHT, 0.035274), "grams": (WEIGHT, 0.035274),
    "kg": (WEIGHT, 35.274),
    "floz": (VOLUME, 1.0),
    "ml": (VOLUME, 0.033814), "l": (VOLUME, 33.814), "ltr": (VOLUME, 33.814),
    "liter": (VOLUME, 33.814), "liters": (VOLUME, 33.814), "litre": (VOLUME, 33.814),
    "gal": (VOLUME, 128.0), "gallon": (VOLUME, 128.0),
    "qt": (VOLUME, 32.0), "quart": (VOLUME, 32.0),
    "pt": (VOLUME, 16.0), "pint": (VOLUME, 16.0),
    "ct": (COUNT, 1.0), "count": (COUNT, 1.0), "pk": (COUNT, 1.0),
    "pack": (COUNT, 1.0), "each": (COUNT, 1.0), "ea": (COUNT, 1.0),
    "dozen": (COUNT, 12.0), "doz": (COUNT, 12.0),
}
_UNIT_ALTERNATION = "|".join(sorted(_UNITS, key=len, reverse=True))
_NUMBER = r"\d+(?:\.\d+)?"

# "6 pk 500 ml" / "4 pack 12 oz" -> six 500ml bottles.
_MULTIPACK = re.compile(
    rf"({_NUMBER})\s*(?:pk|pack|ct|count)\.?\s+({_NUMBER})\s*({_UNIT_ALTERNATION})\b",
    re.IGNORECASE,
)
# "5.4-5.5 oz" / "15 - 18.4 oz" -> the low end.
_RANGE = re.compile(
    rf"({_NUMBER})\s*[-–]\s*{_NUMBER}\s*({_UNIT_ALTERNATION})\b", re.IGNORECASE
)
_SIMPLE = re.compile(rf"({_NUMBER})\s*({_UNIT_ALTERNATION})\b", re.IGNORECASE)
# A bare "dozen"/"each" with no number in front still means a size.
_BARE = re.compile(r"\b(dozen|each)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Size:
    """A size read out of ad copy, plus its value in canonical base units."""

    quantity: float
    unit: str
    base_quantity: float
    base_unit: str

    def __str__(self) -> str:
        amount = f"{self.quantity:g}"
        return f"{amount} {self.unit}"


def _normalize(quantity: float, unit_token: str) -> Size | None:
    base, multiplier = _UNITS[unit_token.lower()]
    total = quantity * multiplier
    if total <= 0:
        return None
    return Size(quantity, unit_token.lower(), total, base)


def parse_size(item_name: str | None) -> Size | None:
    """Read the headline size out of an item name, or ``None`` if unreadable.

    >>> parse_size("16 oz. Simple Truth Organic Peanut Butter")
    Size(quantity=16.0, unit='oz', base_quantity=16.0, base_unit='oz')
    >>> parse_size("Eggo Frozen Waffles") is None
    True
    """
    if not item_name:
        return None
    # Only the first product in an "A or B" promo is priced by this row.
    headline = re.split(r"\bor\b", item_name, maxsplit=1, flags=re.IGNORECASE)[0]
    # "fl oz" / "fl. oz." -> the volume token, so it isn't read as weight.
    headline = re.sub(r"\bfl\.?\s*oz\.?", " floz ", headline, flags=re.IGNORECASE)

    match = _MULTIPACK.search(headline)
    if match:
        packs, each, unit = float(match.group(1)), float(match.group(2)), match.group(3)
        size = _normalize(each, unit)
        return None if size is None else Size(
            packs * each, size.unit, packs * size.base_quantity, size.base_unit
        )

    for pattern in (_RANGE, _SIMPLE):
        match = pattern.search(headline)
        if match:
            return _normalize(float(match.group(1)), match.group(2))

    match = _BARE.search(headline)
    return _normalize(1.0, match.group(1)) if match else None


def cost_per_unit(price: float | None, item_name: str | None) -> tuple[float, str] | None:
    """``(cost, base unit)`` for a deal, or ``None`` when either half is unknown."""
    if price is None or price <= 0:
        return None
    size = parse_size(item_name)
    if size is None:
        return None
    return price / size.base_quantity, size.base_unit


def savings_vs_regular(row: Any) -> tuple[float, float] | None:
    """``(amount saved, percent saved)`` — only when a regular price is on record.

    Scraped rows have no ``regular_price`` (see the module docstring), so this
    is currently answerable for CSV-imported rows only.
    """
    regular, sale = _get(row, "regular_price"), _get(row, "sale_price")
    if regular is None or sale is None or regular <= 0 or sale >= regular:
        return None
    saved = regular - sale
    return saved, saved / regular * 100


def _get(row: Any, key: str, default: Any = None) -> Any:
    """Read a field from a sqlite3.Row or a plain mapping."""
    try:
        value = row[key]
    except (IndexError, KeyError, TypeError):
        return default
    return default if value is None else value


def annotate(rows: Iterable[Any]) -> list[dict[str, Any]]:
    """Turn deal rows into dicts carrying size, unit price and savings.

    Rows whose size or price is unreadable keep ``unit_price=None`` — they are
    still returned, because "we can't compare this one" is information too.
    """
    annotated: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        price = _get(row, "dollar_price") or _get(row, "sale_price")
        size = parse_size(_get(row, "item_name"))
        per_unit = cost_per_unit(price, _get(row, "item_name"))
        item["price"] = price
        item["size"] = str(size) if size else ""
        item["unit_price"] = per_unit[0] if per_unit else None
        item["unit"] = per_unit[1] if per_unit else ""
        saved = savings_vs_regular(row)
        item["saved_amount"] = saved[0] if saved else None
        item["saved_percent"] = saved[1] if saved else None
        annotated.append(item)
    return annotated


def rank_by_unit_price(rows: Iterable[Any], limit: int = 0) -> list[dict[str, Any]]:
    """Cheapest cost-per-unit first; incomparable rows are dropped.

    Units are only compared like with like — a $/oz and a $/each cannot be
    ordered against each other meaningfully, so callers should filter to one
    base unit (or one category) for a ranking that means something. The
    ``unit`` field on every row makes that visible.
    """
    ranked = [item for item in annotate(rows) if item["unit_price"] is not None]
    ranked.sort(key=lambda item: (item["unit"], item["unit_price"]))
    for position, item in enumerate(ranked, start=1):
        item["rank"] = position
    return ranked[:limit] if limit else ranked


@dataclass(frozen=True)
class ProteinCost:
    """Dollars per gram of protein for one deal, plus the provenance a
    nutritionist needs to weigh it: which food it was matched to, how
    confident that match is, and whether the protein figure is a sourced
    USDA value or still a curated estimate (GFP-50 needs the latter)."""

    cost_per_gram_protein: float
    food_id: int
    food_name: str
    protein_source: str          # 'usda' or 'curated' -- foods.source
    match_confidence: float      # 0-1, see matching.CONFIDENCE_*
    match_method: str
    size_grams: float
    protein_grams: float


def cost_per_gram_protein(
    price: float | None,
    item_name: str | None,
    store: str | None,
    conn: sqlite3.Connection | None = None,
) -> ProteinCost | None:
    """Dollars per gram of protein for one deal, or ``None`` when any link in
    the chain is missing -- following this module's rule 1, a missing number
    is ``None``, never a guess.

    The chain: price -> size (:func:`parse_size`) -> grams (only when the
    size is weight-based, see the module docstring) -> matched food
    (``deal_food_match``, GFP-25) -> protein-per-100g (``food_nutrients``,
    GFP-23) -> grams of protein in the package -> price / that.

    Deliberately store-agnostic: ``store`` is used only as half of the
    ``deal_food_match`` lookup key, never to change behavior.
    """
    if price is None or price <= 0 or not item_name or not store:
        return None

    size = parse_size(item_name)
    # Only a weight-based size converts to grams; `each`/`fl oz` cannot (see
    # the module docstring's fourth rule).
    if size is None or size.base_unit != WEIGHT:
        return None

    own = conn or db.connect()
    match = matching.get_match(store, item_name, conn=own)
    if match is None or match["food_id"] is None:
        return None

    food = own.execute(
        "SELECT f.name, f.source, n.amount_per_100g "
        "FROM foods f LEFT JOIN food_nutrients n "
        "ON n.food_id = f.id AND n.nutrient = ? "
        "WHERE f.id = ?",
        (nutrition.PROTEIN, match["food_id"]),
    ).fetchone()
    if food is None or food["amount_per_100g"] is None:
        return None

    size_grams = size.base_quantity * GRAMS_PER_OZ
    protein_grams = size_grams * food["amount_per_100g"] / 100.0
    if protein_grams <= 0:
        return None

    return ProteinCost(
        cost_per_gram_protein=price / protein_grams,
        food_id=match["food_id"],
        food_name=food["name"],
        protein_source=food["source"],
        match_confidence=match["confidence"],
        match_method=match["method"],
        size_grams=size_grams,
        protein_grams=protein_grams,
    )


def rank_by_cost_per_gram_protein(
    rows: Iterable[Any],
    conn: sqlite3.Connection | None = None,
    limit: int = 0,
    min_confidence: float | None = None,
) -> list[dict[str, Any]]:
    """Cheapest dollars-per-gram-of-protein first; incomparable rows are dropped.

    Every surviving row carries ``match_confidence`` and ``protein_source``
    so a caller can see -- and choose to exclude -- a low-confidence guess
    rather than have it ranked indistinguishably alongside a high-confidence
    one (0.3 and 0.9 are never silently treated the same). Pass
    ``min_confidence`` to drop rows below a threshold outright; the default
    (``None``) keeps every computable row visible.
    """
    own = conn or db.connect()
    ranked: list[dict[str, Any]] = []
    for row in rows:
        price = _get(row, "dollar_price") or _get(row, "sale_price")
        result = cost_per_gram_protein(
            price, _get(row, "item_name"), _get(row, "store"), conn=own
        )
        if result is None:
            continue
        if min_confidence is not None and result.match_confidence < min_confidence:
            continue
        item = dict(row)
        item["price"] = price
        item["cost_per_gram_protein"] = result.cost_per_gram_protein
        item["food_id"] = result.food_id
        item["food_name"] = result.food_name
        item["protein_source"] = result.protein_source
        item["match_confidence"] = result.match_confidence
        item["match_method"] = result.match_method
        item["size_grams"] = result.size_grams
        item["protein_grams"] = result.protein_grams
        ranked.append(item)

    ranked.sort(key=lambda item: item["cost_per_gram_protein"])
    for position, item in enumerate(ranked, start=1):
        item["rank"] = position
    return ranked[:limit] if limit else ranked


def score_deals(
    conn: sqlite3.Connection,
    formula_name: str,
    rows: Iterable[Any],
    limit: int = 0,
) -> list[dict[str, Any]]:
    """Rank deals by a user formula, highest score first.

    The formula sees the deal's own numbers — ``price``, ``sale_price``,
    ``unit_price``, ``quantity``, ``saved_percent`` — plus every profile value,
    so "value per dollar for my protein target" is expressible without code.
    Rows whose formula cannot be evaluated (missing size, say) are skipped
    rather than scored as zero.
    """
    row = conn.execute(
        "SELECT expression FROM formulas WHERE name=?", (formula_name,)
    ).fetchone()
    if row is None:
        raise KeyError(f"No formula named {formula_name!r}")
    expression = row["expression"]
    profile = formulas._profile_context(conn)

    scored: list[dict[str, Any]] = []
    for item in annotate(rows):
        size = parse_size(item.get("item_name"))
        names = {
            **profile,
            "price": item["price"] or 0.0,
            "sale_price": item.get("sale_price") or 0.0,
            "unit_price": item["unit_price"] or 0.0,
            "quantity": size.base_quantity if size else 0.0,
            "saved_percent": item["saved_percent"] or 0.0,
        }
        try:
            item["score"] = float(simple_eval(expression, names=names))
        except (InvalidExpression, TypeError, ValueError, ZeroDivisionError, KeyError):
            continue
        scored.append(item)

    scored.sort(key=lambda entry: entry["score"], reverse=True)
    for position, entry in enumerate(scored, start=1):
        entry["rank"] = position
    return scored[:limit] if limit else scored
