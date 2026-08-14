"""GFP-257: which supported stores actually serve a ZIP.

The defect. Nothing gated a store by ZIP -- ``run_scrape(store_key,
postal_code)`` took both and simply tried. So scraping Greensboro 27401 called
every registered scraper including banners with no presence within 400 miles,
and a store with no presence returned zero rows, which trips GFP-67's
EmptyScrapeError. A healthy scraper in the wrong market was indistinguishable
from a broken parser.
"""
from __future__ import annotations

import pytest

from grocery_planner import availability
from grocery_planner.service import ingest


class _Asking:
    """A scraper that can be asked, as Kroger can."""

    DEFAULT_POSTAL_CODE = "27401"
    MERCHANT = "Asking Store"

    def __init__(self, answer):
        self.answer = answer
        self.asked = 0

    def serves(self, postal_code):
        self.asked += 1
        return self.answer

    def scrape(self, postal_code=None, include_coupons=True):
        row = {c: None for c in _cols()}
        row.update(item_name="Chicken Breast, 1 lb", deal_type="Weekly Ad",
                   dollar_price=3.99, sub_category="Meat & Seafood")
        return [row], {"id": 1}, {"total": 1}


class _Declaring:
    """A scraper that cannot be asked and declares an area instead -- the
    PRISM case, because GFP-246 found /store-locator is DataDome-protected."""

    DEFAULT_POSTAL_CODE = "27401"
    MERCHANT = "Declaring Store"
    SERVICE_AREA = ("27", "28")

    def scrape(self, postal_code=None, include_coupons=True):
        return [], {"id": 1}, {"total": 0}


class _Silent:
    """A scraper that declares nothing -- today's behaviour, preserved."""

    DEFAULT_POSTAL_CODE = "27401"
    MERCHANT = "Silent Store"

    def scrape(self, postal_code=None, include_coupons=True):
        return [], {"id": 1}, {"total": 0}


def _cols():
    from grocery_planner import importers
    return importers.DEAL_COLUMNS


@pytest.fixture
def only(monkeypatch):
    """Replace the registry so a test sees exactly the scrapers it registers."""
    def _install(**modules):
        monkeypatch.setattr(ingest, "SCRAPERS", dict(modules))
        monkeypatch.setattr(availability.scrapers, "SCRAPERS", dict(modules))
        return modules
    return _install


# --------------------------------------------------------------------------- #
# The three states
# --------------------------------------------------------------------------- #
def test_a_store_that_serves_the_zip_is_scraped(conn, only):
    only(asking=_Asking(True))
    assert availability.resolve("asking", "27401", conn=conn).state == availability.SERVES
    assert availability.serving_scrapers("27401", conn=conn) == ["asking"]


def test_a_store_that_does_not_serve_the_zip_is_dropped(conn, only):
    """THE POINT: stores in NYC are not called when scraping Greensboro."""
    only(asking=_Asking(False))
    assert availability.resolve("asking", "27401", conn=conn).state == availability.DOES_NOT_SERVE
    assert availability.serving_scrapers("27401", conn=conn) == []


def test_unknown_is_permissive_so_nothing_loses_coverage(conn, only):
    """A store we cannot ask about is still scraped. Collapsing 'could not
    find out' into 'no' would silently remove a store the client may have."""
    only(silent=_Silent())
    got = availability.resolve("silent", "27401", conn=conn)
    assert got.state == availability.UNKNOWN
    assert got.should_scrape
    assert availability.serving_scrapers("27401", conn=conn) == ["silent"]


def test_a_source_that_errors_is_unknown_not_unavailable(conn, only):
    """A network hiccup must not delete a store for the whole TTL."""
    class _Broken(_Asking):
        def serves(self, postal_code):
            raise OSError("connection reset")

    only(broken=_Broken(True))
    got = availability.resolve("broken", "27401", conn=conn)
    assert got.state == availability.UNKNOWN and got.should_scrape


# --------------------------------------------------------------------------- #
# Declared service areas -- the platform that cannot be asked
# --------------------------------------------------------------------------- #
def test_a_declared_service_area_answers_when_the_source_cannot(conn, only):
    only(declaring=_Declaring())
    inside = availability.resolve("declaring", "27401", conn=conn)
    outside = availability.resolve("declaring", "10001", conn=conn)

    assert inside.state == availability.SERVES
    assert inside.method == availability.BY_SERVICE_AREA
    assert outside.state == availability.DOES_NOT_SERVE


def test_no_scraper_declares_a_hand_written_footprint_any_more(conn):
    """Both guesses that shipped were measurably wrong -- Food Lion's claimed
    all of Kentucky (it is in one metro) and all of Georgia (it is not in
    Atlanta), and GIANT's was never verified at all. Every banner now ASKS
    Flipp, which answers exactly and for free."""
    from grocery_planner import scrapers

    declaring = [k for k, m in scrapers.SCRAPERS.items()
                 if getattr(m, "SERVICE_AREA", None)]
    assert declaring == [], f"hand-written footprint guesses came back: {declaring}"


def test_the_food_lion_catalogue_asks_instead_of_declaring(conn):
    """It USED to declare ZIP prefixes, and the list was wrong -- it claimed
    Food Lion served all of Georgia's 30xxx when Food Lion is not in Atlanta at
    all. The catalogue and the weekly ad are the same chain, so the ad's own
    answer is the right one for both."""
    from grocery_planner.scrapers import foodlion_catalog

    assert not hasattr(foodlion_catalog, "SERVICE_AREA"), (
        "a hand-written footprint guess came back")
    assert callable(foodlion_catalog.serves)


def test_a_flyer_list_too_short_to_trust_is_unknown_not_absent(monkeypatch):
    """A missing flyer is weaker evidence than a missing store. A short or
    empty list means the question failed, not that the chain is absent."""
    from grocery_planner.scrapers import base

    class _Client:
        def __init__(self, flyers): self.flyers = flyers
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def fetch_data(self, postal_code): return {"flyers": self.flyers}

    healthy = [{"merchant": "Somebody Else", "name": "Weekly Ad"}] * 40
    monkeypatch.setattr(base, "FlippClient", lambda: _Client(healthy))
    assert base.serves_postal_code(base.FOOD_LION, "30303") is False

    monkeypatch.setattr(base, "FlippClient", lambda: _Client(healthy[:3]))
    assert base.serves_postal_code(base.FOOD_LION, "30303") is None

    def _boom(): raise OSError("flipp unreachable")
    monkeypatch.setattr(base, "FlippClient", _boom)
    assert base.serves_postal_code(base.FOOD_LION, "30303") is None


# --------------------------------------------------------------------------- #
# Caching: this must not run per scrape
# --------------------------------------------------------------------------- #
def test_the_answer_is_cached_and_the_source_is_not_re_asked(conn, only):
    mods = only(asking=_Asking(True))
    for _ in range(5):
        availability.resolve("asking", "27401", conn=conn)
    assert mods["asking"].asked == 1


def test_force_re_asks(conn, only):
    mods = only(asking=_Asking(True))
    availability.resolve("asking", "27401", conn=conn)
    availability.resolve("asking", "27401", conn=conn, force=True)
    assert mods["asking"].asked == 2


def test_a_stale_answer_is_refreshed(conn, only):
    mods = only(asking=_Asking(True))
    availability.resolve("asking", "27401", conn=conn)
    conn.execute("UPDATE store_availability SET checked_at = '2020-01-01T00:00:00+00:00'")
    conn.commit()
    availability.resolve("asking", "27401", conn=conn)
    assert mods["asking"].asked == 2


def test_the_method_is_recorded_so_an_answer_can_be_traced(conn, only):
    only(asking=_Asking(True), declaring=_Declaring(), silent=_Silent())
    got = {a.scraper_key: a.method for a in availability.report("27401", conn=conn)}
    assert got == {
        "asking": availability.BY_LOCATION_API,
        "declaring": availability.BY_SERVICE_AREA,
        "silent": availability.BY_NO_CAPABILITY,
    }


# --------------------------------------------------------------------------- #
# The correctness consequence in run_scrape
# --------------------------------------------------------------------------- #
def test_a_store_not_in_the_market_is_skipped_not_reported_as_broken(conn, only):
    """THE REGRESSION. Before GFP-257 this raised EmptyScrapeError -- the same
    error a broken parser raises."""
    only(declaring=_Declaring())
    result = ingest.run_scrape("declaring", postal_code="10001", conn=conn)

    assert result["skipped"] == "not_in_market"
    assert result["availability"] == availability.DOES_NOT_SERVE


def test_an_empty_scrape_inside_the_market_still_raises(conn, only):
    """The GFP-67 guard must keep working where it was meant to: this is the
    'the parse broke' case, and it must stay loud."""
    only(declaring=_Declaring())
    with pytest.raises(ingest.EmptyScrapeError):
        ingest.run_scrape("declaring", postal_code="27401", conn=conn)


def test_a_serving_store_scrapes_normally(conn, only):
    only(asking=_Asking(True))
    result = ingest.run_scrape("asking", postal_code="27401", conn=conn)
    assert "skipped" not in result
    assert result["stats"]["total"] == 1


def test_a_skipped_store_writes_nothing(conn, only):
    """A skip must not touch data -- least of all delete the rows a previous
    in-market scrape wrote."""
    only(asking=_Asking(True))
    ingest.run_scrape("asking", postal_code="27401", conn=conn)
    before = conn.execute("SELECT COUNT(*) AS n FROM deals").fetchone()["n"]

    only(asking=_Asking(False))
    ingest.run_scrape("asking", postal_code="27401", conn=conn, force=True)

    assert conn.execute("SELECT COUNT(*) AS n FROM deals").fetchone()["n"] == before
