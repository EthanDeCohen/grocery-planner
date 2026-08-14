"""Self-pacing: the scraper slows itself down before the server has to.

Driven by a fake clock throughout -- a pacing test that really sleeps is a
pacing test nobody runs. Every assertion is about the *relationship* between
one request and the next, not about wall-clock time.
"""
from __future__ import annotations

import pytest

from grocery_planner.scrapers import retry


class Clock:
    """A monotonic clock that only moves when something sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def __call__(self) -> float:
        return self.now


def paced(**overrides):
    budget = retry.Budget(
        name="test", min_interval=0.1, max_interval=8.0, backoff=4.0,
        recovery=0.5, recovery_after=3, streak_limit=2, cooldown_seconds=60.0,
        **overrides,
    )
    clock = Clock()
    return retry.Paced(budget, sleep=clock.sleep, clock=clock), clock


# --------------------------------------------------------------------------- #
# Steady state
# --------------------------------------------------------------------------- #
def test_first_request_does_not_wait():
    """Pacing must not tax the very first request -- a one-shot lookup should
    be as fast as it was before any of this existed."""
    pace, clock = paced()
    pace.wait()
    assert clock.slept == []


def test_requests_are_spaced_by_at_least_the_floor():
    pace, clock = paced()
    for _ in range(4):
        pace.wait()
        pace.record_success()
    assert all(s == pytest.approx(0.1) for s in clock.slept)


def test_a_slow_caller_is_not_made_to_wait_at_all():
    """If the caller already took longer than the interval, the slot is due.

    Otherwise pacing would compound with the request's own latency and halve
    the throughput it was only meant to cap.
    """
    pace, clock = paced()
    pace.wait()
    clock.now += 5.0          # the request itself took five seconds
    pace.wait()
    assert clock.slept == []


# --------------------------------------------------------------------------- #
# Backing off -- the multiplicative half of AIMD
# --------------------------------------------------------------------------- #
def test_a_throttle_signal_slows_the_next_request_down():
    pace, _clock = paced()
    before = pace.interval
    pace.record_throttled()
    assert pace.interval == pytest.approx(before * 4.0)


def test_backing_off_stops_at_the_ceiling():
    """Otherwise a long throttled run doubles its way into an infinite sleep."""
    pace, _clock = paced()
    for _ in range(20):
        pace.record_throttled()
    assert pace.interval == pace.budget.max_interval


def test_the_streak_limit_reports_when_to_cool_off():
    pace, _clock = paced()
    assert pace.record_throttled() is False   # 1 of 2
    assert pace.record_throttled() is True    # limit reached


def test_one_clean_response_resets_the_streak():
    """A single blip between successes is not a wall, and treating it as one
    would park a healthy scrape for the whole cooldown."""
    pace, _clock = paced()
    pace.record_throttled()
    pace.record_success()
    assert pace.record_throttled() is False


# --------------------------------------------------------------------------- #
# Recovering -- the additive half
# --------------------------------------------------------------------------- #
def test_recovery_is_slower_than_backoff():
    """The asymmetry is the point: recovering as fast as we back off would
    oscillate straight back into the wall."""
    pace, _clock = paced()
    assert pace.budget.backoff > 1.0 > pace.budget.recovery
    # And it takes several clean requests to earn even one step back.
    assert pace.budget.recovery_after > 1


def test_speed_is_regained_only_after_a_clean_run():
    pace, _clock = paced()
    pace.record_throttled()
    throttled_interval = pace.interval

    for _ in range(pace.budget.recovery_after - 1):
        pace.record_success()
    assert pace.interval == throttled_interval      # not yet earned

    pace.record_success()
    assert pace.interval < throttled_interval


def test_recovery_never_goes_faster_than_the_floor():
    pace, _clock = paced()
    for _ in range(500):
        pace.record_success()
    assert pace.interval == pace.budget.min_interval


# --------------------------------------------------------------------------- #
# The cool-off timer
# --------------------------------------------------------------------------- #
def test_cooling_off_sleeps_for_the_configured_timer():
    pace, clock = paced()
    pace.cool_off()
    assert pace.budget.cooldown_seconds in clock.slept


def test_resuming_after_a_cooldown_starts_slow_not_fast():
    """Coming back at full speed is how a cool-off becomes a loop."""
    pace, _clock = paced()
    pace.cool_off()
    assert pace.interval == pace.budget.max_interval


def test_cooling_off_clears_the_streak_so_it_can_be_earned_again():
    pace, _clock = paced()
    pace.record_throttled()
    pace.record_throttled()
    pace.cool_off()
    assert pace.record_throttled() is False


# --------------------------------------------------------------------------- #
# Reporting -- the no-silent-caps rule
# --------------------------------------------------------------------------- #
def test_a_throttled_run_is_legible_afterwards():
    """A run quietly slowed to a crawl must not look like a clean one."""
    pace, _clock = paced()
    pace.wait()
    pace.record_throttled()
    pace.cool_off()

    stats = pace.stats()
    assert stats["test_throttled"] == 1
    assert stats["test_cooldowns"] == 1
    assert stats["test_slept_seconds"] >= pace.budget.cooldown_seconds
    assert stats["test_interval"] == pytest.approx(pace.budget.max_interval)


def test_a_clean_run_reports_no_throttling():
    pace, _clock = paced()
    for _ in range(5):
        pace.wait()
        pace.record_success()
    stats = pace.stats()
    assert stats["test_throttled"] == 0
    assert stats["test_cooldowns"] == 0


# --------------------------------------------------------------------------- #
# The measured budgets
# --------------------------------------------------------------------------- #
def test_the_product_page_budget_is_far_slower_than_the_graphql_one():
    """The whole reason budgets are per path class: on one host, measured,
    /graphql took 37k requests while the product path died after ~2,300."""
    assert (
        retry.PRODUCT_PAGE_BUDGET.min_interval
        > retry.GRAPHQL_BUDGET.min_interval * 4
    )
    assert (
        retry.PRODUCT_PAGE_BUDGET.cooldown_seconds
        > retry.GRAPHQL_BUDGET.cooldown_seconds
    )


def test_403_counts_as_a_throttle_signal_for_opted_in_callers():
    """retry.request() still treats 4xx as fatal -- correct for Kroger, whose
    403 means a bad token. This set is the opt-in for callers whose 403 means
    'slow down'. Both readings coexist deliberately."""
    assert 403 in retry.THROTTLE_STATUS
    assert 429 in retry.THROTTLE_STATUS
    assert 403 not in retry.RETRYABLE_STATUS
