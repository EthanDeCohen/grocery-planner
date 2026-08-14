# ######### decohen-partners ##########
# Protein Ledger
"""Background refresh scheduling (GFP-7) — the replacement for Excel's "refresh".

A schedule is a row in the ``schedules`` table: one store, one cadence, either
an interval (``6h``) or a cron expression (``0 6 * * *``). The APScheduler
instance is *built from those rows every time the scheduler starts*, which is
what makes a cadence survive restarts without an external job store.

Two behaviours matter more than the ticking itself:

* **Catch-up.** On start, any store whose last clean run is older than its
  cadence is scraped immediately (:func:`due_stores`). A laptop that was asleep
  through its 6am window refreshes when it wakes rather than waiting for 6am
  tomorrow.
* **Reaping.** Jobs left ``running`` by a crash are marked interrupted at start
  (:func:`jobs.recover_interrupted`) so they never look like live runs.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from . import db, jobs, service

INTERVAL = "interval"
CRON = "cron"

# A missed fire (asleep, app closed) still runs when we come back, and several
# missed fires collapse into one catch-up rather than a burst of scrapes.
MISFIRE_GRACE_SECONDS = 6 * 60 * 60
_INTERVAL_PATTERN = re.compile(r"^\s*(\d+)\s*([mhd])\s*$", re.IGNORECASE)
_UNITS = {"m": "minutes", "h": "hours", "d": "days"}


class ScheduleError(ValueError):
    """Raised for a cadence the scheduler cannot parse."""


# --------------------------------------------------------------------------- #
# Cadence parsing
# --------------------------------------------------------------------------- #
def parse_interval(expression: str) -> timedelta:
    """``'30m'`` / ``'6h'`` / ``'2d'`` -> a :class:`timedelta`."""
    match = _INTERVAL_PATTERN.match(expression or "")
    if not match:
        raise ScheduleError(
            f"Bad interval {expression!r}. Use a number plus m, h or d — e.g. 6h."
        )
    amount = int(match.group(1))
    if amount <= 0:
        raise ScheduleError(f"Interval must be positive, got {expression!r}.")
    return timedelta(**{_UNITS[match.group(2).lower()]: amount})


def build_trigger(kind: str, expression: str):
    """Turn a stored cadence into an APScheduler trigger."""
    if kind == INTERVAL:
        return IntervalTrigger(seconds=int(parse_interval(expression).total_seconds()))
    if kind == CRON:
        try:
            return CronTrigger.from_crontab(expression)
        except ValueError as exc:
            raise ScheduleError(f"Bad cron expression {expression!r}: {exc}") from exc
    raise ScheduleError(f"Unknown schedule kind {kind!r}; use {INTERVAL} or {CRON}.")


def describe(kind: str, expression: str) -> str:
    """Human wording for a cadence, e.g. 'every 6h' / 'cron 0 6 * * *'."""
    return f"every {expression}" if kind == INTERVAL else f"cron {expression}"


# --------------------------------------------------------------------------- #
# Schedule storage
# --------------------------------------------------------------------------- #
def set_schedule(
    conn: sqlite3.Connection, store: str, kind: str, expression: str, enabled: bool = True
) -> None:
    """Create or replace a store's cadence. Validates before it writes."""
    # GFP-287: schedulable, not merely available. A source can be ready and
    # correct to run on demand while being a bad idea to run every week, because
    # a metered vendor bills per call. Built for publix-catalog, which cost 9.1
    # credits per usable row against Walmart's 0.14; GFP-304 deleted that
    # scraper, so nothing opts out today -- see service.schedulable_scrapers.
    #
    # Guarded HERE rather than only removing the row, because a schedule is a
    # standing instruction to spend money: deleting one without closing the door
    # leaves it a single UI click from coming back, with nothing recording why
    # it should not.
    if store not in service.schedulable_scrapers():
        raise service.UnknownStoreError(store)
    build_trigger(kind, expression)  # raises ScheduleError on nonsense
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO schedules(store, kind, expression, enabled, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(store) DO UPDATE SET kind=excluded.kind, "
        "expression=excluded.expression, enabled=excluded.enabled, "
        "updated_at=excluded.updated_at",
        (store, kind, expression, int(enabled), now, now),
    )
    conn.commit()


def remove_schedule(conn: sqlite3.Connection, store: str) -> bool:
    cur = conn.execute("DELETE FROM schedules WHERE store=?", (store,))
    conn.commit()
    return cur.rowcount > 0


def list_schedules(
    conn: sqlite3.Connection | None = None, enabled_only: bool = False
) -> list[sqlite3.Row]:
    own = conn or db.connect()
    where = " WHERE enabled=1" if enabled_only else ""
    return own.execute(
        "SELECT store, kind, expression, enabled, updated_at "
        f"FROM schedules{where} ORDER BY store"
    ).fetchall()


def next_run(kind: str, expression: str, after: datetime | None = None) -> datetime | None:
    """When this cadence would next fire — used to show 'next run' to the user."""
    moment = after or datetime.now().astimezone()
    return build_trigger(kind, expression).get_next_fire_time(None, moment)


# --------------------------------------------------------------------------- #
# Catch-up
# --------------------------------------------------------------------------- #
def due_stores(
    conn: sqlite3.Connection, now: datetime | None = None
) -> list[str]:
    """Scheduled stores whose last clean run is older than their cadence.

    Cron schedules use a day as the staleness yardstick: we cannot ask a cron
    expression "how long between fires", but a daily-or-tighter cron that has
    not succeeded in 24h is unambiguously overdue.
    """
    overdue: list[str] = []
    for row in list_schedules(conn, enabled_only=True):
        window = (
            parse_interval(row["expression"])
            if row["kind"] == INTERVAL else timedelta(days=1)
        )
        if jobs.is_due(conn, row["store"], window, now=now):
            overdue.append(row["store"])
    return overdue


def run_catch_up(
    conn: sqlite3.Connection | None = None,
    on_event: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Reap interrupted jobs, then scrape every store that is overdue.

    Safe to call at any time; this is the whole scheduler minus the waiting,
    which makes it the thing tests and ``gplan schedule run --once`` drive.
    """
    own = conn or db.connect()
    say = on_event or (lambda _message: None)

    reaped = jobs.recover_interrupted(own)
    if reaped:
        say(f"reaped {reaped} interrupted job(s) from a previous run")

    ran: list[str] = []
    failed: dict[str, str] = {}
    for store in due_stores(own, now=datetime.now(timezone.utc)):
        say(f"{store}: overdue, scraping now")
        try:
            result = jobs.run_tracked_scrape(store, conn=own)
        except Exception as exc:  # a dead store must not stop the others
            failed[store] = f"{type(exc).__name__}: {exc}"
            say(f"{store}: FAILED — {failed[store]}")
            continue
        ran.append(store)
        say(f"{store}: stored {result['stats']['total']} deals")
    return {"reaped": reaped, "ran": ran, "failed": failed}


# --------------------------------------------------------------------------- #
# The scheduler
# --------------------------------------------------------------------------- #
def _scrape_job(store: str) -> None:
    """Job body. Opens its own connection: APScheduler runs this off-thread."""
    conn = db.connect()
    try:
        jobs.run_tracked_scrape(store, conn=conn)
    except Exception:
        pass  # already recorded on the job row; never kill the scheduler thread
    finally:
        conn.close()


def build_scheduler(
    conn: sqlite3.Connection | None = None, blocking: bool = True
) -> BackgroundScheduler | BlockingScheduler:
    """Construct a scheduler with one job per enabled schedule row."""
    own = conn or db.connect()
    scheduler = BlockingScheduler() if blocking else BackgroundScheduler()
    for row in list_schedules(own, enabled_only=True):
        scheduler.add_job(
            _scrape_job,
            trigger=build_trigger(row["kind"], row["expression"]),
            args=[row["store"]],
            id=f"scrape:{row['store']}",
            name=f"scrape {row['store']} ({describe(row['kind'], row['expression'])})",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=MISFIRE_GRACE_SECONDS,
        )
    return scheduler
