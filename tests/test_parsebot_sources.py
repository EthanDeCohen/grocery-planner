"""Walmart and Publix through Parse.bot, and the double dagger (GFP-270).

No network: the vendor client is driven through ``httpx.MockTransport`` with
payloads copied from the real 2026-08-12 responses, so the field shapes here
are the ones the vendor actually sends rather than the ones we hoped for.

THE TEST THAT MATTERS MOST is
``test_a_per_pound_price_is_never_stored_against_a_package_weight``. Everything
else in this file is bookkeeping next to it. Publix quotes fresh meat as a RATE
("$2.39/lb") and qualifies the package in the name ("4 Lbs. or More"); because
``savings.parse_size`` takes the FIRST size it finds, an earlier version of this
code stored $2.39 against **4 lb** and priced chicken thighs at $0.0066 per gram
of protein -- the cheapest figure in the entire database, and wrong by 4x.

A cheapest-first ranking is unforgiving about that class of error: an
understatement never sits harmlessly mid-list, it goes to the top, which is the
only part the optimiser reads.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from grocery_planner import savings, weight_basis
from grocery_planner.scrapers import SCRAPERS, parsebot, walmart

NOW = datetime(2026, 8, 12, tzinfo=timezone.utc)
KEY = "pmx_test_key_not_a_real_one"


# --------------------------------------------------------------------------- #
# Payloads, copied from the live responses
# --------------------------------------------------------------------------- #
WALMART_PRODUCTS = [
    {   # a real package price with a rate alongside it, on a range-weight tray
        "name": "Freshness Guaranteed Boneless, Skinless Chicken Breasts, 2.75 - 7.0 lb Tray",
        "brand": None, "package_size": "2.75 - 7.0 lb", "price": 10.35,
        "price_per_unit": "$2.23/lb", "item_id": "27935840",
        "url": "https://www.walmart.com/ip/x/27935840",
        "protein_per_serving": None, "serving_size": None,
        "servings_per_container": None,
    },
    {   # a fixed package, fixed price -- no denomination question at all
        "name": "Tyson Frozen Crispy Chicken Breast Strips, 25 oz",
        "brand": None, "package_size": "25 oz", "price": 7.83,
        "price_per_unit": "$5.02/lb", "item_id": "14149967",
        "url": "https://www.walmart.com/ip/x/14149967",
    },
    {   # nothing but a rate: no package total anywhere
        "name": "Fresh Ground Beef 80/20",
        "brand": None, "package_size": None, "price": None,
        "price_per_unit": "$4.48/lb", "item_id": "10450114",
        "url": "https://www.walmart.com/ip/x/10450114",
    },
]

# PUBLIX_PRODUCTS lived here until GFP-304 deleted the Parse.bot Publix
# scraper. The Walmart payload below already exercises every shape it did,
# including the rate-only row with no package price.


def client_for(products, key="products", extra=None):
    """A ParseBotClient whose every endpoint answers with ``products``."""
    def handle(request: httpx.Request) -> httpx.Response:
        body = {key: products}
        if extra:
            body.update(extra)
        return httpx.Response(200, json={"status": "success", "data": body})

    http = httpx.Client(transport=httpx.MockTransport(handle))
    return parsebot.ParseBotClient(client=http, key=KEY,
                                   pace=_instant_pacer())


def _instant_pacer():
    from grocery_planner.scrapers import retry as _retry
    return _retry.Paced(parsebot.PARSEBOT_BUDGET, sleep=lambda _s: None,
                        clock=lambda: 0.0)


# --------------------------------------------------------------------------- #
# THE ONE THAT MATTERS: a rate is never stored against a package weight
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", [
    "Publix Chicken Thighs 4 Lbs. or More, USDA Grade A",
    "Publix Chicken Thighs Less Than 4 Lbs., USDA Grade A",
    "Publix Boneless Skinless Chicken Breast, 97% Fat Free4 Lbs. or More Pkg",
    "Freshness Guaranteed Chicken Breasts, 2.75 - 7.0 lb Tray",
    "Ground Beef 80/20",
])
def test_a_per_pound_price_is_never_stored_against_a_package_weight(name):
    """The 4x bug. The priced quantity of a rate row is ONE POUND, whatever
    weight the name happens to mention -- and ``parse_size`` takes the first
    size it sees, so the qualifier has to go, not merely be out-ranked."""
    parsed = savings.parse_size(walmart.priced_pound_name(name))
    assert parsed is not None
    assert parsed.quantity == pytest.approx(1.0)
    assert parsed.unit.lower().startswith("lb")


def test_the_understated_figure_is_actually_corrected(conn):
    """End to end, in money: a $4.48/lb rate must not price a whole package.

    This was driven through Publix, whose feed quoted every price as a rate
    string. GFP-304 deleted that module; Walmart's rate-only row -- "Fresh
    Ground Beef 80/20", no package price anywhere -- exercises the same grammar,
    which lives in walmart.py and always did.
    """
    rows, _meta, _stats = walmart.scrape(
        postal_code="27401", client=client_for(WALMART_PRODUCTS), now=NOW,
        queries=["ground beef"],
    )
    beef = next(r for r in rows if "Ground Beef" in r["item_name"])
    size = savings.parse_size(beef["item_name"])
    assert beef["dollar_price"] == pytest.approx(4.48)
    assert size.quantity == pytest.approx(1.0)
    # The bug's signature: price divided by a package weight instead of 1 lb.
    assert beef["dollar_price"] / size.quantity == pytest.approx(4.48), (
        "a rate was read as a package price"
    )


# --------------------------------------------------------------------------- #
# The marker
# --------------------------------------------------------------------------- #
def test_rate_rows_carry_the_double_dagger():
    rows, _meta, _stats = walmart.scrape(
        postal_code="27401", client=client_for(WALMART_PRODUCTS), now=NOW,
        queries=["ground beef"],
    )
    rated = [r for r in rows if r["weight_basis"] == weight_basis.RATE]
    assert rated, "expected the rate-only row to survive"
    for row in rated:
        basis = weight_basis.basis_for(row["sold_by"], row["weight_basis"])
        assert weight_basis.marker(basis) == "‡"


def test_the_double_dagger_is_a_distinct_statement_not_a_louder_one():
    """It must not collide with, or be mistaken for, the three GFP-152 states.

    † says "this price may move a little". ‡ says "this is not a price yet".
    Collapsing them would lose the only distinction that matters at a till.
    """
    markers = weight_basis.MARKERS
    assert markers[weight_basis.RATE] == "‡"
    assert markers[weight_basis.RATE] != markers[weight_basis.UNKNOWN]
    assert len(set(markers.values())) == len(markers)


def test_the_legend_explains_the_dagger_whenever_a_rate_row_is_present():
    entries = weight_basis.footnotes_for([weight_basis.RATE, None, None])
    assert entries, "a ‡ on screen with no legend is an unexplained symbol"
    mark, note = entries[0]
    assert mark == "‡"
    assert "per pound" in note.lower()


def test_a_row_that_is_not_by_weight_gets_no_marker_at_all():
    """Marking everything would make the marker meaningless -- GFP-152's rule."""
    rows, _meta, _stats = walmart.scrape(
        postal_code="27401", client=client_for(WALMART_PRODUCTS), now=NOW,
        queries=["chicken"],
    )
    strips = next(r for r in rows if "Crispy" in r["item_name"])
    assert strips["sold_by"] == "UNIT"
    assert weight_basis.marker(
        weight_basis.basis_for(strips["sold_by"], strips["weight_basis"])) == ""


# --------------------------------------------------------------------------- #
# Walmart: a package price is a package price, and is NOT a rate
# --------------------------------------------------------------------------- #
def test_walmart_keeps_its_real_package_price():
    """12.41 at $4.58/lb on a 1.5-4.3 lb tray is 2.71 lb -- a genuine basket
    figure. Rewriting it to a per-pound rate would invent a number."""
    rows, _meta, _stats = walmart.scrape(
        postal_code="27401", client=client_for(WALMART_PRODUCTS), now=NOW,
        queries=["chicken"],
    )
    tray = next(r for r in rows if "Freshness Guaranteed" in r["item_name"])
    assert tray["dollar_price"] == pytest.approx(10.35)
    assert tray["weight_basis"] == weight_basis.PREPACKAGED
    # The GLYPH is looked up, not spelled: GFP-270 changed prepackaged from
    # "**" to "§" to remove a prefix collision, and a test that hardcodes the
    # spelling fails on a change that is not a behaviour change.
    assert weight_basis.marker(
        weight_basis.basis_for(tray["sold_by"], tray["weight_basis"])
    ) == weight_basis.MARKERS[weight_basis.PREPACKAGED]


def test_walmart_falls_back_to_the_rate_when_there_is_no_package_price():
    rows, _meta, _stats = walmart.scrape(
        postal_code="27401", client=client_for(WALMART_PRODUCTS), now=NOW,
        queries=["beef"],
    )
    beef = next(r for r in rows if "Ground Beef" in r["item_name"])
    assert beef["weight_basis"] == weight_basis.RATE
    assert beef["dollar_price"] == pytest.approx(4.48)
    assert savings.parse_size(beef["item_name"]).quantity == pytest.approx(1.0)


@pytest.mark.parametrize("text,expected", [
    ("$4.58/lb", 4.58),
    ("$2.23 / lb", 2.23),
    ("$0.50/oz", 8.0),          # normalised to a pound, or the cheap unit wins
    ("$7.99", None),
    ("", None),
    (None, None),
])
def test_rate_grammar(text, expected):
    got = walmart.is_rate(text)
    if expected is None:
        assert got is None
    else:
        assert got == pytest.approx(expected)


# --------------------------------------------------------------------------- #
# Registry and vendor plumbing
# --------------------------------------------------------------------------- #
def test_the_publix_storefront_does_not_shadow_the_publix_flipp_banner():
    """The collision that cost a live debugging session, still guarded.

    `SCRAPERS.update(flipp_banners.MODULES)` is last-write-wins, so a second
    feed reusing the banner's key is silently unreachable. This was asserted
    about `publix-catalog` until GFP-304 deleted it; the hazard now belongs to
    the Instacart storefront that replaced it.
    """
    from grocery_planner.scrapers import publix_storefront as ps

    assert ps.SCRAPER_KEY == "publix-storefront"
    assert SCRAPERS["publix-storefront"] is ps
    assert SCRAPERS["publix"] is not ps
    assert ps.STORE_KEY == SCRAPERS["publix"].STORE_KEY == "publix"
    assert ps.SOURCE != getattr(SCRAPERS["publix"], "SOURCE", "scrape")


def test_the_vendor_is_named_when_the_key_is_missing(monkeypatch):
    """The reason must name Parse.bot, not merely say "not ready"."""
    monkeypatch.delenv(parsebot.ENV_VAR, raising=False)
    monkeypatch.setattr(parsebot, "api_key", lambda: None)
    for module in (walmart,):
        ready, reason = module.readiness()
        assert ready is False
        assert "Parse.bot" in reason
        assert "Walmart" in reason


def test_a_failed_query_does_not_discard_the_rest_of_the_run():
    """Nothing is written until scrape() returns, so a raise costs everything
    already fetched -- the same asymmetry retry.py was written for."""
    # A 404, not a 500: a 5xx is RETRYABLE, so retry.request would quietly
    # succeed on the second attempt and this test would prove nothing. The
    # failure has to be one the client gives up on.
    def flaky(request: httpx.Request) -> httpx.Response:
        if "thighs" in (request.url.params.get("query") or ""):
            return httpx.Response(404, json={"detail": "not found"})
        return httpx.Response(200, json={"status": "success",
                                         "data": {"products": WALMART_PRODUCTS}})

    http = httpx.Client(transport=httpx.MockTransport(flaky))
    client = parsebot.ParseBotClient(client=http, key=KEY, pace=_instant_pacer())
    rows, _meta, stats = walmart.scrape(
        postal_code="27401", client=client, now=NOW,
        queries=["chicken thighs", "ground beef"],
    )
    assert rows, "the second query's rows were thrown away with the first's failure"
    assert stats["queries_failed"] == 1
    assert stats["queries"] == 2


def test_the_keyword_bound_is_reported_not_hidden():
    """`stats` must say this was a keyword sample. "Walmart has 40 products" is
    otherwise read as an assortment fact rather than a coverage one."""
    _rows, _meta, stats = walmart.scrape(
        postal_code="27401", client=client_for(WALMART_PRODUCTS), now=NOW,
        queries=["a", "b", "c"], limit=2,
    )
    assert stats["queries"] == 2
    assert "priced_by_rate" in stats
    assert "parsebot_throttled" in stats


def test_the_vendor_envelope_is_unwrapped_in_one_place():
    """Parse.bot wraps every success as {"status","data"}. If that changes, one
    function changes -- not every scraper."""
    def handle(request):
        return httpx.Response(200, json={"status": "success", "data": {"products": []}})
    http = httpx.Client(transport=httpx.MockTransport(handle))
    client = parsebot.ParseBotClient(client=http, key=KEY, pace=_instant_pacer())
    assert client.call(walmart.ENDPOINTS["grocery"], query="x", zip_code="27401") == {
        "products": []
    }


def test_a_missing_pinned_scraper_id_says_so_rather_than_404ing_silently():
    def gone(request):
        return httpx.Response(404, json={"detail": "not found"})
    http = httpx.Client(transport=httpx.MockTransport(gone))
    client = parsebot.ParseBotClient(client=http, key=KEY, pace=_instant_pacer())
    with pytest.raises(parsebot.ParseBotError) as exc:
        client.call(walmart.ENDPOINTS["grocery"], query="x", zip_code="27401")
    assert "revised" in str(exc.value) or "deleted" in str(exc.value)


def test_the_store_does_not_claim_to_know_where_it_operates():
    """A hand-written footprint is how Food Lion came to claim all of Kentucky.
    UNKNOWN is permissive, so this removes no coverage."""
    assert walmart.serves("27401") is None
    assert walmart.serves("27401") is None


# --------------------------------------------------------------------------- #
# Credits: a budget, not a rate (GFP-270, hit live 2026-08-12)
# --------------------------------------------------------------------------- #
def test_exhausted_credits_are_their_own_error_not_a_throttle():
    """402 is not 429. A throttle is a rate you wait out in seconds and the
    pacer handles it; credits are a monthly budget, and waiting is the wrong
    response. Conflating them would have the pacer sleep through a billing
    problem."""
    def broke(request):
        return httpx.Response(402, json={"error": {
            "error": "Usage limit exceeded",
            "message": "You've used all your credits this month.",
            "next_tier": {"display_name": "Hobby", "credits": 1000, "price": 30.0},
        }})
    http = httpx.Client(transport=httpx.MockTransport(broke))
    client = parsebot.ParseBotClient(client=http, key=KEY, pace=_instant_pacer())
    with pytest.raises(parsebot.OutOfCreditsError) as exc:
        client.call(walmart.ENDPOINTS["grocery"], query="x", zip_code="27401")
    assert not isinstance(exc.value, parsebot.ThrottledError)
    message = str(exc.value)
    assert "credits" in message.lower()
    assert "Hobby" in message and "30.0" in message      # the actual remedy
    assert "Walmart" in message               # the actual blast radius


def test_a_run_that_runs_out_of_credits_keeps_what_it_already_fetched():
    """Nothing is written until scrape() returns, so raising on the 402 would
    discard a run that is merely short. Stop asking, keep the rows, say so."""
    calls = {"n": 0}

    def dies_after_one(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={"status": "success",
                                             "data": {"products": WALMART_PRODUCTS}})
        return httpx.Response(402, json={"error": {"message": "out of credits"}})

    http = httpx.Client(transport=httpx.MockTransport(dies_after_one))
    client = parsebot.ParseBotClient(client=http, key=KEY, pace=_instant_pacer())
    rows, _meta, stats = walmart.scrape(
        postal_code="27401", client=client, now=NOW,
        queries=["chicken thighs", "ground beef", "eggs", "cheese"],
    )
    assert rows, "the first query's rows were discarded with the billing failure"
    assert stats["credits_exhausted"], "a run cut short by billing must say so"
    # It stopped asking rather than burning the remaining queries.
    assert calls["n"] == 2


def test_every_run_reports_what_it_cost_and_what_is_left():
    """The free tier hit zero mid-session with no warning. A budget you only
    see at the moment it runs out is not a budget."""
    def ok(request):
        return httpx.Response(200, json={"status": "success",
                                         "data": {"products": WALMART_PRODUCTS}},
                              headers={"x-credits-remaining": "812",
                                       "x-ratelimit-daily-remaining": "61"})
    http = httpx.Client(transport=httpx.MockTransport(ok))
    client = parsebot.ParseBotClient(client=http, key=KEY, pace=_instant_pacer())
    _rows, _meta, stats = walmart.scrape(
        postal_code="27401", client=client, now=NOW, queries=["a", "b", "c"])
    assert stats["parsebot_calls"] == 3
    assert stats["parsebot_credits_remaining"] == "812"
    # Credits and the DAILY request cap run out independently -- reporting one
    # would explain only half the failures.
    assert stats["parsebot_ratelimit_daily_remaining"] == "61"
