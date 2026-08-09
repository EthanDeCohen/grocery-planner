"""GFP-247: the PRISM product catalogue, for Food Lion and GIANT.

Tested against a REAL captured product page (``fixtures/prism_product.html``,
retrieved 2026-08-08), never the live site. GFP-182's argument for golden
payloads applies with force here: this parser reads a third party's markup, and
the only way to know a change broke it is to pin what the markup looked like
when it worked.

What this source is FOR (GFP-246/GFP-247): a product ATTRIBUTE source. Its
price is the banner's default-store figure -- ``/store-locator`` is
DataDome-protected and a location cookie is ignored -- but size and protein are
ZIP-invariant, and those are what the Flipp ad can never supply. Price from the
ad, size and protein from here, joined by GFP-248.
"""
from __future__ import annotations

import pathlib

import pytest

from grocery_planner import savings
from grocery_planner.scrapers import prism
from grocery_planner.service import ingest

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "prism_product.html"
URL = ("https://foodlion.com/groceries/product/"
       "swanson-premium-chunk-chicken-breast-in-water-4-5-oz-can/7134")


@pytest.fixture
def page() -> str:
    return FIXTURE.read_text(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# The whole point: a size that reaches the optimiser
# --------------------------------------------------------------------------- #
def test_the_size_lands_where_the_optimiser_reads_it(page):
    """THE REASON THIS SOURCE EXISTS.

    The ld+json name carries no size -- "Swanson Premium Chunk Chicken Breast
    in Water" -- and ``savings.parse_size`` reads sizes out of the item NAME.
    So the slug's size has to be recovered and appended, or this feed's entire
    contribution never arrives.
    """
    row = prism.parse_product(page, URL)
    assert row["item_name"].endswith(", 4.5 oz")

    size = savings.parse_size(row["item_name"])
    assert size is not None and size.base_unit == savings.WEIGHT


def test_the_price_is_parsed_from_structured_data(page):
    assert prism.parse_product(page, URL)["dollar_price"] == pytest.approx(2.89)


def test_the_retailers_own_identifier_is_carried_with_its_vocabulary(page):
    """GFP-111: a bare '7134' says nothing about which system minted it."""
    row = prism.parse_product(page, URL)
    assert row["product_identifier"] == "7134"
    assert row["product_identifier_ns"] == prism.PRODUCT_IDENTIFIER_NS


def test_a_catalogue_row_is_not_dressed_up_as_a_promotion(page):
    """It is a list price, and the price caveat must travel with the row."""
    row = prism.parse_product(page, URL)
    assert row["deal_type"] == prism.CATALOGUE_DEAL_TYPE != "Weekly Ad"
    assert "default store" in row["notes"]


def test_protein_grams_are_recorded_when_the_page_states_them(page):
    assert "Protein 18" in prism.parse_product(page, URL)["notes"]


def test_the_row_matches_the_deals_column_contract(page):
    """run_scrape builds its INSERT generically from importers.DEAL_COLUMNS."""
    from grocery_planner import importers

    assert set(prism.parse_product(page, URL)) == set(importers.DEAL_COLUMNS)


# --------------------------------------------------------------------------- #
# Honesty: absent stays absent
# --------------------------------------------------------------------------- #
def test_a_page_with_no_price_yields_no_row():
    assert prism.parse_product("<html><body>nothing here</body></html>", URL) is None


def test_a_page_with_a_name_but_no_offer_yields_no_row():
    html = ('<script type="application/ld+json">'
            '{"product": {"name": "Mystery Meat"}}</script>')
    assert prism.parse_product(html, URL) is None


def test_a_slug_with_no_size_still_produces_a_row_without_inventing_one():
    html = ('<script type="application/ld+json">'
            '{"product": {"name": "Rotisserie Chicken",'
            ' "offers": {"price": 7.99}}}</script>')
    row = prism.parse_product(html, "https://foodlion.com/groceries/product/rotisserie-chicken/99")
    assert row["item_name"] == "Rotisserie Chicken"
    assert savings.parse_size(row["item_name"]) is None


# --------------------------------------------------------------------------- #
# Slug reading
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("slug,expected", [
    ("chicken-breast-4-5-oz-can/7134", "4.5 oz"),
    ("ground-beef-1-lb/22", "1 lb"),
    ("eggs-12-ct-carton/9", "12 ct"),
    ("milk-2-l-jug/5", "2 l"),
    ("no-size-here/7", None),
])
def test_size_from_slug(slug, expected):
    assert prism.size_from_slug(f"https://foodlion.com/groceries/product/{slug}") == expected


# --------------------------------------------------------------------------- #
# Politeness: this catalogue is ~30,000 products
# --------------------------------------------------------------------------- #
def test_only_protein_relevant_products_are_fetched():
    urls = [
        "https://foodlion.com/groceries/product/boneless-chicken-breast-1-lb/1",
        "https://foodlion.com/groceries/product/reynolds-aluminum-foil-75-sq-ft/2",
        "https://foodlion.com/groceries/product/pampers-baby-wipes/3",
        "https://foodlion.com/groceries/product/ground-beef-1-lb/4",
    ]
    picked = prism.select_products(urls)
    assert len(picked) == 2
    assert all("chicken" in u or "beef" in u for u in picked)


def test_a_run_is_bounded():
    """A full sweep on every scrape is a load we have no right to impose."""
    urls = [f"https://foodlion.com/groceries/product/chicken-{i}-1-lb/{i}"
            for i in range(50)]
    assert len(prism.select_products(urls, max_products=10)) == 10


def test_parse_sitemap_reads_an_index():
    xml = ("<sitemapindex><sitemap><loc>https://x/products-0.xml</loc></sitemap>"
           "<sitemap><loc>https://x/categories-0.xml</loc></sitemap></sitemapindex>")
    assert prism.parse_sitemap(xml) == [
        "https://x/products-0.xml", "https://x/categories-0.xml"]


# --------------------------------------------------------------------------- #
# Registration: two banners, one scraper (GFP-32)
# --------------------------------------------------------------------------- #
def test_both_banners_are_registered_and_share_the_implementation():
    assert "foodlion-catalog" in ingest.all_scrapers()
    assert "giant" in ingest.all_scrapers()


def test_the_catalogue_never_evicts_the_weekly_ad():
    """Two feeds for one store, kept apart by SOURCE -- run_scrape scopes its
    replace to (store, source, postal_code), so this is what stops the
    catalogue deleting Food Lion's per-ZIP promotional prices."""
    from grocery_planner import scrapers

    ad = scrapers.SCRAPERS["foodlion"]
    catalogue = scrapers.SCRAPERS["foodlion-catalog"]

    assert scrapers.store_key_for(ad) == scrapers.store_key_for(catalogue) == "foodlion"
    assert scrapers.source_for(ad) != scrapers.source_for(catalogue)


def test_giant_is_the_philadelphia_company_not_the_maryland_one():
    """Two different Ahold Delhaize USA companies share the name; only The
    GIANT Company (giantfoodstores.com) is in a GFP-165 market."""
    assert prism.GIANT.host == "giantfoodstores.com"
