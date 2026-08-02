"""Tests for the GFP-71 fix: making the GFP-67 scrape-guard escape hatch
(``run_scrape(..., force=True)``) actually reachable by something user-facing.

Before this, ``jobs.run_tracked_scrape`` never forwarded ``force`` and
``gplan scrape`` had no flag for it at all -- so a guard tripping on a
genuinely tiny (or empty) ad week left that store permanently unscrapeable,
which is exactly the trap the escape hatch was meant to prevent. These tests
exercise both new paths:

- ``jobs.run_tracked_scrape(..., force=True)`` (the GUI/scheduler path).
- ``gplan scrape STORE --force`` (the CLI path).
"""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from grocery_planner import jobs, service
from grocery_planner.cli import app
from grocery_planner.service import ingest

runner = CliRunner()


def _fake_row(item_name: str, sale_price: float = 0.99) -> dict:
    return {
        "item_name": item_name, "sub_category": "Produce", "deal_type": "Weekly Ad",
        "deal_description": "", "regular_price": None, "sale_price": sale_price,
        "dollar_price": sale_price, "discount_amount": None, "discount_percent": None,
        "valid_from": "2026-06-08", "valid_to": "2026-06-16", "loyalty_required": "Y",
        "notes": "",
        # Mirrors the identical fixture in tests/test_service.py /
        # tests/test_ingest_guard.py: run_scrape's INSERT is built generically
        # from every column in importers.DEAL_COLUMNS, so a fake row exercised
        # through it must supply every column that list names.
        "source_url": None, "image_url": None,
        "flipp_flyer_id": None, "flipp_item_id": None, "flipp_coupon_id": None,
    }


class _FakeScraperModule:
    """Stands in for a grocery_planner.scrapers.<store> module -- no network.

    Needs MERCHANT/DEFAULT_POSTAL_CODE (unlike test_service.py's/
    test_ingest_guard.py's identical-looking fixture) because these tests
    also drive `gplan scrape` end-to-end via the CLI, and cli.py's scrape()
    command reads scraper.MERCHANT for its status messages before ever
    calling run_scrape.
    """

    MERCHANT = "Food Lion"
    DEFAULT_POSTAL_CODE = "27401"

    def __init__(self, rows):
        self.rows = rows

    def scrape(self, postal_code=None, include_coupons=True):
        return list(self.rows), {"id": 1, "name": "Weekly Ad", "valid_from": "2026-06-08",
                                  "valid_to": "2026-06-16"}, {
            "total": len(self.rows), "valid_from": "2026-06-08", "valid_to": "2026-06-16",
            "weekly_ad": len(self.rows), "no_price": 0, "digital_coupons": 0, "bogo": 0,
            "expired_items": 0, "flyer_status": "active",
        }


@pytest.fixture
def fake_scraper(monkeypatch):
    """Registers a fake 'foodlion' scraper for the duration of one test.

    Patches ingest.SCRAPERS in place (setitem), which is the same dict object
    grocery_planner.scrapers.SCRAPERS and grocery_planner.cli's imported
    SCRAPERS both point at -- so the CLI's own "no scraper registered" check
    sees it too. Mirrors tests/test_service.py's identical fixture.
    """
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
# jobs.run_tracked_scrape -- the GUI / scheduler path
# --------------------------------------------------------------------------- #
def test_run_tracked_scrape_without_force_still_trips_the_guard(conn, fake_scraper):
    """Baseline: force defaults to False, so nothing about existing guard
    behavior changes for a tracked scrape that doesn't ask to bypass it."""
    fake_scraper.rows = _rows(5)
    jobs.run_tracked_scrape("foodlion", conn=conn)
    assert _deal_count(conn) == 5

    fake_scraper.rows = []
    with pytest.raises(service.EmptyScrapeError):
        jobs.run_tracked_scrape("foodlion", conn=conn)
    assert _deal_count(conn) == 5  # untouched


def test_run_tracked_scrape_force_true_bypasses_the_guard(conn, fake_scraper):
    """The actual GFP-71 fix: force=True must reach run_scrape from the
    tracked path (GUI / scheduler), not just the bare service function."""
    fake_scraper.rows = _rows(5)
    jobs.run_tracked_scrape("foodlion", conn=conn)
    assert _deal_count(conn) == 5

    fake_scraper.rows = []
    result = jobs.run_tracked_scrape("foodlion", conn=conn, force=True)

    assert result["stats"]["total"] == 0
    assert _deal_count(conn) == 0


def test_run_tracked_scrape_force_true_job_row_records_success(conn, fake_scraper):
    """The job row itself must reflect that the forced, guard-bypassing
    scrape succeeded -- not silently omitted from `gplan jobs` history."""
    fake_scraper.rows = _rows(5)
    jobs.run_tracked_scrape("foodlion", conn=conn)

    fake_scraper.rows = []
    result = jobs.run_tracked_scrape("foodlion", conn=conn, force=True)

    row = jobs.recent_jobs(conn)[0]
    assert row["id"] == result["job_id"]
    assert row["status"] == jobs.SUCCEEDED
    assert "0" in row["message"]


def test_run_tracked_scrape_without_force_marks_the_job_failed(conn, fake_scraper):
    """The flip side: a guard error that isn't bypassed must still land on
    the job row as a failure, same as any other run_scrape exception."""
    fake_scraper.rows = []
    with pytest.raises(service.EmptyScrapeError):
        jobs.run_tracked_scrape("foodlion", conn=conn)

    row = jobs.recent_jobs(conn)[0]
    assert row["status"] == jobs.FAILED
    assert "EmptyScrapeError" in row["message"]


# --------------------------------------------------------------------------- #
# `gplan scrape STORE --force` -- the CLI path
# --------------------------------------------------------------------------- #
def test_cli_scrape_without_force_reports_the_guard_and_exits_nonzero(
    env_db, fake_scraper
):
    """Before GFP-71 there was no --force flag at all; confirm the CLI now
    surfaces the guard error cleanly (not a raw traceback) and points at the
    fix, rather than leaving the user stuck."""
    result = runner.invoke(app, ["scrape", "foodlion"])
    assert result.exit_code == 1
    assert "0 deals" in result.stderr
    assert "--force" in result.stderr


def test_cli_scrape_with_force_flag_bypasses_the_guard(env_db, fake_scraper):
    """The actual GFP-71 fix: `gplan scrape STORE --force` must reach
    run_scrape's force=True, accepting a zero-row scrape instead of refusing."""
    result = runner.invoke(app, ["scrape", "foodlion", "--force"])
    assert result.exit_code == 0, result.stdout
    assert "stored 0 deals" in result.stdout


def test_cli_scrape_force_defaults_to_false(env_db, fake_scraper):
    """Omitting --force must behave exactly as before this ticket -- the
    guard still trips on a zero-row scrape."""
    result = runner.invoke(app, ["scrape", "foodlion"])
    assert result.exit_code == 1


# --------------------------------------------------------------------------- #
# GFP-71 — re-exporting the guard errors from the service package
# --------------------------------------------------------------------------- #
def test_guard_errors_reachable_from_the_service_package():
    """UnknownStoreError was already re-exported from grocery_planner.service;
    EmptyScrapeError/ImplausibleCollapseError (and their shared base) were
    reachable only via grocery_planner.service.ingest, which the GFP-71
    ticket flagged as inconsistent. A front end should be able to catch
    these without importing the ingest submodule directly."""
    assert service.EmptyScrapeError is ingest.EmptyScrapeError
    assert service.ImplausibleCollapseError is ingest.ImplausibleCollapseError
    assert service.ScrapeGuardError is ingest.ScrapeGuardError
    assert issubclass(service.EmptyScrapeError, service.ScrapeGuardError)
    assert issubclass(service.ImplausibleCollapseError, service.ScrapeGuardError)
