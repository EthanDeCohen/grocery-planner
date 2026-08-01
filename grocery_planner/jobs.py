"""Tracked scrape runs (GFP-7).

Every scrape the app performs on its own — scheduled or caught up after a
restart — is recorded as a row in ``scraping_jobs``, so a run that dies with
the process leaves evidence instead of vanishing. The table is also how the
app answers "is this store's data stale?" without re-reading every deal.

Lifecycle of a row::

    start_job()  -> running
      checkpoint()   (optional progress notes)
    finish_job() -> succeeded     | fail_job() -> failed

A row still marked ``running`` at startup means the process died mid-scrape;
:func:`recover_interrupted` reaps those to ``interrupted`` so they are never
mistaken for a live run.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from . import db, service

RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
INTERRUPTED = "interrupted"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def start_job(conn: sqlite3.Connection, store: str, note: str = "starting") -> int:
    """Open a job row for ``store`` and return its id."""
    cur = conn.execute(
        "INSERT INTO scraping_jobs(source, status, last_checkpoint, started_at, message) "
        "VALUES (?, ?, ?, ?, ?)",
        (store, RUNNING, note, _now(), ""),
    )
    conn.commit()
    return int(cur.lastrowid)


def checkpoint(conn: sqlite3.Connection, job_id: int, note: str) -> None:
    """Record how far a run got, so an interrupted job says where it stopped."""
    conn.execute(
        "UPDATE scraping_jobs SET last_checkpoint=? WHERE id=?", (note, job_id)
    )
    conn.commit()


def finish_job(conn: sqlite3.Connection, job_id: int, message: str = "") -> None:
    conn.execute(
        "UPDATE scraping_jobs SET status=?, finished_at=?, last_checkpoint=?, message=? "
        "WHERE id=?",
        (SUCCEEDED, _now(), "done", message, job_id),
    )
    conn.commit()


def fail_job(conn: sqlite3.Connection, job_id: int, message: str) -> None:
    conn.execute(
        "UPDATE scraping_jobs SET status=?, finished_at=?, message=? WHERE id=?",
        (FAILED, _now(), message, job_id),
    )
    conn.commit()


def recover_interrupted(conn: sqlite3.Connection) -> int:
    """Reap jobs left ``running`` by a crash or power loss. Returns how many."""
    cur = conn.execute(
        "UPDATE scraping_jobs SET status=?, finished_at=?, "
        "message='interrupted — process exited before the scrape finished' "
        "WHERE status=?",
        (INTERRUPTED, _now(), RUNNING),
    )
    conn.commit()
    return cur.rowcount


def recent_jobs(
    conn: sqlite3.Connection | None = None, limit: int = 20, store: str | None = None
) -> list[sqlite3.Row]:
    own = conn or db.connect()
    where, params = ("", [])
    if store:
        where, params = " WHERE source=?", [store]
    lim = f" LIMIT {int(limit)}" if limit else ""
    return own.execute(
        "SELECT id, source, status, last_checkpoint, started_at, finished_at, message "
        f"FROM scraping_jobs{where} ORDER BY id DESC{lim}", params
    ).fetchall()


def last_success(conn: sqlite3.Connection, store: str) -> datetime | None:
    """When this store last scraped cleanly, or None if it never has."""
    row = conn.execute(
        "SELECT finished_at FROM scraping_jobs WHERE source=? AND status=? "
        "ORDER BY id DESC LIMIT 1", (store, SUCCEEDED)
    ).fetchone()
    if row is None or not row["finished_at"]:
        return None
    try:
        return datetime.fromisoformat(row["finished_at"])
    except ValueError:
        return None


def is_due(
    conn: sqlite3.Connection, store: str, interval: timedelta, now: datetime | None = None
) -> bool:
    """True when ``store`` has no clean run inside ``interval``.

    This is what makes a missed window recoverable: after the machine wakes or
    the app restarts, a store whose last success is older than its cadence gets
    scraped straight away rather than waiting for the next tick.
    """
    previous = last_success(conn, store)
    if previous is None:
        return True
    return (now or datetime.now(timezone.utc)) - previous >= interval


def run_tracked_scrape(
    store_key: str,
    postal_code: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Run a scrape with a ``scraping_jobs`` row around it.

    Returns the same payload as :func:`service.run_scrape` plus ``job_id``. On
    failure the job row is marked ``failed`` with the message and the exception
    is re-raised — the caller decides whether that is fatal.
    """
    own = conn or db.connect()
    job_id = start_job(own, store_key)
    try:
        checkpoint(own, job_id, "fetching flyer")
        result = service.run_scrape(store_key, postal_code=postal_code, conn=own)
    except Exception as exc:
        fail_job(own, job_id, f"{type(exc).__name__}: {exc}")
        raise
    stats = result.get("stats", {})
    finish_job(own, job_id, f"stored {stats.get('total', '?')} deals")
    return {**result, "job_id": job_id}
