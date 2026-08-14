"""Scheduler and job-tracking tests (GFP-7). No network, no real waiting."""
from datetime import datetime, timedelta, timezone

import pytest

from grocery_planner import jobs, scheduler, service


# --------------------------------------------------------------------------- #
# Cadence parsing
# --------------------------------------------------------------------------- #
def test_parse_interval_units():
    assert scheduler.parse_interval("30m") == timedelta(minutes=30)
    assert scheduler.parse_interval("6h") == timedelta(hours=6)
    assert scheduler.parse_interval("2d") == timedelta(days=2)
    assert scheduler.parse_interval(" 12H ") == timedelta(hours=12)


@pytest.mark.parametrize("bad", ["", "6", "h", "0h", "-3h", "6 weeks", "soon"])
def test_parse_interval_rejects_nonsense(bad):
    with pytest.raises(scheduler.ScheduleError):
        scheduler.parse_interval(bad)


def test_build_trigger_accepts_both_kinds():
    assert scheduler.build_trigger("interval", "6h") is not None
    assert scheduler.build_trigger("cron", "0 6 * * *") is not None


def test_build_trigger_rejects_bad_cron_and_kind():
    with pytest.raises(scheduler.ScheduleError):
        scheduler.build_trigger("cron", "not a cron")
    with pytest.raises(scheduler.ScheduleError):
        scheduler.build_trigger("whenever", "6h")


def test_next_run_is_in_the_future():
    after = datetime(2026, 6, 12, 10, 0).astimezone()
    upcoming = scheduler.next_run("cron", "0 6 * * *", after=after)
    assert upcoming > after


# --------------------------------------------------------------------------- #
# Schedule storage
# --------------------------------------------------------------------------- #
def test_set_list_and_remove_schedule(conn):
    scheduler.set_schedule(conn, "foodlion", "interval", "6h")
    rows = scheduler.list_schedules(conn)
    assert [(r["store"], r["kind"], r["expression"]) for r in rows] == [
        ("foodlion", "interval", "6h")
    ]

    # Re-setting replaces rather than duplicating — store is the primary key.
    scheduler.set_schedule(conn, "foodlion", "cron", "0 6 * * *")
    rows = scheduler.list_schedules(conn)
    assert len(rows) == 1 and rows[0]["kind"] == "cron"

    assert scheduler.remove_schedule(conn, "foodlion") is True
    assert scheduler.list_schedules(conn) == []
    assert scheduler.remove_schedule(conn, "foodlion") is False


def test_set_schedule_validates_before_writing(conn):
    # GFP-4 registered a real "wholefoods" scraper, so this test (which
    # predates that ticket and used "wholefoods" as its example of an
    # unregistered store) now uses a key guaranteed not to exist -- flagged
    # here per the GFP-4 PR description.
    with pytest.raises(service.UnknownStoreError):
        scheduler.set_schedule(conn, "not-a-real-store", "interval", "6h")
    with pytest.raises(scheduler.ScheduleError):
        scheduler.set_schedule(conn, "foodlion", "interval", "every so often")
    assert scheduler.list_schedules(conn) == []  # nothing persisted


def test_set_schedule_rejects_a_registered_but_unready_store(conn, monkeypatch, tmp_path):
    # GFP-4: wholefoods IS registered, but scheduling automatic refresh for a
    # store with no session cookie yet would just fail on every single run --
    # available_scrapers() (and therefore set_schedule's guard) excludes it
    # until it's configured. See scrapers/wholefoods.py::readiness().
    from grocery_planner.scrapers import wholefoods as wf
    monkeypatch.setattr(wf, "session_path", lambda: tmp_path / "wholefoods_session.json")
    with pytest.raises(service.UnknownStoreError):
        scheduler.set_schedule(conn, "wholefoods", "interval", "6h")
    assert scheduler.list_schedules(conn) == []


def test_disabled_schedules_are_skipped(conn):
    scheduler.set_schedule(conn, "foodlion", "interval", "6h", enabled=False)
    assert len(scheduler.list_schedules(conn)) == 1
    assert scheduler.list_schedules(conn, enabled_only=True) == []


# --------------------------------------------------------------------------- #
# Job lifecycle
# --------------------------------------------------------------------------- #
def test_job_lifecycle_success(conn):
    job_id = jobs.start_job(conn, "foodlion")
    jobs.checkpoint(conn, job_id, "fetching flyer")
    assert jobs.recent_jobs(conn)[0]["status"] == jobs.RUNNING

    jobs.finish_job(conn, job_id, "stored 300 deals")
    row = jobs.recent_jobs(conn)[0]
    assert row["status"] == jobs.SUCCEEDED
    assert row["message"] == "stored 300 deals"
    assert row["finished_at"]


def test_job_lifecycle_failure(conn):
    job_id = jobs.start_job(conn, "foodlion")
    jobs.fail_job(conn, job_id, "RuntimeError: no active flyer")
    row = jobs.recent_jobs(conn)[0]
    assert row["status"] == jobs.FAILED
    assert "no active flyer" in row["message"]


def test_recover_interrupted_reaps_crashed_runs(conn):
    jobs.start_job(conn, "foodlion")          # never finished: process died
    finished = jobs.start_job(conn, "harristeeter")
    jobs.finish_job(conn, finished)

    assert jobs.recover_interrupted(conn) == 1
    statuses = {r["source"]: r["status"] for r in jobs.recent_jobs(conn)}
    assert statuses["foodlion"] == jobs.INTERRUPTED
    assert statuses["harristeeter"] == jobs.SUCCEEDED
    assert jobs.recover_interrupted(conn) == 0  # idempotent


def test_last_success_ignores_failed_runs(conn):
    failed = jobs.start_job(conn, "foodlion")
    jobs.fail_job(conn, failed, "boom")
    assert jobs.last_success(conn, "foodlion") is None

    ok = jobs.start_job(conn, "foodlion")
    jobs.finish_job(conn, ok)
    assert jobs.last_success(conn, "foodlion") is not None


# --------------------------------------------------------------------------- #
# Staleness / catch-up — the "survives sleep and restarts" behaviour
# --------------------------------------------------------------------------- #
def test_is_due_when_never_run(conn):
    assert jobs.is_due(conn, "foodlion", timedelta(hours=6))


def test_is_due_respects_the_window(conn):
    job_id = jobs.start_job(conn, "foodlion")
    jobs.finish_job(conn, job_id)
    now = datetime.now(timezone.utc)

    assert not jobs.is_due(conn, "foodlion", timedelta(hours=6), now=now)
    # Same run, judged from six hours later: overdue.
    assert jobs.is_due(conn, "foodlion", timedelta(hours=6), now=now + timedelta(hours=7))


def test_due_stores_lists_only_overdue_scheduled_stores(conn):
    scheduler.set_schedule(conn, "foodlion", "interval", "6h")
    scheduler.set_schedule(conn, "harristeeter", "interval", "6h")
    fresh = jobs.start_job(conn, "harristeeter")
    jobs.finish_job(conn, fresh)

    # foodlion never ran; harristeeter just did.
    assert scheduler.due_stores(conn) == ["foodlion"]


def test_run_catch_up_scrapes_overdue_stores_and_survives_failures(conn, monkeypatch):
    scheduler.set_schedule(conn, "foodlion", "interval", "6h")
    scheduler.set_schedule(conn, "harristeeter", "interval", "6h")

    def fake_scrape(store_key, postal_code=None, conn=None, force=False, limit=None):
        # GFP-71: jobs.run_tracked_scrape now always forwards `force` as a
        # keyword to service.run_scrape, so this fake's signature must accept
        # it too (even though this test never passes force=True) -- added
        # here per the GFP-71 PR description.
        if store_key == "harristeeter":
            raise RuntimeError("no active flyer")
        return {"flyer": {}, "stats": {"total": 7}, "postal_code": "27401"}

    monkeypatch.setattr(service, "run_scrape", fake_scrape)
    events = []
    summary = scheduler.run_catch_up(conn, on_event=events.append)

    assert summary["ran"] == ["foodlion"]
    assert "harristeeter" in summary["failed"]
    assert "no active flyer" in summary["failed"]["harristeeter"]
    assert any("FAILED" in e for e in events)

    # Both attempts are on the record, with the right outcomes.
    statuses = {r["source"]: r["status"] for r in jobs.recent_jobs(conn)}
    assert statuses == {"foodlion": jobs.SUCCEEDED, "harristeeter": jobs.FAILED}


def test_run_catch_up_reaps_before_scraping(conn, monkeypatch):
    jobs.start_job(conn, "foodlion")  # left running by a "crash"
    monkeypatch.setattr(service, "run_scrape", lambda *a, **k: {"stats": {"total": 1}})
    summary = scheduler.run_catch_up(conn)
    assert summary["reaped"] == 1


def test_run_tracked_scrape_records_the_run(conn, monkeypatch):
    # GFP-71: run_tracked_scrape now always forwards `force` as a keyword to
    # service.run_scrape, so this fake must accept it too -- added per the
    # GFP-71 PR description. GFP-263 added `limit` on the same terms: it is
    # forwarded unconditionally, so a double that omits it fails with a
    # TypeError the scheduler swallows into "this store failed".
    monkeypatch.setattr(
        service, "run_scrape",
        lambda store_key, postal_code=None, conn=None, force=False, limit=None: (
            {"stats": {"total": 42}}
        ),
    )
    result = jobs.run_tracked_scrape("foodlion", conn=conn)
    row = jobs.recent_jobs(conn)[0]

    assert result["job_id"] == row["id"]
    assert row["status"] == jobs.SUCCEEDED
    assert "42" in row["message"]


def test_run_tracked_scrape_reraises_and_marks_failed(conn, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("flyer gone")

    monkeypatch.setattr(service, "run_scrape", boom)
    with pytest.raises(RuntimeError):
        jobs.run_tracked_scrape("foodlion", conn=conn)
    assert jobs.recent_jobs(conn)[0]["status"] == jobs.FAILED


# --------------------------------------------------------------------------- #
# Scheduler assembly
# --------------------------------------------------------------------------- #
def test_build_scheduler_adds_one_job_per_enabled_schedule(conn):
    scheduler.set_schedule(conn, "foodlion", "interval", "6h")
    scheduler.set_schedule(conn, "harristeeter", "cron", "0 6 * * *", enabled=False)

    engine = scheduler.build_scheduler(conn, blocking=False)
    ids = {job.id for job in engine.get_jobs()}
    assert ids == {"scrape:foodlion"}

    job = engine.get_job("scrape:foodlion")
    assert job.misfire_grace_time == scheduler.MISFIRE_GRACE_SECONDS
    assert job.coalesce is True  # missed windows collapse into one catch-up


# --------------------------------------------------------------------------- #
# GFP-287: a source can be READY and still be refused a recurring cadence.
#
# publix-catalog works. It costs 9.1 Parse.bot credits per usable row against
# Walmart's 0.14 -- 65x worse -- and a weekly cadence for it was ~433 credits a
# month against a 200-credit free tier: 77% of the bill for 5% of the rows.
#
# The guard lives in the scheduler rather than only in a deleted row, because a
# schedule is a standing instruction to SPEND MONEY. Removing the row without
# closing the door leaves it one UI click from returning, with nothing on record
# saying why it should not.
# --------------------------------------------------------------------------- #
def test_an_expensive_source_cannot_be_given_a_recurring_cadence(env_db):
    from grocery_planner import db

    with pytest.raises(service.UnknownStoreError):
        scheduler.set_schedule(db.connect(), "publix-catalog", "interval", "7d")


def test_the_expensive_source_is_still_runnable_on_demand(monkeypatch):
    """Refusing a cadence is not the same as removing the source.

    Running it once, deliberately, is a decision someone is making with their
    eyes open. `available_scrapers` is what the GUI's Run-scrape dialog offers,
    and publix-catalog must stay in it.

    READINESS IS FORCED HERE ON PURPOSE. publix-catalog is only *ready* when a
    Parse.bot API key is configured, so asserting on available_scrapers() as it
    happens to be would test whether this machine has a key -- it passed locally
    and failed on CI for exactly that reason, the same environment split GFP-270
    hit in test_stores_shows_scraper_readiness.

    Pinning readiness to True asserts the thing that actually matters and holds
    everywhere: publix-catalog is withheld from the schedule because it is
    EXPENSIVE, not because it is unconfigured. Those are different reasons and
    only one of them is this ticket's.
    """
    from grocery_planner.scrapers import publix

    monkeypatch.setattr(publix, "readiness", lambda: (True, ""))
    assert "publix-catalog" in service.available_scrapers()
    assert "publix-catalog" not in service.schedulable_scrapers()


def test_the_expensive_source_is_still_registered():
    """Unscheduling must not have quietly deregistered it.

    all_scrapers() is the registry, independent of credentials, so this holds
    with or without a Parse.bot key.
    """
    from grocery_planner.scrapers import publix

    assert "publix-catalog" in service.all_scrapers()
    assert publix.SCHEDULABLE is False


def test_the_free_publix_banner_is_unaffected():
    """The Flipp banner and the Parse.bot catalogue are different sources.

    `publix` is the free weekly ad; `publix-catalog` is the metered one. Only
    the metered key is refused -- an over-broad rule here would have silently
    dropped a source that costs nothing.
    """
    assert "publix" in service.schedulable_scrapers()


def test_schedulable_is_a_subset_of_available():
    """The relationship, not a spelling: schedulable can never offer a store
    that is not runnable at all."""
    assert set(service.schedulable_scrapers()) <= set(service.available_scrapers())
