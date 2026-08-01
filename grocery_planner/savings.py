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
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable

from simpleeval import InvalidExpression, simple_eval

from . import formulas

# Canonical bases: everything comparable reduces to one of these.
WEIGHT = "oz"
VOLUME = "fl oz"
COUNT = "each"

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
