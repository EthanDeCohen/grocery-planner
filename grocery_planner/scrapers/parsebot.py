# ######### decohen-partners ##########
# Protein Ledger
"""Parse.bot -- a paid middleman that turns a walled site into a REST API (GFP-270).

In:  a keyword and a ZIP.  Out: JSON product rows.

Walmart and Publix both refuse an ordinary client -- redirects to a challenge
page, 403s on the price endpoint. Parse.bot reverse-engineers a site's own
internal API and re-exposes it. Verified live 2026-08-12; both cleared.

This is a client for ONE VENDOR, not a scraper. It holds the credential, the
pinned scraper ids, the pacing and the errors. `walmart.py` does the field
mapping, because the two chains return different shapes and merging them here
would give us one function with a chain-shaped branch in it.

THE DEPENDENCY, STATED PLAINLY. These rows arrive through infrastructure we do
not own, on a metered credential someone else can revoke. Three consequences:

1. A Parse.bot outage takes both stores down at once -- they are not independent
   failures. So `readiness()` reports the VENDOR, not the store; a missing key
   should read as one problem, not two broken scrapers.
2. The scraper id is pinned and it rots. It names an API generated for our
   account, and deleting it in the dashboard gives a 404 that explains nothing.
   `verify_pinned_ids` is the canary, same idea as Sprouts' query hash.
3. It is metered, so nothing here walks a catalogue. Both scrapers work from a
   bounded keyword list and report in `stats` exactly how bounded. The
   no-silent-caps rule matters more here than anywhere else, because the cap is
   what keeps the bill finite.

The key lives outside the repo in the user data dir, like `kroger-env.config`.
The env var holds the key ITSELF, not a path -- it is one short opaque string,
not a document.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import httpx

from .. import logs, paths
from . import base, retry

log = logs.get_logger(__name__)

API_ROOT = "https://api.parse.bot"

#: Where the key lives when it is not in the environment. NOT in the repo.
CREDENTIAL_FILE = "parsebot.json"
CREDENTIAL_KEY = "api_key"
ENV_VAR = "GROCERY_PLANNER_PARSEBOT_KEY"

#: Parse.bot authenticates with a custom header, not a Bearer token. Getting
#: this wrong returns 401 with no hint about which scheme it wanted.
AUTH_HEADER = "X-API-Key"

#: Credits exhausted. A budget, not a rate -- see :class:`OutOfCreditsError`.
PAYMENT_REQUIRED = 402

#: Measured 2026-08-12: a generated endpoint answers in roughly 2-20 s because
#: it is doing a live fetch of the underlying site on our behalf. That is one
#: to two orders of magnitude slower than talking to a retailer directly, and
#: it is the reason both scrapers are keyword-bounded rather than catalogue
#: walks. The floor is set well below the observed latency: the constraint here
#: is the vendor's meter, not their rate limit.
PARSEBOT_BUDGET = retry.Budget(
    name="parsebot", min_interval=0.25, max_interval=30.0, cooldown_seconds=120.0
)

#: Long, because a call is a live scrape of a hostile site, not a cache read.
DEFAULT_TIMEOUT = 180.0


class ParseBotError(RuntimeError):
    """Any failure that is the vendor's, not the retailer's."""


class MissingCredentialError(ParseBotError):
    """No API key. Distinct because the remedy is a file, not a retry."""


class ThrottledError(ParseBotError):
    """The vendor rate-limited or refused us for going too fast."""


class OutOfCreditsError(ParseBotError):
    """The account's monthly credit allowance is spent (HTTP 402).

    Deliberately NOT a ThrottledError, though both mean "no data right now".
    Throttling is a rate you can wait out in seconds and the pacer handles it;
    this is a *budget* that resets monthly or costs money, and waiting is the
    wrong response. Hit live on 2026-08-12: the free tier's credits ran out
    mid-session, and because a 402 is not in RETRYABLE_STATUS the retry layer
    correctly did not burn attempts on it -- but `raise_for_status()` then
    produced an httpx error that the scrapers' `except ParseBotError` did not
    catch, so one exhausted credit killed an entire run instead of one query.
    """


@dataclass(frozen=True)
class Endpoint:
    """One generated endpoint on one generated API.

    ``scraper_id`` is pinned per chain in that chain's module rather than here,
    so a re-generated Walmart API cannot silently take Publix's id with it.
    """

    scraper_id: str
    name: str

    @property
    def url(self) -> str:
        return f"{API_ROOT}/scraper/{self.scraper_id}/{self.name}"


def credential_path():
    """Where the key file lives, honouring no override (the env holds a VALUE)."""
    return paths.data_dir() / CREDENTIAL_FILE


def api_key() -> str | None:
    """The key, from the environment first, then the data dir. ``None`` if absent.

    Never logged, never returned in an error message, never written to stats.
    """
    from_env = (os.environ.get(ENV_VAR) or "").strip()
    if from_env:
        return from_env
    path = credential_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        # utf-8-sig above because PowerShell's `Out-File -Encoding utf8` writes
        # a BOM and json.loads refuses it -- the exact trap that made a good
        # Whole Foods session file read as unparseable (GFP-93).
        return None
    value = payload.get(CREDENTIAL_KEY)
    return str(value).strip() if value else None


def readiness() -> tuple[bool, str]:
    """``(ready, reason)`` for the vendor as a whole, not for one store."""
    if api_key():
        return True, ""
    return False, (
        "No Parse.bot API key. Put it in "
        f"{credential_path()} as {{\"{CREDENTIAL_KEY}\": \"pmx_...\"}}, or set "
        f"{ENV_VAR}. Walmart depends on it."
    )


class ParseBotClient:
    """Paced, retrying client for the generated endpoints.

    One instance per scrape. The pacer is per-instance rather than module-level
    for the same reason ``retry.Paced`` says: one pacer belongs to one loop.
    """

    def __init__(
        self,
        client: httpx.Client | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        pace: retry.Paced | None = None,
        key: str | None = None,
    ):
        resolved = key or api_key()
        if not resolved:
            raise MissingCredentialError(readiness()[1])
        self._key = resolved
        self.pace = pace or retry.Paced(PARSEBOT_BUDGET)
        #: Calls made by this client, and the vendor's own headroom counters as
        #: of the last response. Surfaced in every scrape's stats because the
        #: budget here is MONEY, and the free tier ran out mid-session on
        #: 2026-08-12 with no warning: two private API builds cost 75 credits
        #: each, 150 of the month's 200, before a single row was scraped.
        #: A number you only see at the moment it hits zero is not a budget.
        self.calls = 0
        self.headroom: dict[str, str] = {}
        self._owned = client is None
        self._http = client or httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": base.user_agent(),
                "Accept": "application/json",
            },
        )

    def __enter__(self) -> "ParseBotClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owned:
            self._http.close()

    def _record_headroom(self, response: httpx.Response) -> None:
        """Keep the vendor's own remaining-budget counters from this response."""
        for name in HEADROOM_HEADERS:
            value = response.headers.get(name)
            if value is not None:
                self.headroom[name.replace("x-", "").replace("-", "_")] = value

    def stats(self) -> dict[str, Any]:
        """What this run cost, and what is left. Goes into every scrape's stats.

        ``calls`` is ours and always accurate; the rest is whatever the vendor
        chose to tell us on the last response, so a missing key means the header
        was absent, never that the budget is fine.
        """
        return {"parsebot_calls": self.calls, **{
            f"parsebot_{k}": v for k, v in self.headroom.items()}}

    def call(self, endpoint: Endpoint, **params: Any) -> dict[str, Any]:
        """Call one generated endpoint and return its ``data`` payload.

        Parse.bot wraps every success as ``{"status": "success", "data": {...}}``.
        Unwrapping here rather than in each scraper keeps that envelope a
        vendor detail: if it changes shape, one function changes.
        """
        self.pace.wait()
        response = retry.request(
            lambda: self._http.get(
                endpoint.url,
                headers={AUTH_HEADER: self._key},
                params={k: v for k, v in params.items() if v is not None},
            ),
            what=f"parse.bot {endpoint.name}",
        )
        if response.status_code in retry.THROTTLE_STATUS:
            if self.pace.record_throttled():
                self.pace.cool_off()
            raise ThrottledError(
                f"Parse.bot returned {response.status_code} for {endpoint.name}. "
                "This is the vendor throttling us, not the retailer."
            )
        self.calls += 1
        self._record_headroom(response)
        self.pace.record_success()
        if response.status_code == PAYMENT_REQUIRED:
            # Surfaced with the vendor's own words, because the remedy is a
            # billing decision the operator has to make -- not something the
            # code can retry, pace around, or work out for itself.
            detail = {}
            try:
                detail = (response.json() or {}).get("error") or {}
            except ValueError:
                pass
            tier = detail.get("next_tier") or {}
            upgrade = (
                f" Next tier: {tier.get('display_name')} — {tier.get('credits')} "
                f"credits/month at ${tier.get('price')}."
                if tier else ""
            )
            raise OutOfCreditsError(
                (detail.get("message") or "Parse.bot credits are exhausted.")
                + upgrade
                + " Walmart cannot be scraped until this is resolved;"
                " every other source is unaffected."
            )
        if response.status_code == 404:
            # The pinned id is the likeliest cause and the least obvious, so
            # name it rather than letting a bare 404 surface.
            raise ParseBotError(
                f"Parse.bot has no endpoint {endpoint.name!r} on scraper "
                f"{endpoint.scraper_id}. The generated API was probably revised "
                "or deleted -- re-pin the id (see verify_pinned_ids)."
            )
        response.raise_for_status()
        body = response.json()
        if isinstance(body, dict) and body.get("status") == "error":
            raise ParseBotError(str(body.get("message") or "parse.bot reported an error"))
        data = body.get("data") if isinstance(body, dict) else None
        return data if isinstance(data, dict) else (body if isinstance(body, dict) else {})


#: Vendor headers worth keeping. Credits are billed monthly but there is ALSO a
#: daily request cap (100 on the free tier), and the two run out independently
#: -- measured 2026-08-12, when credits hit zero while 61 daily requests
#: remained. Reporting only one of them would explain only half the failures.
HEADROOM_HEADERS = (
    "x-credits-remaining", "x-credits-used", "x-credit-cost",
    "x-ratelimit-daily-remaining", "x-ratelimit-daily-limit",
    "x-ratelimit-remaining",
)


def verify_pinned_ids(endpoints: dict[str, Endpoint], client: ParseBotClient | None = None
                      ) -> tuple[bool, str]:
    """Canary: do the pinned scraper ids still exist on this account?

    Exists because the failure is silent in the worst way -- a deleted or
    re-generated API does not degrade coverage, it zeroes a whole store, and
    a 404 from a vendor endpoint reads like a network blip rather than "the
    thing you pinned is gone".
    """
    owned = client is None
    active = client or ParseBotClient()
    try:
        seen = {e.scraper_id for e in endpoints.values()}
        response = active._http.get(          # noqa: SLF001 - canary, not a call path
            f"{API_ROOT}/dispatch/tasks",
            headers={AUTH_HEADER: active._key},
        )
        response.raise_for_status()
        known = {
            (t.get("result_scraper_id") or "")
            for t in (response.json().get("tasks") or [])
        }
        missing = sorted(seen - known) if known else []
        if missing:
            return False, f"pinned scraper id(s) not on this account: {', '.join(missing)}"
        return True, f"{len(seen)} pinned scraper id(s) present"
    except Exception as exc:                  # a canary must not raise
        return False, f"could not verify: {type(exc).__name__}: {exc}"
    finally:
        if owned:
            active.close()
