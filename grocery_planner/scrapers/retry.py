"""Retry the failures that are worth retrying, and only those (GFP-108).

Reported from the GUI: ``harristeeter-api`` failed with "Service unavailable".
Running it again immediately afterwards stored 990 deals. A transient Kroger
503, not a broken integration -- but the whole scrape was thrown away and the
user was shown a message that reads like the product is broken.

Verified before writing this: there was NO retry anywhere in the scraper HTTP
layer. Every non-200 raised.

WHY IT IS WORSE THAN ONE FAILED REQUEST
---------------------------------------
A shelf-price scrape is not one request; it pages through the Products endpoint
many times. A single blip at page 40 of 50 aborted the run and discarded every
page already fetched. **The longer and more valuable the scrape, the more likely
it is to hit one blip and the more it loses when it does** -- which is exactly
backwards.

It also lands on the two things built just before it. GFP-102 runs scrapes
UNATTENDED on a timer, where a transient failure nobody watches means a
silently missing day of price history that can never be backfilled. GFP-105
makes a scrape the first thing a new user ever sees.

WHAT IS RETRIED, AND WHAT MUST NOT BE
-------------------------------------
Retrying something that can never succeed is worse than failing: it delays a
real error behind a wall of waiting, and on a rate-limited API it actively
makes things worse.

* **Retried** -- 5xx, connection errors, read timeouts. The server said "not
  now", or the network hiccuped.
* **Never retried** -- 4xx. A 401/403 is a credential problem and will be a
  credential problem in four seconds' time; ``AuthFailedError`` already tells
  the user to check developer.kroger.com, and burying that behind three
  retries helps nobody.
* **429 is its own case.** Transient, but retrying fast makes it worse.
  ``Retry-After`` is honoured when present, and the budget is finite --
  Kroger allows 10,000 Products calls a day PER CREDENTIAL (GFP-101), so a
  genuinely exhausted quota must be said out loud rather than waited on for
  minutes.

The wall-clock cap matters as much as the attempt cap: a GUI scrape that
appears to hang is a worse experience than one that fails quickly, so this
gives up while a person is still willing to wait.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable

import httpx

from .. import logs

log = logs.get_logger(__name__)

#: Total attempts, including the first. Three means two retries, which covers
#: the overwhelming majority of transient blips without turning a real outage
#: into a long wait.
MAX_ATTEMPTS = 3

#: Never spend longer than this retrying one request, whatever the attempt
#: count allows. A GUI scrape that appears to hang is worse than one that fails.
MAX_ELAPSED_SECONDS = 20.0

#: First backoff, doubling thereafter.
BASE_DELAY_SECONDS = 0.5

#: Ceiling on any single sleep, so one long Retry-After cannot park a scrape.
MAX_DELAY_SECONDS = 8.0

#: A 429 waits longer than a 503: the server is telling us we are the problem.
RATE_LIMIT_DELAY_SECONDS = 5.0

#: Status codes worth trying again.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

#: Transport failures worth trying again. Deliberately NOT bare Exception --
#: a bug in our own parsing must not be retried three times before surfacing.
RETRYABLE_EXCEPTIONS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
)


class RateLimited(RuntimeError):
    """The upstream quota is exhausted, not merely busy.

    Distinct from a generic failure because the remedy is completely
    different: waiting minutes will not help, and on Kroger the budget is per
    credential per day (GFP-101).
    """


@dataclass(frozen=True)
class Attempt:
    """One retry, for a progress callback to report."""

    number: int          # 1-based; the attempt ABOUT to be made
    of: int
    delay: float         # seconds about to be slept
    reason: str          # "HTTP 503" / "ConnectTimeout" -- never a URL


def _retry_after(response: httpx.Response) -> float | None:
    """``Retry-After`` in seconds, if the server sent a usable one.

    Only the integer-seconds form is honoured. The HTTP-date form is legal but
    rare here, and mis-parsing a date into a huge sleep would be worse than
    ignoring it.
    """
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        seconds = float(raw.strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def _delay_for(attempt: int, response: httpx.Response | None) -> float:
    """How long to wait before ``attempt``, with jitter.

    Jitter is not decoration. Several stores can be scraped at once (GFP-103's
    "Scrape all"), and without it their retries line up and hit the upstream
    in the same instant -- turning one blip into a synchronised stampede.
    """
    if response is not None and response.status_code == 429:
        base = _retry_after(response) or RATE_LIMIT_DELAY_SECONDS * attempt
    else:
        base = BASE_DELAY_SECONDS * (2 ** (attempt - 1))
    base = min(base, MAX_DELAY_SECONDS)
    return base * (0.5 + random.random() * 0.5)


def request(
    send: Callable[[], httpx.Response],
    *,
    what: str = "request",
    on_retry: Callable[[Attempt], None] | None = None,
    max_attempts: int = MAX_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
) -> httpx.Response:
    """Call ``send()``, retrying only genuinely transient failures.

    Returns the response -- INCLUDING a non-200 one that is not retryable, so
    each caller keeps its own error handling and its own message. This decides
    *whether to try again*, never *what a failure means*.

    ``on_retry`` is how the GFP-103 scrape row shows "retrying" instead of
    sitting on "Scraping..." with no explanation.
    """
    started = time.monotonic()
    last_exception: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        response = None
        try:
            response = send()
            if response.status_code not in RETRYABLE_STATUS:
                return response
            reason = f"HTTP {response.status_code}"
        except RETRYABLE_EXCEPTIONS as exc:
            last_exception = exc
            reason = type(exc).__name__

        if attempt == max_attempts:
            break

        delay = _delay_for(attempt, response)
        if time.monotonic() - started + delay > MAX_ELAPSED_SECONDS:
            log.info("%s: giving up after %.1fs rather than waiting longer",
                     what, time.monotonic() - started)
            break

        log.info("%s: %s, retrying in %.1fs (attempt %d of %d)",
                 what, reason, delay, attempt + 1, max_attempts)
        if on_retry is not None:
            on_retry(Attempt(number=attempt + 1, of=max_attempts,
                             delay=delay, reason=reason))
        sleep(delay)

    # Out of attempts, or out of time.
    if last_exception is not None:
        raise last_exception
    if response is not None and response.status_code == 429:
        raise RateLimited(
            "The store's API is rate-limiting us and did not recover. This is "
            "usually a daily quota rather than a passing blip -- try again "
            "later rather than immediately."
        )
    return response      # a 5xx the caller will turn into its own error


def describe(exc: Exception) -> str:
    """A user-facing line that says TEMPORARY or BROKEN, not just what broke.

    'Service unavailable' with nothing else is what made a passing 503 look
    like a dead product. The distinction is the whole point of this function.
    """
    if isinstance(exc, RateLimited):
        return str(exc)
    if isinstance(exc, RETRYABLE_EXCEPTIONS):
        return (
            "Could not reach the store just now -- this is usually temporary. "
            "Your existing prices are untouched; try again in a few minutes."
        )
    return str(exc)
