"""Which supported stores actually serve a ZIP (GFP-257).

The gap this closes
-------------------
Nothing gated a store by ZIP. ``run_scrape(store_key, postal_code)`` took both
and simply tried, so scraping Greensboro 27401 would call a New York-only
banner, and every ZIP scrape called every registered scraper. At six scrapers
that is waste; at the ~20 GFP-165 proposes across six markets it is the
arithmetic that decides whether expansion is affordable.

It was also a correctness problem. GFP-67's guard raises
:class:`~grocery_planner.service.ingest.EmptyScrapeError` on a zero-row scrape,
because a silent empty parse used to wipe good data. A store that does not
operate in a ZIP also returns zero rows -- so a healthy scraper in the wrong
market looked exactly like a broken parser. Those are now different outcomes.

Three states, and the third one matters
---------------------------------------
:data:`SERVES` / :data:`DOES_NOT_SERVE` / :data:`UNKNOWN`. Collapsing "we could
not find out" into "no" would silently drop a store the client may genuinely
have, which is worse than showing it with a caveat -- savings.py's rule 1
applied to availability. **UNKNOWN is permissive**: an unknown store is still
scraped, so adding this feature never removes coverage that exists today.

How a scraper answers
---------------------
Capability, not obligation. A scraper module MAY declare either:

- ``serves(postal_code) -> bool | None`` -- ask the source. ``scrapers/kroger.py``
  has the real thing: ``/v1/locations?filter.zipCode.near=`` already returns the
  stores near a ZIP, and it already reads the ``chain`` value *back* rather than
  trusting a name (GFP-77's lesson, learned expensively).
- ``SERVICE_AREA`` -- a tuple of ZIP prefixes, maintained by hand. Coarse and
  honest, and the only option for a platform that cannot be asked.

A module that declares neither is :data:`UNKNOWN` forever, and behaves exactly
as it does today.

**One of our shipped platforms cannot be asked.** GFP-246 measured it: Food
Lion's ``/store-locator`` returns 403 with ``X-DataDome: protected``, and a
location cookie is ignored. The catalogue is reachable; the locator is not. So
PRISM banners declare a ``SERVICE_AREA`` instead -- which is why the capability
is a pair of options rather than one interface.

This is the shape GFP-184 describes generally: a scraper declaring what it can
actually produce.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import db, logs, scrapers

#: The store serves this ZIP.
SERVES = "serves"
#: It does not, established on evidence. Scraping it here would be pointless.
DOES_NOT_SERVE = "does_not_serve"
#: Could not be established. PERMISSIVE -- an unknown store is still scraped,
#: so this feature never removes coverage that exists today.
UNKNOWN = "unknown"

#: How an answer was reached, recorded so a surprising one can be traced.
BY_LOCATION_API = "location_api"
BY_SERVICE_AREA = "service_area"
BY_NO_CAPABILITY = "no_capability"

#: Store footprints change on a timescale of months, not days. Re-asking more
#: often than this spends requests on an answer that will not have changed --
#: and asking on every scrape is exactly the load this exists to remove.
TTL_DAYS = 60


@dataclass(frozen=True)
class Availability:
    scraper_key: str
    postal_code: str
    state: str
    method: str
    checked_at: str

    @property
    def should_scrape(self) -> bool:
        """UNKNOWN scrapes. Only established evidence stops a scrape."""
        return self.state != DOES_NOT_SERVE


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _in_service_area(area, postal_code: str) -> bool:
    return any(postal_code.startswith(prefix) for prefix in area)


def _ask(scraper_key: str, postal_code: str) -> tuple[str, str]:
    """Put the question to the scraper module. Returns ``(state, method)``.

    Never raises: a source that errors or times out is UNKNOWN, not
    unavailable. Treating a transport failure as "does not serve" would delete
    a store from a client's options because a network hiccup happened once, and
    the answer would then sit in the cache for the whole TTL.
    """
    module = scrapers.SCRAPERS.get(scraper_key)
    if module is None:
        return UNKNOWN, BY_NO_CAPABILITY

    ask = getattr(module, "serves", None)
    if callable(ask):
        try:
            answer = ask(postal_code)
        except Exception as exc:                      # noqa: BLE001 -- see docstring
            logs.get_logger(__name__).warning(
                "could not establish whether %s serves %s: %s",
                scraper_key, postal_code, exc)
            return UNKNOWN, BY_LOCATION_API
        if answer is None:
            return UNKNOWN, BY_LOCATION_API
        return (SERVES if answer else DOES_NOT_SERVE), BY_LOCATION_API

    area = getattr(module, "SERVICE_AREA", None)
    if area:
        return (SERVES if _in_service_area(area, postal_code)
                else DOES_NOT_SERVE), BY_SERVICE_AREA

    return UNKNOWN, BY_NO_CAPABILITY


def get(
    scraper_key: str, postal_code: str, conn: sqlite3.Connection | None = None,
) -> Availability | None:
    """The cached answer, or ``None`` if none has been established."""
    own = conn or db.connect()
    try:
        row = own.execute(
            "SELECT scraper_key, postal_code, state, method, checked_at "
            "FROM store_availability WHERE scraper_key = ? AND postal_code = ?",
            (scraper_key, postal_code),
        ).fetchone()
    except sqlite3.OperationalError:
        # Predates migration 0022. Degrade to today's behaviour rather than
        # fail -- this feature is additive.
        return None
    return Availability(*row) if row else None


def resolve(
    scraper_key: str,
    postal_code: str,
    conn: sqlite3.Connection | None = None,
    force: bool = False,
) -> Availability:
    """The availability of one scraper for one ZIP, asking only when stale.

    ``force`` re-asks regardless of the TTL, for an operator who has reason to
    believe a footprint changed.
    """
    own = conn or db.connect()
    cached = get(scraper_key, postal_code, conn=own)
    if cached and not force:
        try:
            age = _now() - datetime.fromisoformat(cached.checked_at)
            if age < timedelta(days=TTL_DAYS):
                return cached
        except ValueError:
            pass                                  # unparseable stamp -> re-ask

    state, method = _ask(scraper_key, postal_code)
    checked_at = _now().isoformat(timespec="seconds")
    try:
        own.execute(
            "INSERT INTO store_availability"
            "(scraper_key, postal_code, state, method, checked_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(scraper_key, postal_code) DO UPDATE SET "
            "state=excluded.state, method=excluded.method, "
            "checked_at=excluded.checked_at",
            (scraper_key, postal_code, state, method, checked_at),
        )
        own.commit()
    except sqlite3.OperationalError:
        pass                                      # predates migration 0022
    return Availability(scraper_key, postal_code, state, method, checked_at)


def serving_scrapers(
    postal_code: str, conn: sqlite3.Connection | None = None, force: bool = False,
) -> list[str]:
    """Which registered scrapers are worth running for this ZIP.

    Includes UNKNOWN, because unknown is permissive: this narrows a scrape plan
    on evidence and never on the absence of it.
    """
    own = conn or db.connect()
    return sorted(
        key for key in scrapers.SCRAPERS
        if resolve(key, postal_code, conn=own, force=force).should_scrape
    )


def report(
    postal_code: str, conn: sqlite3.Connection | None = None,
) -> list[Availability]:
    """Every scraper's availability for a ZIP, for an operator to read."""
    own = conn or db.connect()
    return [resolve(key, postal_code, conn=own) for key in sorted(scrapers.SCRAPERS)]
