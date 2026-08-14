"""Lidl catalogue scraper (GFP-267) -- the traps, pinned as relationships.

The load-bearing one is negative: this source can read a protein figure but
CANNOT compute a protein density, because Lidl publishes no serving size and no
servings-per-container. So the tests that matter most assert what the module
refuses to do.
"""
from __future__ import annotations

import pytest

from grocery_planner import savings
from grocery_planner.scrapers import lidl_catalogue as lidl

ALT = (
    'alt="Nutrition Facts label for a product with 50 calories, 1g total fat, '
    '10g protein, and 270mg sodium per serving."'
)

PAGE = (
    "<html><head>"
    '<script type="application/ld+json">'
    '{"@context":"http://schema.org","@type":"Product","sku":"11237179",'
    '"description":"<ul><li>fully cooked</li><li>12.5 oz.</li></ul>",'
    '"name":"premium chunk chicken breast",'
    '"offers":[{"@type":"Offer","price":2.59,"priceCurrency":"USD"}]}'
    "</script></head><body><img " + ALT + "></body></html>"
)


def listing(**over):
    base = dict(
        sku="11237179",
        url="https://lidl.com/p/thing/p11237179",
        name="Premium Chunk Chicken Breast",
        price=2.59,
        size_text="12.5 oz",
        protein_per_serving=10.0,
    )
    base.update(over)
    return lidl.Listing(**base)


# --------------------------------------------------------------------------- #
# THE HEADLINE: a claim is not a density
# --------------------------------------------------------------------------- #
def test_the_scraper_writes_no_food_facts_at_all():
    """The module must not expose a food-fact upsert, because it has no density.

    Lidl gives protein per serving with NO serving size and NO servings count
    (measured: 0 of 14). Turning that into a density is GFP-73's bug, and here
    it would be worse -- an overstated density sorts to the TOP of a
    cheapest-cost-per-gram ranking, which is the number a customer acts on.
    """
    assert not hasattr(lidl, "_upsert_food_fact")
    assert not hasattr(lidl, "_FoodFact")


def test_a_row_is_a_row_not_a_row_and_a_fact():
    """kroger/sprouts return (row, fact); this returns a row alone. The shape
    itself is the guard -- there is no fact to accidentally write."""
    from datetime import datetime, timezone

    result = lidl.listing_to_row(listing(), "27401", datetime.now(timezone.utc))
    assert isinstance(result, dict)


def test_the_protein_claim_is_labelled_as_a_claim_in_notes():
    """Provenance travels with the number so nothing downstream can mistake it
    for a measured density."""
    from datetime import datetime, timezone

    row = lidl.listing_to_row(listing(), "27401", datetime.now(timezone.utc))
    assert "protein_claim_per_serving_g=10" in row["notes"]
    assert "protein_source=nutrition_label_alt_text" in row["notes"]
    assert "protein_density=not_computable_no_serving_size" in row["notes"]


def test_stats_never_claim_measured_protein():
    """`with_protein` means a density elsewhere in this codebase. It must stay
    zero here, and the claim count must have its own name."""
    _rows, _meta, stats = lidl.scrape(urls=[], client=lidl.LidlClient())
    assert stats["with_protein"] == 0
    assert "with_protein_claim" in stats


# --------------------------------------------------------------------------- #
# The engine reads both facts off item_name -- verified together, not separately
# --------------------------------------------------------------------------- #
def test_size_and_claim_both_survive_into_item_name():
    """The whole integration, asserted as a round trip through the real parsers.

    `savings.parse_size` and `savings.parse_protein_claim` each read
    `item_name`; rule 4 (GFP-69) is what stops "10G Protein" being read as a
    10-gram size. If either regressed, this breaks.
    """
    name = lidl.display_item_name(listing())
    assert savings.parse_size(name) is not None
    assert savings.parse_protein_claim(name) == 10.0


def test_a_product_without_a_claim_reads_exactly_as_before():
    """No claim in, no claim token out -- the name must not gain noise."""
    name = lidl.display_item_name(listing(protein_per_serving=None))
    assert savings.parse_protein_claim(name) is None
    assert savings.parse_size(name) is not None


def test_the_claim_is_not_duplicated_when_the_name_already_says_it():
    name = lidl.display_item_name(
        listing(name="Chobani 10G Protein Drink", protein_per_serving=10.0)
    )
    assert name.lower().count("10g protein") == 1


def test_the_size_is_not_doubled_when_the_name_already_carries_one():
    name = lidl.display_item_name(
        listing(name="Chicken Breast 12.5 oz", protein_per_serving=None)
    )
    assert name.count("12.5 oz") == 1


# --------------------------------------------------------------------------- #
# The alt-text parser: narrow on purpose
# --------------------------------------------------------------------------- #
def test_protein_is_read_from_the_nutrition_alt_text():
    assert lidl.parse_alt_text_protein(PAGE) == 10.0


@pytest.mark.parametrize(
    "html",
    [
        "<html><img alt='A photo of a chicken'></html>",
        "<html><img alt='Nutrition Facts label'></html>",   # no numbers
        "<html></html>",
        "",
    ],
)
def test_a_page_without_the_sentence_yields_none_not_a_guess(html):
    """~4 in 5 food products have no such image. Absence is the common case and
    must never be filled in."""
    assert lidl.parse_alt_text_protein(html) is None


def test_the_parser_does_not_match_a_loose_protein_mention():
    """Narrow on purpose: this figure comes from an ACCESSIBILITY string, not a
    published API, so a permissive pattern would keep returning numbers after
    the sentence is reworded -- and a wrong number is worse than none, because
    nobody notices it."""
    assert lidl.parse_alt_text_protein("<p>a great source of 25g protein</p>") is None


# --------------------------------------------------------------------------- #
# Size, dug out of prose
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "description,expected_readable",
    [
        ("<ul><li>fully cooked</li><li>12.5 oz.</li></ul>", True),
        ("<li>1 lb</li>", True),
        ("<li>64 fl. oz.</li>", True),
        ("<li>no preservatives</li>", False),
        ("", False),
        (None, False),
    ],
)
def test_size_is_recovered_from_the_description_prose(description, expected_readable):
    """Lidl has no size field; the size is a bullet in marketing copy."""
    size = lidl.parse_size_from_description(description)
    assert (size is not None) == expected_readable
    if size is not None:
        assert savings.parse_size(f"Thing, {size}") is not None


# --------------------------------------------------------------------------- #
# Page parsing
# --------------------------------------------------------------------------- #
def test_a_real_page_shape_parses_end_to_end():
    got = lidl.parse_listing("https://lidl.com/p/x/p11237179", PAGE)
    assert got.sku == "11237179"
    assert got.price == 2.59
    assert got.protein_per_serving == 10.0
    assert savings.parse_size(f"x, {got.size_text}") is not None


def test_a_page_with_no_product_json_is_none_not_an_exception():
    assert lidl.parse_listing("https://lidl.com/p/x/p1", "<html>nope</html>") is None


def test_truncating_the_page_loses_everything():
    """Pins WHY the whole page is read.

    Copying sprouts.py's 30 KB partial read produced zero rows from every
    product: Lidl's JSON-LD starts ~232 KB in and the alt text ~322 KB in. This
    asserts the failure mode directly, so nobody re-introduces the optimisation
    without seeing what it costs.
    """
    padded = "<html>" + ("x" * 200_000) + PAGE
    assert lidl.parse_listing("u", padded[:30_000]) is None
    assert lidl.parse_listing("u", padded) is not None


# --------------------------------------------------------------------------- #
# Registry and enumeration
# --------------------------------------------------------------------------- #
def test_it_does_not_collide_with_the_flipp_banner():
    """`lidl` is the weekly ad. A colliding key is silently overwritten."""
    assert lidl.SCRAPER_KEY == "lidl-catalogue"
    assert lidl.STORE_KEY == "lidl"
    assert lidl.SOURCE and lidl.SOURCE != "scrape"


def test_sku_is_read_off_the_url():
    assert lidl.sku_from_url("https://lidl.com/p/thing/p11237179") == "11237179"
    assert lidl.sku_from_url("https://lidl.com/c/category/s100") == ""


def test_food_filter_is_generous_rather_than_precise():
    """A false positive costs one request; a false negative silently drops a
    product. Those are not symmetric, so the filter leans inclusive."""
    assert lidl.looks_like_food("https://lidl.com/p/chicken-breast/p1")
    assert lidl.looks_like_food("https://lidl.com/p/organic-whole-milk/p2")
    assert not lidl.looks_like_food("https://lidl.com/p/cast-iron-dutch-oven/p3")


def test_serves_is_unknown_not_false():
    """Lidl publishes no locator this module can ask. `False` would remove a
    store the client may genuinely have; `None` is the honest third state and
    availability.py treats it permissively (GFP-257)."""
    assert lidl.serves("27401") is None


def test_readiness_needs_no_credential():
    ready, _why = lidl.readiness()
    assert ready is True
