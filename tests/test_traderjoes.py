"""Trader Joe's scraper -- the traps, pinned as relationships (GFP-264).

Every test here exists because the live catalogue actually contains the shape
it describes, and most of them run against ``tests/fixtures/traderjoes_products.json``
-- nine real products captured from the API on 2026-08-11, with the ~600-store
price and availability blobs trimmed to three store codes so the fixture stays
readable while keeping its real structure.

They assert *relationships* rather than magic numbers or exact spellings, so a
refactor that keeps the behaviour keeps the tests and only a change in
behaviour breaks them. Nothing here touches the network.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from grocery_planner import savings
from grocery_planner.scrapers import traderjoes as tj

FIXTURE = Path(__file__).parent / "fixtures" / "traderjoes_products.json"


@pytest.fixture(scope="module")
def products():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def by_sku(products):
    return {p["sku"]: p for p in products}


def panel(protein=10.0, serving="1 bar (40g)", servings="Serves 12", title="Per serving", seq=0):
    return tj.Panel(
        sequence=seq, title=title, serving_size=serving,
        servings_per_container=servings, protein_per_serving=protein,
    )


NOW = datetime(2026, 8, 11, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# THE 'less than' TRAP
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("amount", [
    "less than 1 g", "Less than 1g", "less than 1g", "Less than 1g g",
])
def test_bounded_protein_is_refused_not_rounded(amount):
    """An upper bound is not a measurement (savings.py rule 1).

    93 of 1,660 live protein lines read like this. Stripping the non-digits
    would yield 1, overstating protein on an item that has essentially none --
    and overstating protein understates cost per gram of protein, which
    silently promotes the item up every ranking.
    """
    assert tj.protein_grams(amount) is None


@pytest.mark.parametrize("amount,want", [("26 g", 26.0), ("26g", 26.0), ("0 g", 0.0), ("3.5g", 3.5)])
def test_real_protein_figures_survive_their_unit_suffix(amount, want):
    assert tj.protein_grams(amount) == want


@pytest.mark.parametrize("amount", [None, "", "   ", "n/a"])
def test_absent_protein_is_none(amount):
    assert tj.protein_grams(amount) is None


def test_a_bounded_figure_never_becomes_a_density(by_sku):
    """End to end: the guard must survive all the way to the density.

    Asserted on the real product rather than a constructed panel, because the
    point is that this shape exists in the live catalogue.
    """
    bounded = [
        p for p in by_sku.values()
        if "less than" in (tj.attribute_map(p).get("nutrition") or "").lower()
    ]
    assert bounded, "fixture should carry at least one 'less than' panel"
    for product in bounded:
        listing = tj.parse_listing(product, "0750")
        assert tj.protein_per_100g(listing.panel, tj.size_text(
            listing.size_quantity, listing.size_uom)) is None


# --------------------------------------------------------------------------- #
# THE PANEL TRAP -- 'Per Container' repeats the per-serving serving size
# --------------------------------------------------------------------------- #
def test_container_panel_is_never_selected(by_sku):
    """The headline trap, asserted as the relationship that makes it dangerous.

    The two panels share an identical ``serving_size`` string while their
    amounts differ by the servings count, so picking the wrong one overstates
    density by that factor. This asserts the selected panel is the *smaller*
    protein figure and that the naive alternative would have been materially
    larger -- without hard-coding either number.
    """
    product = by_sku["046189"]                      # CHICKEN SHU MAI, 2 panels
    panels = tj.parse_panels(tj.attribute_map(product).get("nutrition"))
    assert len(panels) > 1, "fixture should carry a multi-panel product"

    chosen = tj.select_panel(panels)
    others = [p for p in panels if p is not chosen]

    # The trap only bites because the serving-size STRING is identical.
    assert any(p.serving_size == chosen.serving_size for p in others)
    # And the amounts are not.
    assert all(p.protein_per_serving > chosen.protein_per_serving for p in others)

    servings = tj.servings_per_container(chosen.servings_per_container)
    naive = max(p.protein_per_serving for p in others)
    # The discarded figure is the whole container: about `servings` times the
    # per-serving one. That is the multiple the density would have been wrong by.
    assert naive == pytest.approx(chosen.protein_per_serving * servings, rel=0.35)


@pytest.mark.parametrize("title", [
    "Per Container", "per container", "Per package", "Per Bottle", "PER PKG",
])
def test_whole_container_titles_are_recognised(title):
    assert tj.panel_is_whole_container(title) is True


@pytest.mark.parametrize("title", ["Per serving", "Per Serving", "", None, "Per piece"])
def test_per_serving_titles_are_left_alone(title):
    assert tj.panel_is_whole_container(title) is False


def test_selection_survives_either_signal_failing():
    """Two independent rules, so one changing upstream degrades to the other."""
    titled = [panel(protein=11.0, title="Per Serving", seq=0),
              panel(protein=33.0, title="Per Container", seq=1)]
    # Title says container but the sequence has been shuffled: still excluded.
    shuffled = [panel(protein=33.0, title="Per Container", seq=0),
                panel(protein=11.0, title="Per Serving", seq=1)]
    # Titles are useless (both blank): sequence decides.
    untitled = [panel(protein=11.0, title="", seq=0), panel(protein=33.0, title="", seq=1)]

    for panels in (titled, shuffled, untitled):
        assert tj.select_panel(panels).protein_per_serving == 11.0


def test_no_usable_panel_is_an_absence_not_an_error():
    """One product in 2,454 has nothing left after the title filter."""
    assert tj.select_panel([panel(title="Per Container")]) is None
    assert tj.select_panel([]) is None
    assert tj.parse_panels("[]") == []
    assert tj.parse_panels(None) == []


# --------------------------------------------------------------------------- #
# THE 'Serves about' TRAP
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value,want", [
    ("Serves 4", 4.0), ("Serves about 8", 8.0), ("Serves about 2.5", 2.5),
    ("serves 1", 1.0), ("Servings: 6", 6.0), ("12", 12.0), (" 2.5 ", 2.5),
])
def test_servings_reads_the_prose_dialect(value, want):
    """Only 6 of 1,664 live panels hold a bare numeral; the rest are prose.

    A plain-number rule would return None for 99.6% of this catalogue.
    """
    assert tj.servings_per_container(value) == want


@pytest.mark.parametrize("value", [
    "Servings varied", "Varied", "", None, "Serves 4-6", "about a few",
])
def test_servings_refuses_anything_that_is_not_a_count(value):
    """'Servings varied' must not become a defaulted 1, which would understate
    protein per package on exactly the variable-weight items."""
    assert tj.servings_per_container(value) is None


def test_servings_rejects_zero():
    assert tj.servings_per_container("Serves 0") is None


# --------------------------------------------------------------------------- #
# THE VOLUME TRAP
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,want", [
    ("4 pieces(80g)", 80.0),                       # no space before the unit
    ("1 cake (108g)", 108.0),
    ("2 Cups (85g)", 85.0),
    ("1/4 Pizza (129g/4.5 oz)", 129.0),            # metric first, then imperial
    ("3 oz (84g/about 1/6 pkg)", 84.0),
    ("1 oz (28g/about 1 inch cube)", 28.0),
    ("1/2 cup dry (43g)", 43.0),
])
def test_serving_grams_reads_every_printed_shape(text, want):
    """The shapes are cosmetic; the mass they report is not."""
    assert tj.serving_grams(text) == pytest.approx(want)


@pytest.mark.parametrize("text", [
    "1 Tbsp. (15mL)", "1 Tbsp. (15 mL)", "2 Tbsp (30 mL)",
    "12 fl oz (360mL)", "8 fl oz (240 mL)", "1 L", "PER CONTAINER",
])
def test_serving_grams_never_reads_a_volume_as_a_mass(text):
    """The guard that stops the scraper inventing a protein density for every
    beverage and condiment in the catalogue."""
    assert tj.serving_grams(text) is None


def test_serving_grams_prefers_metric_over_imperial():
    """When a label prints both, the metric figure is the one it rounded to.

    Taking the ounces would import a rounding error the label had already
    resolved -- so the two must not be interchangeable.
    """
    both = tj.serving_grams("3 oz (84g)")
    imperial_only = tj.serving_grams("3 oz")
    assert both == 84.0
    assert both != imperial_only
    assert imperial_only == pytest.approx(84.0, rel=0.02)


@pytest.mark.parametrize("text", [None, "", "1 piece"])
def test_serving_grams_is_none_when_no_mass_is_printed(text):
    assert tj.serving_grams(text) is None


# --------------------------------------------------------------------------- #
# Size: two fields that are meaningless apart, in the shared grammar
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("quantity,uom", [
    ("1.000000", "Lb"), ("13.400000", "Oz"), ("16.000000", "Fl Oz"),
    ("500.000000", "mL"), ("1.000000", "Doz"), ("6.000000", "Each"),
])
def test_size_text_round_trips_through_the_shared_parser(quantity, uom):
    """The output's whole job is to be readable by ``savings.parse_size``.

    Round-tripped rather than string-compared, so reformatting the label cannot
    break the contract while a change that loses the size does.
    """
    text = tj.size_text(quantity, uom)
    parsed = savings.parse_size(text)
    assert parsed is not None
    assert parsed.quantity == pytest.approx(float(quantity))


def test_size_text_preserves_the_dimension_it_was_given():
    """A pound is a weight and a fluid ounce is not -- the parser must agree."""
    assert savings.parse_size(tj.size_text("1", "Lb")).base_unit == savings.WEIGHT
    assert savings.parse_size(tj.size_text("16", "Fl Oz")).base_unit == savings.VOLUME
    assert savings.parse_size(tj.size_text("6", "Each")).base_unit == savings.COUNT


def test_a_pound_here_is_the_same_pound_as_everywhere_else():
    """There must be exactly one definition of a pound in this codebase."""
    assert tj.package_grams(tj.size_text("1", "Lb")) == pytest.approx(savings.GRAMS_PER_LB)
    assert tj.package_grams(tj.size_text("1", "Oz")) == pytest.approx(savings.GRAMS_PER_OZ)


@pytest.mark.parametrize("quantity,uom", [
    (None, "Oz"), ("1.0", None), (None, None), ("0.000000", "Oz"), ("abc", "Oz"),
])
def test_size_text_needs_both_halves(quantity, uom):
    assert tj.size_text(quantity, uom) is None


@pytest.mark.parametrize("uom", ["Fl Oz", "mL", "L", "Pint", "Qt", "Each", "Bag", "Doz"])
def test_package_grams_refuses_anything_that_is_not_a_mass(uom):
    """444 of 2,454 products are volumes; reading those as weight would invent
    a density for the entire beverage aisle."""
    assert tj.package_grams(tj.size_text("12", uom)) is None


# --------------------------------------------------------------------------- #
# Density: two routes, one answer
# --------------------------------------------------------------------------- #
def test_both_density_routes_agree_on_a_consistent_label():
    """A label whose serving mass and package size are consistent must give the
    same density either way. That is what makes the fallback safe."""
    # 12 servings x 40 g = 480 g, declared as ~16.93 oz.
    with_mass = panel(protein=10.0, serving="1 bar (40g)", servings="Serves 12")
    without_mass = panel(protein=10.0, serving="1 bar", servings="Serves 12")
    size = tj.size_text("16.93", "Oz")

    assert tj.protein_per_100g(with_mass, size) == pytest.approx(
        tj.protein_per_100g(without_mass, size), rel=0.01
    )


def test_density_prefers_the_serving_route_when_both_are_available():
    """The serving route cannot be contaminated by a wrong servings count."""
    # Deliberately inconsistent: the package route would disagree wildly.
    facts = panel(protein=10.0, serving="1 bar (40g)", servings="Serves 30")
    assert tj.protein_per_100g(facts, tj.size_text("16.93", "Oz")) == pytest.approx(25.0)


def test_density_is_none_when_neither_route_is_available():
    facts = panel(protein=10.0, serving="1 piece", servings="Servings varied")
    assert tj.protein_per_100g(facts, tj.size_text("12", "Each")) is None


@pytest.mark.parametrize("protein", [None, 0.0, -1.0])
def test_density_requires_a_positive_protein_figure(protein):
    assert tj.protein_per_100g(panel(protein=protein), tj.size_text("14", "Oz")) is None


def test_density_of_a_missing_panel_is_none():
    assert tj.protein_per_100g(None, tj.size_text("14", "Oz")) is None


@pytest.mark.parametrize("density", [100.1, 307.0, 677.0, 1e6])
def test_impossible_densities_are_rejected_not_clamped(density):
    """Clamping would turn a data error into a plausible-looking figure that
    then sorts near the top of every cheapest-protein ranking.

    The sibling Instacart scraper produced 677 and 307 g/100 g on a real run
    before it had this guard; the package route here can reach the same
    nonsense the same way.
    """
    assert tj.plausible_density(density) is None


@pytest.mark.parametrize("density", [0.0, -1.0, None])
def test_non_positive_densities_are_rejected(density):
    assert tj.plausible_density(density) is None


@pytest.mark.parametrize("density", [0.1, 10.6, 64.3, 90.0, 100.0])
def test_possible_densities_pass_through_unchanged(density):
    """The bound is inclusive at 100 and must not alter a legitimate figure."""
    assert tj.plausible_density(density) == density


def test_a_wrong_servings_count_cannot_produce_an_impossible_density():
    """The package route is the one that can inflate without any single input
    looking wrong -- so the invariant is asserted through it, not just directly."""
    absurd = panel(protein=10.0, serving="1 piece", servings="Serves 300")
    assert tj.protein_per_100g(absurd, tj.size_text("16", "Oz")) is None


def test_live_densities_stay_physically_possible(products):
    """No food is more than ~90% protein by mass.

    A blunt sanity bound rather than a pinned value: it is the assertion that
    would have caught the panel trap, since the container panel produced
    densities several times the true figure.
    """
    for product in products:
        listing = tj.parse_listing(product, "0750")
        density = tj.protein_per_100g(
            listing.panel, tj.size_text(listing.size_quantity, listing.size_uom)
        )
        if density is not None:
            assert 0 < density < 90, f"{listing.sku} {listing.name}: {density}"


# --------------------------------------------------------------------------- #
# Attribute indirection: JSON documents inside String fields
# --------------------------------------------------------------------------- #
def test_empty_attribute_is_an_absence_not_an_error():
    """These arrive as "[]", not null."""
    assert tj.json_attribute("[]") == []
    assert tj.json_attribute(None) == []
    assert tj.json_attribute("") == []


def test_malformed_attribute_does_not_abandon_the_scrape():
    """One bad product must not cost the other 2,453."""
    assert tj.json_attribute("{not json") == []
    assert tj.json_attribute('"a string"') == []


# --------------------------------------------------------------------------- #
# THE PARTIAL-FAILURE TRAP -- HTTP 200 with a server error inside
# --------------------------------------------------------------------------- #
def test_a_server_side_attribute_failure_is_not_read_as_absent_data(by_sku):
    """132 of 2,454 products return ``custom_attributesV2: null`` with an
    'Internal server error', inside an otherwise perfectly healthy 200.

    'We could not ask' and 'there is none' are different facts, and conflating
    them is what made this module's own first measurement pass report
    '132 products have no size on file'.
    """
    failed = tj.parse_listing(by_sku["001487"], "0750")
    ok = tj.parse_listing(by_sku["046189"], "0750")

    assert failed.has_attributes is False
    assert ok.has_attributes is True
    # The distinction survives into the row, so it is visible after the fact.
    row, _fact = tj.listing_to_row(failed, "27401", NOW, "0750")
    assert "attributes_unavailable=true" in row["notes"]


def test_a_product_with_no_panel_is_not_confused_with_one_that_failed(by_sku):
    """SOURDOUGH BREAD genuinely has no nutrition; PEANUT BUTTER failed."""
    genuinely_empty = tj.parse_listing(by_sku["000152"], "0750")
    assert genuinely_empty.has_attributes is True
    assert genuinely_empty.panel is None

    row, _fact = tj.listing_to_row(genuinely_empty, "27401", NOW, "0750")
    assert "attributes_unavailable" not in row["notes"]


def test_a_failed_product_is_still_priced_and_kept(by_sku):
    """Degraded, not dropped: the national price is still a real figure."""
    row, fact = tj.listing_to_row(
        tj.parse_listing(by_sku["001487"], "0750"), "27401", NOW, "0750"
    )
    assert row["dollar_price"] is not None
    assert "pricing_scope=national" in row["notes"]
    assert fact is None                              # no nutrition to record


def test_attribute_failures_are_counted_in_stats(conn, products):
    """The no-silent-caps rule applied to a partial upstream failure."""
    _rows, _meta, stats = tj.scrape(
        conn=conn, client=_offline_client(), now=NOW, products=products,
    )
    assert stats["products_missing_attributes"] == 1
    # Counted separately from a genuine absence of nutrition.
    assert stats["no_nutrition_panel"] > stats["products_missing_attributes"]


def test_attribute_map_ignores_entries_with_no_scalar_value():
    product = {"custom_attributesV2": {"items": [
        {"code": "nutrition", "value": "[]"},
        {"code": "fun_tags"},                       # AttributeSelectedOptions
        {"value": "orphan"},
    ]}}
    assert tj.attribute_map(product) == {"nutrition": "[]"}


# --------------------------------------------------------------------------- #
# Store scoping
# --------------------------------------------------------------------------- #
def test_store_price_is_read_for_the_chosen_store(by_sku):
    """Prices genuinely differ between stores, so the code must select."""
    product = by_sku["015688"]                      # differs 0003 vs 0750
    here = tj.parse_listing(product, "0750").store_price
    elsewhere = tj.parse_listing(product, "0003").store_price
    assert here is not None and elsewhere is not None
    assert here != elsewhere


def test_a_zero_store_price_is_a_placeholder_not_a_price():
    """0.00 would sort straight to the top of every cheapest-protein list."""
    assert tj.store_price("0") is None
    assert tj.store_price("") is None
    assert tj.store_price(None) is None
    assert tj.store_price("2.49") == 2.49


def test_no_store_code_means_no_store_price(by_sku):
    """With nothing chosen, the store blob must not be guessed at."""
    listing = tj.parse_listing(by_sku["015688"], None)
    assert listing.store_price is None
    assert listing.national_price is not None


def test_missing_store_row_falls_back_to_the_national_price(by_sku):
    """566 of 2,454 products have no price on file at the Greensboro store.

    The national figure is a real published number ('Global TJ price'), so the
    row is priced -- and says which figure it is quoting.
    """
    product = by_sku["006372"]                      # empty retail_price blob
    listing = tj.parse_listing(product, "0750")
    assert listing.store_price is None
    row, _fact = tj.listing_to_row(listing, "27401", NOW, "0750")
    assert row["dollar_price"] == listing.national_price
    assert "pricing_scope=national" in row["notes"]


def test_availability_has_three_states_not_two():
    """Unknown is not absent -- the same rule `serves` follows."""
    assert tj.is_available("1") is True
    assert tj.is_available("0") is False
    assert tj.is_available(None) is None
    assert tj.is_available("") is None


def test_store_scoped_value_returns_none_for_an_unlisted_store():
    blob = [{"store_code": "0003", "value": "7.99"}]
    assert tj.store_scoped_value(blob, "0750") is None
    assert tj.store_scoped_value(blob, "0003") == "7.99"
    assert tj.store_scoped_value(blob, None) is None


# --------------------------------------------------------------------------- #
# The locator -- the join between store numbers and store codes
# --------------------------------------------------------------------------- #
def test_sitemap_store_numbers_are_padded_to_catalogue_store_codes():
    """`750` in the locator is `0750` in the catalogue. That mapping is the
    only reason a locator lookup can say anything about a price."""
    xml = (
        "<urlset>"
        "<url><loc>https://locations.traderjoes.com/nc/greensboro/750/</loc></url>"
        "<url><loc>https://locations.traderjoes.com/nh/bedford/562/</loc></url>"
        "<url><loc>https://locations.traderjoes.com/oh/dublin/</loc></url>"
        "</urlset>"
    )
    stores = tj.parse_store_sitemap(xml)
    assert set(stores) == {"0750", "0562"}
    assert stores["0750"].number == "750"
    assert (stores["0750"].state, stores["0750"].city) == ("nc", "greensboro")


def test_every_pinned_store_code_matches_the_catalogue_blob_width():
    """A pin that is not in the blob's format can never match anything."""
    for code in tj.STORE_CODE_BY_POSTAL_CODE.values():
        assert code == code.strip() and len(code) == 4 and code.isdigit()


def test_store_postal_code_is_read_from_the_schema_org_block():
    html = (
        '<script type="application/ld+json">{"@context":"https://schema.org",'
        '"@type": "PostalAddress","@id":"https://locations.traderjoes.com/nc/greensboro/750/",'
        '"streetAddress":"3721 Battleground Ave.","addressLocality":"Greensboro",'
        '"addressRegion":"NC","postalCode":"27410","addressCountry":"US"}</script>'
    )
    assert tj.parse_store_postal_code(html) == "27410"
    assert tj.parse_store_postal_code("<html>no address here</html>") is None


# --------------------------------------------------------------------------- #
# Readiness / serves
# --------------------------------------------------------------------------- #
def test_readiness_is_a_constant_because_there_is_no_credential():
    ready, reason = tj.readiness()
    assert ready is True and reason


def test_serves_never_answers_false():
    """Unknown is not absent. The source exposes no location argument at all,
    so a False here would claim knowledge this module does not have."""
    assert tj.serves(tj.DEFAULT_POSTAL_CODE) is True
    for unknown in ("99999", "10001", "", "not-a-zip"):
        assert tj.serves(unknown) is None


def test_store_code_lookup_agrees_with_serves():
    """The two must never disagree: serving a ZIP *is* having a store for it."""
    for postal in (tj.DEFAULT_POSTAL_CODE, "99999", ""):
        assert (tj.store_code_for(postal) is not None) == (tj.serves(postal) is True)


# --------------------------------------------------------------------------- #
# Row shape
# --------------------------------------------------------------------------- #
def _row(product, store_code="0750"):
    return tj.listing_to_row(tj.parse_listing(product, store_code), "27401", NOW, store_code)


def test_row_carries_every_key_the_deals_pipeline_expects(by_sku):
    """The CLI formats these without per-store branching, so a missing key is
    a per-store special case waiting to happen."""
    row, _fact = _row(by_sku["046189"])
    required = {
        "item_name", "sub_category", "deal_type", "deal_description",
        "regular_price", "sale_price", "dollar_price", "discount_amount",
        "discount_percent", "valid_from", "valid_to", "loyalty_required",
        "notes", "source_url", "image_url", "flipp_flyer_id", "flipp_item_id",
        "flipp_coupon_id", "sold_by", "weight_basis", "price_per_unit",
        "price_per_unit_uom", "product_identifier", "product_identifier_ns",
    }
    assert required <= set(row)


def test_size_survives_into_the_item_name(by_sku):
    """The point of folding the size in is that the shared parser reads it back,
    so price and size refer to the same quantity with no branching downstream."""
    row, _fact = _row(by_sku["058523"])             # 1 Lb package
    assert savings.parse_size(row["item_name"]) is not None


def test_a_name_that_already_states_its_size_is_not_given_a_second_one():
    """Two sizes in one name is worse than none -- the parser reads the first."""
    name = tj.display_item_name("Roasted & Salted Almonds 1 lb", None, "1 lb")
    assert name.count("lb") == 1


def test_item_title_is_preferred_over_the_shelf_tag_name():
    """`name` is upper-case shelf text; `item_title` is the cased title."""
    assert tj.display_item_name("Pesto Chicken Breast", "PESTO CHICKEN BREAST", None) == (
        "Pesto Chicken Breast"
    )
    assert tj.display_item_name(None, "PESTO CHICKEN BREAST", None) == "PESTO CHICKEN BREAST"


def test_no_row_is_ever_denominated_by_weight(by_sku):
    """Trader Joe's publishes no per-pound rate and the schema has no field for
    one, so claiming a weight basis would be inventing a fact."""
    for product in by_sku.values():
        row, _fact = _row(product)
        assert row["sold_by"] == "UNIT"
        assert row["weight_basis"] is None


def test_priceless_row_is_labelled_rather_than_dropped():
    """A nutrition-only row is degraded, not failed -- same call as kroger.py."""
    listing = tj.Listing(
        sku="000001", name="MYSTERY ITEM", title=None, url_key=None,
        national_price=None, store_price=None, available=None,
        size_quantity="8.000000", size_uom="Oz", country_of_origin=None,
        panel=panel(),
    )
    row, _fact = tj.listing_to_row(listing, "27401", NOW, "0750")
    assert row["dollar_price"] is None
    assert "price_missing=true" in row["notes"]
    assert "price not listed" in row["deal_type"]


def test_food_fact_only_written_when_a_density_exists(by_sku):
    _row_with, fact = _row(by_sku["046189"])        # has a usable panel
    assert fact is not None and fact.protein_per_100g > 0

    _row_without, none_fact = _row(by_sku["000152"])  # no panel at all
    assert none_fact is None


def test_food_fact_is_keyed_on_the_sku_not_the_name(by_sku):
    """The SKU is the stable identity; names change with marketing."""
    row, fact = _row(by_sku["046189"])
    assert fact.sku == row["product_identifier"]
    assert row["product_identifier_ns"] == tj.PRODUCT_IDENTIFIER_NS


def test_missing_identifier_yields_neither_half():
    """A namespace labelling nothing is worse than no identifier."""
    listing = tj.parse_listing({"sku": "", "name": "x"}, "0750")
    assert listing is None


# --------------------------------------------------------------------------- #
# scrape() -- no network, no silent caps
# --------------------------------------------------------------------------- #
def _offline_client():
    """A client whose HTTP transport would raise if anything touched it."""
    return tj.TraderJoesClient(client=object())


def test_scrape_maps_the_fixture_catalogue(conn, products):
    rows, meta, stats = tj.scrape(
        postal_code="27401", conn=conn, client=_offline_client(),
        now=NOW, products=products,
    )
    assert rows
    assert stats["total"] == len(rows)
    assert stats["priced"] + stats["no_price"] == len(rows)
    assert meta["store_code"] == tj.STORE_CODE_BY_POSTAL_CODE["27401"]
    assert stats["pricing_scope"] == "store"
    # Every priced row is one of the two published figures, and the split adds up.
    assert stats["store_priced"] + stats["national_priced"] == stats["priced"]


def test_scrape_reports_every_bound_it_applied(conn, products):
    """The no-silent-caps rule: a truncated run must not read as a complete one."""
    rows, _meta, stats = tj.scrape(
        conn=conn, client=_offline_client(), now=NOW, products=products, limit=2,
    )
    assert len(rows) == 2
    assert stats["limit_applied"] is True
    # products_seen still reports the whole catalogue, so the truncation is visible.
    assert stats["products_seen"] > len(rows)

    _rows, _meta, unbounded = tj.scrape(
        conn=conn, client=_offline_client(), now=NOW, products=products,
    )
    assert unbounded["limit_applied"] is False


def test_unstocked_products_are_dropped_but_counted(conn, products):
    """A price for something you cannot buy is not a deal -- but the drop has
    to be legible, not silent."""
    _rows, _meta, filtered = tj.scrape(
        conn=conn, client=_offline_client(), now=NOW, products=products,
    )
    kept_rows, _meta, kept = tj.scrape(
        conn=conn, client=_offline_client(), now=NOW, products=products,
        include_unavailable=True,
    )
    assert filtered["filtered_unavailable"] > 0
    assert kept["filtered_unavailable"] == 0
    assert len(kept_rows) == filtered["total"] + filtered["filtered_unavailable"]


def test_unknown_availability_keeps_the_product(conn, products):
    """With no store chosen nothing can be known to be absent, so nothing is
    dropped -- unknown is not absent."""
    rows, meta, stats = tj.scrape(
        postal_code="99999", conn=conn, client=_offline_client(),
        now=NOW, products=products,
    )
    assert stats["filtered_unavailable"] == 0
    assert len(rows) == len(products)
    assert stats["pricing_scope"] == "national"
    assert meta["store_code"] is None


def test_national_run_still_prices_every_row(conn, products):
    """Falling back to the national figure is a degradation, not a failure."""
    rows, _meta, stats = tj.scrape(
        postal_code="99999", conn=conn, client=_offline_client(),
        now=NOW, products=products,
    )
    assert stats["store_priced"] == 0
    assert stats["priced"] > 0
    assert all("pricing_scope=national" in r["notes"] for r in rows if r["dollar_price"])


def test_scrape_writes_nutrition_alongside_the_price(conn, products):
    """Like kroger.py and sprouts.py: the label is the authority for this SKU,
    so no USDA matching pass is needed for these rows."""
    _rows, _meta, stats = tj.scrape(
        conn=conn, client=_offline_client(), now=NOW, products=products,
    )
    stored = conn.execute(
        "SELECT COUNT(*) AS n FROM food_nutrients fn "
        "JOIN foods f ON f.id = fn.food_id "
        "WHERE f.source = 'traderjoes' AND fn.nutrient = 'protein'"
    ).fetchone()["n"]
    assert stored == stats["with_protein"] > 0


def test_nutrition_match_is_manual_so_the_matcher_cannot_downgrade_it(conn, products):
    from grocery_planner import matching

    tj.scrape(conn=conn, client=_offline_client(), now=NOW, products=products)
    sources = {
        r["match_source"] for r in conn.execute(
            "SELECT match_source FROM deal_food_match WHERE store = ?", (tj.STORE_KEY,)
        )
    }
    assert sources == {matching.MANUAL}


def test_rescraping_is_idempotent(conn, products):
    """`deals` is replaced wholesale but foods/food_nutrients are upserted, so a
    second run must not double them."""
    tj.scrape(conn=conn, client=_offline_client(), now=NOW, products=products)
    first = conn.execute("SELECT COUNT(*) AS n FROM foods WHERE source='traderjoes'").fetchone()["n"]
    tj.scrape(conn=conn, client=_offline_client(), now=NOW, products=products)
    second = conn.execute("SELECT COUNT(*) AS n FROM foods WHERE source='traderjoes'").fetchone()["n"]
    assert first == second > 0


def test_pacing_counters_reach_the_stats(conn, products):
    """A run that was throttled into crawling has to say so."""
    _rows, _meta, stats = tj.scrape(
        conn=conn, client=_offline_client(), now=NOW, products=products,
    )
    assert f"{tj.CATALOGUE_BUDGET.name}_throttled" in stats
    assert f"{tj.CATALOGUE_BUDGET.name}_cooldowns" in stats


def test_budget_starts_slower_than_the_sprouts_measured_one():
    """One host's tolerance is not evidence about another's, so this source
    must not inherit a floor that was measured somewhere else."""
    from grocery_planner.scrapers import retry

    assert tj.CATALOGUE_BUDGET.min_interval > retry.GRAPHQL_BUDGET.min_interval
    assert tj.CATALOGUE_BUDGET.name != retry.GRAPHQL_BUDGET.name
