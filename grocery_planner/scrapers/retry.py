# ######### decohen-partners ##########
# Protein Ledger
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


# --------------------------------------------------------------------------- #
# Self-pacing
#
# Retrying is what you do after a request fails. Pacing is what you do so it
# doesn't. They are different jobs and this is the second one.
#
# WHY THIS EXISTS: the Sprouts measurement
# ----------------------------------------
# One host, two paths, policed completely differently -- measured 2026-08-11:
#
#   /graphql                  37,500+ requests at ~36/s   0 errors, 0 429s
#   /store/.../products/<id>  ~2,300 pages at ~8/s        hard 403 on every
#                                                         subsequent page
#
# The storefront and /graphql kept serving throughout, and the product path
# recovered after a cool-off. So the 403 was a *rate* verdict about one path,
# not a credential problem and not an outage.
#
# THAT BREAKS AN ASSUMPTION THIS MODULE STATES ABOVE. `request()` says a 4xx is
# never retried because "a 403 is a credential problem and will be a credential
# problem in four seconds' time". True of Kroger, whose 403 means a bad token.
# False of a WAF, whose 403 means "slow down". Both readings are right for their
# own source, so the distinction is made explicit rather than silently changed:
# `request()` keeps its rule, and a caller that KNOWS its 403 is a throttle
# opts in by pacing the path with a Budget.
#
# The strategy is AIMD, the same shape TCP uses: multiplicative slow-down on a
# throttle signal, gradual additive recovery on sustained success. It is chosen
# because it converges on a sustainable rate WITHOUT anyone having to guess the
# server's real limit -- which is good, because the real limit is undocumented,
# is clearly not a simple req/s figure (2,300 requests got through before the
# wall), and can change without notice.
# --------------------------------------------------------------------------- #

#: Status codes that mean "you are going too fast", when a caller has told us
#: that is what they mean for this path. 403 is here ONLY for callers that opt
#: in -- see the note above.
THROTTLE_STATUS = frozenset({403, 429})


@dataclass(frozen=True)
class Budget:
    """A pacing policy for one path class.

    Per *path class*, not per host, because the Sprouts measurement shows one
    host can police two paths by completely different rules. A single
    host-wide budget would have to be as slow as the strictest path, throwing
    away the 36 req/s the GraphQL endpoint genuinely allows.
    """

    name: str
    #: Fastest we will ever go, even after a long clean run. A floor, not a target.
    min_interval: float
    #: Slowest we will go before giving up on the path entirely.
    max_interval: float
    #: Multiply the interval by this on a throttle signal. >1 slows down.
    backoff: float = 4.0
    #: Multiply by this after `recovery_after` clean requests. <1 speeds up.
    recovery: float = 0.8
    #: Clean requests before easing off. Deliberately >1: recovering as fast as
    #: we back off would oscillate straight back into the wall.
    recovery_after: int = 25
    #: Consecutive throttle signals before the cool-off timer fires.
    streak_limit: int = 3
    #: How long to sit out once the streak limit is hit. The "timer".
    cooldown_seconds: float = 300.0


#: Measured against shop.sprouts.com. GraphQL tolerated 36 req/s; this asks for
#: about a third of that, because a shipped scraper has no reason to take the
#: fastest rate a host will bear -- only a rate it will bear indefinitely.
GRAPHQL_BUDGET = Budget(
    name="graphql", min_interval=0.08, max_interval=2.0, cooldown_seconds=120.0
)
#: The path that actually hit the wall. ~2,300 requests at 8/s tripped it, so
#: the floor here is deliberately an order of magnitude slower.
PRODUCT_PAGE_BUDGET = Budget(
    name="product-page", min_interval=0.5, max_interval=30.0, cooldown_seconds=600.0
)


class Paced:
    """Self-rate-limiter for one path class. Slows itself down on 403/429.

    Not thread-safe by design: one instance belongs to one scrape loop, and
    sharing it across threads would need a lock whose contention would itself
    become the rate limit. A concurrent caller should hold one per worker and
    divide the budget.
    """

    def __init__(self, budget: Budget, sleep: Callable[[float], None] | None = None,
                 clock: Callable[[], float] | None = None):
        # Resolved at CALL time, not at def time. A default argument of
        # `time.sleep` is bound once when this module is imported, which makes
        # the pacer impossible to neutralise from outside -- and the test suite
        # then pays the production pacing in real seconds. conftest.py patches
        # `retry.time.sleep`, and that only works because of this late binding.
        self.budget = budget
        self.interval = budget.min_interval
        self._sleep = sleep if sleep is not None else (lambda s: time.sleep(s))
        self._clock = clock if clock is not None else (lambda: time.monotonic())
        self._next_slot = 0.0
        self._clean_run = 0
        self._throttle_streak = 0
        #: Counters worth surfacing in a scrape's stats -- a run that was
        #: quietly halved in speed should be legible afterwards.
        self.throttled = 0
        self.cooldowns = 0
        self.slept = 0.0

    def wait(self) -> None:
        """Block until this path's next slot is due."""
        now = self._clock()
        delay = self._next_slot - now
        if delay > 0:
            self._sleep(delay)
            self.slept += delay
            now = self._next_slot
        self._next_slot = now + self.interval

    def record_success(self) -> None:
        """A clean response. Ease off, but slowly."""
        self._throttle_streak = 0
        self._clean_run += 1
        if self._clean_run >= self.budget.recovery_after:
            self._clean_run = 0
            self.interval = max(
                self.budget.min_interval, self.interval * self.budget.recovery
            )

    def record_throttled(self) -> bool:
        """A throttle signal. Slow down hard; report whether to cool off.

        Returns ``True`` when the streak limit is reached, meaning the caller
        should stop hitting this path and let :meth:`cool_off` run its timer.
        """
        self.throttled += 1
        self._clean_run = 0
        self._throttle_streak += 1
        self.interval = min(self.budget.max_interval, self.interval * self.budget.backoff)
        log.warning(
            "%s: throttled (%d in a row); interval now %.2fs",
            self.budget.name, self._throttle_streak, self.interval,
        )
        return self._throttle_streak >= self.budget.streak_limit

    def cool_off(self) -> None:
        """Sit out the timer, then resume at the slowest rate rather than the
        fastest -- coming back at full speed is how a cool-off turns into a loop.
        """
        self.cooldowns += 1
        self._throttle_streak = 0
        self.interval = self.budget.max_interval
        log.warning(
            "%s: cooling off for %.0fs after repeated throttling",
            self.budget.name, self.budget.cooldown_seconds,
        )
        self._sleep(self.budget.cooldown_seconds)
        self.slept += self.budget.cooldown_seconds
        self._next_slot = self._clock() + self.interval

    def stats(self) -> dict[str, float | int]:
        """Pacing facts for a scrape's stats blob. See the no-silent-caps rule:
        a run that was throttled into crawling must say so."""
        return {
            f"{self.budget.name}_throttled": self.throttled,
            f"{self.budget.name}_cooldowns": self.cooldowns,
            f"{self.budget.name}_interval": round(self.interval, 3),
            f"{self.budget.name}_slept_seconds": round(self.slept, 1),
        }
