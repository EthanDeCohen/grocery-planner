"""GFP-165: Wegmans' own JSON API -- the best source found outside Kroger.

Tested against REAL captured payloads (``fixtures/wegmans_product.json``,
``fixtures/wegmans_stores.json``, retrieved 2026-08-10), never the live API.
GFP-182's argument applies: this parser reads a third party's JSON, and the only
way to know a change broke it is to pin what it looked like when it worked.

What makes this source different from every other one probed:

* a REAL per-store price -- the same SKU was $1.69 at store 140 and $1.79 at
  store 48, where PRISM and Albertsons serve one default figure;
* ``packSize``, a machine-readable size, where Flipp gives one on 0-5% of rows;
* protein grams WITH the serving size in grams, so per-100g is arithmetic
  rather than a USDA name match;
* 114 stores with ZIP and coordinates, which answers GFP-257 exactly.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from grocery_planner import savings
from grocery_planner.scrapers import wegmans_api as wegmans

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture
def product() -> dict:
    return json.loads((FIXTURES / "wegmans_product.json").read_text(encoding="utf-8"))


@pytest.fixture
def store() -> wegmans.Store:
    return wegmans.Store("140", "Chapel Hill", "Chapel Hill", "NC", "27514",
                         35.9, -79.0)


# --------------------------------------------------------------------------- #
# The thing no other source gives: protein without a name match
# --------------------------------------------------------------------------- #
def test_protein_comes_from_the_retailers_own_panel(product):
    """THE REASON THIS SOURCE MATTERS. The panel states grams of protein AND a
    serving size in grams, so per-100g is arithmetic -- no matching, no
    confidence score, no USDA lookup."""
    per_100g = wegmans.protein_per_100g(product)
    assert per_100g is not None
    assert 15 < per_100g < 35, f"implausible protein density: {per_100g}"


def test_protein_is_absent_when_the_serving_is_not_in_grams():
    """A protein-per-serving with an unknown serving is not a per-100g figure.
    Guessing the serving would make an item look cheaper per gram than it is --
    savings.py rule 4's dangerous direction."""
    assert wegmans.protein_per_100g({"nutrition": {
        "serving": {"servingSize": "1", "servingSizeUom": "cup"},
        "nutritions": [{"general": [{"name": "Protein", "quantity": 8}]}]}}) is None


def test_protein_is_absent_when_there_is_no_panel():
    assert wegmans.protein_per_100g({}) is None
    assert wegmans.protein_per_100g({"nutrition": {"serving": {}}}) is None


# --------------------------------------------------------------------------- #
# The row
# --------------------------------------------------------------------------- #
def test_the_size_lands_where_the_optimiser_reads_it(product, store):
    """savings.parse_size reads the item NAME, so packSize has to be appended
    there or this feed's machine-readable size never arrives -- the same trap
    prism.py had to solve for its slug."""
    row, fact = wegmans.to_row(product, store)
    size = savings.parse_size(row["item_name"])
    assert size is not None and size.base_unit == savings.WEIGHT


def test_the_row_carries_a_real_per_store_price(product, store):
    row, fact = wegmans.to_row(product, store)
    assert row["dollar_price"] == pytest.approx(13.49)
    assert row["deal_type"] == "Shelf price"
    assert f"Store {store.number}" in row["notes"]


def test_the_row_records_the_upc_and_the_protein_it_found(product, store):
    """A UPC resolves to protein through FoodData Central (GFP-24) even when
    the panel does not -- so it is worth carrying either way."""
    notes = wegmans.to_row(product, store)[0]["notes"]
    assert "UPC" in notes
    assert "Protein" in notes and "g/100g" in notes


def test_the_identifier_carries_its_vocabulary(product, store):
    """GFP-111: a bare SKU says nothing about which system minted it, and must
    never be compared with a Kroger productId or a PRISM product id."""
    row, fact = wegmans.to_row(product, store)
    assert row["product_identifier"] == str(product["skuId"])
    assert row["product_identifier_ns"] == "wegmans.sku"


def test_the_row_matches_the_deals_column_contract(product, store):
    from grocery_planner import importers

    assert set(wegmans.to_row(product, store)[0]) == set(importers.DEAL_COLUMNS)


def test_a_product_with_no_price_yields_no_row(store):
    """Absent stays absent: an unpriced product is not a row with a guess."""
    assert wegmans.to_row({"skuId": "1", "name": "Mystery"}, store) == (None, None)
    assert wegmans.to_row({"skuId": "1", "name": "X",
                           "price_inStore": {"amount": 0}}, store) == (None, None)


def test_sold_by_weight_is_carried_from_the_source(product, store):
    """GFP-98: a WEIGHT price buys one pound while a UNIT price buys the
    package, and shown identically they invite a wrong buying decision."""
    row, fact = wegmans.to_row(product, store)
    assert row["sold_by"] in {"WEIGHT", "UNIT"}


# --------------------------------------------------------------------------- #
# Store resolution -- availability (GFP-257) answered exactly
# --------------------------------------------------------------------------- #
@pytest.fixture
def store_list(monkeypatch):
    rows = json.loads((FIXTURES / "wegmans_stores.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(wegmans, "stores", lambda client=None: [
        wegmans.Store(str(r["storeNumber"]), r.get("name", ""), r.get("city", ""),
                      r.get("stateAbbreviation", ""),
                      str(r.get("zip") or r.get("iwsZip") or "")[:5],
                      r.get("latitude"), r.get("longitude")) for r in rows])
    return rows


def test_a_zip_with_a_store_resolves_to_it(store_list):
    found = wegmans.store_for("27514")
    assert found is not None and found.state == "NC"


def test_a_zip_with_no_store_anywhere_near_resolves_to_nothing(store_list):
    assert wegmans.store_for("90210") is None


def test_serves_is_asked_not_declared(store_list):
    """No SERVICE_AREA anywhere -- a hand-written prefix list was measurably
    wrong for Food Lion (it claimed all of Kentucky)."""
    assert not hasattr(wegmans, "SERVICE_AREA")
    assert wegmans.serves("27514") is True
    assert wegmans.serves("90210") is False


def test_an_unreachable_store_list_is_unknown_not_unavailable(monkeypatch):
    """A network failure must not delete a store for a 60-day TTL."""
    def _boom(client=None):
        raise __import__("httpx").HTTPError("down")

    monkeypatch.setattr(wegmans, "stores", _boom)
    assert wegmans.serves("27514") is None


# --------------------------------------------------------------------------- #
# Registration: a second source for a store that already has one
# --------------------------------------------------------------------------- #
def test_the_api_never_evicts_the_weekly_ad():
    """Same STORE_KEY, different SOURCE, so run_scrape's replace scope keeps
    the Flipp ad and the API apart -- as it does for harristeeter."""
    from grocery_planner import scrapers

    ad = scrapers.SCRAPERS["wegmans"]
    api = scrapers.SCRAPERS["wegmans-api"]
    assert scrapers.store_key_for(ad) == scrapers.store_key_for(api) == "wegmans"
    assert scrapers.source_for(ad) != scrapers.source_for(api)


def test_the_item_name_is_the_product_not_the_brand(product, store):
    """FOUND BY RUNNING IT. Reading `name`/`consumerBrandName` produced rows
    called "Wegmans, 1 lb" -- the brand plus a size, which identifies nothing
    and matches nothing. productName carries the full retail name."""
    name = wegmans.to_row(product, store)[0]["item_name"]
    assert "Top Round Cutlets" in name
    assert name.lower() != "wegmans, 1 lb"


def test_a_product_with_no_productName_falls_back_rather_than_failing(store):
    row, _ = wegmans.to_row({"skuId": "9", "consumerBrandName": "Acme",
                          "webProductDescription": "Boneless Chicken Breast",
                          "packSize": "1 lb.",
                          "price_inStore": {"amount": 5.99}}, store)
    assert row["item_name"] == "Acme Boneless Chicken Breast, 1 lb"


# --------------------------------------------------------------------------- #
# The protein reaches the ENGINE, not just the notes
# --------------------------------------------------------------------------- #
def test_a_product_with_a_panel_yields_a_food_fact(product, store):
    """The retailer states this product's protein, so the engine must be able
    to use it instead of matching a name to a USDA food."""
    row, fact = wegmans.to_row(product, store)
    assert row is not None and fact is not None
    assert fact.sku == str(product["skuId"])
    assert 15 < fact.protein_per_100g < 35


def test_a_product_without_a_panel_still_yields_a_row(store):
    """A shelf price with no nutrition is still a real shelf price. Absent
    stays absent -- it just gets no fact."""
    row, fact = wegmans.to_row(
        {"skuId": "5", "productName": "Mystery Roast", "packSize": "1 lb.",
         "price_inStore": {"amount": 9.99}}, store)
    assert row is not None and fact is None


def test_the_fact_makes_the_row_priceable_without_any_name_matching(conn, store, product):
    """END TO END, and the point of the whole exercise. No USDA food, no
    keyword match -- the figure came off the retailer's own label."""
    row, fact = wegmans.to_row(product, store)
    wegmans.upsert_food_fact(conn, fact)
    conn.commit()

    priced = savings.cost_per_gram_protein(
        row["dollar_price"], row["item_name"], "wegmans", conn=conn)
    assert priced is not None
    assert priced.match_method == "wegmans_api_direct"
    assert priced.match_confidence == 1.0
    assert priced.protein_source == "wegmans"


def test_the_auto_matcher_never_overwrites_a_stated_figure(conn, store, product):
    """match_source=MANUAL is load-bearing: a keyword guess about a
    similarly-named food must not replace the retailer's own label."""
    from grocery_planner import matching

    row, fact = wegmans.to_row(product, store)
    wegmans.upsert_food_fact(conn, fact)
    conn.execute(
        "INSERT INTO deals(store, item_name, deal_type, dollar_price, source) "
        "VALUES ('wegmans', ?, 'Shelf price', ?, 'wegmans-api')",
        (row["item_name"], row["dollar_price"]))
    conn.commit()

    matching.match_deals(conn=conn)

    got = matching.get_match("wegmans", row["item_name"], conn=conn)
    assert got["method"] == "wegmans_api_direct"
    assert got["confidence"] == 1.0
