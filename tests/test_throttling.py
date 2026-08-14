"""Throttling, at the level of the scrapers rather than the pacer (GFP-263).

``test_pacing.py`` proves :class:`~grocery_planner.scrapers.retry.Paced` does
the right arithmetic. Nothing proved the scrapers were *wired to it*, and that
is the half that fails silently: a client that forgets to call ``wait()``, or
one that treats a 403 as "this product is missing" rather than "you are going
too fast", passes every parser test in the suite while walking straight into
the wall the pacer exists to avoid.

So these tests assert the wiring, on four sources, through their real code:

* a throttle signal is an ERROR, never an empty result. A 403 read as "no data"
  is the worst outcome available -- it looks exactly like a delisted product,
  so a fully-blocked run reports a small catalogue instead of a problem.
* the next request is slower than the last one.
* the run SURVIVES and says what it lost. Giving up on one product is not
  giving up on the run; losing 2,300 already-fetched ones to a blip is.
* the pacer's counters reach ``stats``, per the no-silent-caps rule.

Every test drives a fake clock (see :class:`Clock`). A throttling test that
really sleeps is a throttling test nobody runs -- the cool-off alone is 600 s.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from grocery_planner.scrapers import (
    aldi, instacart_storefront as ist, lidl_catalogue, prism, retry, sprouts,
    traderjoes, wegmans_api,
)

NOW = datetime(2026, 8, 12, tzinfo=timezone.utc)


class Clock:
    """A monotonic clock that only moves when something sleeps.

    Same shape as ``test_pacing.py``'s, duplicated rather than shared: these
    two files test different subjects, and a shared helper would make a change
    made for one of them silently rewrite the other's meaning.
    """

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def __call__(self) -> float:
        return self.now


def paced(budget: retry.Budget) -> tuple[retry.Paced, Clock]:
    clock = Clock()
    return retry.Paced(budget, sleep=clock.sleep, clock=clock), clock


# --------------------------------------------------------------------------- #
# Instacart Storefront Pro (Sprouts, ALDI) -- the source that measured the wall
#
# ~2,300 product pages, then a hard 403 on every subsequent one while /graphql
# and the storefront kept serving. That measurement is why any of this exists.
# --------------------------------------------------------------------------- #
def storefront_client(handler, page_pace=None, graphql_pace=None, tenant=None):
    tenant = tenant or sprouts.TENANT
    http = httpx.Client(
        base_url=tenant.base_url, transport=httpx.MockTransport(handler)
    )
    return ist.StorefrontClient(
        tenant, client=http, page_pace=page_pace, graphql_pace=graphql_pace
    )


def product_page(price: str = "6.49") -> str:
    graph = {"@context": "https://schema.org", "@graph": [{
        "@type": "Product", "name": "Ground Beef",
        "brand": {"@type": "Brand", "name": "Test"}, "category": "Meat",
        "size": "16 oz", "offers": {"price": price,
                                    "availability": "https://schema.org/InStock"},
    }]}
    return ('<html><head><script type="application/ld+json">'
            + json.dumps(graph) + "</script></head></html>")


def wall(status: int = 403):
    """A handler that answers everything data-bearing with a throttle verdict."""
    def handle(request: httpx.Request) -> httpx.Response:
        if "/products/" in request.url.path:
            return httpx.Response(status)
        return httpx.Response(200, text="<html></html>")
    return handle


@pytest.mark.parametrize("status", sorted(retry.THROTTLE_STATUS))
def test_a_throttled_product_page_is_an_error_not_an_empty_page(status):
    """THE ONE THAT MATTERS MOST.

    ``listing()`` returns ``None`` for a page that served but carried no
    JSON-LD -- a delisted product. If a 403 took that same path, a fully
    blocked run would report "the catalogue has no products" and every count
    downstream would agree with it. The two must not be the same value.
    """
    pace, _ = paced(retry.PRODUCT_PAGE_BUDGET)
    client = storefront_client(wall(status), page_pace=pace)
    with pytest.raises(ist.ThrottledError):
        client.listing("1234-ground-beef")


def test_a_throttled_page_slows_the_next_one_down():
    pace, _ = paced(retry.PRODUCT_PAGE_BUDGET)
    client = storefront_client(wall(), page_pace=pace)
    before = pace.interval
    with pytest.raises(ist.ThrottledError):
        client.listing("1234-ground-beef")
    assert pace.interval > before
    assert pace.interval == pytest.approx(before * retry.PRODUCT_PAGE_BUDGET.backoff)


def test_a_streak_of_throttles_makes_the_scraper_sit_out_the_timer():
    """Backing off per-request is not enough once the wall is up: the pacer has
    to stop hitting the path at all. Asserted through the client rather than the
    pacer, because the wiring is what is in doubt."""
    pace, clock = paced(retry.PRODUCT_PAGE_BUDGET)
    client = storefront_client(wall(), page_pace=pace)
    for _ in range(retry.PRODUCT_PAGE_BUDGET.streak_limit):
        with pytest.raises(ist.ThrottledError):
            client.listing("1234-ground-beef")
    assert pace.cooldowns == 1
    assert retry.PRODUCT_PAGE_BUDGET.cooldown_seconds in clock.slept


def test_a_clean_page_paces_itself_without_ever_being_throttled():
    """The floor is honoured on the happy path too -- otherwise the first
    evidence that pacing exists would be the wall it was meant to prevent."""
    pace, clock = paced(retry.PRODUCT_PAGE_BUDGET)
    client = storefront_client(
        lambda request: httpx.Response(200, text=product_page()), page_pace=pace
    )
    for _ in range(3):
        client.listing("1234-ground-beef")
    assert pace.throttled == 0
    # First request is free; the next two wait at least the floor.
    assert len(clock.slept) == 2
    assert all(s >= retry.PRODUCT_PAGE_BUDGET.min_interval for s in clock.slept)


def test_a_wall_on_the_product_path_does_not_slow_graphql_down():
    """One host, two paths, policed differently -- so one pacer per path class.

    A single host-wide pacer would drag the GraphQL bulk pass down to the
    product page's floor and turn a 46,000-product catalogue walk into a
    multi-hour crawl for no reason.
    """
    page_pace, _ = paced(retry.PRODUCT_PAGE_BUDGET)
    graphql_pace, _ = paced(retry.GRAPHQL_BUDGET)
    client = storefront_client(wall(), page_pace=page_pace, graphql_pace=graphql_pace)
    graphql_before = graphql_pace.interval
    with pytest.raises(ist.ThrottledError):
        client.listing("1234-ground-beef")
    assert page_pace.throttled == 1
    assert graphql_pace.throttled == 0
    assert graphql_pace.interval == graphql_before


def test_a_price_pass_survives_a_throttled_page_and_reports_what_it_lost(tmp_path):
    """A blocked page costs one product, not the run -- and the loss is counted.

    The failure this pins is a run that quietly returns 4 rows out of 40 and
    reads, in every UI and every stat, exactly like a store with 4 products.
    """
    calls = {"n": 0}

    def half_blocked(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/products/" in path:
            calls["n"] += 1
            # Every other page walls up.
            if calls["n"] % 2 == 0:
                return httpx.Response(403)
            return httpx.Response(200, text=product_page())
        if path == "/graphql":
            return httpx.Response(200, json={"data": {"shopCollection": {"shops": [
                {"id": "515202", "serviceType": "instore",
                 "retailerLocationId": "124437"}]}}})
        return httpx.Response(200, text="<html></html>")

    pace, _ = paced(retry.PRODUCT_PAGE_BUDGET)
    client = storefront_client(half_blocked, page_pace=pace)
    slugs = [f"{i}-ground-beef" for i in range(6)]

    rows, _meta, stats = ist.scrape_prices_only(
        aldi.TENANT, limit=len(slugs), postal_code="27401",
        conn=_memory_db(), client=client, now=NOW, slugs=slugs,
    )

    assert stats["skipped_throttled"] == 3
    assert len(rows) == 3
    # ...and the run still produced rows rather than aborting on the first 403.
    assert stats["total"] == 3


def test_a_throttled_run_says_so_in_its_stats():
    """No silent caps: a run that was slowed to a crawl must be legible as one
    afterwards, or the next person reads it as a source that got smaller."""
    pace, _ = paced(retry.PRODUCT_PAGE_BUDGET)
    client = storefront_client(wall(), page_pace=pace)
    for _ in range(2):
        with pytest.raises(ist.ThrottledError):
            client.listing("1234-ground-beef")

    stats = client.page_pace.stats()
    assert stats["product-page_throttled"] == 2
    assert stats["product-page_interval"] > retry.PRODUCT_PAGE_BUDGET.min_interval


def _memory_db():
    """A schema'd in-memory DB, for the two tests that write food facts."""
    from grocery_planner import db as _db

    conn = _db.connect(":memory:")
    return conn


# --------------------------------------------------------------------------- #
# Lidl -- unmeasured limits, so it starts on the conservative budget
# --------------------------------------------------------------------------- #
def lidl_client(handler, pace=None):
    http = httpx.Client(
        base_url="https://www.lidl.com", transport=httpx.MockTransport(handler)
    )
    return lidl_catalogue.LidlClient(client=http, pace=pace)


def test_lidl_treats_a_throttle_as_a_rate_verdict_not_a_missing_product():
    pace, _ = paced(retry.PRODUCT_PAGE_BUDGET)
    client = lidl_client(lambda request: httpx.Response(403), pace=pace)
    with pytest.raises(lidl_catalogue.ThrottledError):
        client.listing("https://www.lidl.com/p/thing/p12345")
    assert pace.throttled == 1
    assert pace.interval > retry.PRODUCT_PAGE_BUDGET.min_interval


def test_lidl_starts_on_the_cautious_budget_because_its_limits_are_unmeasured():
    """Assumed strict until measured. Assuming the generous direction is what
    gets an IP blocked, and Lidl was never pushed to a wall the way Sprouts was.
    """
    client = lidl_client(lambda request: httpx.Response(200, text="<html></html>"))
    assert client.pace.budget is retry.PRODUCT_PAGE_BUDGET


def test_a_lidl_scrape_keeps_the_pages_it_got_and_counts_the_ones_it_did_not():
    ok = 0

    def alternating(request: httpx.Request) -> httpx.Response:
        nonlocal ok
        ok += 1
        if ok % 2 == 0:
            return httpx.Response(403)
        return httpx.Response(200, text=_lidl_page())

    pace, _ = paced(retry.PRODUCT_PAGE_BUDGET)
    client = lidl_client(alternating, pace=pace)
    urls = [f"https://www.lidl.com/p/thing-{i}/p1000{i}" for i in range(6)]

    rows, _meta, stats = lidl_catalogue.scrape(
        postal_code="27401", client=client, now=NOW, urls=urls
    )

    assert stats["skipped_throttled"] == 3
    assert len(rows) == 3
    assert stats["products_seen"] == 6
    # The pacer's own counters ride along in the same blob.
    assert stats["product-page_throttled"] == 3


def _lidl_page() -> str:
    graph = {"@context": "https://schema.org", "@type": "Product",
             "sku": "12345", "name": "Chicken Breast",
             "description": "Fresh chicken breast, 16 oz.",
             "offers": {"@type": "Offer", "price": "5.99",
                        "priceCurrency": "USD"}}
    return ('<html><head><script type="application/ld+json">'
            + json.dumps(graph) + "</script></head></html>")


# --------------------------------------------------------------------------- #
# Trader Joe's -- no throttling was ever observed, which is exactly why the
# behaviour has to be pinned: nobody will notice it changed until it costs a run
# --------------------------------------------------------------------------- #
def tj_client(handler, pace=None):
    http = httpx.Client(
        base_url=traderjoes.BASE_URL, transport=httpx.MockTransport(handler)
    )
    return traderjoes.TraderJoesClient(client=http, pace=pace)


def test_traderjoes_raises_on_a_throttle_rather_than_returning_no_products():
    pace, _ = paced(traderjoes.CATALOGUE_BUDGET)
    client = tj_client(lambda request: httpx.Response(429), pace=pace)
    with pytest.raises(traderjoes.ThrottledError):
        client.query("{__typename}")
    assert pace.throttled == 1


def test_traderjoes_paces_on_its_own_measured_floor_not_sprouts():
    """0.5 s, because that is the rate that was proven against THIS host. One
    host's tolerance is not evidence about another's, and inheriting Sprouts'
    0.08 s floor would be a 6x speed-up nobody measured."""
    assert traderjoes.CATALOGUE_BUDGET is not retry.GRAPHQL_BUDGET
    assert (traderjoes.CATALOGUE_BUDGET.min_interval
            > retry.GRAPHQL_BUDGET.min_interval)


def test_a_throttle_at_page_twenty_does_not_discard_the_first_nineteen():
    """The regression retry.py was written for, in its other form.

    A blip at page 40 of 50 used to abort the run and throw away every page
    already fetched -- 'the longer and more valuable the scrape, the more it
    loses'. Pagination must keep what it has, and say that it stopped early.
    """
    page = {"n": 0}

    def throttle_on_the_third_page(request: httpx.Request) -> httpx.Response:
        page["n"] += 1
        if page["n"] >= 3:
            return httpx.Response(429)
        return httpx.Response(200, json={"data": {"products": {
            "total_count": 500,
            "items": [{"sku": f"p{page['n']}-{i}"} for i in range(2)],
            "page_info": {"total_pages": 25},
        }}})

    pace, _ = paced(traderjoes.CATALOGUE_BUDGET)
    client = tj_client(throttle_on_the_third_page, pace=pace)

    items, total = client.catalogue()

    assert len(items) == 4              # two pages' worth, kept
    assert total == 500
    assert client.truncated_by_throttling is True


def test_a_throttle_on_the_very_first_page_is_still_an_error():
    """Nothing was fetched, so there is nothing to salvage -- and returning an
    empty catalogue here would be indistinguishable from 'Trader Joe's has no
    products', which is the failure this whole file is about."""
    pace, _ = paced(traderjoes.CATALOGUE_BUDGET)
    client = tj_client(lambda request: httpx.Response(429), pace=pace)
    with pytest.raises(traderjoes.ThrottledError):
        client.catalogue()


def test_a_truncated_traderjoes_run_is_legible_in_its_stats():
    pace, _ = paced(traderjoes.CATALOGUE_BUDGET)
    client = tj_client(lambda request: httpx.Response(200, json={"data": {}}), pace=pace)
    _rows, _meta, stats = traderjoes.scrape(
        postal_code="27401", conn=_memory_db(), client=client, now=NOW,
        products=[], store_code="0750",
    )
    assert stats["throttled_truncation"] is False
    assert "traderjoes-graphql_throttled" in stats


# --------------------------------------------------------------------------- #
# The fixed-delay sources: PRISM and Wegmans
#
# These pace with a constant sleep rather than an AIMD pacer, which is a
# deliberate difference (neither has ever been throttled) but a REAL one. These
# tests pin what they do and do not do, so the difference is a decision on
# record rather than something discovered during an outage.
# --------------------------------------------------------------------------- #
def test_prism_waits_between_product_pages(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(prism.time, "sleep", slept.append)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "sitemap" in url:
            return httpx.Response(200, text=_sitemap([
                "https://www.foodlion.com/sitemap-products-1.xml"
            ] if url.endswith("sitemap.xml") else [
                f"https://www.foodlion.com/groceries/product/chicken-breast-{i}/{i}"
                for i in range(4)
            ]))
        return httpx.Response(200, text="<html></html>")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    prism.scrape_store(prism.FOOD_LION, max_products=4, client=http)

    # One fewer sleep than fetches: the first request is not delayed.
    assert slept == [prism.REQUEST_DELAY] * 3


def test_wegmans_waits_between_products(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(wegmans_api.time, "sleep", slept.append)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/stores"):
            return httpx.Response(200, json=[{
                "storeNumber": 140, "name": "Chapel Hill", "city": "Chapel Hill",
                "stateAbbreviation": "NC", "zip": "27514", "latitude": 35.9,
                "longitude": -79.0,
            }])
        # Every product 404s: the point here is the spacing between attempts,
        # not what comes back from them.
        return httpx.Response(404)

    monkeypatch.setattr(
        wegmans_api, "_client",
        lambda *a, **k: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    wegmans_api.scrape(postal_code="27514", skus=["1", "2", "3"], conn=_memory_db())

    assert slept == [wegmans_api.REQUEST_DELAY] * 2


def test_the_fixed_delay_sources_do_not_pretend_to_adapt():
    """Stated as a fact rather than left to be discovered.

    PRISM and Wegmans count a 403 as a failed fetch and keep going at the same
    rate. That is tolerable *because* neither has ever returned one -- and if
    either starts to, this test is the thing that says the remedy is to give it
    a Budget, not to lower a constant.
    """
    for module in (prism, wegmans_api):
        source = module.__dict__
        assert "REQUEST_DELAY" in source
        assert not any(
            isinstance(v, retry.Paced) for v in source.values()
        ), f"{module.__name__} grew a pacer -- give it a throttle test above"


def _sitemap(urls: list[str]) -> str:
    return ("<urlset>" + "".join(f"<loc>{u}</loc>" for u in urls) + "</urlset>")


# --------------------------------------------------------------------------- #
# The suite's own pacing bill
# --------------------------------------------------------------------------- #
def test_the_suite_does_not_pay_production_pacing():
    """conftest neutralises ``retry.time.sleep`` for every test.

    Without it the suite inherits 0.5 s between simulated product pages and the
    full run went from ~256 s to over 600 s. This asserts the fixture is still
    in force, because the symptom of it silently lapsing is a slow CI job that
    nobody attributes to pacing.
    """
    import time as _time

    pace = retry.Paced(retry.PRODUCT_PAGE_BUDGET)
    started = _time.monotonic()
    for _ in range(5):
        pace.wait()          # 4 x 0.5 s of real sleeping, without the fixture
    elapsed = _time.monotonic() - started

    # The pacer's own books say it slept at least the floor between each pair...
    assert pace.slept >= 4 * retry.PRODUCT_PAGE_BUDGET.min_interval
    # ...and the wall clock says the suite did not pay for any of it.
    assert elapsed < 0.5
