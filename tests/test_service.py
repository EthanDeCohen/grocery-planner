"""Tests for the shared front-end-agnostic service layer (GFP-14)."""
import pytest

from grocery_planner import service

# --------------------------------------------------------------------------- #
# GFP-43 — service.py became the service/ package; every name a front end
# imports must still resolve from grocery_planner.service so the split stays
# invisible to cli.py, gui/app.py, jobs.py and scheduler.py. A future re-split
# that silently drops an export should fail here, not in a call site.
# --------------------------------------------------------------------------- #
PUBLIC_API = [
    "DEAL_TYPE_GROUPS",
    # GFP-36: the price-trends pane needs the numbers behind its chart from the
    # same front-end-agnostic layer as everything else, so service.trends is
    # re-exported here like deals/ingest rather than reached into directly.
    "DEFAULT_WINDOW_DAYS",
    "MIN_POINTS_TO_PLOT",
    # GFP-40 widened that one question into two metrics over two dimensions,
    # so `gplan trends` and the chart share a definition instead of drifting.
    "Dimension",
    "Metric",
    "PriceTrend",
    "TrendPoint",
    "TrendSeries",
    "UnknownFoodError",
    "UnscopedPriceTrendError",
    "price_trend",
    "protein_price_trend",
    "EXPORT_COLUMNS",
    # GFP-71: EmptyScrapeError/ImplausibleCollapseError (and their shared base,
    # ScrapeGuardError) were previously reachable only via service.ingest,
    # unlike UnknownStoreError -- re-exported here for consistency, added per
    # the GFP-71 PR description.
    "EmptyScrapeError",
    "ImplausibleCollapseError",
    "ScrapeGuardError",
    # GFP-4: registered and ready are no longer the same question (Whole
    # Foods needs a hand-minted session cookie before it's scrapable) --
    # ScraperStatus/all_scrapers/scraper_status are the new vocabulary for
    # telling them apart; available_scrapers() itself now means "ready".
    "ScraperStatus",
    "UnknownDealTypeError",
    "UnknownStoreError",
    "all_scrapers",
    "available_scrapers",
    "best_deals",
    "count_deals",
    "deal_categories",
    "export_deals",
    "fetch_deals",
    "is_expired",
    "run_scrape",
    "scraper_status",
    "stores_with_deals",
    "today_iso",
]


def test_public_api_surface_is_unchanged():
    for name in PUBLIC_API:
        assert hasattr(service, name), f"grocery_planner.service lost {name!r}"
    assert set(service.__all__) == set(PUBLIC_API)


def test_available_scrapers_lists_known_stores():
    scrapers = service.available_scrapers()
    assert scrapers == sorted(scrapers)
    assert {"foodlion", "harristeeter"} <= set(scrapers)


def test_all_scrapers_includes_every_registered_store_regardless_of_readiness():
    # GFP-4: wholefoods is registered but (in this test environment, with no
    # hand-minted session cookie) not ready -- all_scrapers() must still name
    # it; available_scrapers() must not.
    # GFP-98 adds 'harristeeter-api' (Kroger shelf prices), which like
    # wholefoods is registered but not ready without credentials.
    assert set(service.all_scrapers()) == {
        "foodlion", "harristeeter", "harristeeter-api", "wholefoods",
    }


def test_scraper_status_unknown_store_raises():
    with pytest.raises(service.UnknownStoreError):
        service.scraper_status("not-a-real-store")


def test_scraper_status_defaults_to_ready_when_a_module_defines_no_readiness():
    # foodlion/harristeeter predate GFP-4 and were never touched by it --
    # confirming they still report ready is what "no existing scraper needs
    # to change" actually means.
    status = service.scraper_status("foodlion")
    assert status.ready is True
    assert status.reason == ""


def test_scraper_status_reflects_wholefoods_configuration(monkeypatch, tmp_path):
    from grocery_planner.scrapers import wholefoods as wf

    # No session file: not ready, with an actionable reason.
    monkeypatch.setattr(wf, "session_path", lambda: tmp_path / "wholefoods_session.json")
    status = service.scraper_status("wholefoods")
    assert status.ready is False
    assert "session cookie" in status.reason
    assert "wholefoods" not in service.available_scrapers()

    # A session file existing (even a trivial one) is enough for the cheap
    # readiness check -- full validation happens at actual scrape time.
    (tmp_path / "wholefoods_session.json").write_text('{"wfm_store_d8": "x"}', encoding="utf-8")
    status = service.scraper_status("wholefoods")
    assert status.ready is True
    assert status.reason == ""
    assert "wholefoods" in service.available_scrapers()


def test_run_scrape_unknown_store_raises(conn):
    # GFP-4 registered a real "wholefoods" scraper, so this test (which
    # predates that ticket and used "wholefoods" as its example of an
    # unregistered store) now uses a key that is guaranteed not to exist
    # instead -- flagged here per the GFP-4 PR description.
    with pytest.raises(service.UnknownStoreError):
        service.run_scrape("not-a-real-store", conn=conn)


# --------------------------------------------------------------------------- #
# GFP-54/GFP-55/GFP-39 — postal_code on deals, scoped DELETE, price_history
# --------------------------------------------------------------------------- #
def _fake_row(item_name, sale_price):
    return {
        "item_name": item_name, "sub_category": "Produce", "deal_type": "Weekly Ad",
        "deal_description": "", "regular_price": None, "sale_price": sale_price,
        "dollar_price": sale_price, "discount_amount": None, "discount_percent": None,
        "valid_from": "2026-06-08", "valid_to": "2026-06-16", "loyalty_required": "Y",
        "notes": "",
        # GFP-15: run_scrape's INSERT is built generically from every column
        # in importers.DEAL_COLUMNS (that's the point -- see ingest.py), so a
        # fake row exercised through it must supply every column that list
        # names, same as the real scraper row builders in scrapers/base.py
        # do. None here (no link/image/identifier for this synthetic row) --
        # these fields aren't what any test in this file is about.
        "source_url": None, "image_url": None,
        "flipp_flyer_id": None, "flipp_item_id": None, "flipp_coupon_id": None,
    }


class _FakeScraperModule:
    """Stands in for a grocery_planner.scrapers.<store> module so run_scrape
    can be exercised without any network access."""

    DEFAULT_POSTAL_CODE = "27401"

    def __init__(self, rows):
        self.rows = rows

    def scrape(self, postal_code=None, include_coupons=True):
        return list(self.rows), {"id": 1}, {"total": len(self.rows)}


@pytest.fixture
def fake_scraper(monkeypatch):
    """Registers a fake 'foodlion' scraper for the duration of one test."""
    from grocery_planner.service import ingest

    fake = _FakeScraperModule([])
    monkeypatch.setitem(ingest.SCRAPERS, "foodlion", fake)
    return fake


def test_run_scrape_writes_the_postal_code_it_scraped_for(conn, fake_scraper):
    fake_scraper.rows = [_fake_row("Apples", 0.99)]
    service.run_scrape("foodlion", postal_code="27409", conn=conn)

    row = conn.execute(
        "SELECT postal_code FROM deals WHERE store='foodlion' AND item_name='Apples'"
    ).fetchone()
    assert row["postal_code"] == "27409"


def test_run_scrape_delete_is_scoped_by_postal_code(conn, fake_scraper):
    """GFP-55 regression: scraping a second ZIP must not wipe the first ZIP's
    rows for the same store. Before this fix the DELETE only scoped on
    (store, source), so the second scrape below would have deleted 27401's
    'Apples' row along with replacing anything at 27409."""
    fake_scraper.rows = [_fake_row("Apples", 0.99)]
    service.run_scrape("foodlion", postal_code="27401", conn=conn)

    fake_scraper.rows = [_fake_row("Bananas", 0.59)]
    service.run_scrape("foodlion", postal_code="27409", conn=conn)

    rows = conn.execute(
        "SELECT item_name, postal_code FROM deals WHERE store='foodlion' ORDER BY postal_code"
    ).fetchall()
    assert [(r["item_name"], r["postal_code"]) for r in rows] == [
        ("Apples", "27401"),
        ("Bananas", "27409"),
    ]


def test_run_scrape_rescoped_delete_still_replaces_same_zip(conn, fake_scraper):
    """A second scrape of the *same* ZIP still replaces that ZIP's prior rows
    (the pre-existing "replace on rescrape" behavior must survive the new
    postal_code scoping)."""
    fake_scraper.rows = [_fake_row("Apples", 0.99)]
    service.run_scrape("foodlion", postal_code="27401", conn=conn)

    fake_scraper.rows = [_fake_row("Bananas", 0.59)]
    service.run_scrape("foodlion", postal_code="27401", conn=conn)

    rows = conn.execute(
        "SELECT item_name FROM deals WHERE store='foodlion' AND postal_code='27401'"
    ).fetchall()
    assert [r["item_name"] for r in rows] == ["Bananas"]


def test_run_scrape_appends_to_price_history(conn, fake_scraper):
    fake_scraper.rows = [_fake_row("Apples", 0.99)]
    service.run_scrape("foodlion", postal_code="27401", conn=conn)

    row = conn.execute(
        "SELECT store, postal_code, item_name, sale_price FROM price_history "
        "WHERE store='foodlion' AND item_name='Apples'"
    ).fetchone()
    assert row["postal_code"] == "27401"
    assert row["sale_price"] == 0.99


def test_price_history_survives_the_deal_being_replaced(conn, fake_scraper):
    """GFP-39: `deals` stays a current-snapshot table, so a rescrape drops
    'Apples' from it -- but its price_history row must not disappear."""
    fake_scraper.rows = [_fake_row("Apples", 0.99)]
    service.run_scrape("foodlion", postal_code="27401", conn=conn)

    fake_scraper.rows = [_fake_row("Bananas", 0.59)]
    service.run_scrape("foodlion", postal_code="27401", conn=conn)

    assert conn.execute(
        "SELECT item_name FROM deals WHERE store='foodlion' AND item_name='Apples'"
    ).fetchone() is None
    assert conn.execute(
        "SELECT item_name FROM price_history WHERE store='foodlion' AND item_name='Apples'"
    ).fetchone() is not None


def test_run_scrape_twice_same_day_does_not_fabricate_price_movement(conn, fake_scraper):
    """Re-running a scrape twice in one day must update today's price_history
    row in place, not append a second data point for the same day."""
    fake_scraper.rows = [_fake_row("Apples", 0.99)]
    service.run_scrape("foodlion", postal_code="27401", conn=conn)

    fake_scraper.rows = [_fake_row("Apples", 1.29)]
    service.run_scrape("foodlion", postal_code="27401", conn=conn)

    rows = conn.execute(
        "SELECT sale_price FROM price_history WHERE store='foodlion' AND item_name='Apples'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["sale_price"] == 1.29


def test_price_history_keeps_zips_separate(conn, fake_scraper):
    fake_scraper.rows = [_fake_row("Apples", 0.99)]
    service.run_scrape("foodlion", postal_code="27401", conn=conn)

    fake_scraper.rows = [_fake_row("Apples", 1.49)]
    service.run_scrape("foodlion", postal_code="27409", conn=conn)

    rows = conn.execute(
        "SELECT postal_code, sale_price FROM price_history "
        "WHERE store='foodlion' AND item_name='Apples' ORDER BY postal_code"
    ).fetchall()
    assert [(r["postal_code"], r["sale_price"]) for r in rows] == [
        ("27401", 0.99),
        ("27409", 1.49),
    ]


def test_fetch_deals_empty_and_filtered(conn):
    assert service.fetch_deals(conn=conn) == []

    conn.execute(
        "INSERT INTO deals(store, item_name, sale_price, source) "
        "VALUES ('foodlion', 'Milk', 2.5, 'scrape')"
    )
    conn.commit()

    assert len(service.fetch_deals(conn=conn)) == 1
    assert len(service.fetch_deals(store="foodlion", conn=conn)) == 1
    assert service.fetch_deals(store="harristeeter", conn=conn) == []


# --------------------------------------------------------------------------- #
# GFP-16 — deal freshness
# --------------------------------------------------------------------------- #
TODAY = "2026-06-12"


def _seed_deals(conn):
    conn.executemany(
        "INSERT INTO deals(store, item_name, sale_price, valid_to, source) "
        "VALUES ('foodlion', ?, ?, ?, 'scrape')",
        [
            ("Fresh Chicken", 1.99, "2026-06-16"),   # still valid
            ("Ends Today", 0.99, TODAY),             # valid through today
            ("Stale Apples", 0.79, "2026-06-09"),    # expired
            ("Undated Feature", None, None),         # no end date -> unknown
        ],
    )
    conn.commit()


def test_fetch_deals_flags_expired_rows(conn):
    _seed_deals(conn)
    flags = {r["item_name"]: r["expired"] for r in service.fetch_deals(today=TODAY, conn=conn)}
    assert flags == {
        "Fresh Chicken": 0,
        "Ends Today": 0,      # a deal is good through its last day
        "Stale Apples": 1,
        "Undated Feature": 0,  # unknown is not expired
    }


def test_fetch_deals_hide_expired_and_count_agree(conn):
    _seed_deals(conn)
    kept = [r["item_name"] for r in
            service.fetch_deals(hide_expired=True, today=TODAY, conn=conn)]
    assert "Stale Apples" not in kept
    assert len(kept) == 3
    assert service.count_deals(hide_expired=True, today=TODAY, conn=conn) == 3
    assert service.count_deals(today=TODAY, conn=conn) == 4


def test_fetch_deals_on_sale_filter(conn):
    _seed_deals(conn)
    names = [r["item_name"] for r in service.fetch_deals(on_sale=True, today=TODAY, conn=conn)]
    assert "Undated Feature" not in names
    assert service.count_deals(on_sale=True, hide_expired=True, today=TODAY, conn=conn) == 2


def test_count_deals_limit_is_ignored_by_count(conn):
    _seed_deals(conn)
    assert len(service.fetch_deals(limit=2, today=TODAY, conn=conn)) == 2
    assert service.count_deals(today=TODAY, conn=conn) == 4


def test_is_expired_helper():
    assert service.is_expired("2026-06-09", TODAY)
    assert not service.is_expired(TODAY, TODAY)
    assert not service.is_expired("", TODAY)
    assert not service.is_expired(None, TODAY)


# --------------------------------------------------------------------------- #
# GFP-17 — shared filter params (one definition, CLI flags + GUI controls)
# --------------------------------------------------------------------------- #
def _seed_filterable(conn):
    conn.executemany(
        "INSERT INTO deals(store, item_name, sub_category, deal_type, deal_description, "
        "sale_price, valid_from, valid_to, loyalty_required, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'scrape')",
        [
            ("foodlion", "Boneless Chicken Breast", "Meat & Seafood", "Weekly Ad",
             "$1.99/lb", 1.99, "2026-06-08", "2026-06-16", "Y"),
            ("foodlion", "Mystery Feature", "Weekly Ad Feature (price not listed)",
             "Weekly Ad (price not listed)", "Weekly ad item", None,
             "2026-06-08", "2026-06-16", "Y"),
            ("foodlion", "Oscar Mayer", "Digital Coupon", "Digital Coupon",
             "Save $2.00 on bacon", None, "2026-06-08", "2026-06-16", "Y"),
            ("harristeeter", "Chips BOGO", "Snacks & Chips", "Bogo",
             "Buy one get one free", None, "2026-06-08", "2026-06-16", "N"),
            ("wholefoods", "Wild Salmon", "Meat & Seafood", "Weekly Ad",
             "$9.99/lb", 9.99, "2026-06-20", "2026-06-30", "N"),
        ],
    )
    conn.commit()


def test_category_and_store_filters(conn):
    _seed_filterable(conn)
    meat = service.fetch_deals(category="Meat & Seafood", today=TODAY, conn=conn)
    assert {r["store"] for r in meat} == {"foodlion", "wholefoods"}
    assert service.count_deals(
        store="foodlion", category="Meat & Seafood", today=TODAY, conn=conn) == 1


def test_deal_type_groups(conn):
    _seed_filterable(conn)

    def names(group):
        return {r["item_name"] for r in
                service.fetch_deals(deal_type=group, today=TODAY, conn=conn)}

    # "weekly" spans both the priced and price-not-listed variants.
    assert names("weekly") == {"Boneless Chicken Breast", "Mystery Feature", "Wild Salmon"}
    assert names("coupon") == {"Oscar Mayer"}
    assert names("bogo") == {"Chips BOGO"}
    assert len(names("all")) == 5


def test_unknown_deal_type_group_raises(conn):
    with pytest.raises(service.UnknownDealTypeError):
        service.fetch_deals(deal_type="nonsense", conn=conn)


def test_search_matches_name_or_description(conn):
    _seed_filterable(conn)

    def names(term):
        return {r["item_name"] for r in
                service.fetch_deals(search=term, today=TODAY, conn=conn)}

    assert names("chicken") == {"Boneless Chicken Breast"}   # item name
    assert names("bacon") == {"Oscar Mayer"}                  # description only
    assert names("CHICKEN") == {"Boneless Chicken Breast"}    # LIKE is case-insensitive


def test_search_treats_wildcards_literally(conn):
    _seed_filterable(conn)
    # A bare "%" would otherwise match every row.
    assert service.fetch_deals(search="%", today=TODAY, conn=conn) == []


def test_loyalty_filter(conn):
    _seed_filterable(conn)
    rows = service.fetch_deals(loyalty_only=True, today=TODAY, conn=conn)
    assert {r["store"] for r in rows} == {"foodlion"}


def test_valid_on_picks_deals_live_that_day(conn):
    _seed_filterable(conn)

    def count(day):
        return service.count_deals(valid_on=day, today=TODAY, conn=conn)

    assert count("2026-06-10") == 4   # the four June 8-16 deals
    assert count("2026-06-25") == 1   # only the Whole Foods June 20-30 one
    assert count("2026-06-17") == 0   # gap between the two windows


def test_filters_compose(conn):
    _seed_filterable(conn)
    rows = service.fetch_deals(
        store="foodlion", deal_type="weekly", on_sale=True, search="chicken",
        loyalty_only=True, valid_on="2026-06-10", today=TODAY, conn=conn,
    )
    assert [r["item_name"] for r in rows] == ["Boneless Chicken Breast"]


def test_choice_helpers_come_from_the_data(conn, monkeypatch, tmp_path):
    _seed_filterable(conn)
    # Stores with data always show up, whether or not they're scrapable.
    assert service.stores_with_deals(conn=conn) == ["foodlion", "harristeeter", "wholefoods"]
    # GFP-4 gave Whole Foods a real scraper, but it isn't READY until its
    # session cookie is hand-minted (see scrapers/wholefoods.py) -- pin that
    # to a guaranteed-empty tmp_path rather than relying on whatever this
    # test happens to be running on to actually lack one.
    from grocery_planner.scrapers import wholefoods as wf
    monkeypatch.setattr(wf, "session_path", lambda: tmp_path / "wholefoods_session.json")
    assert "wholefoods" not in service.available_scrapers()
    assert "wholefoods" in service.all_scrapers()

    assert service.deal_categories(store="harristeeter", conn=conn) == ["Snacks & Chips"]
    assert "Meat & Seafood" in service.deal_categories(conn=conn)
