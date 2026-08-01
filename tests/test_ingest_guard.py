"""Tests for the GFP-67 replace-guard in service/ingest.py::run_scrape.

The bug: run_scrape() DELETEd every deal row for (store, source, postal_code)
before checking whether the scraper actually returned anything to replace
them with. If Flipp (the sole, undocumented, unauthenticated data source)
changes shape and a parse silently yields zero -- or implausibly few -- rows
without raising, the DELETE still ran and nothing real replaced it: total
silent data loss for that store and ZIP.

These tests exercise the two guards added in front of that DELETE:
  - EmptyScrapeError on a zero-row scrape.
  - ImplausibleCollapseError on a drastic drop from the last known-good
    price_history capture (e.g. 900 rows yesterday, 3 today).
Both are skippable via force=True, the escape hatch that keeps a genuinely
tiny (or empty) ad week from becoming permanently unscrapeable.
"""
from __future__ import annotations

import pytest

from grocery_planner import service
from grocery_planner.service import ingest


def _fake_row(item_name: str, sale_price: float = 0.99) -> dict:
    return {
        "item_name": item_name, "sub_category": "Produce", "deal_type": "Weekly Ad",
        "deal_description": "", "regular_price": None, "sale_price": sale_price,
        "dollar_price": sale_price, "discount_amount": None, "discount_percent": None,
        "valid_from": "2026-06-08", "valid_to": "2026-06-16", "loyalty_required": "Y",
        "notes": "",
        # GFP-15: run_scrape's INSERT is built generically from every column
        # in importers.DEAL_COLUMNS, so a fake row exercised through it must
        # supply every column that list names, same as the real scraper row
        # builders in scrapers/base.py do (mirrors tests/test_service.py's
        # identical _fake_row fixture).
        "source_url": None, "image_url": None,
        "flipp_flyer_id": None, "flipp_item_id": None, "flipp_coupon_id": None,
    }


class _FakeScraperModule:
    """Stands in for a grocery_planner.scrapers.<store> module so run_scrape
    can be exercised without any network access. Mirrors the fixture in
    tests/test_service.py."""

    DEFAULT_POSTAL_CODE = "27401"

    def __init__(self, rows):
        self.rows = rows

    def scrape(self, postal_code=None, include_coupons=True):
        return list(self.rows), {"id": 1}, {"total": len(self.rows)}


@pytest.fixture
def fake_scraper(monkeypatch):
    """Registers a fake 'foodlion' scraper for the duration of one test."""
    fake = _FakeScraperModule([])
    monkeypatch.setitem(ingest.SCRAPERS, "foodlion", fake)
    return fake


def _rows(n: int, prefix: str = "Item") -> list[dict]:
    return [_fake_row(f"{prefix} {i}") for i in range(n)]


def _deal_count(conn, store="foodlion", postal_code="27401") -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM deals WHERE store=? AND postal_code=?",
        (store, postal_code),
    ).fetchone()["n"]


# --------------------------------------------------------------------------- #
# Zero-row guard
# --------------------------------------------------------------------------- #
def test_zero_row_scrape_raises_and_leaves_existing_deals_untouched(conn, fake_scraper):
    fake_scraper.rows = _rows(5)
    service.run_scrape("foodlion", postal_code="27401", conn=conn)
    assert _deal_count(conn) == 5

    fake_scraper.rows = []
    with pytest.raises(ingest.EmptyScrapeError):
        service.run_scrape("foodlion", postal_code="27401", conn=conn)

    # Nothing was deleted -- the original 5 rows are still exactly there.
    assert _deal_count(conn) == 5


def test_zero_row_scrape_message_says_what_happened_and_what_to_do(conn, fake_scraper):
    fake_scraper.rows = []
    with pytest.raises(ingest.EmptyScrapeError) as exc_info:
        service.run_scrape("foodlion", postal_code="27401", conn=conn)

    message = str(exc_info.value)
    assert "foodlion" in message
    assert "27401" in message
    assert "0 deals" in message
    assert "force=True" in message


def test_zero_row_scrape_raises_even_on_the_very_first_ever_scrape(conn, fake_scraper):
    """No baseline to compare against yet -- the zero-row guard still fires,
    since an empty first scrape is just as likely to be a broken parse as a
    genuinely empty catalog."""
    fake_scraper.rows = []
    with pytest.raises(ingest.EmptyScrapeError):
        service.run_scrape("foodlion", postal_code="27401", conn=conn)
    assert _deal_count(conn) == 0


def test_force_true_allows_a_zero_row_scrape_to_replace(conn, fake_scraper):
    fake_scraper.rows = _rows(5)
    service.run_scrape("foodlion", postal_code="27401", conn=conn)
    assert _deal_count(conn) == 5

    fake_scraper.rows = []
    result = service.run_scrape("foodlion", postal_code="27401", conn=conn, force=True)

    assert result["stats"]["total"] == 0
    assert _deal_count(conn) == 0


# --------------------------------------------------------------------------- #
# Implausible-collapse guard
# --------------------------------------------------------------------------- #
def test_implausible_collapse_raises_and_leaves_existing_data_untouched(conn, fake_scraper):
    """900 -> 3 is the ticket's example; here 30 -> 2 (well under the 10%
    floor) exercises the same rule at a scale conftest fixtures can build
    quickly."""
    fake_scraper.rows = _rows(30)
    service.run_scrape("foodlion", postal_code="27401", conn=conn)
    assert _deal_count(conn) == 30

    fake_scraper.rows = _rows(2)
    with pytest.raises(ingest.ImplausibleCollapseError):
        service.run_scrape("foodlion", postal_code="27401", conn=conn)

    assert _deal_count(conn) == 30


def test_collapse_guard_message_says_what_happened_and_what_to_do(conn, fake_scraper):
    fake_scraper.rows = _rows(30)
    service.run_scrape("foodlion", postal_code="27401", conn=conn)

    fake_scraper.rows = _rows(2)
    with pytest.raises(ingest.ImplausibleCollapseError) as exc_info:
        service.run_scrape("foodlion", postal_code="27401", conn=conn)

    message = str(exc_info.value)
    assert "30" in message
    assert "2 deals" in message
    assert "force=True" in message


def test_a_smaller_but_plausible_scrape_does_not_trip_the_guard(conn, fake_scraper):
    """A real smaller ad week (well within the 90%-drop threshold) must go
    through normally -- the guard should not become a trap for ordinary
    variation."""
    fake_scraper.rows = _rows(30)
    service.run_scrape("foodlion", postal_code="27401", conn=conn)

    fake_scraper.rows = _rows(20)  # a 33% drop, nowhere near the 90% floor
    service.run_scrape("foodlion", postal_code="27401", conn=conn)

    assert _deal_count(conn) == 20


def test_collapse_guard_ignored_when_previous_capture_was_already_small(conn, fake_scraper):
    """A store whose last capture was itself tiny (below
    _COLLAPSE_MIN_PREVIOUS) shrinking further is not evidence of upstream
    breakage -- only the zero-row guard should still apply."""
    fake_scraper.rows = _rows(5)
    service.run_scrape("foodlion", postal_code="27401", conn=conn)

    fake_scraper.rows = _rows(1)
    service.run_scrape("foodlion", postal_code="27401", conn=conn)  # must not raise

    assert _deal_count(conn) == 1


def test_force_true_bypasses_the_collapse_guard(conn, fake_scraper):
    fake_scraper.rows = _rows(30)
    service.run_scrape("foodlion", postal_code="27401", conn=conn)

    fake_scraper.rows = _rows(2)
    result = service.run_scrape(
        "foodlion", postal_code="27401", conn=conn, force=True
    )

    assert result["stats"]["total"] == 2
    assert _deal_count(conn) == 2


def test_collapse_guard_compares_against_the_last_capture_not_the_first(conn, fake_scraper):
    """The baseline rolls forward with each successful scrape (via
    price_history), so a gradual, repeatedly-accepted decline does not
    suddenly trip the guard against a stale, much-larger original count."""
    fake_scraper.rows = _rows(30)
    service.run_scrape("foodlion", postal_code="27401", conn=conn)

    fake_scraper.rows = _rows(25)  # accepted: 25 >= 30 * 0.1
    service.run_scrape("foodlion", postal_code="27401", conn=conn)

    fake_scraper.rows = _rows(21)  # accepted: 21 >= 25 * 0.1, would also pass vs. 30
    service.run_scrape("foodlion", postal_code="27401", conn=conn)

    assert _deal_count(conn) == 21


def test_collapse_guard_is_scoped_per_postal_code(conn, fake_scraper):
    """A large capture in one ZIP must not make an unrelated ZIP's small
    scrape look like a collapse."""
    fake_scraper.rows = _rows(30)
    service.run_scrape("foodlion", postal_code="27401", conn=conn)

    fake_scraper.rows = _rows(3)
    service.run_scrape("foodlion", postal_code="27409", conn=conn)  # must not raise

    assert _deal_count(conn, postal_code="27409") == 3
    assert _deal_count(conn, postal_code="27401") == 30
