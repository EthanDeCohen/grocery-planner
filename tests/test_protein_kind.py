"""GFP-106: which animal a protein food is.

The interesting tests here are the traps. A substring match gets every one of
them wrong, and each was either named in the ticket or found by auditing the
real 640-food catalog rather than by reading the code.
"""
from __future__ import annotations

import pytest

from grocery_planner import nutrition, protein_kind as pk


# --------------------------------------------------------------------------- #
# The traps named in the ticket
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name, expected", [
    # 'beef' appears, but this is produce.
    ("Beefsteak Tomato", pk.OTHER),
    ("Fresh Beefsteak Tomatoes, 2 lb", pk.OTHER),
    # A brand that names the wrong animal entirely.
    ("Chicken of the Sea Chunk Light Tuna in Water", "fish"),
    ("Chicken of the Sea Pink Salmon", "fish"),
    # The more specific bird must beat the generic cured-pork word.
    ("Butterball Turkey Bacon, Lower Sodium", "turkey"),
    ("Turkey Sausage Links", "turkey"),
    ("Chicken Sausage, Sweet Italian", "chicken"),
    # Flavouring, not a cut.
    ("Beef Flavored Ramen Noodles", pk.OTHER),
    ("Chicken Broth, Low Sodium", pk.OTHER),
    ("Organic Bone Broth", pk.OTHER),
    ("Beef Bouillon Cubes", pk.OTHER),
    ("Chicken Gravy Mix", pk.OTHER),
    ("Steak Seasoning", pk.OTHER),
    # Pet food.
    ("Purina Dog Food Beef Dinner", pk.OTHER),
])
def test_the_named_traps(name, expected):
    assert pk.classify(name) == expected


# --------------------------------------------------------------------------- #
# Traps found by auditing the live catalog -- each was a REAL misclassification
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name, category, expected", [
    # Bread filed under 'Meat' by the store catalog. 'hamburger' matched it.
    ("Pepperidge Farm Golden Potato Hamburger Buns", "Meat", pk.OTHER),
    ("Wonder White Hamburger Buns", "Meat", pk.OTHER),
    # ...but a burger that is actually a burger still resolves.
    ("Prime Pub Burger", "Meat", "beef"),
    ("Schweid & Sons Chuck & Ribeye Ground Beef Patties", "Meat", "beef"),
    # A bakery word inside a real meat product must NOT veto it. Both of these
    # were thrown away by a first-pass absolute bakery rule.
    ("Shady Brook Farms 85% Lean Ground Turkey Roll, Frozen", "Meat", "turkey"),
    ("Acme Smoked Fish Everything Bagel Smoked Salmon 3 oz", "Seafood", "fish"),
    # A preparation is not a product form: 'Dry-Rub' and 'Popcorn' are how the
    # meat was cooked, not what it is.
    ("Easy Street Korean BBQ Pre-Diced, Dry-Rub Seasoned Chicken Thighs", "Meat", "chicken"),
    ("Gorton's Popcorn Shrimp, Whole Tail-Off Shrimp", "Seafood", "shellfish"),
    # Plant-based analogues borrow meat words on purpose. They are protein, but
    # letting one win a MEAT ranking would answer the question wrongly.
    ("Beyond Burger® Plant-Based Patties", "Meat", pk.OTHER),
    ("Lightlife Smart Bacon® Plant-Based Vegan Bacon Strips", "Meat", pk.OTHER),
    ("Impossible Sausage Made From Plants", "Meat", pk.OTHER),
    # Cooked IN beef fat; it is still fries.
    ("Jesse & Ben's Grass-Fed Beef Tallow House-Cut Fries", "Meat", pk.OTHER),
])
def test_traps_found_by_auditing_the_real_catalog(name, category, expected):
    assert pk.classify(name, category) == expected


def test_ham_does_not_match_words_that_merely_contain_it():
    """`\\bham\\b` must not fire on 'hamburger' or 'graham'."""
    assert pk.classify("Graham Crackers") == pk.OTHER
    assert pk.classify("Honey Baked Ham, Spiral Cut", "Meat") == "pork"


# --------------------------------------------------------------------------- #
# Category handling
# --------------------------------------------------------------------------- #
def test_a_non_meat_category_settles_it_before_the_name_is_read():
    """A supplement is not a cut of beef however its flavour is branded."""
    assert pk.classify("Beef Protein Isolate Powder", "Supplements") == pk.OTHER
    assert pk.classify("Chicken Flavor Whey", "Whey") == pk.OTHER
    assert pk.classify("Eggland's Best Large Eggs", "Dairy") == pk.OTHER


def test_usda_lowercase_categories_are_trusted_when_the_name_says_nothing():
    """GFP-24's rows already name the kind; no name parsing needed."""
    assert pk.classify("Sirloin, raw", "beef") == "beef"
    assert pk.classify("Something unparseable", "chicken") == "chicken"


def test_the_name_beats_a_coarse_category_because_it_is_more_specific():
    """A real bug: a row categorised 'fish' but named 'Shrimp Salad' is shellfish."""
    assert pk.classify("Chesapeake Bay Shrimp Salad", "fish") == "shellfish"
    assert pk.classify("365 Patagonian Sea Scallops", "fish") == "shellfish"


def test_a_meat_category_with_an_unreadable_name_is_unknown_not_a_guess():
    """savings.py rule 1: a mislabelled cut is worse than an unlabelled one."""
    assert pk.classify("Store Brand Family Value Pack", "Meat") == pk.UNKNOWN


def test_no_category_and_no_signal_is_unknown():
    assert pk.classify("Mystery Item", None) == pk.UNKNOWN


# --------------------------------------------------------------------------- #
# Store-agnosticism (GFP-32)
# --------------------------------------------------------------------------- #
def test_classification_never_depends_on_a_store():
    """classify() takes no store argument at all -- a name is a name."""
    import inspect

    assert "store" not in inspect.signature(pk.classify).parameters


# --------------------------------------------------------------------------- #
# Persistence: four states, and cheap re-runs
# --------------------------------------------------------------------------- #
def _add(conn, name, category):
    cur = conn.execute(
        "INSERT INTO foods(name, slug, category, source) VALUES (?, ?, ?, 'test')",
        (name, name.lower().replace(" ", "-")[:60], category),
    )
    conn.commit()
    return int(cur.lastrowid)


def test_classify_all_fills_the_column_and_reports_what_it_wrote(conn):
    _add(conn, "Boneless Chicken Breast", "Meat")
    _add(conn, "Hamburger Buns", "Meat")

    written = pk.classify_all(conn)
    assert written.get("chicken", 0) >= 1
    assert written.get(pk.OTHER, 0) >= 1
    assert conn.execute(
        "SELECT COUNT(*) FROM foods WHERE protein_kind IS NULL"
    ).fetchone()[0] == 0


def test_a_second_run_does_nothing_because_unknown_is_stored_not_left_null(conn):
    """This is the whole reason UNKNOWN is a value rather than NULL."""
    _add(conn, "Store Brand Value Pack", "Meat")
    pk.classify_all(conn)
    assert conn.execute(
        "SELECT protein_kind FROM foods WHERE name='Store Brand Value Pack'"
    ).fetchone()[0] == pk.UNKNOWN

    assert pk.classify_all(conn) == {}      # nothing left with a NULL
    assert pk.ensure_classified(conn) == 0


def test_reclassify_redoes_everything(conn):
    _add(conn, "Boneless Chicken Breast", "Meat")
    pk.classify_all(conn)
    again = pk.classify_all(conn, reclassify=True)
    assert sum(again.values()) == conn.execute("SELECT COUNT(*) FROM foods").fetchone()[0]


def test_ensure_classified_picks_up_a_food_added_later(conn):
    pk.classify_all(conn)
    _add(conn, "Fresh Atlantic Salmon Fillet", "Seafood")

    assert pk.ensure_classified(conn) >= 1
    assert conn.execute(
        "SELECT protein_kind FROM foods WHERE name='Fresh Atlantic Salmon Fillet'"
    ).fetchone()[0] == "fish"


def test_coverage_separates_never_looked_from_could_not_tell(conn):
    """Reporting them as one number hides whether the classifier ever ran."""
    _add(conn, "Store Brand Value Pack", "Meat")
    before = pk.coverage(conn)
    assert before["unclassified"] > 0 and before["unknown"] == 0

    pk.classify_all(conn)
    after = pk.coverage(conn)
    assert after["unclassified"] == 0 and after["unknown"] >= 1


# --------------------------------------------------------------------------- #
# The read side (nutrition.list_foods)
# --------------------------------------------------------------------------- #
def test_list_foods_can_filter_by_kind_which_category_cannot_do(conn):
    """The point of the whole ticket: ask 'chicken' of rows that say 'Meat'."""
    _add(conn, "Boneless Chicken Breast", "Meat")
    _add(conn, "Pork Loin Chops", "Meat")

    names = [f.name for f in nutrition.list_foods(kind="chicken", conn=conn)]
    assert "Boneless Chicken Breast" in names
    assert "Pork Loin Chops" not in names
    # ...and both still share one category, so category could not have done it.
    assert len(nutrition.list_foods(category="Meat", conn=conn)) >= 2


def test_meat_only_excludes_other_and_unknown(conn):
    _add(conn, "Boneless Chicken Breast", "Meat")
    _add(conn, "Hamburger Buns", "Meat")
    _add(conn, "Silken Tofu", "Plant Protein")

    names = [f.name for f in nutrition.list_foods(meat_only=True, conn=conn)]
    assert "Boneless Chicken Breast" in names
    assert "Hamburger Buns" not in names
    assert "Silken Tofu" not in names


def test_seafood_counts_as_meat_by_product_decision(conn):
    _add(conn, "Fresh Atlantic Salmon Fillet", "Seafood")
    _add(conn, "Raw Shrimp 21/30 CT", "Seafood")

    names = [f.name for f in nutrition.list_foods(meat_only=True, conn=conn)]
    assert "Fresh Atlantic Salmon Fillet" in names
    assert "Raw Shrimp 21/30 CT" in names
    assert {"fish", "shellfish"} <= pk.MEAT_KINDS


def test_a_kind_filter_classifies_anything_new_first(conn):
    """A food added by this morning's scrape must not be silently missing."""
    _add(conn, "Boneless Chicken Breast", "Meat")   # never classified
    # Membership, not equality: db.connect() seeds a curated catalog (GFP-50),
    # so this database is never empty.
    names = [f.name for f in nutrition.list_foods(kind="chicken", conn=conn)]
    assert "Boneless Chicken Breast" in names


def test_food_item_exposes_the_kind(conn):
    _add(conn, "Pork Loin Chops", "Meat")
    pk.classify_all(conn)
    food = nutrition.get_food("Pork Loin Chops", conn=conn)
    assert food.protein_kind == "pork"
