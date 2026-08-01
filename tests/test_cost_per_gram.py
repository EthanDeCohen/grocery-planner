"""Tests for grocery_planner.savings.cost_per_gram_protein (GFP-26): the
number the whole app exists to compute.

This is the final link in a chain built across three earlier tickets: a
deal's price (GFP-6/-8), its size in ad copy (GFP-8's parse_size), its match
to a food (GFP-25's deal_food_match) and that food's protein-per-100g
(GFP-23's food_nutrients). Every test here builds that chain explicitly with
real db.connect() fixtures (migrations + the curated 32-item catalog are
already seeded by conftest's `conn` fixture) rather than mocking any one
link, because the whole point of GFP-26 is that all four links have to hold
at once.
"""
from __future__ import annotations

import pytest

from grocery_planner import matching, savings


def _insert_deal(
    conn,
    store,
    item_name,
    dollar_price=None,
    sale_price=None,
    sub_category="Meat & Seafood",
):
    conn.execute(
        "INSERT INTO deals(store, item_name, sub_category, deal_type, "
        "dollar_price, sale_price, source) "
        "VALUES (?, ?, ?, 'Weekly Ad', ?, ?, 'scrape')",
        (store, item_name, sub_category, dollar_price, sale_price),
    )
    conn.commit()


def _insert_food(conn, name, category, source, source_ref, protein_per_100g=None):
    conn.execute(
        "INSERT INTO foods(name, category, source, source_ref, slug) "
        "VALUES (?, ?, ?, ?, ?)",
        (name, category, source, source_ref, source_ref),
    )
    food_id = conn.execute(
        "SELECT id FROM foods WHERE source_ref=? AND source=?", (source_ref, source)
    ).fetchone()["id"]
    if protein_per_100g is not None:
        conn.execute(
            "INSERT INTO food_nutrients(food_id, nutrient, amount_per_100g, unit) "
            "VALUES (?, 'protein', ?, 'g')",
            (food_id, protein_per_100g),
        )
    conn.commit()
    return food_id


# --------------------------------------------------------------------------- #
# The happy path, with a sanity check on the actual number -- GFP-26's own
# instruction is "if chicken breast comes out at $0.001/g, something is
# wrong", so this test checks the figure lands in a plausible range, not
# just that it's a float.
# --------------------------------------------------------------------------- #
def test_chicken_breast_cost_per_gram_protein_is_plausible(conn):
    _insert_deal(conn, "foodlion", "16 oz. Boneless Skinless Chicken Breast", dollar_price=3.99)
    matching.match_deals(conn=conn)

    result = savings.cost_per_gram_protein(
        3.99, "16 oz. Boneless Skinless Chicken Breast", "foodlion", conn=conn
    )
    assert result is not None
    # 16 oz -> 453.592 g package; 23 g protein / 100 g -> ~104.3 g protein.
    assert result.size_grams == pytest.approx(453.592, rel=1e-4)
    assert result.protein_grams == pytest.approx(104.326, rel=1e-3)
    assert result.cost_per_gram_protein == pytest.approx(3.99 / 104.326, rel=1e-3)
    # Sane range: real chicken breast is a few cents per gram of protein,
    # not fractions of a cent (a wrong unit conversion would land there).
    assert 0.01 < result.cost_per_gram_protein < 0.15
    assert result.food_name == "Chicken breast, skinless/boneless, raw"
    assert result.protein_source == "curated"
    assert result.match_confidence == matching.CONFIDENCE_HIGH


# --------------------------------------------------------------------------- #
# Honesty rule: None, never a guess, whichever link is missing.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("price", [None, 0.0, -1.0])
def test_none_when_price_is_missing_or_nonsense(conn, price):
    _insert_deal(conn, "foodlion", "16 oz. Boneless Skinless Chicken Breast", dollar_price=3.99)
    matching.match_deals(conn=conn)
    assert savings.cost_per_gram_protein(
        price, "16 oz. Boneless Skinless Chicken Breast", "foodlion", conn=conn
    ) is None


def test_none_when_size_is_unreadable(conn):
    _insert_deal(conn, "foodlion", "Boneless Skinless Chicken Breast", dollar_price=3.99)
    matching.match_deals(conn=conn)
    # No size at all in the name -- parse_size itself returns None.
    assert savings.cost_per_gram_protein(
        3.99, "Boneless Skinless Chicken Breast", "foodlion", conn=conn
    ) is None


def test_none_when_the_item_has_no_food_match(conn):
    _insert_deal(conn, "foodlion", "16 oz. Eggo Frozen Waffles", dollar_price=3.99)
    matching.match_deals(conn=conn)
    # Never matched to anything -- no deal_food_match row at all.
    assert matching.get_match("foodlion", "16 oz. Eggo Frozen Waffles", conn=conn) is None
    assert savings.cost_per_gram_protein(
        3.99, "16 oz. Eggo Frozen Waffles", "foodlion", conn=conn
    ) is None


def test_none_when_the_matched_food_has_no_protein_figure(conn):
    food_id = _insert_food(conn, "Test food with no protein row", "beef", "curated", "test-no-protein")
    _insert_deal(conn, "foodlion", "16 oz. Test Cut", dollar_price=3.99)
    matching.correct_match("foodlion", "16 oz. Test Cut", food_id, conn=conn)

    assert savings.cost_per_gram_protein(
        3.99, "16 oz. Test Cut", "foodlion", conn=conn
    ) is None


# --------------------------------------------------------------------------- #
# "Only weight-based sizes can work" -- each/fl oz have no gram weight.
# --------------------------------------------------------------------------- #
def test_none_for_a_count_based_size_even_when_matched(conn):
    """'8 ct. Chicken Drumsticks' is a real matchable item (chicken +
    drumstick), but its size is COUNT, not WEIGHT -- there is no gram weight
    to divide by, so honesty demands None, not a guess at what 'each' weighs."""
    size = savings.parse_size("8 ct. Chicken Drumsticks")
    assert size is not None and size.base_unit == savings.COUNT  # sanity on the fixture itself

    _insert_deal(conn, "foodlion", "8 ct. Chicken Drumsticks", dollar_price=5.99)
    matching.match_deals(conn=conn)
    assert matching.get_match("foodlion", "8 ct. Chicken Drumsticks", conn=conn) is not None

    assert savings.cost_per_gram_protein(
        5.99, "8 ct. Chicken Drumsticks", "foodlion", conn=conn
    ) is None


def test_none_for_a_fl_oz_size_even_when_matched(conn):
    _insert_deal(conn, "foodlion", "32 fl oz. Whey Protein, Ready-To-Drink Shake", dollar_price=6.99)
    matching.match_deals(conn=conn)

    size = savings.parse_size("32 fl oz. Whey Protein, Ready-To-Drink Shake")
    assert size is not None and size.base_unit == savings.VOLUME

    assert savings.cost_per_gram_protein(
        6.99, "32 fl oz. Whey Protein, Ready-To-Drink Shake", "foodlion", conn=conn
    ) is None


# --------------------------------------------------------------------------- #
# Confidence and provenance must be surfaced, not flattened.
# --------------------------------------------------------------------------- #
def test_low_confidence_match_is_distinguishable_from_high_confidence(conn):
    _insert_deal(conn, "foodlion", "16 oz. Boneless Skinless Chicken Breast", dollar_price=3.99)
    _insert_deal(conn, "foodlion", "1.5 lb. Top Round London Broil", dollar_price=8.99)
    matching.match_deals(conn=conn)

    high = savings.cost_per_gram_protein(
        3.99, "16 oz. Boneless Skinless Chicken Breast", "foodlion", conn=conn
    )
    low = savings.cost_per_gram_protein(
        8.99, "1.5 lb. Top Round London Broil", "foodlion", conn=conn
    )
    assert high.match_confidence == matching.CONFIDENCE_HIGH
    assert low.match_confidence == matching.CONFIDENCE_LOW
    assert low.match_method == "category_fallback"
    assert high.match_confidence != low.match_confidence


def test_protein_source_distinguishes_usda_from_curated(conn):
    _insert_deal(conn, "foodlion", "16 oz. Boneless Skinless Chicken Breast", dollar_price=3.99)
    matching.match_deals(conn=conn)
    curated = savings.cost_per_gram_protein(
        3.99, "16 oz. Boneless Skinless Chicken Breast", "foodlion", conn=conn
    )
    assert curated.protein_source == "curated"

    usda_food_id = _insert_food(
        conn, "Chicken breast, boneless, skinless, raw (USDA)", "chicken",
        "usda", "12345", protein_per_100g=23.09,
    )
    _insert_deal(conn, "foodlion", "16 oz. USDA Chicken Breast", dollar_price=4.29)
    matching.correct_match("foodlion", "16 oz. USDA Chicken Breast", usda_food_id, conn=conn)

    usda = savings.cost_per_gram_protein(
        4.29, "16 oz. USDA Chicken Breast", "foodlion", conn=conn
    )
    assert usda.protein_source == "usda"


# --------------------------------------------------------------------------- #
# Store-agnostic: the same item/price/match produces the same figure no
# matter which store the deal came from.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("store", ["foodlion", "harristeeter", "wholefoods"])
def test_result_does_not_depend_on_which_store_the_deal_came_from(conn, store):
    _insert_deal(conn, store, "16 oz. Boneless Skinless Chicken Breast", dollar_price=3.99)
    matching.match_deals(conn=conn)
    result = savings.cost_per_gram_protein(
        3.99, "16 oz. Boneless Skinless Chicken Breast", store, conn=conn
    )
    assert result is not None
    assert result.cost_per_gram_protein == pytest.approx(3.99 / 104.326, rel=1e-3)


# --------------------------------------------------------------------------- #
# Ranking
# --------------------------------------------------------------------------- #
def test_rank_by_cost_per_gram_protein_orders_cheapest_first(conn):
    _insert_deal(conn, "foodlion", "16 oz. Boneless Skinless Chicken Breast", dollar_price=3.99)
    _insert_deal(conn, "foodlion", "8 oz. Boneless Skinless Chicken Breast Cutlets", dollar_price=2.99)
    _insert_deal(conn, "foodlion", "32 oz. Whey Protein Isolate Powder", dollar_price=39.99)
    matching.match_deals(conn=conn)

    rows = [
        {"store": "foodlion", "item_name": "16 oz. Boneless Skinless Chicken Breast", "dollar_price": 3.99},
        {"store": "foodlion", "item_name": "8 oz. Boneless Skinless Chicken Breast Cutlets", "dollar_price": 2.99},
        {"store": "foodlion", "item_name": "32 oz. Whey Protein Isolate Powder", "dollar_price": 39.99},
    ]
    ranked = savings.rank_by_cost_per_gram_protein(rows, conn=conn)

    assert [r["item_name"] for r in ranked] == [
        "16 oz. Boneless Skinless Chicken Breast",
        "32 oz. Whey Protein Isolate Powder",
        "8 oz. Boneless Skinless Chicken Breast Cutlets",
    ]
    assert [r["rank"] for r in ranked] == [1, 2, 3]
    prices = [r["cost_per_gram_protein"] for r in ranked]
    assert prices == sorted(prices)


def test_rank_drops_rows_with_no_computable_figure(conn):
    _insert_deal(conn, "foodlion", "16 oz. Boneless Skinless Chicken Breast", dollar_price=3.99)
    matching.match_deals(conn=conn)

    rows = [
        {"store": "foodlion", "item_name": "16 oz. Boneless Skinless Chicken Breast", "dollar_price": 3.99},
        {"store": "foodlion", "item_name": "16 oz. Eggo Frozen Waffles", "dollar_price": 4.49},  # unmatched
        {"store": "foodlion", "item_name": "8 ct. Chicken Drumsticks", "dollar_price": 5.99},  # count-based
    ]
    ranked = savings.rank_by_cost_per_gram_protein(rows, conn=conn)
    assert [r["item_name"] for r in ranked] == ["16 oz. Boneless Skinless Chicken Breast"]


def test_rank_min_confidence_excludes_weak_matches_without_hiding_them_by_default(conn):
    _insert_deal(conn, "foodlion", "16 oz. Boneless Skinless Chicken Breast", dollar_price=3.99)
    _insert_deal(conn, "foodlion", "1.5 lb. Top Round London Broil", dollar_price=8.99)
    matching.match_deals(conn=conn)

    rows = [
        {"store": "foodlion", "item_name": "16 oz. Boneless Skinless Chicken Breast", "dollar_price": 3.99},
        {"store": "foodlion", "item_name": "1.5 lb. Top Round London Broil", "dollar_price": 8.99},
    ]

    everything = savings.rank_by_cost_per_gram_protein(rows, conn=conn)
    assert {r["item_name"] for r in everything} == {
        "16 oz. Boneless Skinless Chicken Breast", "1.5 lb. Top Round London Broil",
    }
    confidences = {r["item_name"]: r["match_confidence"] for r in everything}
    assert confidences["16 oz. Boneless Skinless Chicken Breast"] == matching.CONFIDENCE_HIGH
    assert confidences["1.5 lb. Top Round London Broil"] == matching.CONFIDENCE_LOW

    filtered = savings.rank_by_cost_per_gram_protein(
        rows, conn=conn, min_confidence=matching.CONFIDENCE_MEDIUM
    )
    assert [r["item_name"] for r in filtered] == ["16 oz. Boneless Skinless Chicken Breast"]


def test_rank_respects_limit(conn):
    _insert_deal(conn, "foodlion", "16 oz. Boneless Skinless Chicken Breast", dollar_price=3.99)
    _insert_deal(conn, "foodlion", "8 oz. Boneless Skinless Chicken Breast Cutlets", dollar_price=2.99)
    matching.match_deals(conn=conn)

    rows = [
        {"store": "foodlion", "item_name": "16 oz. Boneless Skinless Chicken Breast", "dollar_price": 3.99},
        {"store": "foodlion", "item_name": "8 oz. Boneless Skinless Chicken Breast Cutlets", "dollar_price": 2.99},
    ]
    ranked = savings.rank_by_cost_per_gram_protein(rows, conn=conn, limit=1)
    assert len(ranked) == 1
    assert ranked[0]["item_name"] == "16 oz. Boneless Skinless Chicken Breast"
