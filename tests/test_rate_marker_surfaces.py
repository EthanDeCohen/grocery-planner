"""The double dagger reaches EVERY surface that lists an item (GFP-270).

The requirement is "if it is listed anywhere in the app, it carries the
marker", and the failure mode is not that a function returns the wrong glyph --
``test_parsebot_sources.py`` covers that. It is that someone adds a fifth place
where items are listed and does not know this rule exists.

So these tests drive the real renderers and assert the glyph comes out the far
end. When this file fails, the answer is almost always "a new surface needs the
marker", not "the marker is broken".

Why it matters concretely: a Publix row reads ``$2.39``. On the strip, in the
bill, and on the printed list that figure is the price of ONE POUND, not of the
day's protein. The marker is the only thing on the page that says so.
"""
from __future__ import annotations

import pytest

from grocery_planner import weight_basis
from grocery_planner.service import shoppingfmt
from grocery_planner.service.cheapest import CheapestProtein
from grocery_planner.service.shopping import GroceryItem, GroceryList

DAGGER = weight_basis.MARKERS[weight_basis.RATE]


def rate_shopping_line(**over) -> GroceryItem:
    base = dict(
        store="publix", store_label="Publix",
        item_name="Publix Chicken Thighs, USDA Grade A, 1 lb",
        food_name="chicken thigh", quantity=1.5, quantity_unit="lb",
        estimated_cost=3.59, shelf_price=2.39, grams_protein=136.0,
        product_identifier="96001", product_identifier_ns="publix.item_id",
        source_url="https://www.publix.com/pd/x", sold_by="WEIGHT",
        weight_basis=weight_basis.RATE, deal_id=1,
    )
    base.update(over)
    return GroceryItem(**base)


def a_list(*lines) -> GroceryList:
    """A GroceryList over ``lines``, built by keyword so a new field on the
    dataclass fails loudly here rather than silently dropping the marker."""
    return GroceryList(
        client_name="Test", days=7, generated_on="2026-08-12",
        target_grams_per_day=180.0, items=list(lines),
    )


# --------------------------------------------------------------------------- #
# The printed list -- the surface that leaves the screen
# --------------------------------------------------------------------------- #
def test_the_printed_list_carries_the_dagger_on_the_item():
    text = shoppingfmt.to_text(a_list(rate_shopping_line()))
    assert DAGGER in text
    item_line = next(l for l in text.splitlines() if "Chicken Thighs" in l)
    assert item_line.rstrip().endswith(DAGGER), (
        "the marker must sit on the item name, where a shopper reads it"
    )


def test_the_printed_list_explains_the_dagger():
    text = shoppingfmt.to_text(a_list(rate_shopping_line()))
    assert "per pound" in text.lower(), "a glyph with no legend is a mystery"


def test_the_printed_list_stays_clean_when_nothing_is_rated():
    text = shoppingfmt.to_text(
        a_list(rate_shopping_line(sold_by="UNIT", weight_basis=None))
    )
    assert DAGGER not in text
    assert "per pound" not in text.lower(), (
        "explaining a marker that is not on the page is the noise that makes "
        "people stop reading footnotes"
    )


def test_the_html_list_carries_and_explains_the_dagger():
    page = shoppingfmt.to_html(a_list(rate_shopping_line()))
    assert DAGGER in page
    assert "per pound" in page.lower()


def test_the_csv_carries_the_basis_as_data_not_as_a_glyph():
    """A spreadsheet cannot filter on a dagger."""
    csv_text = shoppingfmt.to_csv(a_list(rate_shopping_line()))
    header, row = csv_text.splitlines()[0], csv_text.splitlines()[1]
    assert "weight_basis" in header
    assert weight_basis.RATE in row
    assert DAGGER not in csv_text


# --------------------------------------------------------------------------- #
# The cheapest-protein strip -- the first thing read on screen
# --------------------------------------------------------------------------- #
def test_the_cheapest_strip_carries_the_dagger():
    from grocery_planner.gui import cheapest as strip

    item = CheapestProtein(
        store="publix", label="Publix",
        item_name="Publix Chicken Thighs, USDA Grade A, 1 lb",
        kind="chicken", cost_per_gram_protein=0.0263, price=2.39,
        protein_grams=90.7, sold_by="WEIGHT",
        weight_basis=weight_basis.RATE,
        price_per_unit=2.39, price_per_unit_uom="lb",
        source_url="https://www.publix.com/pd/x", product_identifier="96001",
    )
    assert DAGGER in strip.describe(item)


def test_the_strip_marks_a_rate_even_with_no_product_link():
    """The link is optional; the marker is not."""
    from grocery_planner.gui import cheapest as strip

    item = CheapestProtein(
        store="publix", label="Publix", item_name="Publix Chicken Thighs, 1 lb",
        kind="chicken", cost_per_gram_protein=0.0263, price=2.39,
        protein_grams=90.7, sold_by="WEIGHT", weight_basis=weight_basis.RATE,
        source_url=None,
    )
    assert DAGGER in strip.describe(item)


def test_the_strip_carries_the_basis_field_at_all():
    """Guards the plumbing, not the glyph: the field has to survive the query.

    Without it every by-weight row in the strip falls back to UNKNOWN (†),
    which is a weaker claim than the truth and therefore a wrong one.
    """
    assert "weight_basis" in CheapestProtein.__dataclass_fields__


# --------------------------------------------------------------------------- #
# The rule itself
# --------------------------------------------------------------------------- #
#: Looked up rather than spelled -- see the note in test_parsebot_sources.
@pytest.mark.parametrize("sold_by,stored", [
    ("WEIGHT", weight_basis.RATE),
    ("WEIGHT", weight_basis.PREPACKAGED),
    ("WEIGHT", weight_basis.DELI),
    ("WEIGHT", None),               # by weight, kind unestablished -> UNKNOWN
    ("UNIT", None),                 # fixed package, no question to answer
    (None, None),                   # source never stated a denomination
])
def test_every_state_renders_exactly_one_marker(sold_by, stored):
    basis = weight_basis.basis_for(sold_by, stored)
    expected = weight_basis.MARKERS.get(basis or "", "")
    assert weight_basis.marker(basis) == expected


def test_no_marker_is_a_prefix_of_another():
    """The duplication that mattered. "Chicken**" could be read as prepackaged
    OR as deli marked twice, and in Markdown it just turns the name bold."""
    glyphs = list(weight_basis.MARKERS.values())
    for one in glyphs:
        for other in glyphs:
            if one != other:
                assert not other.startswith(one), f"{one!r} prefixes {other!r}"


def test_a_stored_rate_is_never_downgraded_to_unknown():
    """`basis_for` prefers the stored classification over the fallback. If that
    order ever flipped, every Publix row would silently become a † -- the same
    figure, described as less provisional than it is."""
    assert weight_basis.basis_for("WEIGHT", weight_basis.RATE) == weight_basis.RATE
