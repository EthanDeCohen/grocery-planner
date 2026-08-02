"""Tests for GFP-73: a protein LABEL CLAIM is per SERVING; the deal's PRICE
is per PACKAGE.

``savings.parse_protein_claim()`` (GFP-69) reads a manufacturer's own "20G
Protein" figure off ad copy for use as the numerator in
``cost_per_gram_protein()`` when no weight-based size is available. That
figure is a nutrition-facts number, and nutrition facts are always stated
per SERVING -- for a single-serve item that coincides with the package
total, but for a multi-serving container it does not, and treating it as
the package total silently overstates cost-per-gram-of-protein by the
serving count.

The real deal this ticket was opened against, straight out of the shipped
1,450-distinct-item database (measured read-only, see the PR description):
"Chobani 20G Protein Multiserve Greek Yogurt" at $10.00/each, computed
pre-fix at $0.50/g as though the tub held 20g of protein total. Its own name
says "Multiserve" -- direct evidence the tub holds more than the one
serving the label figure describes.

Four things are under test:

1. There is no field anywhere in the Flipp pipeline (Food Lion, Harris
   Teeter) that says how many servings a package holds, so
   ``cost_per_gram_protein`` must never guess a servings count out of thin
   air -- guessing upward would make an overpriced multi-serving container
   look artificially cheap, the dangerous direction.
2. Absent that count, the pre-GFP-73 number (per-serving treated as
   per-package) is kept, not "corrected" into a different guess --
   deliberately, because it *overstates* cost, the safe direction (the
   engine under-recommends rather than pushes something it shouldn't). What
   changes is the confidence attached to it, which no longer claims
   near-certainty.
3. A name that says "Multiserve" (or a spacing/hyphen variant) is stronger,
   specific evidence of the ambiguity than the generic case, and is reflected
   with a lower confidence still.
4. When a caller DOES know the servings count (``servings_per_container``),
   ``cost_per_gram_protein`` folds it in correctly (per-serving x servings)
   and returns to near-certainty -- this is the mechanism a future
   structured source (mirroring how ``scrapers/wholefoods.py`` already folds
   ``servingsPerContainer`` into its own weight-based-size path) would use.

This file does not touch ``tests/test_protein_claim.py`` (GFP-69's own
tests), ``tests/test_cost_per_gram.py``, or ``tests/test_savings.py`` --
every assertion here is new, additive coverage for GFP-73 specifically.
"""
from __future__ import annotations

import pytest

from grocery_planner import matching, savings


# --------------------------------------------------------------------------- #
# The real-data regression this ticket exists for.
# --------------------------------------------------------------------------- #
def test_multiserve_item_lands_at_the_reported_overstated_figure(conn):
    """The exact real deal: $10.00 / 20g (one serving's worth) = $0.50/g.
    That is the number this ticket says is overstated -- and it stays
    exactly that number (not silently "corrected" to a guess), per honesty
    rule 4's preserved error direction."""
    result = savings.cost_per_gram_protein(
        10.0, "Chobani 20G Protein Multiserve Greek Yogurt", "harristeeter", conn=conn
    )
    assert result is not None
    assert result.cost_per_gram_protein == pytest.approx(0.50)
    assert result.protein_grams == 20.0
    assert result.protein_source == savings.LABEL_CLAIM_SOURCE


def test_multiserve_item_is_flagged_with_lower_confidence_than_baseline_label_claim(conn):
    plain = savings.cost_per_gram_protein(
        5.0, "Chobani 20G Protein Drinks", "harristeeter", conn=conn
    )
    multiserve = savings.cost_per_gram_protein(
        10.0, "Chobani 20G Protein Multiserve Greek Yogurt", "harristeeter", conn=conn
    )
    assert plain.match_confidence == savings.LABEL_CLAIM_CONFIDENCE
    assert multiserve.match_confidence == savings.LABEL_CLAIM_MULTISERVE_CONFIDENCE
    assert multiserve.match_confidence < plain.match_confidence
    assert multiserve.match_method == savings.LABEL_CLAIM_MULTISERVE_METHOD
    assert multiserve.match_method != plain.match_method


@pytest.mark.parametrize("item_name", [
    "Chobani 20G Protein Multiserve Greek Yogurt",
    "Chobani 20G Protein Multi-Serve Greek Yogurt",
    "Chobani 20G Protein Multi Serve Greek Yogurt",
    "Chobani 20G Protein MULTISERVE Greek Yogurt",
])
def test_multiserve_signal_is_spacing_hyphen_and_case_insensitive(conn, item_name):
    result = savings.cost_per_gram_protein(10.0, item_name, "harristeeter", conn=conn)
    assert result is not None
    assert result.match_confidence == savings.LABEL_CLAIM_MULTISERVE_CONFIDENCE
    assert result.match_method == savings.LABEL_CLAIM_MULTISERVE_METHOD


def test_multiserve_word_belonging_to_the_second_promo_item_does_not_leak(conn):
    """Rule 2 (only the headline product counts) applies to the multiserve
    signal exactly as it does to the claim itself."""
    result = savings.cost_per_gram_protein(
        5.0, "Chobani 20G Protein Drinks or Some Multiserve Thing", "harristeeter", conn=conn
    )
    assert result is not None
    assert result.match_confidence == savings.LABEL_CLAIM_CONFIDENCE
    assert result.match_method == savings.LABEL_CLAIM_METHOD


# --------------------------------------------------------------------------- #
# The pre-GFP-73 confidence claim (near-certainty) is gone even for the
# baseline (non-"Multiserve") case -- the serving-vs-package question is real
# for ANY label claim, not just ones that announce it by name.
# --------------------------------------------------------------------------- #
def test_label_claim_confidence_no_longer_claims_near_certainty():
    assert savings.LABEL_CLAIM_CONFIDENCE < 1.0
    assert savings.LABEL_CLAIM_MULTISERVE_CONFIDENCE < savings.LABEL_CLAIM_CONFIDENCE


# --------------------------------------------------------------------------- #
# Known servings: the correct fold-in, once the count is actually known.
# --------------------------------------------------------------------------- #
def test_known_servings_per_container_computes_the_accurate_figure(conn):
    """This is the ticket's own worked example: a 4-serving tub's real figure
    is $10 / (20g x 4) = $0.125/g -- 4x cheaper than the $0.50/g the same
    deal lands at when servings are unknown (see the test above)."""
    result = savings.cost_per_gram_protein(
        10.0, "Chobani 20G Protein Multiserve Greek Yogurt", "harristeeter",
        conn=conn, servings_per_container=4.0,
    )
    assert result is not None
    assert result.protein_grams == pytest.approx(80.0)
    assert result.cost_per_gram_protein == pytest.approx(0.125)
    assert result.cost_per_gram_protein == pytest.approx(0.50 / 4)


def test_known_servings_per_container_restores_near_certain_confidence(conn):
    result = savings.cost_per_gram_protein(
        10.0, "Chobani 20G Protein Multiserve Greek Yogurt", "harristeeter",
        conn=conn, servings_per_container=4.0,
    )
    assert result.match_confidence == savings.LABEL_CLAIM_KNOWN_SERVINGS_CONFIDENCE
    assert result.match_method == savings.LABEL_CLAIM_KNOWN_SERVINGS_METHOD
    assert result.match_method != savings.LABEL_CLAIM_MULTISERVE_METHOD


@pytest.mark.parametrize("servings", [0.0, -1.0, None])
def test_nonpositive_or_missing_servings_falls_back_to_the_unknown_case(conn, servings):
    """A zero/negative servings count is not real information (probably a
    parsing artifact upstream) -- treat it the same as "unknown", never as
    "this package has zero/negative protein"."""
    result = savings.cost_per_gram_protein(
        10.0, "Chobani 20G Protein Multiserve Greek Yogurt", "harristeeter",
        conn=conn, servings_per_container=servings,
    )
    assert result is not None
    assert result.cost_per_gram_protein == pytest.approx(0.50)
    assert result.match_confidence == savings.LABEL_CLAIM_MULTISERVE_CONFIDENCE


def test_known_servings_is_ignored_when_a_real_weight_based_size_is_present(conn):
    """servings_per_container is only ever consulted on the label-claim path
    -- a genuine weight-based size + food match still governs exactly as
    before (GFP-26/GFP-69 unchanged), never second-guessed by this new
    parameter."""
    conn.execute(
        "INSERT INTO deals(store, item_name, sub_category, deal_type, "
        "dollar_price, source) VALUES (?, ?, ?, 'Weekly Ad', ?, 'scrape')",
        ("foodlion", "16 oz. Boneless Skinless Chicken Breast", "Meat & Seafood", 3.99),
    )
    conn.commit()
    matching.match_deals(conn=conn)

    with_servings = savings.cost_per_gram_protein(
        3.99, "16 oz. Boneless Skinless Chicken Breast", "foodlion",
        conn=conn, servings_per_container=4.0,
    )
    without_servings = savings.cost_per_gram_protein(
        3.99, "16 oz. Boneless Skinless Chicken Breast", "foodlion", conn=conn,
    )
    assert with_servings == without_servings
    assert with_servings.protein_source == "curated"


# --------------------------------------------------------------------------- #
# rank_by_cost_per_gram_protein: a row carrying its own servings_per_container
# flows straight through with no plumbing changes beyond the dict key -- no
# schema change needed to prove the wiring.
# --------------------------------------------------------------------------- #
def test_rank_by_cost_per_gram_protein_uses_a_row_level_servings_per_container(conn):
    rows = [
        {
            "store": "harristeeter",
            "item_name": "Chobani 20G Protein Multiserve Greek Yogurt",
            "sale_price": 10.0,
            "servings_per_container": 4.0,
        },
        {
            "store": "harristeeter",
            "item_name": "Chobani 20G Protein Drinks",
            "sale_price": 5.0,
            # no servings_per_container key at all -- must not raise.
        },
    ]
    ranked = savings.rank_by_cost_per_gram_protein(rows, conn=conn)
    by_name = {r["item_name"]: r for r in ranked}

    multiserve = by_name["Chobani 20G Protein Multiserve Greek Yogurt"]
    assert multiserve["cost_per_gram_protein"] == pytest.approx(0.125)
    assert multiserve["match_confidence"] == savings.LABEL_CLAIM_KNOWN_SERVINGS_CONFIDENCE

    plain = by_name["Chobani 20G Protein Drinks"]
    assert plain["cost_per_gram_protein"] == pytest.approx(0.25)
    assert plain["match_confidence"] == savings.LABEL_CLAIM_CONFIDENCE

    # Now that its true per-gram figure is known, the multiserve tub is
    # actually the cheaper of the two -- exactly the ranking inversion this
    # ticket exists to make possible.
    assert ranked[0]["item_name"] == "Chobani 20G Protein Multiserve Greek Yogurt"


# --------------------------------------------------------------------------- #
# parse_protein_claim / _label_claim_is_multiserve: unit-level checks of the
# building blocks cost_per_gram_protein relies on above.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("item_name,expected", [
    ("Chobani 20G Protein Multiserve Greek Yogurt", True),
    ("Chobani 20G Protein Multi-Serve Greek Yogurt", True),
    ("Chobani 20G Protein Multi Serve Greek Yogurt", True),
    ("Chobani 20G Protein Drinks", False),
    ("16 oz. Simple Truth Peanut Butter", False),
])
def test_label_claim_is_multiserve(item_name, expected):
    assert savings._label_claim_is_multiserve(item_name) is expected
