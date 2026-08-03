"""Retrying only what is worth retrying (GFP-108).

Reported from the GUI: ``harristeeter-api`` failed with "Service unavailable",
and running it again immediately stored 990 deals. A transient Kroger 503 threw
away a whole scrape and showed a message that reads like a broken product.

**Half these tests are about what must NOT be retried.** Retrying something
that can never succeed is worse than failing fast: it buries a real error
behind a wall of waiting, and on a rate-limited API it actively makes things
worse.
"""
from __future__ import annotations

import httpx
import pytest

from grocery_planner.scrapers import retry


def _response(status: int, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status, headers=headers or {},
        request=httpx.Request("GET", "https://example.invalid"),
    )


class _Sequence:
    """Returns each queued outcome in turn: raises the exceptions, returns
    the responses."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def no_sleep():
    """Retries are tested for BEHAVIOUR, never for real elapsed time."""
    slept: list[float] = []
    return slept, slept.append


# --------------------------------------------------------------------------- #
# What IS retried
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_a_server_error_is_retried(status, no_sleep):
    _, sleep = no_sleep
    send = _Sequence(_response(status), _response(200))
    assert retry.request(send, sleep=sleep).status_code == 200
    assert send.calls == 2


@pytest.mark.parametrize("exc", [
    httpx.ConnectError("down"),
    httpx.ConnectTimeout("slow"),
    httpx.ReadTimeout("slow"),
    httpx.RemoteProtocolError("rude"),
])
def test_a_transport_failure_is_retried(exc, no_sleep):
    _, sleep = no_sleep
    send = _Sequence(exc, _response(200))
    assert retry.request(send, sleep=sleep).status_code == 200
    assert send.calls == 2


def test_the_reported_bug_now_recovers(no_sleep):
    """The exact case from the ticket: one 503, then success."""
    _, sleep = no_sleep
    send = _Sequence(_response(503), _response(200))
    assert retry.request(send, what="Kroger /products", sleep=sleep).status_code == 200


def test_it_gives_up_eventually(no_sleep):
    """A real outage must fail, not retry forever."""
    _, sleep = no_sleep
    send = _Sequence(*[_response(503)] * retry.MAX_ATTEMPTS)
    assert retry.request(send, sleep=sleep).status_code == 503
    assert send.calls == retry.MAX_ATTEMPTS


def test_a_persistent_transport_failure_raises_the_original(no_sleep):
    """The caller's own error handling needs the real exception, not a wrapper
    that hides which host was unreachable."""
    _, sleep = no_sleep
    send = _Sequence(*[httpx.ConnectError("down")] * retry.MAX_ATTEMPTS)
    with pytest.raises(httpx.ConnectError):
        retry.request(send, sleep=sleep)


# --------------------------------------------------------------------------- #
# What must NOT be retried -- the half that matters most
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_a_client_error_is_never_retried(status, no_sleep):
    """A 401 will still be a 401 in four seconds. AuthFailedError already tells
    the user to check developer.kroger.com; burying that behind three retries
    helps nobody."""
    slept, sleep = no_sleep
    send = _Sequence(_response(status))
    assert retry.request(send, sleep=sleep).status_code == status
    assert send.calls == 1
    assert slept == []


def test_a_success_is_returned_immediately(no_sleep):
    _, sleep = no_sleep
    send = _Sequence(_response(200))
    assert retry.request(send, sleep=sleep).status_code == 200
    assert send.calls == 1


def test_an_unexpected_exception_is_not_retried(no_sleep):
    """A bug in our own parsing must surface, not be attempted three times.
    RETRYABLE_EXCEPTIONS is deliberately a list, never bare Exception."""
    _, sleep = no_sleep
    send = _Sequence(ValueError("a bug in our code"))
    with pytest.raises(ValueError):
        retry.request(send, sleep=sleep)
    assert send.calls == 1


# --------------------------------------------------------------------------- #
# 429 -- transient, but retrying fast makes it worse
# --------------------------------------------------------------------------- #
def test_a_rate_limit_waits_longer_than_a_server_error(no_sleep):
    slept, sleep = no_sleep
    retry.request(_Sequence(_response(429), _response(200)), sleep=sleep)
    rate_limited = slept[0]

    slept.clear()
    retry.request(_Sequence(_response(503), _response(200)), sleep=sleep)
    server_error = slept[0]

    assert rate_limited > server_error


def test_retry_after_is_honoured(no_sleep):
    slept, sleep = no_sleep
    retry.request(
        _Sequence(_response(429, {"Retry-After": "2"}), _response(200)), sleep=sleep
    )
    assert 1.0 <= slept[0] <= 2.0        # 2s, with jitter applied


def test_a_nonsense_retry_after_is_ignored_not_obeyed(no_sleep):
    """Mis-parsing a header into a huge sleep would be worse than ignoring it.
    The HTTP-date form is legal but rare here."""
    slept, sleep = no_sleep
    retry.request(
        _Sequence(_response(429, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
                  _response(200)),
        sleep=sleep,
    )
    assert slept[0] <= retry.MAX_DELAY_SECONDS


def test_an_exhausted_quota_says_so_rather_than_failing_vaguely(no_sleep):
    """Kroger's budget is 10,000 Products calls a day PER CREDENTIAL (GFP-101).
    Waiting minutes will not help, and the remedy is completely different from
    a passing blip."""
    _, sleep = no_sleep
    send = _Sequence(*[_response(429)] * retry.MAX_ATTEMPTS)
    with pytest.raises(retry.RateLimited) as caught:
        retry.request(send, sleep=sleep)
    assert "later" in str(caught.value)


# --------------------------------------------------------------------------- #
# Bounded, and visible
# --------------------------------------------------------------------------- #
def test_no_single_wait_is_unbounded(no_sleep):
    slept, sleep = no_sleep
    retry.request(
        _Sequence(_response(429, {"Retry-After": "3600"}), _response(200)), sleep=sleep
    )
    assert slept[0] <= retry.MAX_DELAY_SECONDS


def test_backoff_grows(no_sleep):
    slept, sleep = no_sleep
    retry.request(
        _Sequence(_response(503), _response(503), _response(200)),
        max_attempts=3, sleep=sleep,
    )
    assert len(slept) == 2
    assert slept[1] > slept[0]


def test_delays_are_jittered():
    """Several stores scrape at once ("Scrape all"), and without jitter their
    retries line up and hit the upstream in the same instant -- turning one
    blip into a synchronised stampede."""
    seen = set()
    for _ in range(12):
        slept: list[float] = []
        retry.request(_Sequence(_response(503), _response(200)), sleep=slept.append)
        seen.add(round(slept[0], 6))
    assert len(seen) > 1, "every retry waited exactly the same time"


def test_a_retry_is_reported_so_the_ui_can_show_it(no_sleep):
    """A GUI row sitting on "Scraping..." with no explanation is what made a
    passing 503 look like a hang."""
    _, sleep = no_sleep
    seen: list[retry.Attempt] = []
    retry.request(
        _Sequence(_response(503), _response(200)), on_retry=seen.append, sleep=sleep
    )
    assert len(seen) == 1
    assert seen[0].number == 2 and seen[0].of == retry.MAX_ATTEMPTS
    assert "503" in seen[0].reason


def test_the_reason_never_leaks_a_url(no_sleep):
    """It reaches a UI label, and a URL can carry a postal code."""
    _, sleep = no_sleep
    seen: list[retry.Attempt] = []
    retry.request(
        _Sequence(_response(503), _response(200)), on_retry=seen.append, sleep=sleep
    )
    # "HTTP 503" is fine -- a URL is not. Check for the scheme separator
    # rather than the word, which the status reason legitimately contains.
    assert "://" not in seen[0].reason
    assert "example.invalid" not in seen[0].reason


# --------------------------------------------------------------------------- #
# The message a user actually reads
# --------------------------------------------------------------------------- #
def test_a_transient_failure_reads_as_temporary():
    """"Service unavailable" with no context is what made this look like a
    dead product."""
    message = retry.describe(httpx.ConnectError("down"))
    assert "temporary" in message.lower()
    assert "untouched" in message.lower()          # their prices are safe


def test_a_rate_limit_message_says_try_later():
    """Raised through the real path rather than constructed: the wording that
    matters is the one request() actually produces."""
    send = _Sequence(*[_response(429)] * retry.MAX_ATTEMPTS)
    with pytest.raises(retry.RateLimited) as caught:
        retry.request(send, sleep=lambda _s: None)
    assert "later" in retry.describe(caught.value).lower()


def test_an_unknown_error_is_passed_through_unchanged():
    """Never dress up an error this module does not understand."""
    assert retry.describe(ValueError("something specific")) == "something specific"
