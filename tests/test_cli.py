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


def _seed_sized_deals():
    from grocery_planner import db as _db

    conn = _db.connect()
    conn.executemany(
        "INSERT INTO deals(store, item_name, sub_category, deal_type, dollar_price, "
        "sale_price, valid_to, source) VALUES (?, ?, 'Pantry & Seasoning', 'Weekly Ad', "
        "?, ?, '2099-01-01', 'scrape')",
        [
            ("foodlion", "16 oz. Peanut Butter", 4.00, 4.00),
            ("harristeeter", "28 oz. Peanut Butter", 5.60, 5.60),
            ("foodlion", "Mystery Feature", 1.00, 1.00),
        ],
    )
    conn.commit()
    return conn


def test_best_ranks_by_cost_per_unit(env_db):
    """GFP-8: the bigger jar wins on $/oz even though it costs more."""
    _seed_sized_deals()
    result = runner.invoke(app, ["best", "-u", "oz"])
    assert result.exit_code == 0, result.stdout

    lines = [ln for ln in result.stdout.splitlines() if "Peanut Butter" in ln]
    assert "28 oz" in lines[0] and "$0.200/oz" in lines[0]
    assert "16 oz" in lines[1] and "$0.250/oz" in lines[1]
    # The unreadable row is excluded, and the exclusion is stated, not hidden.
    assert "Mystery Feature" not in result.stdout
    assert "no readable size" in result.stdout


def test_best_with_a_formula(env_db):
    _seed_sized_deals()
    assert runner.invoke(app, ["formula", "set", "value", "1 / unit_price"]).exit_code == 0
    result = runner.invoke(app, ["best", "--score", "value", "-n", "1"])
    assert result.exit_code == 0
    assert "28 oz. Peanut Butter" in result.stdout


def test_best_rejects_unknown_formula_and_type(env_db):
    assert runner.invoke(app, ["best", "--score", "nope"]).exit_code == 1
    assert runner.invoke(app, ["best", "--type", "nonsense"]).exit_code == 1


def test_schedule_set_list_remove(env_db):
    """GFP-7: cadence round-trips through the CLI."""
    created = runner.invoke(app, ["schedule", "set", "foodlion", "--every", "12h"])
    assert created.exit_code == 0, created.stdout
    assert "every 12h" in created.stdout

    listed = runner.invoke(app, ["schedule", "list"])
    assert listed.exit_code == 0
    assert "foodlion" in listed.stdout
    assert "never" in listed.stdout  # no successful run yet

    removed = runner.invoke(app, ["schedule", "remove", "foodlion"])
    assert removed.exit_code == 0
    assert runner.invoke(app, ["schedule", "remove", "foodlion"]).exit_code == 1


def test_schedule_set_rejects_bad_input(env_db):
    # Exactly one of --every / --cron.
    assert runner.invoke(app, ["schedule", "set", "foodlion"]).exit_code == 1
    assert runner.invoke(
        app, ["schedule", "set", "foodlion", "--every", "6h", "--cron", "0 6 * * *"]
    ).exit_code == 1
    # Unparseable cadence, and a store with no scraper.
    assert runner.invoke(app, ["schedule", "set", "foodlion", "--every", "soon"]).exit_code == 1
    assert runner.invoke(
        app, ["schedule", "set", "wholefoods", "--every", "6h"]).exit_code == 2


def test_schedule_run_once_needs_a_schedule(env_db):
    result = runner.invoke(app, ["schedule", "run", "--once"])
    assert result.exit_code == 1
    assert "No schedules configured" in result.stdout


def test_jobs_command_shows_history(env_db):
    from grocery_planner import db as _db, jobs as _jobs

    conn = _db.connect()
    job_id = _jobs.start_job(conn, "foodlion")
    _jobs.finish_job(conn, job_id, "stored 5 deals")

    result = runner.invoke(app, ["jobs"])
    assert result.exit_code == 0
    assert "succeeded" in result.stdout
    assert "stored 5 deals" in result.stdout


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
