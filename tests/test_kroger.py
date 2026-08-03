"""Tests for the Harris Teeter / Kroger API scraper (GFP-98).

No network: ``KrogerClient`` is faked the same way ``test_wholefoods.py`` fakes
``WholeFoodsClient``, and the pure extraction functions are exercised against
product dicts copied from real GFP-77 spike responses.

The centrepiece is ``TestSoldByWeightTrap``. PORK_LOIN below is a VERBATIM
capture of the record that produced an impossible $0.0035/g protein during the
spike, and it is here to make sure that exact shape can never be mispriced
again.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from grocery_planner import db, savings
from grocery_planner.scrapers import kroger

NOW = datetime(2026, 8, 2, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Real payloads, captured from the live API during GFP-77.
# --------------------------------------------------------------------------- #
_UOM = {
    "Gram": {"abbreviation": "g", "code": "GRM", "name": "Gram"},
    # A volume: Kroger states it the same way, but it cannot become grams
    # without a density we do not have.
    "Millilitre": {"abbreviation": "ml", "code": "MLT", "name": "Millilitre"},
}


def _nutrition(protein_g, serving_qty, serving_unit="Gram", servings_per_package=None):
    block = {
        "servingSize": {"quantity": serving_qty,
                        "unitOfMeasure": _UOM[serving_unit]},
        "nutrients": [
            {"code": "FAT", "displayName": "Total Fat", "quantity": 3},
            {"code": "PRO-", "displayName": "Protein", "quantity": protein_g},
        ],
    }
    block["servingsPerPackage"] = (
        {"description": f"{servings_per_package}.0", "value": servings_per_package}
        if servings_per_package is not None else {}
    )
    return [block]


# THE trap case, verbatim from the spike. Sold per POUND, but
# servingsPerPackage describes the whole 7.4 lb loin.
PORK_LOIN = {
    "productId": "0001111041700",
    "description": "Pork Loin Whole",
    "items": [{
        "size": "1 lb",
        "soldBy": "WEIGHT",
        "price": {"regular": 2.99, "promo": 2.49,
                  "regularPerUnitEstimate": 2.99, "promoPerUnitEstimate": 2.49},
    }],
    "nutritionInformation": _nutrition(24, 112, servings_per_package=30),
}

# An ordinary packaged item: the price buys the package.
CHICKEN_PACKET = {
    "productId": "0002222052800",
    "description": "Harris Teeter Boneless Skinless Chicken Breast",
    "items": [{
        "size": "16 oz",
        "soldBy": "UNIT",
        "price": {"regular": 5.49, "promo": 4.99, "regularPerUnitEstimate": 4.99},
    }],
    "nutritionInformation": _nutrition(26, 112, servings_per_package=4),
}

NO_NUTRITION = {
    "productId": "0003333063900",
    "description": "Paper Towels",
    "items": [{"size": "6 ct", "soldBy": "UNIT", "price": {"regular": 8.99}}],
}

NO_PRICE = {
    "productId": "0004444074000",
    "description": "Mystery Item",
    "items": [{"size": "1 lb", "soldBy": "WEIGHT", "price": {}}],
    "nutritionInformation": _nutrition(20, 100),
}


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("GROCERY_PLANNER_DB", str(tmp_path / "kroger.sqlite3"))
    c = db.connect()
    yield c
    c.close()


# --------------------------------------------------------------------------- #
# THE SOLD-BY TRAP -- the reason this ticket has a mandatory requirement
# --------------------------------------------------------------------------- #
class TestSoldByWeightTrap:
    """A per-pound price must never be multiplied by the whole cut's protein.

    Doing so understates cost ~7x, and it hits 30% of the catalog -- precisely
    the fresh meat, i.e. the highest-protein items, which would then dominate
    every recommendation while being wrong. Same class of error as GFP-73.
    """

    def test_servings_per_package_is_never_used_for_a_weight_item(self):
        # The naive computation is price / (protein_per_serving * spp)
        #   = 2.49 / (24 * 30) = 2.49 / 720g = $0.0035/g  <- impossible
        # 453g of pork cannot contain 720g of protein.
        naive = 2.49 / (24 * 30)
        assert naive < 0.005  # this is the WRONG answer we must never produce

        row, fact = kroger.product_to_row(PORK_LOIN, "Meat", "27401", NOW)
        assert fact is not None
        # Density, not a package total: 24g protein per 112g serving.
        assert fact.protein_per_100g == pytest.approx(24 / 112 * 100, rel=1e-6)

        # The size folded into the name is the PRICED pound, so price and size
        # describe the same quantity.
        assert row["item_name"] == "Pork Loin Whole, 1 lb"

    def test_the_engine_prices_a_weight_item_sanely_end_to_end(self, conn):
        row, fact = kroger.product_to_row(PORK_LOIN, "Meat", "27401", NOW)
        kroger._upsert_food_fact(conn, fact)
        conn.commit()

        cost = savings.cost_per_gram_protein(
            row["dollar_price"], row["item_name"], kroger.STORE_KEY, conn=conn
        )
        assert cost is not None
        # One pound of pork at 21.4% protein is ~97g, so $2.49/lb is ~$0.026/g.
        assert cost.cost_per_gram_protein == pytest.approx(0.0257, abs=0.002)
        # And emphatically NOT the naive figure.
        assert cost.cost_per_gram_protein > 0.01

    def test_a_unit_item_prices_off_its_package(self, conn):
        row, fact = kroger.product_to_row(CHICKEN_PACKET, "Meat", "27401", NOW)
        kroger._upsert_food_fact(conn, fact)
        conn.commit()
        cost = savings.cost_per_gram_protein(
            row["dollar_price"], row["item_name"], kroger.STORE_KEY, conn=conn
        )
        assert cost is not None
        # 16 oz = 454g at 23.2% protein = ~105g; $4.99 / 105g = ~$0.047/g.
        assert cost.cost_per_gram_protein == pytest.approx(0.047, abs=0.005)

    def test_both_denominations_go_through_the_same_code_path(self):
        # The engine must not branch on sold_by -- that is what makes the trap
        # unrepeatable rather than merely fixed once. Both rows carry a size
        # that means "the quantity this price buys".
        weight_row, _ = kroger.product_to_row(PORK_LOIN, "Meat", "27401", NOW)
        unit_row, _ = kroger.product_to_row(CHICKEN_PACKET, "Meat", "27401", NOW)
        for row in (weight_row, unit_row):
            assert savings.parse_size(row["item_name"]) is not None

    def test_sold_by_is_persisted_for_the_ui_tag(self):
        weight_row, _ = kroger.product_to_row(PORK_LOIN, "Meat", "27401", NOW)
        unit_row, _ = kroger.product_to_row(CHICKEN_PACKET, "Meat", "27401", NOW)
        assert weight_row["sold_by"] == "WEIGHT"
        assert unit_row["sold_by"] == "UNIT"
        # The description carries the /lb marker so a glance is not misleading
        # even before the GUI tag lands (GFP-36/37/38/48/50/52).
        assert "/lb" in weight_row["deal_description"]
        assert "/lb" not in unit_row["deal_description"]


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #
def test_protein_density_needs_both_halves():
    assert kroger.protein_per_100g(PORK_LOIN) == pytest.approx(21.43, abs=0.01)
    assert kroger.protein_per_100g(NO_NUTRITION) is None
    # A serving stated in a non-weight unit cannot become grams without a
    # density we do not have, so it is None rather than a guess.
    no_weight = {"nutritionInformation": _nutrition(20, 240, serving_unit="Millilitre")}
    assert kroger.protein_per_100g(no_weight) is None


def test_promo_price_beats_regular():
    assert kroger.extract_price(kroger.first_item(PORK_LOIN)) == (2.49, 2.99)


def test_missing_price_is_none_not_zero():
    price, regular = kroger.extract_price(kroger.first_item(NO_PRICE))
    assert price is None and regular is None


def test_price_per_unit_uom_follows_the_denomination():
    assert kroger.extract_price_per_unit(kroger.first_item(PORK_LOIN), "WEIGHT") == (2.49, "lb")
    assert kroger.extract_price_per_unit(kroger.first_item(CHICKEN_PACKET), "UNIT") == (4.99, "each")


def test_only_a_weight_readable_size_is_folded_into_the_name():
    # Same discipline as wholefoods (GFP-73): the name is what parse_size
    # reads, so nothing approximate may be laundered into it.
    assert kroger.display_item_name("Thing", "6 ct") == "Thing"
    assert kroger.display_item_name("Thing", None) == "Thing"
    assert kroger.display_item_name("Thing", "16 oz") == "Thing, 16 oz"


def test_row_without_nutrition_still_becomes_a_deal():
    row, fact = kroger.product_to_row(NO_NUTRITION, "Household", "27401", NOW)
    assert fact is None
    assert row["dollar_price"] == 8.99
    assert row["sold_by"] == "UNIT"


def test_row_without_a_price_is_marked_not_faked():
    row, _ = kroger.product_to_row(NO_PRICE, "Meat", "27401", NOW)
    assert row["dollar_price"] is None
    assert "price not listed" in row["deal_type"]
    assert "price_missing=true" in row["notes"]


# --------------------------------------------------------------------------- #
# Store resolution -- the false-negative trap from GFP-77
# --------------------------------------------------------------------------- #
GROCERY = {"locationId": "09700347", "chain": "HART",
           "name": "Harris Teeter - Lawndale Crossing"}
FUEL = {"locationId": "09799001", "chain": "HARRIS TEETER FUEL",
        "name": "Harris Teeter Fuel Center"}
KROGER_STORE = {"locationId": "01400335", "chain": "KROGER", "name": "Kroger - Oxford"}


def test_picks_the_grocery_store_by_chain_code():
    assert kroger.pick_store([KROGER_STORE, GROCERY])["locationId"] == "09700347"


def test_never_picks_a_fuel_centre():
    """GFP-77's false-negative trap, inverted.

    'HARRIS TEETER FUEL' is the only thing in /v1/chains that READS as Harris
    Teeter, and it is a petrol station. Matching on the readable name instead
    of the HART code is how the spike first concluded, wrongly, that Harris
    Teeter was not in the catalog.
    """
    assert kroger.pick_store([FUEL]) is None
    assert kroger.pick_store([FUEL, GROCERY])["locationId"] == "09700347"


def test_no_store_at_all_is_none():
    assert kroger.pick_store([KROGER_STORE]) is None
    assert kroger.pick_store([]) is None


# --------------------------------------------------------------------------- #
# Credentials
# --------------------------------------------------------------------------- #
def test_credentials_read_from_an_ini_with_a_section(tmp_path):
    path = tmp_path / "kroger-env.config"
    path.write_text("[kroger]\nclient_id = abc\nclient_secret = xyz\n", encoding="utf-8")
    creds = kroger.load_credentials(path)
    assert (creds.client_id, creds.client_secret) == ("abc", "xyz")


def test_credentials_read_from_a_bare_key_value_file(tmp_path):
    # An "env config" is as likely to be written without a section header;
    # failing on that would be a pointless papercut.
    path = tmp_path / "kroger-env.config"
    path.write_text("client_id=abc\nclient_secret=xyz\n", encoding="utf-8")
    assert kroger.load_credentials(path).client_id == "abc"


def test_credentials_tolerate_a_utf8_bom(tmp_path):
    # PowerShell 5.1's `Out-File -Encoding utf8` writes a BOM, and a BOM before
    # a section header makes configparser fail obscurely -- the same trap
    # GFP-93 hit with the Whole Foods session file.
    path = tmp_path / "kroger-env.config"
    path.write_text("﻿[kroger]\nclient_id = abc\nclient_secret = xyz\n", encoding="utf-8")
    assert kroger.load_credentials(path).client_secret == "xyz"


def test_missing_credentials_say_how_to_get_them(tmp_path):
    with pytest.raises(kroger.CredentialsMissingError, match="developer.kroger.com"):
        kroger.load_credentials(tmp_path / "nope.config")


def test_readiness_is_false_without_credentials(tmp_path):
    ready, reason = kroger.readiness(tmp_path / "nope.config")
    assert ready is False
    assert "credentials" in reason


def test_readiness_is_true_once_the_file_exists(tmp_path):
    path = tmp_path / "kroger-env.config"
    path.write_text("client_id=a\nclient_secret=b\n", encoding="utf-8")
    assert kroger.readiness(path) == (True, "")


# --------------------------------------------------------------------------- #
# Orchestration, against a fake client
# --------------------------------------------------------------------------- #
class FakeClient:
    def __init__(self, locations, products_by_term):
        self._locations = locations
        self._products = products_by_term
        self.calls: list[str] = []

    def locations_near(self, postal_code, radius_miles=25):
        return self._locations

    def products(self, term, location_id, limit=50):
        self.calls.append(term)
        return self._products.get(term, [])

    def close(self):
        pass


def test_scrape_maps_products_and_reports_stats(conn):
    client = FakeClient([GROCERY], {"chicken breast": [PORK_LOIN, CHICKEN_PACKET]})
    rows, meta, stats = kroger.scrape(
        postal_code="27401", queries=[("chicken breast", "Meat")],
        conn=conn, client=client, now=NOW,
    )
    assert len(rows) == 2
    assert stats["priced"] == 2
    assert stats["with_protein"] == 2
    assert stats["sold_by_weight"] == 1
    assert meta["location_id"] == "09700347"
    assert meta["chain"] == "HART"


def test_scrape_deduplicates_a_product_returned_by_several_terms(conn):
    client = FakeClient([GROCERY], {"chicken breast": [CHICKEN_PACKET],
                                    "turkey": [CHICKEN_PACKET]})
    rows, _, stats = kroger.scrape(
        postal_code="27401",
        queries=[("chicken breast", "Meat"), ("turkey", "Meat")],
        conn=conn, client=client, now=NOW,
    )
    # The same product legitimately answers several searches; counts should
    # mean products, not search hits.
    assert len(rows) == 1
    assert stats["total"] == 1


def test_scrape_refuses_when_there_is_no_harris_teeter_nearby(conn):
    client = FakeClient([KROGER_STORE, FUEL], {})
    with pytest.raises(kroger.NoStoreFoundError, match="HART"):
        kroger.scrape(postal_code="99999", queries=[("x", "y")], conn=conn, client=client)


def test_scrape_writes_nutrition_the_engine_can_read(conn):
    client = FakeClient([GROCERY], {"chicken breast": [CHICKEN_PACKET]})
    kroger.scrape(postal_code="27401", queries=[("chicken breast", "Meat")],
                  conn=conn, client=client, now=NOW)
    conn.commit()
    matched = conn.execute(
        "SELECT method, match_source FROM deal_food_match WHERE store=?",
        (kroger.STORE_KEY,),
    ).fetchone()
    assert matched["method"] == "kroger_api_direct"
    # MANUAL so the keyword auto-matcher can never downgrade a figure taken
    # from the retailer's own label for this exact product (as wholefoods does).
    from grocery_planner import matching
    assert matched["match_source"] == matching.MANUAL


# --------------------------------------------------------------------------- #
# GFP-99 -- product page and image links
#
# The URI below is a REAL one, copied verbatim from a live API response, and
# https://www.harristeeter.com + its path was opened in a browser and confirmed
# to resolve to the product page. That verification cannot be automated: the
# storefront read-times-out for httpx, so any test that fetched it would either
# hang or fail for a reason unrelated to this code.
# --------------------------------------------------------------------------- #
REAL_URI = (
    "/p/harris-teeter-boneless-chicken-breast-value-pack/0020895500000"
    "?cid=dis.api.tpi_products-api_20240521_b:all_c:p_t:decohenpartners-bbcg"
)

WITH_LINKS = {
    "productId": "0020895500000",
    "description": "Harris Teeter Boneless Chicken Breast Value Pack",
    "productPageURI": REAL_URI,
    "images": [
        {"perspective": "back",
         "sizes": [{"size": "large",
                    "url": "https://www.kroger.com/product/images/large/back/0020895500000"}]},
        {"perspective": "front",
         "sizes": [{"size": "xlarge",
                    "url": "https://www.kroger.com/product/images/xlarge/front/0020895500000"}]},
    ],
    "items": [{"price": {"regular": 1.99}, "size": "1 lb", "soldBy": "WEIGHT"}],
}


def test_the_product_url_is_built_from_the_api_uri_not_guessed():
    url = kroger.product_page_url(WITH_LINKS)
    assert url == (
        "https://www.harristeeter.com"
        "/p/harris-teeter-boneless-chicken-breast-value-pack/0020895500000"
    )


def test_the_campaign_tag_is_stripped():
    """It identifies OUR developer account; it does not belong in a client's link."""
    assert "cid=" not in kroger.product_page_url(WITH_LINKS)
    assert "decohenpartners" not in kroger.product_page_url(WITH_LINKS)


def test_a_missing_uri_yields_no_link_rather_than_a_bare_host():
    """A link to the homepage is not a link to the product (GFP-38)."""
    assert kroger.product_page_url({"productId": "1"}) is None
    assert kroger.product_page_url({"productPageURI": ""}) is None
    assert kroger.product_page_url({"productPageURI": "   "}) is None


def test_an_absolute_uri_is_refused_rather_than_double_prefixed():
    """If the API ever returns a full URL, prefixing a host makes nonsense."""
    absolute = {"productPageURI": "https://www.harristeeter.com/p/x/1"}
    assert kroger.product_page_url(absolute) is None


def test_the_front_image_is_preferred_over_the_back():
    assert kroger.product_image_url(WITH_LINKS) == (
        "https://www.kroger.com/product/images/xlarge/front/0020895500000"
    )


def test_any_perspective_beats_no_image():
    only_back = {"images": [{"perspective": "back",
                             "sizes": [{"url": "https://example.invalid/back.jpg"}]}]}
    assert kroger.product_image_url(only_back) == "https://example.invalid/back.jpg"


def test_no_images_is_none_not_an_empty_string():
    assert kroger.product_image_url({}) is None
    assert kroger.product_image_url({"images": []}) is None
    assert kroger.product_image_url({"images": [{"perspective": "front", "sizes": []}]}) is None


def test_a_scraped_row_carries_both_links():
    row, _ = kroger.product_to_row(WITH_LINKS, "Meat", "27401", NOW)
    assert row["source_url"].startswith("https://www.harristeeter.com/p/")
    assert row["image_url"].startswith("https://www.kroger.com/product/images/")


def test_a_row_from_a_payload_without_links_still_scrapes():
    """The GFP-38 contract: no link degrades to plain text, never a dead control."""
    row, _ = kroger.product_to_row(NO_PRICE, "Meat", "27401", NOW)
    assert row["source_url"] is None
    assert row["image_url"] is None
