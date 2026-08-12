"""The Run scrape dialog offers stores that exist near the user (GFP-257).

Reported from the running app: the dialog offered **ACME Markets** to a client
in Greensboro, NC. ACME is a Northeast chain with no branch within several
hundred miles.

The resolver to prevent that already existed -- `availability.serving_scrapers`
-- and `ingest.run_scrape` already refused those stores. Only the UI never
asked, so the sole effect of the old list was a wasted click and a result the
user could not interpret. These tests pin the two halves that were missing:
the dialog's list, and the "Scrape all" button that has to agree with it.

Everything asserts a relationship. Naming the seven stores that happen to be
excluded from 27401 today would break the moment ALDI opens in Greensboro,
which is a fact about the world, not a regression.
"""
from __future__ import annotations

import pytest

from grocery_planner import availability, service


def test_the_plan_is_readiness_intersected_with_location(conn):
    """Two independent filters. Neither alone is the answer."""
    plan = service.scrapers_for_postal_code("27401", conn=conn)
    ready = set(service.available_scrapers())
    serving = set(availability.serving_scrapers("27401", conn=conn))

    assert set(plan.keys) == ready & serving
    assert set(plan.excluded) == ready - serving


def test_nothing_offered_is_a_store_the_resolver_rejected(conn):
    """The property the bug violated, stated directly."""
    plan = service.scrapers_for_postal_code("27401", conn=conn)
    for key in plan.keys:
        assert availability.resolve(key, "27401", conn=conn).should_scrape


def test_excluded_stores_were_rejected_on_evidence_not_ignorance(conn):
    """UNKNOWN must never be a reason to hide a store.

    Collapsing "we could not find out" into "no" silently removes a store the
    client may genuinely have. Only established evidence may exclude.
    """
    plan = service.scrapers_for_postal_code("27401", conn=conn)
    for key in plan.excluded:
        assert availability.resolve(key, "27401", conn=conn).state == (
            availability.DOES_NOT_SERVE
        )


def test_an_unknown_store_is_still_offered(conn):
    """The permissive direction, asserted over whatever is unknown today."""
    plan = service.scrapers_for_postal_code("27401", conn=conn)
    unknown = [
        k for k in service.available_scrapers()
        if availability.resolve(k, "27401", conn=conn).state == availability.UNKNOWN
    ]
    for key in unknown:
        assert key in plan.keys


def test_the_filter_is_visible_when_it_removes_something(conn):
    """A hidden cap reads as 'this is everything' -- the no-silent-caps rule."""
    plan = service.scrapers_for_postal_code("27401", conn=conn)
    if plan.excluded:
        assert plan.summary
        assert plan.postal_code in plan.summary
        # It names them, so the user can tell a deliberate filter from a gap.
        assert len(plan.summary) > len(plan.postal_code)
    else:
        assert plan.summary == ""


def test_a_plan_that_hides_nothing_says_nothing(conn):
    """No banner when there is nothing to report."""
    plan = service.ScrapePlan(postal_code="27401", keys=["aldi"], excluded=[])
    assert plan.summary == ""


def test_excluded_stores_are_named_for_a_human(conn):
    """'ACME Markets', not 'acme' -- the message is user-facing."""
    plan = service.ScrapePlan(
        postal_code="27401", keys=[], excluded=["acme"],
    )
    assert "ACME Markets" in plan.summary


def test_a_second_source_key_still_gets_a_name(conn):
    """`sprouts-storefront` shares the banner's `stores` entry and has none of
    its own, so the fallback has to hold rather than crash."""
    plan = service.ScrapePlan(
        postal_code="27401", keys=[], excluded=["sprouts-storefront"],
    )
    assert "sprouts-storefront" in plan.summary


def test_different_zips_get_different_plans(conn):
    """The whole point: this is a function of location, not a constant.

    The availability rows are SEEDED rather than resolved live. The first
    version of this test let `availability.resolve` ask the real sources, which
    made it a network test in disguise -- and when conftest started blocking the
    network, every store resolved UNKNOWN, UNKNOWN is permissive, and the two
    ZIPs came back identical. It failed for the right reason and revealed that
    it had never been testing the plan logic at all, only the resolver's ability
    to reach the internet.

    Asserted as 'the two differ', not as two hard-coded store lists, so it keeps
    testing the behaviour as the registry grows.
    """
    now = "2026-08-12T00:00:00+00:00"
    absent = service.scrapers_for_postal_code("27401", conn=conn).keys[0]
    conn.execute(
        "INSERT INTO store_availability"
        "(scraper_key, postal_code, state, method, checked_at) VALUES (?,?,?,?,?)",
        (absent, "19103", availability.DOES_NOT_SERVE, availability.BY_LOCATION_API, now),
    )
    conn.commit()

    greensboro = service.scrapers_for_postal_code("27401", conn=conn)
    philadelphia = service.scrapers_for_postal_code("19103", conn=conn)
    assert set(greensboro.keys) != set(philadelphia.keys)
    assert absent in greensboro.keys and absent not in philadelphia.keys


def test_the_plan_makes_no_network_call(conn, monkeypatch):
    """Opening a dialog must stay instant, and must not depend on being online.

    `availability` resolves on a 60-day TTL and reads the database thereafter;
    this pins that the read path never reaches for the network.
    """
    import httpx

    def explode(*_a, **_k):
        raise AssertionError("scrapers_for_postal_code made a network request")

    monkeypatch.setattr(httpx.Client, "request", explode, raising=False)
    monkeypatch.setattr(httpx.Client, "send", explode, raising=False)
    service.scrapers_for_postal_code("27401", conn=conn)
