"""Preferences match what they say they match (GFP-134, GFP-135).

Two bugs, found together, that between them made a client's protein
preferences quietly wrong.

**GFP-134** -- ``foods.category`` holds TWO taxonomies: broad buckets ("Meat",
208 rows) beside specific kinds ("chicken", 28). GFP-52 renders a checkbox per
distinct value, so a nutritionist saw "chicken" and "Meat" as peers. Matching on
the string meant a client who ticked *chicken* did not get the cheapest chicken
in the database -- Harris Teeter Drumsticks, filed under "Meat" -- and was
priced against breast at **2.5x**.

**GFP-135** -- ``protein_kind`` read pork as beef whenever the name carried a
beef *cut* word. "Pork Boston Butt Steak" matched ``\bsteaks?\b`` before
``\bpork\b`` ever ran.

They compound: GFP-134's fix makes the filter read ``protein_kind``, so
GFP-135's wrong values would have started reaching clients. Offering pork to
someone who ticked beef is not a cosmetic error when the reason is religious or
medical.
"""
from __future__ import annotations

import pytest

from grocery_planner import nutrition
from grocery_planner.protein_kind import classify


# --------------------------------------------------------------------------- #
# GFP-134: matching across the two taxonomies
# --------------------------------------------------------------------------- #
def test_a_specific_kind_finds_food_filed_under_a_broad_bucket():
    """THE BUG. Harris Teeter Chicken Drumsticks: category "Meat", kind
    "chicken". Ticking "chicken" must find it."""
    assert nutrition.food_matches("chicken", "Meat", "chicken") is True


def test_a_broad_bucket_finds_the_specific_kinds_beneath_it():
    """The mirror of the same bug: ticking "Meat" must find the beef."""
    assert nutrition.food_matches("Meat", "beef", None) is True
    assert nutrition.food_matches("Meat", "Meat", "chicken") is True
    assert nutrition.food_matches("Seafood", "fish", None) is True


def test_the_original_exact_match_still_works():
    assert nutrition.food_matches("beef", "beef", None) is True
    assert nutrition.food_matches("Dairy", "Dairy", None) is True


def test_matching_ignores_case():
    """The data itself is inconsistent -- lowercase "beef" beside capitalised
    "Dairy" -- so the comparison cannot be case-sensitive."""
    assert nutrition.food_matches("CHICKEN", "meat", "Chicken") is True
    assert nutrition.food_matches("dairy", "Dairy", None) is True


def test_an_unrelated_category_does_not_match():
    assert nutrition.food_matches("beef", "Dairy", None) is False
    assert nutrition.food_matches("chicken", "Meat", "beef") is False


def test_a_broad_bucket_does_not_swallow_another_bucket():
    """Meat must not match Dairy just because both are broad."""
    assert nutrition.food_matches("Meat", "Dairy", None) is False
    assert nutrition.food_matches("Seafood", "Meat", "chicken") is False


def test_a_food_with_neither_column_never_matches():
    """"Cannot confirm this belongs" is not "belongs". A category-constrained
    pool must exclude it rather than guess it in -- the same rule an
    unpriceable deal already follows."""
    assert nutrition.food_matches("chicken", None, None) is False
    assert nutrition.food_matches("Meat", None, None) is False


def test_an_empty_selection_matches_nothing():
    assert nutrition.food_matches("", "Meat", "chicken") is False
    assert nutrition.food_matches("   ", "Meat", "chicken") is False


def test_no_categories_resolves_to_no_ids(conn):
    """[] means unconstrained upstream, and is never passed here as a filter."""
    assert nutrition.food_ids_in([], conn=conn) == set()


def test_food_ids_in_reads_both_columns(conn):
    """Ids are assigned by the database -- the seed data already has foods in
    it, so pinning literal ids would collide."""
    def add(name, category, kind):
        return conn.execute(
            "INSERT INTO foods(name, slug, category, protein_kind, source) "
            "VALUES (?,?,?,?, 'test')",
            (name, name.lower(), category, kind),
        ).lastrowid

    drumsticks = add("Test Drumsticks", "Meat", "chicken")
    breast = add("Test Breast", "chicken", "chicken")
    milk = add("Test Milk", "Dairy", None)
    conn.commit()

    chicken = nutrition.food_ids_in(["chicken"], conn=conn)
    assert drumsticks in chicken, "the bug: meat that IS chicken was missed"
    assert breast in chicken
    assert milk not in chicken

    meat = nutrition.food_ids_in(["Meat"], conn=conn)
    assert drumsticks in meat and breast in meat
    assert milk not in meat

    dairy = nutrition.food_ids_in(["Dairy"], conn=conn)
    assert dairy >= {milk}
    assert drumsticks not in dairy


# --------------------------------------------------------------------------- #
# GFP-135: an explicit species beats a cut word
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", [
    "Pork Boston Butt Steak Value Pack",
    "Pork Ham Steak Bnls Smoked Uncured, 8 Ounce",
    "Villari Foods Prime Boneless Pork Ribeye",
])
def test_a_pork_product_with_a_beef_cut_word_is_pork(name):
    """All three are real rows from the live database, all classified beef."""
    assert classify(name) == "pork"


@pytest.mark.parametrize("name,kind", [
    ("Beef Ribeye Steak", "beef"),
    ("Ground Beef 80/20", "beef"),
    ("Veal Cutlets", "beef"),
])
def test_an_explicit_beef_is_still_beef(name, kind):
    assert classify(name) == kind


@pytest.mark.parametrize("name", ["Ribeye Steak", "Boneless Sirloin",
                                  "Beef Brisket", "Chuck Roast"])
def test_a_cut_word_with_no_species_still_falls_to_beef(name):
    """Reaching a cut word means no animal was named, and beef is the right
    default: an unqualified "Ribeye Steak" is beef."""
    assert classify(name) == "beef"


@pytest.mark.parametrize("name,kind", [
    ("Turkey Bacon", "turkey"),          # the bird beats the cured-pork word
    ("Chicken Sausage", "chicken"),
    ("Applewood Bacon", "pork"),         # no bird named -> pork
    ("Spiral Sliced Ham", "pork"),
    ("Chicken of the Sea Chunk Light Tuna", "fish"),
    ("Lamb Shoulder Chops", "lamb"),
])
def test_every_documented_case_still_holds(name, kind):
    """The reorder must not cost any behaviour the module already documents."""
    assert classify(name) == kind


def test_the_species_rules_precede_the_cut_rules():
    """Structural: the ordering IS the fix, so a later edit that moves beef's
    cut words back above pork would reintroduce the bug silently."""
    from grocery_planner.protein_kind import KIND_RULES

    order = [kind for kind, _ in KIND_RULES]
    patterns = {i: pats for i, (_, pats) in enumerate(KIND_RULES)}

    pork_species = next(
        i for i, (k, p) in enumerate(KIND_RULES)
        if k == "pork" and any("pork" in pat for pat in p)
    )
    beef_cuts = next(
        i for i, (k, p) in enumerate(KIND_RULES)
        if k == "beef" and any("steak" in pat for pat in p)
    )
    assert pork_species < beef_cuts, (
        "beef's cut words run before pork's species word, so "
        "'Pork Boston Butt Steak' is beef again"
    )
    assert order  # keep the linter quiet about `order`/`patterns`
    assert patterns


def test_a_wrong_category_cannot_override_the_classifier():
    """The second real row: "365 Dark Ground Turkey" is filed under category
    "beef" in the live data.

    Ticking beef offered turkey. Category is not merely ambiguous here, it is
    WRONG -- so for a specific kind the classifier's opinion wins outright and
    category is not consulted at all.
    """
    assert nutrition.food_matches("beef", "beef", "turkey") is False
    assert nutrition.food_matches("turkey", "beef", "turkey") is True


def test_a_broad_bucket_still_consults_both_columns():
    """Only SPECIFIC kinds ignore category. A bucket cannot -- Dairy and
    Plant Protein have no protein_kind at all, so category is their only
    witness, and a food filed directly as "beef" belongs under Meat."""
    assert nutrition.food_matches("Meat", "beef", None) is True
    assert nutrition.food_matches("Meat", "beef", "turkey") is True


def test_a_food_with_no_kind_still_matches_on_category():
    """Dairy, Plant Protein and Supplements are never classified."""
    assert nutrition.food_matches("Dairy", "Dairy", None) is True
    assert nutrition.food_matches("Plant Protein", "Plant Protein", None) is True
