"""One way to record a retailer's own protein figure (GFP-302).

Every source that reads nutrition off a retailer's label lands it the same way:
a ``foods`` row, its protein density in ``food_nutrients``, and a
``deal_food_match`` at confidence 1.0. Before this module that write existed
**six times** -- kroger, traderjoes, wholefoods, wegmans_api,
instacart_storefront, and sprouts (which already delegated).

Six copies of one write is six places to drift, and a drift here is not
cosmetic: this is the path that decides which protein density an item is priced
with, which is what the optimiser ranks on and what GFP-281's harness scores.

WHY match_source=MANUAL IS LOAD-BEARING
---------------------------------------
The figure came off the retailer's own label for this exact product. ``MANUAL``
is what stops ``matching.match_deals``'s keyword auto-matcher from later
overwriting a measurement with a guess about a similarly-named food. ``method``
keeps the real provenance auditable -- it is the retailer, not a human.

That rationale used to be repeated, near-identically, in every one of the six
copies. It is stated once here.

THE ONE ASYMMETRY, RECORDED RATHER THAN SMOOTHED OVER
-----------------------------------------------------
``name`` and ``item_name`` are different things:

* ``name`` -- the retailer's own product title, for ``foods.name``.
* ``item_name`` -- the ``deals`` row name, which ``deal_food_match`` keys on and
  which may carry a size the scraper folded in (``f"{name}, {size_text}"``).

Four sources carry both and fall back (``name=description or item_name``).
``wegmans_api`` has no separate title in its feed at all, so it passes
``name=item_name`` and always takes that fallback. Verified equivalent before
consolidating, per GFP-302's own instruction to check rather than assume.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from .. import matching


def now_iso() -> str:
    """UTC, second precision. Was ``_now_iso`` in six modules."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class FoodFact:
    """A retailer's own protein figure for one exact product.

    Stored as a DENSITY -- grams per 100g -- rather than per serving, because a
    per-serving figure is meaningless without the serving, and the whole point
    of these sources is that they state both.

    ``source_ref`` is the retailer's own product id. It was called
    ``product_id`` in kroger and instacart, ``sku`` in traderjoes and wegmans,
    and ``asin`` in wholefoods -- four names for one concept, which is most of
    why the write could not be shared before.
    """

    source_ref: str
    name: str
    category: str
    protein_per_100g: float
    item_name: str


def upsert_food_fact(
    conn: sqlite3.Connection, source: str, store: str, method: str, fact: FoodFact
) -> None:
    """Record ``fact`` so ``cost_per_gram_protein`` can just use it.

    THE VENDOR IS NOT ALWAYS THE BANNER, WHICH IS WHY THESE ARE TWO ARGUMENTS.

    * ``source`` -- ``foods.source`` and the slug prefix. The API the figure
      came from.
    * ``store`` -- ``deal_food_match.store``, which must equal ``deals.store``
      or the match joins to nothing.
    * ``method`` -- the provenance on the match (``kroger_api_direct``,
      ``wholefoods_direct``, ...).

    For three of the four callers these coincide. **Kroger is the exception**:
    it writes ``source='kroger'`` (the API) against ``store='harristeeter'``
    (the banner it serves). Collapsing them to one argument would have written
    ``store='kroger'``, which matches no ``deals`` row -- so Harris Teeter would
    have silently lost all 537 of its retailer-direct matches while every test
    that only checks kroger's own rows still passed. Measured 2026-08-14.

    All three are passed rather than derived: a scraper knows its own
    provenance, and inferring it here would put a naming rule in the one place
    that must not care about naming.

    No new column and no new code path -- the figure reaches the engine through
    the chain it already walks, which is why this shape was chosen when kroger
    and wholefoods first did it.
    """
    now = now_iso()
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
    conn.execute(
        "INSERT INTO deal_food_match"
        "(store, item_name, food_id, confidence, method, match_source, updated_at) "
        "VALUES (?, ?, ?, 1.0, ?, ?, ?) "
        "ON CONFLICT(store, item_name) DO UPDATE SET "
        "food_id=excluded.food_id, confidence=1.0, method=excluded.method, "
        "match_source=excluded.match_source, updated_at=excluded.updated_at",
        (store, fact.item_name, food_id, method, matching.MANUAL, now),
    )
