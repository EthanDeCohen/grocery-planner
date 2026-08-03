"""Is a refresh due, and which stores (GFP-105)?

Reported from first use: a fresh install had no data, and the only way to get
any was Data ▸ Run scrape, then picking each store in turn and clicking through
four times. A nutritionist opening this for the first time sees an empty app
with no indication that the fix is four manual actions buried in a dialog.

The rule the user stated, implemented here verbatim:

* **No data for any store** -> refresh. An empty database is unambiguous: there
  is nothing to lose and nothing to weigh.
* **A new day, and nothing has run yet** -> refresh.
* Otherwise, don't.

**Why this is a module and not two lines in the GUI.** The second condition is
the whole reason: GFP-102 will run scrapes on an OS timer, and the app must not
scrape again just because someone opened it. Both need to reach the *same*
answer to "is a refresh due?", so the answer lives in one place and both ask it
— the rule GFP-40 established for the trend query, applied to a decision instead
of a number.

**One calendar day, not 24 hours.** ``jobs.is_due`` already answers "has it been
N hours", which is the right question for a cadence. This is a different one: a
nutritionist opening the app on Tuesday morning wants Tuesday's prices, whether
Monday's run was at 09:00 or 23:00. Comparing dates says that; comparing a
24-hour delta does not.

**Deciding is separate from doing.** This module answers the question and never
scrapes. The caller owns showing progress and letting the user stop — automatic
network activity that cannot be seen or cancelled is not acceptable on someone
else's machine, so the decision must not be entangled with the doing.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from .. import db, jobs
from . import ingest


#: Why a refresh is (or is not) due. A plain string rather than an enum because
#: it is shown to a human and logged, and the set will grow (GFP-102's opt-out).
NO_DATA = "no-data"
NEW_DAY = "new-day"
UP_TO_DATE = "up-to-date"


@dataclass(frozen=True)
class RefreshDecision:
    """Whether to refresh, which stores, and the reason — in words a user reads."""

    due: bool
    reason: str
    stores: list[str]
    #: The most recent successful scrape of ANY store, or None if there has
    #: never been one. Carried so a caller can say "last updated ..." without
    #: asking again.
    last_success_on: str | None = None

    @property
    def explanation(self) -> str:
        """One sentence a UI can show without composing it itself."""
        if self.reason == NO_DATA:
            return "No prices on record yet — fetching this week's prices."
        if self.reason == NEW_DAY:
            since = f" (last updated {self.last_success_on})" if self.last_success_on else ""
            return f"Prices have not been fetched today{since} — refreshing."
        return f"Prices are up to date (last updated {self.last_success_on})."


def _last_success_date(conn: sqlite3.Connection, stores: list[str]) -> date | None:
    """The most recent day ANY store scraped cleanly."""
    days = []
    for store in stores:
        when = jobs.last_success(conn, store)
        if when is not None:
            days.append(when.date())
    return max(days) if days else None


def has_any_data(conn: sqlite3.Connection) -> bool:
    """Has this install ever captured a price?

    Deals rather than price_history: `deals` is what every panel reads, and an
    install could in principle have history retained (GFP-42) while its current
    snapshot is empty. What a user means by "no data" is an empty screen.
    """
    return conn.execute("SELECT 1 FROM deals LIMIT 1").fetchone() is not None


def refresh_decision(
    today: date | None = None,
    conn: sqlite3.Connection | None = None,
) -> RefreshDecision:
    """Should a refresh run right now, and for which stores?

    Only stores that are actually *ready* are returned — a registered scraper
    whose credentials are missing (Whole Foods without a session, Kroger without
    a key) is skipped rather than attempted and reported as a failure. That
    distinction already exists as ``service.available_scrapers``; this reuses it
    instead of restating what "ready" means.
    """
    own = conn or db.connect()
    anchor = today or date.today()
    ready = list(ingest.available_scrapers())
    last = _last_success_date(own, ready)
    last_iso = last.isoformat() if last else None

    if not has_any_data(own):
        return RefreshDecision(True, NO_DATA, ready, last_iso)
    if last is None or last < anchor:
        return RefreshDecision(True, NEW_DAY, ready, last_iso)
    return RefreshDecision(False, UP_TO_DATE, [], last_iso)


def mark_refreshed(store: str, conn: sqlite3.Connection | None = None) -> None:
    """Record a successful refresh outside the normal scrape path.

    Exists so GFP-102's background timer and this app cannot double-scrape: the
    timer records its run the same way a GUI scrape does, and both then read the
    same `scraping_jobs` history through :func:`refresh_decision`. Nothing here
    invents a second source of truth about when a refresh last happened.
    """
    own = conn or db.connect()
    job_id = jobs.start_job(own, store, note="external refresh")
    jobs.finish_job(own, job_id, message="recorded by mark_refreshed")


def stores_needing_refresh(
    today: date | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[str]:
    """Shorthand for callers that only want the list."""
    return refresh_decision(today=today, conn=conn).stores


def last_refreshed_on(
    conn: sqlite3.Connection | None = None,
) -> str | None:
    """ISO date of the most recent successful scrape, or None."""
    return refresh_decision(conn=conn).last_success_on


def describe_now(conn: sqlite3.Connection | None = None) -> str:
    """The current state in one sentence, for a status bar or a log line."""
    return refresh_decision(conn=conn).explanation
