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


def test_list_deals_filter_flags(env_db, sample_data):
    """GFP-17: each flag maps onto the same service param the GUI uses."""
    assert runner.invoke(app, ["import", str(sample_data)]).exit_code == 0
    base = ["list", "deals", "--limit", "0"]

    found = runner.invoke(app, base + ["--search", "chicken"])
    assert found.exit_code == 0
    assert "Boneless Chicken Breast" in found.stdout
    assert "Gala Apples" not in found.stdout

    by_category = runner.invoke(app, base + ["--category", "Produce"])
    assert by_category.exit_code == 0
    assert "Gala Apples" in by_category.stdout
    assert "Boneless Chicken Breast" not in by_category.stdout

    coupons = runner.invoke(app, base + ["--type", "coupon"])
    assert coupons.exit_code == 0
    assert "0 shown of 0 deals" in coupons.stdout  # sample data is weekly-ad only

    on_date = runner.invoke(app, base + ["--valid-on", "2026-06-12"])
    assert on_date.exit_code == 0
    assert "Boneless Chicken Breast" in on_date.stdout


def test_list_deals_rejects_bad_filter_values(env_db):
    assert runner.invoke(app, ["list", "deals", "--type", "nonsense"]).exit_code == 1
    assert runner.invoke(app, ["list", "deals", "--valid-on", "june 12"]).exit_code == 1


def test_categories_command(env_db, sample_data):
    assert runner.invoke(app, ["import", str(sample_data)]).exit_code == 0
    result = runner.invoke(app, ["categories", "--store", "foodlion"])
    assert result.exit_code == 0
    assert "Produce" in result.stdout


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
