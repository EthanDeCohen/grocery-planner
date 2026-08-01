"""End-to-end CLI smoke tests via Typer's CliRunner (isolated DB, no network)."""
from typer.testing import CliRunner

from grocery_planner.cli import app

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "grocery-planner" in result.stdout


def test_stores_empty(env_db):
    result = runner.invoke(app, ["stores"])
    assert result.exit_code == 0
    assert "Food Lion" in result.stdout


def test_import_then_list(env_db, sample_data):
    imp = runner.invoke(app, ["import", str(sample_data)])
    assert imp.exit_code == 0, imp.stdout
    assert "Imported" in imp.stdout

    listed = runner.invoke(app, ["list", "deals", "--store", "foodlion", "--limit", "0"])
    assert listed.exit_code == 0
    assert "Boneless Chicken Breast" in listed.stdout


def test_scrape_rejects_unimplemented_store(env_db):
    result = runner.invoke(app, ["scrape", "wholefoods"])
    assert result.exit_code == 2


def test_unknown_store_filter_errors(env_db):
    result = runner.invoke(app, ["list", "deals", "--store", "bogus"])
    assert result.exit_code == 1


def test_list_deals_marks_and_hides_expired(env_db, sample_data):
    """Sample deals end 2026-06-16, so they are stale by the time this runs (GFP-16)."""
    assert runner.invoke(app, ["import", str(sample_data)]).exit_code == 0

    shown = runner.invoke(app, ["list", "deals", "--store", "foodlion", "--limit", "0"])
    assert shown.exit_code == 0
    assert "(expired)" in shown.stdout
    assert "EXPIRED" in shown.stdout  # the nudge to re-scrape

    hidden = runner.invoke(
        app, ["list", "deals", "--store", "foodlion", "--limit", "0", "--hide-expired"]
    )
    assert hidden.exit_code == 0
    assert "Boneless Chicken Breast" not in hidden.stdout
    assert "0 shown of 0 deals" in hidden.stdout
