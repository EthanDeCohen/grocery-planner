"""Sprouts storefront scraper -- the traps, pinned as relationships.

Every test here exists because the live catalogue actually contains the shape
it describes. They deliberately assert *relationships* rather than magic
numbers or exact spellings, so that a refactor which keeps the behaviour keeps
the tests, and only a change in behaviour breaks them.
"""
from __future__ import annotations

import urllib.parse

import pytest

from grocery_planner import savings
from grocery_planner.scrapers import instacart_storefront as ist, sprouts


def panel(protein=10.0, serving="1 BAR  40 Gram", servings="12"):
    return ist.Nutrition(
        protein_per_serving=protein, serving_size=serving, servings_per_container=servings
    )


# --------------------------------------------------------------------------- #
# The 'Varied' trap
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", ["Varied", "", None, "about 4", "4-6", "1 (approx)"])
def test_servings_per_container_refuses_non_numeric(value):
    """Anything that is not a plain number is None, never a coerced guess.

    ~15% of live panels are 'Varied' or blank. A ``float()`` here raises; a
    ``try/except`` defaulting to 1 would silently understate protein per
    package on exactly the variable-weight items where it matters most.
    """
    assert ist.servings_per_container(value) is None


@pytest.mark.parametrize("value,want", [("12", 12.0), (" 2.5 ", 2.5), ("1", 1.0)])
def test_servings_per_container_accepts_plain_numbers(value, want):
    assert ist.servings_per_container(value) == want


def test_servings_per_container_rejects_zero():
    """Zero servings would make the package route divide protein into nothing."""
    assert ist.servings_per_container("0") is None


# --------------------------------------------------------------------------- #
# Serving size: three printed shapes, one meaning
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text",
    [
        "8 oz (227g)",             # parenthesised metric
        "227 Gram",                # bare, spelled out
        "1 CONTAINER  227 Gram",   # double-spaced after a household measure
        "1.0 stick(s) (227.0 Gram)",
    ],
)
def test_serving_grams_reads_every_metric_shape(text):
    """The shapes are cosmetic; the mass they report is not."""
    assert ist.serving_grams(text) == pytest.approx(227.0, rel=0.01)


def test_serving_grams_prefers_metric_over_imperial():
    """When a label prints both, the metric figure is the one it rounded to."""
    both = ist.serving_grams("3 OZ  85 Gram")
    imperial_only = ist.serving_grams("3 OZ")
    assert both == 85.0
    # The imperial conversion is close but not identical -- which is the point:
    # taking it when the label already gave grams would import a rounding error.
    assert both != imperial_only
    assert imperial_only == pytest.approx(85.0, rel=0.02)


@pytest.mark.parametrize(
    "text",
    ["8 FL OZ  240 Millilitre", "12 FL OZ. (1 PINT)", "1 TBSP  15 Millilitre",
     "PER CONTAINER", "1 fluid ounce"],
)
def test_serving_grams_never_reads_a_volume_as_a_mass(text):
    """A volume is not a mass, and 'FL OZ' must not be read as ounces.

    This is the guard that stops the scraper inventing a protein density for
    every beverage in the catalogue.
    """
    assert ist.serving_grams(text) is None


# --------------------------------------------------------------------------- #
# THE WEIGHT TRAP -- the same class of bug as GFP-98's soldBy=WEIGHT
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("size", ["per lb", "Per Lb", "per pound", "per oz"])
def test_size_is_weight_recognises_rate_denominations(size):
    assert ist.size_is_weight(size) is True


@pytest.mark.parametrize("size", ["14 oz", "1 lb", "6 ct", "16 OZ (1 LB)"])
def test_size_is_weight_leaves_package_quantities_alone(size):
    """'1 lb' is a package that weighs a pound; 'per lb' is a price rate."""
    assert ist.size_is_weight(size) is False


def test_per_lb_item_never_multiplies_by_servings_per_container():
    """The trap, asserted as a relationship rather than a number.

    For a per-pound item the servings count describes the whole cut while the
    price buys one pound. The package route would therefore be wrong by
    whatever the cut weighs. This asserts the density is the *serving-based*
    one -- never the package-based one -- for an item whose two routes would
    disagree wildly.
    """
    facts = panel(protein=21.0, serving="4 OZ  113 Gram", servings="30")
    by_serving = 21.0 / 113.0 * 100.0

    assert ist.protein_per_100g(facts, "per lb") == pytest.approx(by_serving)
    # And with no serving mass to fall back on, it refuses rather than guessing.
    no_mass = panel(protein=21.0, serving="4 OZ", servings="30")
    no_mass = ist.Nutrition(21.0, "PER CONTAINER", "30")
    assert ist.protein_per_100g(no_mass, "per lb") is None


def test_package_grams_refuses_a_rate_denomination():
    """'per lb' describes what a dollar buys, not what the package weighs."""
    assert ist.package_grams("per lb") is None
    assert ist.package_grams("14 oz") == pytest.approx(396.9, rel=0.01)


# --------------------------------------------------------------------------- #
# Density: two routes, one answer
# --------------------------------------------------------------------------- #
def test_both_density_routes_agree_on_a_consistent_label():
    """A label whose serving mass and package size are consistent must give the
    same density either way. This is what makes the fallback safe."""
    # 12 servings x 40 g = 480 g package, declared as 480 g / ~16.93 oz.
    with_mass = panel(protein=10.0, serving="1 BAR  40 Gram", servings="12")
    without_mass = ist.Nutrition(10.0, "1 BAR", "12")

    route_one = ist.protein_per_100g(with_mass, "16.93 oz")
    route_two = ist.protein_per_100g(without_mass, "16.93 oz")
    assert route_one == pytest.approx(route_two, rel=0.01)


def test_density_is_none_when_neither_route_is_available():
    """No serving mass and no numeric servings count -> no answer, not a zero."""
    facts = ist.Nutrition(10.0, "PER CONTAINER", "Varied")
    assert ist.protein_per_100g(facts, "1 ct") is None


@pytest.mark.parametrize("protein", [None, 0.0, -1.0])
def test_density_requires_a_positive_protein_figure(protein):
    assert ist.protein_per_100g(panel(protein=protein), "14 oz") is None


# --------------------------------------------------------------------------- #
# Name/size round trip -- a relationship, not a spelling
# --------------------------------------------------------------------------- #
def test_weight_denominated_size_survives_into_item_name():
    """The point of folding the size in is that the shared parser reads it back.

    Asserted by round-tripping through ``savings.parse_size`` rather than by
    comparing the string, so reformatting the label cannot break the contract
    while a change that loses the size does.
    """
    name = ist.display_item_name("Grass Fed Ground Beef", "per lb")
    assert savings.parse_size(name) is not None


def test_package_sized_item_also_carries_its_size_into_the_name():
    """A package size must reach `item_name` too, not just weight-denominated ones.

    This test previously asserted the OPPOSITE -- that a package item kept a
    clean name -- because it was written from kroger.py's rule, where a UNIT
    item already carries its size in `description`. Here the JSON-LD splits name
    and size, so "clean" meant the size was lost and the row was unrankable:
    a real 155-row run produced 32 rankable rows and `gplan cheapest` said
    "no protein with a usable size in the current offers".

    Asserted through `parse_size` rather than against a string, so it pins the
    property the engine actually depends on rather than a formatting choice.
    """
    name = ist.display_item_name("Greek Yogurt", "7 oz")
    assert savings.parse_size(name) is not None


def test_the_size_is_not_doubled_when_the_name_already_has_one():
    """Two sizes in one name would leave the parser choosing between them."""
    name = ist.display_item_name("Chicken Breast, 1 lb", "per lb")
    assert name.count("1 lb") == 1


@pytest.mark.parametrize(
    "product_name,size",
    [("Greek Yogurt", "7 oz"), ("Grass Fed Ground Beef", "per lb"),
     ("Sprouts Hibachi Beef Bowl", "11 oz"), ("Cottage Cheese", "16 oz")],
)
def test_every_sized_listing_becomes_rankable(product_name, size):
    """The invariant, stated once: if the listing had a size, the row can be ranked.

    This is the guard whose absence let the bug ship -- every individual helper
    was correct and the pipeline still produced unrankable rows.
    """
    name = ist.display_item_name(product_name, size)
    assert savings.parse_size(name) is not None


# --------------------------------------------------------------------------- #
# Persisted-query discovery
# --------------------------------------------------------------------------- #
def test_discovery_needs_the_second_decode_pass():
    """The blob is doubly URL-encoded; one pass finds nothing, silently.

    That silence is the danger -- it degrades to stale pins with no error --
    so the double decode is pinned here as a behaviour.
    """
    sha = "a" * 64
    raw = (
        f'operationName=SimpleShopCollection&variables=%7B%7D&extensions='
        f'{{"persistedQuery":{{"version":1,"sha256Hash":"{sha}"}}}}'
    )
    once = urllib.parse.quote(raw)
    twice = urllib.parse.quote(once)

    assert ist.discover_persisted_queries(twice)["SimpleShopCollection"] == sha
    # Singly-encoded input is what a naive implementation would be handed.
    assert ist.discover_persisted_queries(urllib.parse.quote(once)) != {}


def test_discovery_returns_nothing_for_a_page_without_the_blob():
    assert ist.discover_persisted_queries("<html><body>hi</body></html>") == {}


def test_pinned_operation_is_declared_and_present():
    """The pin cannot self-heal, so its absence must be a loud, testable fact."""
    for operation in ist.PINNED_OPERATIONS:
        assert operation in sprouts.FALLBACK_HASHES
        assert len(sprouts.FALLBACK_HASHES[operation]) == 64


def test_missing_hash_raises_rather_than_returning_empty():
    """Zero products and no error reads exactly like 'the store has nothing'."""
    # StorefrontClient bound to the tenant directly: the SproutsClient
    # subclass added no behaviour and is gone (GFP-305).
    client = ist.StorefrontClient(sprouts.TENANT, client=object())
    client.hashes = {}
    with pytest.raises(ist.QueryNotAllowedError):
        client._query("ProductNutritionalInfo", {})


# --------------------------------------------------------------------------- #
# Payload mapping
# --------------------------------------------------------------------------- #
def test_nutrition_from_payload_reads_a_real_panel():
    payload = {
        "data": {
            "productNutritionalInfo": {
                "nutritionalInfo": {
                    "protein": 13.0,
                    "calories": 370.0,
                    "servingSize": "8 oz (227g)",
                    "servingsPerContainer": "Varied",
                }
            }
        }
    }
    facts = ist.nutrition_from_payload(payload)
    assert facts.protein_per_serving == 13.0
    assert ist.serving_grams(facts.serving_size) == 227.0


def test_missing_panel_is_an_absence_not_an_error():
    """Two thirds of the catalogue has no panel. Produce and fresh-cut meat
    routinely carry none, so this must be an ordinary None."""
    assert ist.nutrition_from_payload({"data": {"productNutritionalInfo": {}}}) is None
    assert ist.nutrition_from_payload({"data": {}}) is None


def test_parse_listing_reads_price_and_size_from_json_ld():
    head = (
        '<html><head><script type="application/ld+json">'
        '{"@context":"https://schema.org","@graph":[{"@type":"Product",'
        '"name":"Greek Yogurt","brand":{"@type":"Brand","name":"Sprouts"},'
        '"category":"Yogurt","size":"7.5 oz","offers":{"price":"4.99",'
        '"availability":"https://schema.org/InStock"}}]}'
        "</script></head></html>"
    )
    listing = ist.parse_listing("70516703-sprouts-greek-yogurt-7-oz", head)
    assert listing.price == 4.99
    assert listing.size == "7.5 oz"
    assert listing.availability == "InStock"
    assert listing.product_id == "70516703"


def test_parse_listing_is_none_when_the_head_was_cut_short():
    """HEAD_BYTES is a bandwidth optimisation; if it ever truncates before the
    JSON-LD block the answer must be None, not a half-parsed listing."""
    assert ist.parse_listing("1-x", "<html><head>" + "x" * 500) is None


# --------------------------------------------------------------------------- #
# Row shape
# --------------------------------------------------------------------------- #
def _listing(price=4.99, size="7.5 oz"):
    return ist.Listing(
        product_id="70516703", slug="70516703-greek-yogurt-7-oz", name="Greek Yogurt",
        brand="Sprouts", category="Yogurt", size=size, price=price, availability="InStock",
    )


def test_priceless_row_is_labelled_rather_than_dropped(monkeypatch):
    """A nutrition-only row is degraded, not failed -- same call as kroger.py."""
    from datetime import datetime, timezone

    row, _fact = sprouts.listing_to_row(
        _listing(price=None), panel(), "27401", datetime.now(timezone.utc)
    )
    assert row["dollar_price"] is None
    assert "price_missing=true" in row["notes"]
    assert "price not listed" in row["deal_type"]


def test_weight_row_carries_the_weight_basis_marker():
    from datetime import datetime, timezone

    row, _fact = sprouts.listing_to_row(
        _listing(price=11.19, size="per lb"), panel(), "27401", datetime.now(timezone.utc)
    )
    assert row["sold_by"] == "WEIGHT"
    assert row["deal_description"].endswith("/lb")


def test_food_fact_only_written_when_a_density_exists():
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    _row, with_density = sprouts.listing_to_row(_listing(), panel(), "27401", now)
    assert with_density is not None and with_density.protein_per_100g > 0

    unusable = ist.Nutrition(10.0, "PER CONTAINER", "Varied")
    _row, without = sprouts.listing_to_row(_listing(size="1 ct"), unusable, "27401", now)
    assert without is None
