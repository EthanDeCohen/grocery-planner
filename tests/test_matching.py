"""Tests for grocery_planner.matching (GFP-25): deal-to-food matching.

Item-name fixtures below are lifted verbatim from the real 1,573-row scraped
database (see the PR description for the full match-rate measurement run
against it) -- that is where the genuinely hard cases live, same rationale
as tests/test_savings.py's fixtures.
"""
from __future__ import annotations

import pytest

from grocery_planner import matching


# --------------------------------------------------------------------------- #
# headline() -- same "before the first standalone 'or'" rule as
# savings.parse_size, reproduced independently here.
# --------------------------------------------------------------------------- #
def test_headline_stops_at_the_first_or():
    assert matching.headline(
        "Boneless Pork Loin Roast or Whole Pork Tenderloin"
    ) == "Boneless Pork Loin Roast"


def test_headline_admits_when_there_is_nothing():
    assert matching.headline(None) == ""
    assert matching.headline("") == ""


# --------------------------------------------------------------------------- #
# match_item() -- the ticket's own explicit real-data examples.
# --------------------------------------------------------------------------- #
def test_lean_ground_beef_matches_ground_beef():
    result = matching.match_item("73% Lean Fresh Ground Beef")
    assert result is not None
    assert result.source_ref == "beef-ground-80-20"  # nearest of 80/93
    assert result.method == "lean_pct_nearest"
    assert result.confidence == matching.CONFIDENCE_MEDIUM


def test_ground_round_is_recognized_as_a_beef_cut_without_the_word_beef():
    """'Round' is a beef cut -- the ticket's own example of a case the
    matcher must handle even though the headline never says 'beef'."""
    result = matching.match_item("85% Lean Fresh Ground Round")
    assert result is not None
    assert result.source_ref == "beef-ground-80-20"


def test_ground_beef_patties_is_still_ground_beef():
    """Same meat, different form (patties) -- still counts as a match."""
    result = matching.match_item("73% Lean Fresh Ground Beef Patties")
    assert result is not None
    assert result.source_ref == "beef-ground-80-20"


def test_exact_lean_percentage_is_higher_confidence_than_nearest():
    exact = matching.match_item("Verde Organic 93% Lean Ground Beef")
    assert exact.source_ref == "beef-ground-93-7"
    assert exact.method == "lean_pct_exact"
    assert exact.confidence == matching.CONFIDENCE_HIGH


@pytest.mark.parametrize("name", [
    "5.4-5.5 Oz. Select Varieties",  # size prefix, no product at all
    "7-Up or Canada Dry Products",   # an "or" alternative list, not protein
])
def test_non_product_text_never_matches(name):
    assert matching.match_item(name) is None


# --------------------------------------------------------------------------- #
# False-positive traps found in the real data -- these all contain a
# protein-sounding word but are not a human protein deal.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", [
    "Abound Chicken & Beef Variety Pack Cups Wet Dog Food",
    "Newman's Own Beef Jerky Dog Treats",
    "Nestle Drumstick Cones",              # ice cream, not chicken
    "Nestlé Drumstick Cones",
    "Simple Truth Organic Chicken Broth",  # broth, not chicken meat
    "Arm & Hammer Slide or Double Duty Litter",
    "Batiste Dry Shampoo",
    "Rotisserie Chicken Salad",            # mayo-based, not raw chicken
])
def test_non_food_and_prepared_traps_are_excluded(name):
    assert matching.match_item(name) is None


# --------------------------------------------------------------------------- #
# "Only the headline counts" for matching too, not just size parsing.
# --------------------------------------------------------------------------- #
def test_or_alternative_only_matches_the_headline_product():
    """The headline is pork loin, not the tenderloin named after 'or'."""
    result = matching.match_item("Boneless Pork Loin Roast or Whole Pork Tenderloin")
    assert result.source_ref == "pork-loin-chop"
    assert result.method == "loin_proxy"


def test_or_alternative_with_a_high_confidence_headline_cut():
    result = matching.match_item("Villari Prime Pork Chops or Ribeye Chops")
    assert result.source_ref == "pork-loin-chop"
    assert result.confidence == matching.CONFIDENCE_HIGH


def test_tuna_or_salmon_matches_the_headline_species():
    result = matching.match_item(
        "Wild Caught Tuna Steaks or Fresh Farm Raised Salmon Fillets"
    )
    assert result.source_ref == "tuna-yellowfin"


# --------------------------------------------------------------------------- #
# Category coverage, one representative real example per cut.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name,source_ref,confidence", [
    ("Boneless Ribeye Steak", "beef-ribeye-steak", matching.CONFIDENCE_HIGH),
    ("Beef Brisket, raw", "beef-brisket", matching.CONFIDENCE_HIGH),
    ("Farmer Focus Boneless Chicken Thighs", "chicken-thigh-skinless-boneless", matching.CONFIDENCE_HIGH),
    ("Chicken Drumsticks", "chicken-drumstick", matching.CONFIDENCE_HIGH),
    ("Chicken Wings", "chicken-wing", matching.CONFIDENCE_HIGH),
    ("Family Size Rotisserie Chicken", "chicken-whole", matching.CONFIDENCE_MEDIUM),
    ("Niman Ranch Antibiotic Free Boneless Pork Chops", "pork-loin-chop", matching.CONFIDENCE_HIGH),
    ("Smithfield Boneless Pork Tenderloin", "pork-tenderloin", matching.CONFIDENCE_HIGH),
    ("Food Lion Sliced Bacon", "bacon", matching.CONFIDENCE_HIGH),
    ("Alaska Sockeye Salmon Fillets", "salmon-atlantic", matching.CONFIDENCE_HIGH),
    ("ACME Everything Bagel Smoked Nova Salmon", "salmon-atlantic", matching.CONFIDENCE_MEDIUM),
    ("Fishermans Market Farm Raised 21 - 30 ct. EZ Peel White Shrimp", "shrimp", matching.CONFIDENCE_HIGH),
    ("Tuna Steaks", "tuna-yellowfin", matching.CONFIDENCE_HIGH),
    ("Tofu, extra firm", "tofu-extra-firm", matching.CONFIDENCE_HIGH),
    ("Whey Protein Isolate Powder", "whey-protein-isolate", matching.CONFIDENCE_HIGH),
])
def test_category_cut_examples(name, source_ref, confidence):
    result = matching.match_item(name)
    assert result is not None, name
    assert result.source_ref == source_ref
    assert result.confidence == confidence


def test_generic_beef_without_a_specific_cut_is_a_low_confidence_fallback():
    result = matching.match_item("Top Round London Broil")
    assert result is not None
    assert result.confidence == matching.CONFIDENCE_LOW
    assert result.method == "category_fallback"


def test_protein_shake_without_the_word_whey_is_low_confidence():
    """We don't actually know the protein source -- surfaced, but weakly."""
    result = matching.match_item("Quest Protein Shakes")
    assert result is not None
    assert result.confidence == matching.CONFIDENCE_LOW
    assert result.method == "protein_uncertain_source"


def test_protein_fortified_snacks_do_not_match_whey():
    """Cereal/bars/crackers being 'protein'-branded isn't a whey deal."""
    for name in [
        "GENERAL MILLS CHEERIOS PROTEIN CEREAL",
        "Barebells Protein Bars",
        "Simple Truth Protein Crisp Crackers",
    ]:
        assert matching.match_item(name) is None, name


# --------------------------------------------------------------------------- #
# match_deals() -- the persistence layer.
# --------------------------------------------------------------------------- #
def _insert_deal(conn, store, item_name, sub_category="Meat & Seafood"):
    conn.execute(
        "INSERT INTO deals(store, item_name, sub_category, deal_type, source) "
        "VALUES (?, ?, ?, 'Weekly Ad', 'scrape')",
        (store, item_name, sub_category),
    )
    conn.commit()


def test_match_deals_persists_matches_and_reports_counts(conn):
    _insert_deal(conn, "foodlion", "73% Lean Fresh Ground Beef")
    _insert_deal(conn, "foodlion", "5.4-5.5 Oz. Select Varieties")  # unmatched
    _insert_deal(conn, "harristeeter", "Chicken Drumsticks")

    stats = matching.match_deals(conn=conn)
    assert stats["distinct_items"] == 3
    assert stats["matched"] == 2
    assert stats["unmatched"] == 1
    assert stats["by_confidence"]["medium"] == 1  # lean_pct_nearest
    assert stats["by_confidence"]["high"] == 1    # drumsticks

    row = matching.get_match("harristeeter", "Chicken Drumsticks", conn=conn)
    assert row is not None
    assert row["match_source"] == "auto"
    food = conn.execute(
        "SELECT name FROM foods WHERE id=?", (row["food_id"],)
    ).fetchone()
    assert food["name"] == "Chicken drumstick, raw"


def test_match_deals_is_keyed_on_store_and_item_name_not_deal_id():
    """Duplicate (store, item_name) rows -- which genuinely occur in the real
    data (e.g. the same 'Pork Chops' recurring across scrapes under
    different deals.id) -- collapse into a single match, not one per row."""
    import grocery_planner.db as db_module

    conn = db_module.connect(":memory:")
    _insert_deal(conn, "foodlion", "Pork Chops")
    _insert_deal(conn, "foodlion", "Pork Chops")
    _insert_deal(conn, "foodlion", "Pork Chops")

    stats = matching.match_deals(conn=conn)
    assert stats["distinct_items"] == 1
    assert stats["matched"] == 1
    count = conn.execute("SELECT COUNT(*) FROM deal_food_match").fetchone()[0]
    assert count == 1


def test_match_deals_defaults_to_db_connect(monkeypatch, tmp_path):
    # No conn passed -> falls back to db.connect(), matching the
    # service/deals.py / nutrition.py convention.
    monkeypatch.setenv("GROCERY_PLANNER_DB", str(tmp_path / "default.sqlite3"))
    stats = matching.match_deals()
    assert stats["distinct_items"] == 0


# --------------------------------------------------------------------------- #
# Corrections: keyed on (store, item_name), survive being re-matched, and
# are distinguishable from "unmatched".
# --------------------------------------------------------------------------- #
def test_correct_match_overrides_the_automatic_result(conn):
    _insert_deal(conn, "foodlion", "Top Round London Broil")  # low-confidence auto match
    matching.match_deals(conn=conn)

    sirloin = conn.execute(
        "SELECT id FROM foods WHERE source_ref='beef-sirloin-steak'"
    ).fetchone()["id"]
    chuck = conn.execute(
        "SELECT id FROM foods WHERE source_ref='beef-chuck-roast'"
    ).fetchone()["id"]
    before = matching.get_match("foodlion", "Top Round London Broil", conn=conn)
    assert before["food_id"] == sirloin  # the low-confidence auto guess

    matching.correct_match("foodlion", "Top Round London Broil", chuck, conn=conn)
    after = matching.get_match("foodlion", "Top Round London Broil", conn=conn)
    assert after["food_id"] == chuck
    assert after["match_source"] == "manual"
    assert after["confidence"] == 1.0


def test_rerunning_match_deals_never_overwrites_a_manual_correction(conn):
    _insert_deal(conn, "foodlion", "Top Round London Broil")
    matching.match_deals(conn=conn)

    chuck = conn.execute(
        "SELECT id FROM foods WHERE source_ref='beef-chuck-roast'"
    ).fetchone()["id"]
    matching.correct_match("foodlion", "Top Round London Broil", chuck, conn=conn)

    # Simulate a rescrape re-running the matcher.
    stats = matching.match_deals(conn=conn)
    assert stats["skipped_manual"] == 1

    row = matching.get_match("foodlion", "Top Round London Broil", conn=conn)
    assert row["food_id"] == chuck
    assert row["match_source"] == "manual"


def test_correction_survives_a_rescrape_that_deletes_and_reinserts_deal_rows(conn):
    """The scenario the ticket calls out by name: service/ingest.run_scrape()
    deletes and reinserts deals rows (with new autoincrement ids) on every
    scrape. A correction keyed on (store, item_name) must not care."""
    _insert_deal(conn, "foodlion", "Pork Chops")
    original_id = conn.execute(
        "SELECT id FROM deals WHERE item_name='Pork Chops'"
    ).fetchone()["id"]

    loin_chop = conn.execute(
        "SELECT id FROM foods WHERE source_ref='pork-loin-chop'"
    ).fetchone()["id"]
    matching.correct_match("foodlion", "Pork Chops", loin_chop, conn=conn)

    # Simulate run_scrape()'s DELETE + reinsert: same store/item_name, a
    # brand new deals.id.
    conn.execute("DELETE FROM deals WHERE store='foodlion' AND item_name='Pork Chops'")
    _insert_deal(conn, "foodlion", "Pork Chops")
    new_id = conn.execute(
        "SELECT id FROM deals WHERE item_name='Pork Chops'"
    ).fetchone()["id"]
    assert new_id != original_id

    row = matching.get_match("foodlion", "Pork Chops", conn=conn)
    assert row is not None
    assert row["food_id"] == loin_chop
    assert row["match_source"] == "manual"


def test_reject_match_records_a_considered_no_match(conn):
    _insert_deal(conn, "foodlion", "Family Size Rotisserie Chicken")
    matching.match_deals(conn=conn)
    before = matching.get_match("foodlion", "Family Size Rotisserie Chicken", conn=conn)
    assert before is not None  # auto-matched (whole chicken, medium confidence)

    matching.reject_match("foodlion", "Family Size Rotisserie Chicken", conn=conn)
    after = matching.get_match("foodlion", "Family Size Rotisserie Chicken", conn=conn)
    assert after["food_id"] is None
    assert after["match_source"] == "manual"
    assert after["method"] == "manual_reject"

    # And a re-match must not resurrect the rejected guess.
    stats = matching.match_deals(conn=conn)
    assert stats["skipped_manual"] == 1
    still = matching.get_match("foodlion", "Family Size Rotisserie Chicken", conn=conn)
    assert still["food_id"] is None


def test_get_match_returns_none_for_an_unmatched_item(conn):
    _insert_deal(conn, "foodlion", "5.4-5.5 Oz. Select Varieties")
    matching.match_deals(conn=conn)
    assert matching.get_match("foodlion", "5.4-5.5 Oz. Select Varieties", conn=conn) is None


def test_matching_survives_a_usda_sync(tmp_path):
    """GFP-68: a food must stay matchable when its provenance improves.

    `gplan nutrition sync` supersedes a curated row in place, flipping
    `source` to 'usda' and replacing `source_ref` with the FDC id. Keying the
    match vocabulary on either of those made eight foods -- every beef cut
    plus chicken breast -- vanish the moment their protein figure got a
    better source, which is exactly backwards.
    """
    from grocery_planner import db, matching, usda

    conn = db.connect(tmp_path / "sync.sqlite3")
    conn.execute(
        "INSERT INTO deals(store, item_name, sub_category, source) "
        "VALUES ('foodlion', '73% Lean Fresh Ground Beef', 'Meat & Seafood', 'scrape')"
    )
    conn.commit()

    matching.match_deals(conn=conn)
    assert conn.execute("SELECT COUNT(*) FROM deal_food_match").fetchone()[0] == 1

    usda.sync(conn=conn)
    conn.execute("DELETE FROM deal_food_match")
    matching.match_deals(conn=conn)

    row = conn.execute(
        "SELECT f.source FROM deal_food_match m JOIN foods f ON f.id = m.food_id"
    ).fetchone()
    assert row is not None, "match was lost when the food gained a USDA source"
    assert row["source"] == "usda"
    conn.close()


# --------------------------------------------------------------------------- #
# GFP-279: the matcher and protein_kind must share ONE vocabulary.
#
# Before this, `matching._EXCLUDE` and `protein_kind.DISQUALIFIERS` were two
# hand-maintained lists of "not a cut of meat", and they had drifted. _EXCLUDE
# had `broth` but not `stock`, so measured live on 2026-08-12:
#
#   "Hanover Brown Sugar & Bacon Baked Beans, 16 oz" -> 'Bacon, raw'  conf 0.9
#   "beef cooking stock 3G Protein, 32 oz"           -> beef sirloin  conf 0.3
#
# The beans row inherited BACON's protein density at 0.9 confidence. Confidence
# was never the issue -- the matcher was very sure and wrong about the kind.
# --------------------------------------------------------------------------- #
from grocery_planner import protein_kind as pk           # noqa: E402


@pytest.mark.parametrize("name", [
    # The row measured live. (The other one, "Hanover ... Bacon Baked Beans",
    # moved to test_plant_protein_never_matches_a_meat_food when GFP-295 gave
    # legumes a kind instead of a veto -- same guarantee, different mechanism.)
    "beef cooking stock 3G Protein, 32 oz",
    # One per disqualifier family, so a family losing its veto is caught.
    "Swanson Chicken Broth, 48 oz",              # broth
    "Knorr Beef Bouillon Cubes",                 # bouillon
    "Campbell's Beef Consomme, 10.5 oz",         # consomme
    "Heinz Turkey Gravy, 12 oz",                 # gravy
    "Montreal Steak Seasoning, 3 oz",            # seasoning
    "Beef Flavored Ramen Noodles, 3 oz",         # form
    "Beefsteak Tomato, 2 lb",                    # produce wearing a beef name
])
def test_the_matcher_honours_every_protein_kind_disqualifier(name):
    """The RELATIONSHIP, not a spelling (GFP-179).

    Asserting the precondition first is what makes this a relationship test: it
    says "protein_kind rejects this, THEREFORE the matcher must". Add a word to
    DISQUALIFIERS and the matcher inherits the veto with no second edit; remove
    the matcher's call to it and every case here fails at once.
    """
    assert pk.is_disqualified(name) is True
    assert matching.match_item(name) is None


@pytest.mark.parametrize("name", [
    "Hanover Brown Sugar & Bacon Baked Beans, 16 oz",
    "Bush's Best Pork and Beans, 28 oz",
    "Beyond Burger Plant-Based Patties, 8 oz",
])
def test_plant_protein_never_matches_a_meat_food(name):
    """Same guarantee as the veto above, reached a different way (GFP-295).

    These used to be DISQUALIFIERS -- the matcher refused them outright. They
    are real protein, so they now classify as `plant` instead. The protection
    that mattered is unchanged: `plant` maps only to the tofu matcher, so a tin
    of beans still resolves to nothing rather than to somebody's cheapest pork.
    """
    assert pk.classify(name) == "plant"
    assert pk.classify(name) not in pk.MEAT_KINDS
    assert matching.match_item(name) is None


def test_real_meat_still_matches_after_the_veto():
    """The other half of the guard: a veto that eats real rows is worse.

    This is the `rub`/`popcorn` lesson -- a first attempt at disqualifiers threw
    away "Dry-Rub Seasoned Chicken Thighs" and "Popcorn Shrimp".
    """
    for name in (
        "Boneless Skinless Chicken Breast, 3 lb",
        "Ground Beef 80/20, 1 lb",
        "Pork Loin Chops, Family Pack",
    ):
        assert matching.match_item(name) is not None, name


# --------------------------------------------------------------------------- #
# GFP-280 / GFP-285: every species protein_kind can name must be ACCOUNTED FOR.
#
# This test exists because its absence cost a real regression. Routing species
# through protein_kind, I assumed shellfish had no curated food and listed it as
# unmatchable -- but `shrimp` exists and `_match_fish` handles it, so every
# shrimp deal silently stopped matching. One test caught it; nothing else would
# have, because declining looks identical to "we never had that data".
# --------------------------------------------------------------------------- #
def test_every_protein_kind_is_either_matchable_or_declared_unmatchable():
    """A new kind cannot be added without deciding what it matches to.

    Asserts a RELATIONSHIP between two modules (GFP-179): add "duck" to
    protein_kind's KIND_RULES and this fails until someone either points it at a
    curated food or states, deliberately, that there is not one. Silence stops
    being an option, which is the property that was missing.
    """
    accounted = set(matching._CUT_MATCHER_FOR_KIND) | matching._KINDS_WITHOUT_A_CURATED_FOOD
    unaccounted = set(pk.KINDS) - accounted
    assert not unaccounted, (
        f"protein_kind can return {sorted(unaccounted)}, and matching neither "
        "maps them to a cut matcher nor declares them unmatchable"
    )


def test_the_unmatchable_kinds_really_have_no_curated_food(conn):
    """The other direction: nothing is declared unmatchable while a food exists.

    Without this, `_KINDS_WITHOUT_A_CURATED_FOOD` becomes a place to silence an
    awkward species rather than a record of a genuine catalog gap -- which is
    the mistake this pair of tests was written after making.
    """
    # DERIVED from the name, not read from foods.protein_kind. That column is
    # NULL until `classify_all` runs, and it does not run in the test fixture --
    # so reading it made this test pass against an empty set, i.e. pass while
    # asserting nothing. It was written that way first and did NOT catch the
    # shellfish regression above, which is the whole reason it now computes.
    rows = conn.execute("SELECT name, category FROM foods").fetchall()
    have_a_food = {pk.classify(r["name"], r["category"]) for r in rows}
    wrongly_declared = matching._KINDS_WITHOUT_A_CURATED_FOOD & have_a_food
    assert not wrongly_declared, (
        f"{sorted(wrongly_declared)} are declared unmatchable but the catalog "
        "has a food for them -- those deals are declining for no reason"
    )
