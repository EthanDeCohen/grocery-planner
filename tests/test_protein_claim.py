"""Tests for GFP-69: a bare gram figure next to the word "protein" is a
nutrition claim ("Chobani 20G Protein Drinks" = 20 grams of protein per
package), not a package weight -- a drink does not weigh 20 grams.

Two things are under test:

1. :func:`grocery_planner.savings.parse_size` must stop reading that figure
   as a size, while continuing to read every genuine size exactly as before
   (this file does not touch ``tests/test_savings.py``, so a handful of the
   same style of regression checks are repeated here deliberately).
2. :func:`grocery_planner.savings.parse_protein_claim` reads the same figure
   for what it actually is, and :func:`grocery_planner.savings.cost_per_gram_protein`
   uses it directly -- with no size and no catalog food match required -- when
   no weight-based size is available. This turns three previously-unreadable
   real deals into three more accurately-priced ones (a manufacturer's own
   printed figure for the exact product beats a matched catalog estimate).
"""
from __future__ import annotations

import pytest

from grocery_planner import savings


# --------------------------------------------------------------------------- #
# parse_size: the misread is gone.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("item_name", [
    "Chobani 20G Protein Drinks",
    "Chobani 20G Protein Multiserve Greek Yogurt",
    "Chobani 20g Protein Greek Yogurt",
    "20 Grams Protein Shake",
    "Muscle Milk 25g Protein Shake",
])
def test_parse_size_no_longer_reads_a_protein_claim_as_a_size(item_name):
    assert savings.parse_size(item_name) is None


def test_parse_size_still_reads_a_real_gram_size_when_not_adjacent_to_protein():
    """'500g Whey Protein Powder' -- a word ("Whey") sits between the number
    and "Protein", so this is a real 500-gram package weight, not a claim.
    The discriminator is adjacency to the word "protein", not the unit."""
    size = savings.parse_size("500g Whey Protein Powder")
    assert size is not None
    assert size.base_unit == savings.WEIGHT
    assert size.base_quantity == pytest.approx(500 * 0.035274)


def test_parse_size_still_reads_a_real_oz_size_next_to_the_word_protein():
    """'20 oz Protein Shake Bottle' IS a real size -- the unit is oz, not g,
    so the word "protein" nearby does not disqualify it."""
    size = savings.parse_size("20 oz Protein Shake Bottle")
    assert size is not None
    assert size.quantity == 20.0
    assert size.unit == "oz"
    assert size.base_unit == savings.WEIGHT


# --------------------------------------------------------------------------- #
# parse_size: existing honesty-rule behavior must not regress (GFP-8).
# --------------------------------------------------------------------------- #
def test_parse_size_simple_ounce_size_unaffected():
    size = savings.parse_size("16 oz. Simple Truth Peanut Butter")
    assert size is not None
    assert size.quantity == 16.0
    assert size.unit == "oz"
    assert size.base_quantity == 16.0
    assert size.base_unit == savings.WEIGHT


def test_parse_size_range_takes_low_end_unaffected():
    size = savings.parse_size("5.4-5.5 Oz. Some Product")
    assert size is not None
    assert size.quantity == 5.4


def test_parse_size_multipack_unaffected():
    size = savings.parse_size("6 pk 500 ml Water")
    assert size is not None
    assert size.base_unit == savings.VOLUME
    assert size.base_quantity == pytest.approx(6 * 500 * 0.033814)


def test_parse_size_unreadable_name_still_none():
    assert savings.parse_size("Eggo Frozen Waffles") is None


def test_parse_size_or_promo_only_reads_headline_product():
    """A protein claim belonging to the SECOND item in an "A or B" promo must
    not leak into the first item's (still unreadable) size."""
    assert savings.parse_size(
        "Eggo Frozen Waffles or Chobani 20G Protein Drinks"
    ) is None


# --------------------------------------------------------------------------- #
# parse_protein_claim: reading the claim for what it is.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("item_name,expected_grams", [
    ("Chobani 20G Protein Drinks", 20.0),
    ("Chobani 20G Protein Multiserve Greek Yogurt", 20.0),
    ("Chobani 20g Protein Greek Yogurt", 20.0),
    ("20 Grams Protein Shake", 20.0),
    ("Muscle Milk 25g Protein Shake", 25.0),
])
def test_parse_protein_claim_reads_the_grams_figure(item_name, expected_grams):
    assert savings.parse_protein_claim(item_name) == expected_grams


@pytest.mark.parametrize("item_name", [
    "Eggo Frozen Waffles",
    "16 oz. Simple Truth Peanut Butter",
    "500g Whey Protein Powder",       # real size, not a claim -- rule 1 twin
    None,
    "",
])
def test_parse_protein_claim_none_when_absent(item_name):
    assert savings.parse_protein_claim(item_name) is None


def test_parse_protein_claim_only_reads_headline_products_claim():
    """Only the first product in an "A or B" promo counts (GFP-8 rule 2) --
    a claim belonging to the second product must not be borrowed by the first."""
    assert savings.parse_protein_claim(
        "Eggo Frozen Waffles or Chobani 20G Protein Drinks"
    ) is None
    assert savings.parse_protein_claim(
        "Chobani 20G Protein Drinks or Eggo Frozen Waffles"
    ) == 20.0


# --------------------------------------------------------------------------- #
# cost_per_gram_protein: the label-claim path -- more accurate than a catalog
# match, and needs neither a size nor a deal_food_match row to work.
# --------------------------------------------------------------------------- #
def test_cost_per_gram_protein_uses_the_label_claim_when_no_size_is_readable(conn):
    # Deliberately no deal insert, no matching.match_deals call, no foods row
    # at all -- the whole point is that this path needs none of that.
    result = savings.cost_per_gram_protein(
        5.0, "Chobani 20G Protein Drinks", "harristeeter", conn=conn
    )
    assert result is not None
    assert result.cost_per_gram_protein == pytest.approx(5.0 / 20.0)
    assert result.protein_grams == 20.0
    assert result.size_grams is None
    assert result.food_id is None
    assert result.food_name is None
    assert result.protein_source == savings.LABEL_CLAIM_SOURCE
    assert result.protein_source == "label"
    assert result.match_method == savings.LABEL_CLAIM_METHOD
    assert result.match_confidence == savings.LABEL_CLAIM_CONFIDENCE


def test_cost_per_gram_protein_label_claim_gives_a_plausible_number(conn):
    """The real-data regression this ticket exists for: before the fix,
    'Chobani 20G Protein Drinks' at $5.00 misread 20G as a 20-gram package
    and landed at $3.13/g -- about 17x too high. The correct reading (20
    grams of protein per package) lands at $0.25/g, in the same ballpark as
    other real protein deals rather than an order of magnitude off."""
    result = savings.cost_per_gram_protein(
        5.0, "Chobani 20G Protein Drinks", "harristeeter", conn=conn
    )
    assert result is not None
    assert result.cost_per_gram_protein == pytest.approx(0.25)
    assert result.cost_per_gram_protein < 3.0  # sanity: nowhere near the old misread


def test_cost_per_gram_protein_prefers_a_real_weight_based_size_over_the_claim(conn):
    """When a genuine weight-based size IS present, the normal size/match/
    food_nutrients chain still governs -- the label-claim path only fires
    when there is no weight-based size to work with."""
    from grocery_planner import matching

    conn.execute(
        "INSERT INTO deals(store, item_name, sub_category, deal_type, "
        "dollar_price, source) VALUES (?, ?, ?, 'Weekly Ad', ?, 'scrape')",
        ("foodlion", "16 oz. Boneless Skinless Chicken Breast", "Meat & Seafood", 3.99),
    )
    conn.commit()
    matching.match_deals(conn=conn)

    result = savings.cost_per_gram_protein(
        3.99, "16 oz. Boneless Skinless Chicken Breast", "foodlion", conn=conn
    )
    assert result is not None
    assert result.protein_source == "curated"
    assert result.size_grams is not None
    assert result.food_id is not None


def test_cost_per_gram_protein_still_none_with_neither_size_nor_claim(conn):
    assert savings.cost_per_gram_protein(
        3.99, "Boneless Skinless Chicken Breast", "foodlion", conn=conn
    ) is None


@pytest.mark.parametrize("price", [None, 0.0, -1.0])
def test_cost_per_gram_protein_label_claim_still_honors_price_guard(conn, price):
    assert savings.cost_per_gram_protein(
        price, "Chobani 20G Protein Drinks", "harristeeter", conn=conn
    ) is None


def test_rank_by_cost_per_gram_protein_includes_label_claim_rows(conn):
    rows = [
        {"store": "harristeeter", "item_name": "Chobani 20G Protein Drinks", "sale_price": 5.0},
        {"store": "harristeeter", "item_name": "Chobani 20g Protein Greek Yogurt", "sale_price": 10.0},
        {"store": "foodlion", "item_name": "Eggo Frozen Waffles", "sale_price": 4.49},  # stays undropped-in
    ]
    ranked = savings.rank_by_cost_per_gram_protein(rows, conn=conn)
    names = [r["item_name"] for r in ranked]
    assert "Chobani 20G Protein Drinks" in names
    assert "Chobani 20g Protein Greek Yogurt" in names
    assert "Eggo Frozen Waffles" not in names
    # Cheapest first: $5/20g = 0.25 before $10/20g = 0.50.
    assert names[0] == "Chobani 20G Protein Drinks"
    for row in ranked:
        assert row["protein_source"] == "label"
