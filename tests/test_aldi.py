"""ALDI storefront -- GFP-265. The tenant's own facts, not the platform's.

The shared machinery is tested in ``test_instacart_storefront.py``. What is
tested here is what makes ALDI different from Sprouts, and every one of those
differences is a negative: it publishes no nutrition, so it must not claim any;
its shop list is ordered differently, so it must not be picked by position; its
registry key is already taken, so it must not evict the incumbent.

Negatives are worth pinning precisely because nothing breaks when they rot. A
scraper that quietly starts inventing protein densities looks healthier than one
that reports zero.

No network.
"""
from __future__ import annotations

from datetime import datetime, timezone

from grocery_planner import savings
from grocery_planner.scrapers import (
    aldi, flipp_banners, instacart_storefront as ist, sprouts,
)
from tests.test_instacart_storefront import Recorder, client_for

NOW = datetime(2026, 8, 11, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# The headline: nutrition-blocked, and honest about it
# --------------------------------------------------------------------------- #
def test_aldi_declares_itself_nutrition_blocked():
    """Measured over the full catalogue: nutritionalInfo is null everywhere.

    The pinned hash is accepted and the envelope is well formed -- the data is
    simply not published -- so this must read as an absence of data, never as a
    broken client.
    """
    available, why = aldi.nutrition_available()
    assert available is False
    assert "null" in why.lower() or "not published" in why.lower()


def test_the_recorded_coverage_is_internally_consistent():
    """A coverage claim has to be a set of numbers that can all be true at once.

    Asserted as relationships so re-measuring is a data edit, not a test edit:
    every panel count is bounded by the catalogue, protein by panels, and a
    computable density by protein. If ALDI ever starts publishing, whoever
    updates COVERAGE cannot leave it self-contradictory.
    """
    c = aldi.COVERAGE
    assert c["products_in_sitemap"] > 0
    assert 0 <= c["nutrition_panels"] <= c["products_in_sitemap"]
    assert 0 <= c["protein_above_zero"] <= c["nutrition_panels"]
    assert 0 <= c["computable_protein_density"] <= c["protein_above_zero"]
    assert c["shop_id"] == aldi.DEFAULT_SHOP_ID


def test_no_canary_because_there_is_nothing_to_point_one_at():
    """A canary proves the pin still resolves. ALDI has no panel to prove it
    against, so the honest value is None -- not a hopeful product id whose
    failure would be misread as a rotated hash."""
    assert aldi.CANARY_PRODUCT_ID is None
    ok, why = aldi.verify_pinned_hashes()      # returns before any network call
    assert ok is False
    assert "canary" in why.lower()


def test_verification_failure_is_not_reported_as_a_rotated_hash():
    """The two failures need different remedies -- re-capture a hash, versus
    accept that the tenant has no data -- so they must not read alike."""
    _ok, why = aldi.verify_pinned_hashes()
    assert "reject" not in why.lower()
    assert "re-capture" not in why.lower()


def test_an_aldi_row_never_carries_a_protein_figure_it_does_not_have():
    """The row still ships -- price and size are real and useful -- but with no
    density, and therefore no food fact for the optimiser to trust."""
    listing = ist.Listing(
        product_id="21171551", slug="21171551-bbq-sauce-18-5-oz",
        name="Original BBQ Sauce", brand="Ray's", category="Barbecue Sauces",
        size="18.5 oz", price=3.22, availability="InStock",
    )
    row, fact = aldi.listing_to_row(listing, None, "27401", NOW)

    assert fact is None
    assert row["dollar_price"] == 3.22
    assert "protein_per_100g" not in row["notes"]
    assert "protein_per_serving_g" not in row["notes"]


def test_aldi_is_still_a_real_price_source():
    """Nutrition-blocked is not useless. A price with a parseable size is
    exactly what the Flipp-sourced stores contribute."""
    listing = ist.Listing(
        product_id="1", slug="1-ground-beef", name="Ground Beef", brand="Kirkwood",
        category="Meat", size="per lb", price=4.29, availability="InStock",
    )
    row, _fact = aldi.listing_to_row(listing, None, "27401", NOW)
    assert row["dollar_price"] == 4.29
    assert row["sold_by"] == "WEIGHT"
    # The size has to survive into the name, or price and size stop agreeing.
    assert savings.parse_size(row["item_name"]) is not None


# --------------------------------------------------------------------------- #
# The shop: three ids, one physical store
# --------------------------------------------------------------------------- #
def test_the_default_shop_is_the_instore_one():
    """6823 is delivery and 22443 is pickup; all three are the same store
    (retailerLocationId 124437). Shelf price is what this project compares."""
    rec = Recorder([("6823", "delivery"), ("22443", "pickup"), ("515201", "instore")])
    context = client_for(aldi.TENANT, rec).shop_context("27401")
    assert context.shop_id == aldi.DEFAULT_SHOP_ID
    assert context.service_type == "instore"


def test_aldi_and_sprouts_defaults_are_not_the_same_shop():
    """Adjacent numbers (515201/515202) are exactly the kind of pair a
    copy-paste turns into a silent cross-tenant bug."""
    assert aldi.DEFAULT_SHOP_ID != sprouts.DEFAULT_SHOP_ID
    assert aldi.RETAILER_ID != sprouts.RETAILER_ID


def test_the_sitemap_host_key_is_not_the_sprouts_shape():
    """`shop_aldi_com` -- what copying Sprouts' pattern produces -- returns 403.
    A wrong sitemap URL fails as an empty catalogue, not as an error."""
    assert aldi.SITEMAP_HOST_KEY in aldi.TENANT.sitemap_index
    assert "shop_aldi_com" not in aldi.TENANT.sitemap_index
    assert aldi.TENANT.sitemap_index.startswith(aldi.BASE_URL)


# --------------------------------------------------------------------------- #
# Registry: a second source for a store that already had one
# --------------------------------------------------------------------------- #
def test_aldi_storefront_does_not_evict_the_flipp_aldi_banner():
    """The two complement each other -- Flipp has BOGO and coupons the
    storefront does not; the storefront has sizes the weekly ad never has -- so
    the registry key must differ. Asserted against the incumbent's real key
    rather than a literal, so renaming either one keeps this honest."""
    assert "aldi" in flipp_banners.MODULES
    assert aldi.SCRAPER_KEY not in flipp_banners.MODULES
    assert aldi.SCRAPER_KEY != aldi.STORE_KEY


def test_both_feeds_claim_the_same_store_but_different_sources():
    """One store identity (a shopper shops at ALDI), two feeds that must not
    delete each other: ingest scopes its replace to (store, source, zip)."""
    banner = flipp_banners.MODULES["aldi"]
    assert aldi.STORE_KEY == getattr(banner, "STORE_KEY", None)
    assert aldi.SOURCE != getattr(banner, "SOURCE", "scrape")


def test_the_two_storefront_tenants_do_not_collide_with_each_other():
    assert aldi.SCRAPER_KEY != sprouts.SCRAPER_KEY
    assert aldi.STORE_KEY != sprouts.STORE_KEY


def test_readiness_is_ready_but_says_what_is_missing():
    """'Ready' alone would let a user expect Sprouts-like behaviour, and the
    store table is the first place that should correct that."""
    ready, message = aldi.readiness()
    assert ready is True
    assert "nutrition" in message.lower()


# --------------------------------------------------------------------------- #
# The price bound must be visible, never silent
# --------------------------------------------------------------------------- #
def test_scrape_bounds_the_html_crawl_and_reports_the_bound(conn):
    """The product-page path returned a hard 403 after ~2,300 pages on Sprouts.

    So ALDI's scrape is bounded -- and the bound has to show up in stats next to
    the catalogue size, or a short scrape reads as 'the store shrank' rather
    than 'we stopped on purpose'. That is the no-silent-caps rule.
    """
    catalogue = [f"{i}-product-{i}" for i in range(50)]
    rec = Recorder([("6823", "delivery"), ("515201", "instore")])
    client = client_for(aldi.TENANT, rec)

    rows, meta, stats = aldi.scrape(
        postal_code="27401", limit=7, conn=conn, client=client,
        now=NOW, slugs=catalogue,
    )

    assert len(rows) == 7
    assert stats["price_limit"] == 7
    assert stats["products_seen"] == len(catalogue)
    assert stats["price_limit"] < stats["products_seen"]
    assert stats["shop_service_type"] == "instore"
    assert meta["retailer_id"] == aldi.RETAILER_ID


def test_scrape_never_runs_unbounded(conn):
    """``limit=None`` means 'use the default bound', not 'crawl 15,256 pages'."""
    catalogue = [f"{i}-product-{i}" for i in range(3)]
    rec = Recorder([("515201", "instore")])
    rows, _meta, stats = aldi.scrape(
        postal_code="27401", limit=None, conn=conn,
        client=client_for(aldi.TENANT, rec), now=NOW, slugs=catalogue,
    )
    assert stats["price_limit"] == aldi.DEFAULT_PRICE_LIMIT
    assert stats["price_limit"] is not None
    assert len(rows) == len(catalogue)


def test_the_default_bound_stays_under_the_measured_wall():
    """~2,300 pages tripped a hard 403 on Sprouts, and ALDI is assumed to be
    policed at least as tightly until measured otherwise."""
    assert 0 < aldi.DEFAULT_PRICE_LIMIT < 2300


def test_the_skipped_nutrition_pass_is_stated_rather_than_implied(conn):
    """``no_nutrition_panel == products_seen`` could mean 'we asked and got
    nothing' or 'we never asked'. Those are different facts about the store."""
    rec = Recorder([("515201", "instore")])
    _rows, _meta, stats = aldi.scrape(
        postal_code="27401", conn=conn, client=client_for(aldi.TENANT, rec),
        now=NOW, slugs=["1-a", "2-b"],
    )
    assert "skipped" in stats["nutrition_pass"]
    assert "ProductNutritionalInfo" not in rec.ops()


def test_no_food_facts_are_written_for_a_nutrition_blocked_tenant(conn):
    """A density ALDI never published must not appear in food_nutrients."""
    rec = Recorder([("515201", "instore")])
    _rows, _meta, stats = aldi.scrape(
        postal_code="27401", conn=conn, client=client_for(aldi.TENANT, rec),
        now=NOW, slugs=["1-a", "2-b"],
    )
    assert stats["with_protein"] == 0
    written = conn.execute(
        "SELECT COUNT(*) AS n FROM foods WHERE source=?", (aldi.STORE_KEY,)
    ).fetchone()["n"]
    assert written == 0
