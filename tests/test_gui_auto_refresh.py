"""GFP-105: the app fetches prices itself on first run and on a new day.

Reported from first use: a fresh install showed an empty app, and the only fix
was finding Data > Run scrape and clicking through every store by hand.

The decision lives in service.refresh_decision so this and GFP-102's background
timer cannot reach different answers and double-scrape; these tests cover the
decision AND the wiring, because either alone would let the feature be wrong.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

pytest.importorskip("PySide6")

from grocery_planner import config, db, jobs, service
from grocery_planner.service import refresh


def _a_deal(conn) -> None:
    conn.execute(
        "INSERT INTO deals(store, item_name, deal_type, dollar_price, source, postal_code) "
        "VALUES ('foodlion', 'Something', 'Weekly Ad', 1.0, 'test', '27401')")
    conn.commit()


def _succeeded_today(conn, store="foodlion", when: date | None = None) -> None:
    job_id = jobs.start_job(conn, store, note="test")
    jobs.finish_job(conn, job_id, message="ok")
    if when is not None:
        conn.execute(
            "UPDATE scraping_jobs SET finished_at = ? WHERE id = ?",
            (f"{when.isoformat()}T09:00:00+00:00", job_id))
        conn.commit()


# --------------------------------------------------------------------------- #
# The decision
# --------------------------------------------------------------------------- #
def test_an_empty_database_is_always_due(conn):
    """Unambiguous: nothing to lose and nothing to weigh."""
    decision = refresh.refresh_decision(conn=conn)
    assert decision.due and decision.reason == refresh.NO_DATA
    assert decision.stores, "an empty database must name stores to fetch"
    assert "No prices on record yet" in decision.explanation


def test_a_new_day_is_due_even_though_data_exists(conn):
    _a_deal(conn)
    _succeeded_today(conn, when=date.today() - timedelta(days=1))
    decision = refresh.refresh_decision(conn=conn)

    assert decision.due and decision.reason == refresh.NEW_DAY
    assert "not been fetched today" in decision.explanation


def test_a_second_run_on_the_same_day_is_not_due(conn):
    """This is what stops the app scraping every time it is opened."""
    _a_deal(conn)
    _succeeded_today(conn, when=date.today())
    decision = refresh.refresh_decision(conn=conn)

    assert not decision.due and decision.reason == refresh.UP_TO_DATE
    assert decision.stores == []


def test_the_day_boundary_is_a_calendar_day_not_24_hours(conn):
    """Opening on Tuesday wants Tuesday's prices, whether Monday ran at 09:00 or 23:00."""
    _a_deal(conn)
    yesterday = date.today() - timedelta(days=1)
    conn.execute(
        "INSERT INTO scraping_jobs(source, status, started_at, finished_at) "
        "VALUES ('foodlion', 'succeeded', ?, ?)",
        (f"{yesterday}T23:59:00+00:00", f"{yesterday}T23:59:00+00:00"))
    conn.commit()
    # Less than 24 hours ago, but a different calendar day -> due.
    assert refresh.refresh_decision(conn=conn).due


def test_only_ready_stores_are_named(conn):
    """A scraper with no credentials is skipped, not attempted and failed."""
    decision = refresh.refresh_decision(conn=conn)
    assert set(decision.stores) <= set(service.available_scrapers())


def test_a_failed_run_does_not_count_as_a_refresh(conn):
    """Otherwise one broken scrape would suppress refreshes for the rest of the day."""
    _a_deal(conn)
    job_id = jobs.start_job(conn, "foodlion", note="test")
    jobs.fail_job(conn, job_id, "boom")
    assert refresh.refresh_decision(conn=conn).due


def test_mark_refreshed_makes_it_not_due(conn):
    """GFP-102's timer records its run this way, so the app then stands down."""
    _a_deal(conn)
    assert refresh.refresh_decision(conn=conn).due
    refresh.mark_refreshed("foodlion", conn=conn)
    assert not refresh.refresh_decision(conn=conn).due


# --------------------------------------------------------------------------- #
# The wiring
# --------------------------------------------------------------------------- #
def test_an_empty_install_starts_a_visible_refresh(window, monkeypatch):
    """Visible, because the scrape dialog is what runs it -- not a silent thread."""
    started = []
    import grocery_planner.gui.scrape as scrape_module
    monkeypatch.setattr(scrape_module.jobs, "run_tracked_scrape",
                        lambda store_key, force=False: started.append(store_key))

    assert window.maybe_auto_refresh() is True
    dialog = window._dialogs["scrape"]
    assert dialog.isVisibleTo(window) or dialog._rows, "the user must be able to see it"
    assert dialog._rows, "a row per store is what makes it visible and stoppable"
    dialog.wait_for_runs()


def test_it_does_not_run_again_when_prices_are_current(window):
    conn = db.connect()
    _a_deal(conn)
    _succeeded_today(conn, when=date.today())
    assert window.maybe_auto_refresh() is False


def test_the_opt_out_is_honoured_from_the_environment(window, monkeypatch):
    """Automatic network activity on someone else's machine must be refusable."""
    monkeypatch.setenv("GROCERY_PLANNER_AUTO_REFRESH", "false")
    assert window.maybe_auto_refresh() is False


def test_the_opt_out_is_honoured_from_the_config_file(window, monkeypatch, tmp_path):
    """GFP-85 gave this a real home; the env var was a documented placeholder.

    Both paths are tested because the config layer gives every setting an
    environment override, and a user turning this off in config.json must not
    need to know that.
    """
    import json

    target = tmp_path / "config.json"
    target.write_text(json.dumps({"auto_refresh": False}), encoding="utf-8")
    monkeypatch.setenv(config.CONFIG_ENV_VAR, str(target))
    monkeypatch.delenv("GROCERY_PLANNER_AUTO_REFRESH", raising=False)

    assert config.auto_refresh() is False
    assert window.maybe_auto_refresh() is False
