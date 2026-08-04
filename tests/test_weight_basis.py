"""Deli vs pre-packaged by-weight markers (GFP-151 spike, GFP-152 build).

**The rule that matters most: an item we cannot classify gets the DAGGER,
never a guessed asterisk.** Same discipline as ``protein_kind`` (GFP-106) and
``savings.py``'s rule 1 -- absent stays absent. A confident "pre-packaged" on
something that turns out to be a deli counter is worse than admitting the
uncertainty, because a shopper plans around it.

Every case below is drawn from the real payloads the spike measured, so the
tests fail if the rule stops matching the data it was derived from.
"""
from __future__ import annotations

import pytest

from grocery_planner import weight_basis as wb


# --------------------------------------------------------------------------- #
# The signal the spike found: `categories`, and nothing else
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", [
    "Boar's Head Deluxe Ham",
    "Boar's Head Ovengold Roasted Turkey Breast",
    "Harris Teeter Yellow American Cheese Fresh Sliced Deli Cheese",
    "Harris Teeter Pulled Rotisserie Chicken",
])
def test_the_deli_category_confirms_a_deli_item(name):
    """All 49 'Deli' by-weight products in the sample were genuinely
    counter-cut -- zero read as packaged."""
    assert wb.classify("WEIGHT", ["Deli"], name) == wb.DELI


@pytest.mark.parametrize("name", [
    "Pork Loin Whole",
    "Harris Teeter Boneless Chicken Breast Value Pack",
    "Smart Chicken Fresh Boneless Chicken Breast",
])
def test_meat_and_seafood_confirms_a_pre_packaged_item(name):
    assert wb.classify("WEIGHT", ["Meat & Seafood"], name) == wb.PREPACKAGED


def test_a_secondary_category_does_not_change_the_answer(self=None):
    """'Natural & Organic' rides along on 13 of the sampled items."""
    assert wb.classify(
        "WEIGHT", ["Meat & Seafood", "Natural & Organic"], "Organic Chicken"
    ) == wb.PREPACKAGED


# --------------------------------------------------------------------------- #
# THE 1.7%: real deli items misfiled under Meat & Seafood
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", [
    "Boar's Head Pepperoni Fresh Sliced Deli Meat",
    "Boar's Head Italian Roasted Uncured Ham Fresh Sliced Deli Meat",
    "Private Selection Mesquite Smoked Turkey Breast Fresh Sliced Deli Meat",
])
def test_a_name_that_contradicts_the_category_yields_unknown(name):
    """THE CASE THAT SHAPED THE DESIGN. These three are real deli items the
    category files under Meat & Seafood. We cannot confirm either state, so
    the honest answer is the dagger -- not a confident 'pre-packaged'."""
    assert wb.classify("WEIGHT", ["Meat & Seafood"], name) == wb.UNKNOWN


def test_the_name_is_not_trusted_to_confirm_deli_on_its_own():
    """It only ever DOWNGRADES a packaged claim to unknown. Promoting on a
    name would import the 47% misclassification rate the spike measured."""
    assert wb.classify("WEIGHT", ["Meat & Seafood"], "Deli Meat") == wb.UNKNOWN
    assert wb.classify("WEIGHT", ["Meat & Seafood"], "Deli Meat") != wb.DELI


@pytest.mark.parametrize("name", [
    "Harris Teeter Thinly Sliced Chicken Breast",     # raw packaged cutlets
    "Carando Brown Sugar & Spice Boneless Sliced Half Ham",
    "Niman Ranch Sliced Applewood Smoked Uncured Ham",
])
def test_a_merely_sliced_name_does_not_trigger_the_dagger(name):
    """"Sliced" is why a general word list fails: 7 of the 15 names matching
    deli-ish words were packaged products. Only the retailer's own unambiguous
    phrases count."""
    assert wb.classify("WEIGHT", ["Meat & Seafood"], name) == wb.PREPACKAGED


# --------------------------------------------------------------------------- #
# Never guess
# --------------------------------------------------------------------------- #
def test_an_uncategorised_by_weight_item_is_unknown():
    assert wb.classify("WEIGHT", [], "Mystery Meat") == wb.UNKNOWN
    assert wb.classify("WEIGHT", None, "Mystery Meat") == wb.UNKNOWN


def test_an_unexpected_category_is_unknown_not_prepackaged():
    """The 7-layer bean dip: priced by weight, filed under neither. The rule
    was never measured against it, so it does not get an answer."""
    assert wb.classify(
        "WEIGHT", ["International", "Dairy", "Produce"], "7-Layer Mexican Bean Dip"
    ) == wb.UNKNOWN


# --------------------------------------------------------------------------- #
# No marker where the question does not arise
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("sold_by", ["UNIT", "EACH", "unit", None, ""])
def test_a_fixed_price_package_gets_no_marker(sold_by):
    """Marking everything would make the marker meaningless."""
    assert wb.classify(sold_by, ["Deli"], "Private Selection Deli Slices") is None


def test_none_and_unknown_are_different_things():
    """None = the question does not arise. UNKNOWN = it does and we could not
    answer. Collapsing them puts a caveat on items that need none and hides it
    on items that do."""
    assert wb.classify("UNIT", ["Deli"], "x") is None
    assert wb.classify("WEIGHT", [], "x") == wb.UNKNOWN
    assert wb.marker(None) == ""
    assert wb.marker(wb.UNKNOWN) != ""


# --------------------------------------------------------------------------- #
# Display-time fallback, so this works without a re-scrape
# --------------------------------------------------------------------------- #
def test_rows_scraped_before_this_feature_fall_to_the_dagger():
    """They carry sold_by='WEIGHT' and a NULL basis. Showing no marker would
    be wrong -- the price genuinely does depend on weight."""
    assert wb.basis_for("WEIGHT", None) == wb.UNKNOWN
    assert wb.marker(wb.basis_for("WEIGHT", None)) == "†"


def test_a_stored_classification_wins_over_the_fallback():
    assert wb.basis_for("WEIGHT", wb.DELI) == wb.DELI
    assert wb.basis_for("WEIGHT", wb.PREPACKAGED) == wb.PREPACKAGED


def test_a_source_that_never_stated_a_denomination_gets_nothing():
    """Every Flipp and csv row. "Sold by weight" and "we were not told" are
    different facts."""
    assert wb.basis_for(None, None) is None
    assert wb.marker(wb.basis_for(None, None)) == ""


def test_a_junk_stored_value_is_not_trusted():
    assert wb.basis_for("WEIGHT", "banana") == wb.UNKNOWN


# --------------------------------------------------------------------------- #
# Markers and footnotes
# --------------------------------------------------------------------------- #
def test_each_state_has_a_distinct_marker_and_footnote():
    marks = [wb.marker(s) for s in (wb.DELI, wb.PREPACKAGED, wb.UNKNOWN)]
    assert len(set(marks)) == 3
    notes = [wb.footnote(s) for s in (wb.DELI, wb.PREPACKAGED, wb.UNKNOWN)]
    assert all(notes) and len(set(notes)) == 3


def test_the_legend_shows_only_the_states_present():
    """A legend explaining three markers when one is on screen is noise, and
    noise is how a caveat stops being read."""
    only_deli = wb.footnotes_for([wb.DELI, None, None])
    assert [m for m, _ in only_deli] == ["*"]

    mixed = wb.footnotes_for([wb.UNKNOWN, wb.DELI])
    assert [m for m, _ in mixed] == ["*", "†"]      # stable order, not arrival

    assert wb.footnotes_for([None, None]) == []


def test_every_marker_survives_a_legacy_windows_console():
    """GFP-95: these strings reach the CLI as well as Qt."""
    for text in list(wb.MARKERS.values()) + list(wb.FOOTNOTES.values()):
        text.encode("cp1252")


# --------------------------------------------------------------------------- #
# Categories arrive in several shapes
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("categories", [
    ["Deli"], ("Deli",), '["Deli"]', "Deli", "Deli, Meat & Seafood",
])
def test_categories_are_read_from_any_shape(categories):
    """The value round-trips through SQLite as text and arrives from the API
    as a list."""
    assert wb.classify("WEIGHT", categories, "Ham") == wb.DELI


def test_a_malformed_category_string_does_not_raise():
    assert wb.classify("WEIGHT", "[not json", "Ham") == wb.UNKNOWN
