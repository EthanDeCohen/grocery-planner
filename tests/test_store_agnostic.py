"""Tests for GFP-32: a third store (GFP-4's ``wholefoods``) proved this
product needed to answer a standing question -- does adding a store ever
require touching the matching/pricing engine, or does registering a
scraper module under ``scrapers/`` alone make it flow all the way through?

This file encodes the answer as executable fact using a FOURTH, entirely
synthetic store invented only in this test (never ``wholefoods`` -- the
point is that *any* new store works, not that this one in particular does).
If a future change ever makes ``matching.py`` or ``savings.py`` branch on a
store's identity (an ``if store == "wholefoods":`` or similar), the
identical-input/identical-output tests below stop being identical and fail.

--------------------------------------------------------------------------
The contract a new store scraper module must satisfy
--------------------------------------------------------------------------
Modeled on ``scrapers/foodlion.py``/``scrapers/harristeeter.py`` (Flipp-
sourced, via ``scrapers/base.py::StoreConfig``) and ``scrapers/wholefoods.py``
(a differently-shaped source, plain ``httpx`` against a storefront) -- two
very different implementations that both satisfy the same, small contract:

1. A module-level ``STORE_KEY: str`` constant -- becomes the dict key in
   ``scrapers/__init__.py``'s ``SCRAPERS`` registry, and every ``deals.store``
   / ``deal_food_match.store`` value this store's rows ever carry.
2. A callable
   ``scrape(postal_code=None, conn=None, ...) -> (rows, meta, stats)``,
   where ``rows`` is a list of dicts matching ``importers.DEAL_COLUMNS``
   (see ``service/ingest.py::run_scrape``, which inserts them into ``deals``
   and ``price_history`` with no per-store branching at all).
3. Optionally, a module-level ``DEFAULT_POSTAL_CODE: str`` (used by
   ``run_scrape`` when the caller doesn't specify one).
4. Optionally, a callable ``readiness() -> (bool, str)`` when "registered"
   and "ready to scrape right now" are different questions for this store
   (GFP-4's ``wholefoods`` needs an out-of-band, human-minted session
   cookie; a Flipp-sourced store needs no setup at all and simply omits
   this). See :func:`test_a_registered_but_not_ready_store_is_all_scrapers_
   but_not_available_scrapers` below for exactly what that distinction means
   in practice:

   - ``service/ingest.py::all_scrapers()`` -- every REGISTERED store key,
     ready or not. This is what a store-listing UI (`gplan stores`) should
     use so an unready store is still visible, annotated as needing setup,
     rather than hidden outright.
   - ``service/ingest.py::available_scrapers()`` -- only the READY ones.
     This is what anything that's about to actually *scrape* (a store
     picker, ``gplan schedule set``) should use, so it never offers or
     schedules a store that would just fail on every run.

Then: adding the module to ``scrapers/__init__.py``'s ``SCRAPERS`` dict is
the ONLY change anywhere in the codebase this new store needs. Neither
``matching.py`` nor ``savings.py`` contains a single line that inspects
``store``'s value to decide what to do with it -- ``store`` flows through
both purely as an opaque half of the ``deal_food_match`` lookup key
(``(store, item_name)``, see ``matching.py``'s module docstring).
"""
from __future__ import annotations

import sqlite3
import types

import pytest

from grocery_planner import importers, matching, savings
from grocery_planner.scrapers import SCRAPERS
from grocery_planner.service import ingest

CHICKEN = "16 oz. Boneless Skinless Chicken Breast"
CHICKEN_PRICE = 3.99

# The expected, exact figures for CHICKEN/CHICKEN_PRICE against the GFP-23
# curated catalog (16 oz -> 453.592g; 23g protein/100g -> ~104.326g protein;
# see tests/test_cost_per_gram.py's identical fixture) -- asserted below
# against a REAL store key AND a never-before-seen synthetic one, so a
# hidden branch that (say) special-cases every currently-registered store
# identically would still be caught the moment an unseen store key shows up.
EXPECTED_CONFIDENCE = matching.CONFIDENCE_HIGH
EXPECTED_METHOD = "cut_keyword"
EXPECTED_FOOD_NAME = "Chicken breast, skinless/boneless, raw"
EXPECTED_PROTEIN_SOURCE = "curated"


def _insert_deal(conn, store: str, item_name: str, price: float) -> None:
    conn.execute(
        "INSERT INTO deals(store, item_name, sub_category, deal_type, "
        "dollar_price, source) VALUES (?, ?, ?, 'Weekly Ad', ?, 'scrape')",
        (store, item_name, "Meat & Seafood", price),
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# The core proof: matching + pricing behave identically for a brand-new,
# never-registered store key as for an existing real one.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("store", [
    "foodlion",                              # a real, already-registered store
    "brand-new-grocery-mart-9000",            # invented here, registered nowhere
    "another-hypothetical-store",             # a second invented key, for good measure
])
def test_matching_produces_the_same_result_for_any_store_identity(conn, store):
    _insert_deal(conn, store, CHICKEN, CHICKEN_PRICE)
    matching.match_deals(conn=conn)

    match = matching.get_match(store, CHICKEN, conn=conn)
    assert match is not None
    assert match["confidence"] == EXPECTED_CONFIDENCE
    assert match["method"] == EXPECTED_METHOD


@pytest.mark.parametrize("store", [
    "foodlion",
    "brand-new-grocery-mart-9000",
    "another-hypothetical-store",
])
def test_cost_per_gram_protein_produces_the_same_result_for_any_store_identity(conn, store):
    _insert_deal(conn, store, CHICKEN, CHICKEN_PRICE)
    matching.match_deals(conn=conn)

    result = savings.cost_per_gram_protein(CHICKEN_PRICE, CHICKEN, store, conn=conn)
    assert result is not None
    assert result.cost_per_gram_protein == pytest.approx(CHICKEN_PRICE / 104.326, rel=1e-3)
    assert result.match_confidence == EXPECTED_CONFIDENCE
    assert result.match_method == EXPECTED_METHOD
    assert result.food_name == EXPECTED_FOOD_NAME
    assert result.protein_source == EXPECTED_PROTEIN_SOURCE


def test_identical_deal_under_two_different_store_keys_yields_identical_pricing(conn):
    """Same item, same price, two store identities -- one real, one that has
    never existed anywhere in this codebase before this test. The only
    thing allowed to differ between the two ``ProteinCost`` results is
    nothing at all: ``store`` plays no role in the computation itself."""
    real_store, synthetic_store = "harristeeter", "zzz-totally-invented-store"
    for store in (real_store, synthetic_store):
        _insert_deal(conn, store, CHICKEN, CHICKEN_PRICE)
    matching.match_deals(conn=conn)

    real_result = savings.cost_per_gram_protein(CHICKEN_PRICE, CHICKEN, real_store, conn=conn)
    synthetic_result = savings.cost_per_gram_protein(
        CHICKEN_PRICE, CHICKEN, synthetic_store, conn=conn
    )
    assert real_result is not None and synthetic_result is not None
    # food_id is the same underlying catalog row either way; every other
    # field must match exactly (store is not one of the fields at all).
    assert real_result.food_id == synthetic_result.food_id
    assert real_result == synthetic_result


def test_rank_by_cost_per_gram_protein_treats_every_store_key_alike(conn):
    """rank_by_cost_per_gram_protein (the actual function the CLI/GUI call)
    over a mixed batch spanning a real store and a synthetic one -- both
    rows must be priced and ranked exactly the same way."""
    stores = ["foodlion", "totally-new-co-op"]
    for store in stores:
        _insert_deal(conn, store, CHICKEN, CHICKEN_PRICE)
    matching.match_deals(conn=conn)

    rows = [{"store": s, "item_name": CHICKEN, "sale_price": CHICKEN_PRICE} for s in stores]
    ranked = savings.rank_by_cost_per_gram_protein(rows, conn=conn)
    assert len(ranked) == 2
    costs = {r["cost_per_gram_protein"] for r in ranked}
    assert len(costs) == 1  # both stores land on the exact same figure


# --------------------------------------------------------------------------- #
# The registration contract itself: a fourth store module, built only in
# this test (never scrapers/wholefoods.py), registered the same way any real
# store module is -- through scrapers/__init__.py's SCRAPERS dict.
# --------------------------------------------------------------------------- #
def _synthetic_scraper_module(
    key: str, *, ready: tuple[bool, str] | None = None
) -> types.SimpleNamespace:
    """A minimal object satisfying the store-scraper contract documented in
    this file's module docstring: STORE_KEY + scrape(). ``ready`` is
    optional -- omitting it models a Flipp-sourced store (always ready);
    supplying it models a wholefoods-shaped store (registered != ready)."""
    module = types.SimpleNamespace(
        STORE_KEY=key,
        DEFAULT_POSTAL_CODE="27401",
        scrape=lambda postal_code=None, conn=None, **kw: ([], {}, {}),
    )
    if ready is not None:
        module.readiness = lambda: ready
    return module


def test_a_plain_registered_store_is_both_all_scrapers_and_available_scrapers(monkeypatch):
    key = "synth-store-plain"
    monkeypatch.setitem(SCRAPERS, key, _synthetic_scraper_module(key))

    assert key in ingest.all_scrapers()
    # No readiness() defined at all -- "always ready", same as every
    # Flipp-sourced store today (see ingest.scraper_status's docstring).
    assert key in ingest.available_scrapers()


def test_a_registered_but_not_ready_store_is_all_scrapers_but_not_available_scrapers(monkeypatch):
    """The GFP-4 distinction, proven generically: a store can be registered
    (appears in SCRAPERS / all_scrapers()) the moment its module ships, while
    still needing setup before it's actually scrapable (readiness() ->
    False). available_scrapers() -- what a scrape-time picker should use --
    must exclude it; all_scrapers() -- what a listing should use -- must not."""
    key = "synth-store-needs-setup"
    monkeypatch.setitem(
        SCRAPERS, key, _synthetic_scraper_module(key, ready=(False, "needs a one-time setup step"))
    )

    assert key in ingest.all_scrapers()
    assert key not in ingest.available_scrapers()

    status = ingest.scraper_status(key)
    assert status.ready is False
    assert status.reason == "needs a one-time setup step"


def test_an_unregistered_store_key_is_neither(monkeypatch):
    """Sanity check on the registry itself: a key nobody registered is not
    magically available just because a deal/food row happens to use it as a
    string (matching/pricing are store-agnostic; the SCRAPERS registry is
    not, by design -- it's specifically the list of scrapable stores)."""
    key = "nobody-ever-registered-this-key"
    assert key not in ingest.all_scrapers()
    assert key not in ingest.available_scrapers()
    with pytest.raises(ingest.UnknownStoreError):
        ingest.scraper_status(key)


# --------------------------------------------------------------------------- #
# GFP-121: ingest must MATCH what it writes, for every store
#
# The defect these cover. `matching.match_deals` was called from nowhere in
# production code -- only from tests, which is exactly why it hid. Kroger and
# Whole Foods write `deal_food_match` inline from their own scraper modules, so
# the two stores with bespoke ingest looked healthy; Food Lion, which has no
# bespoke path, had ZERO match rows and its 297 priced deals could never reach
# a $/g protein figure, `gplan cheapest`, the trends chart or a grocery list.
#
# Every test above this point calls match_deals() by hand, so all of them
# passed throughout. The missing assertion was never "does the matcher work"
# -- it does -- but "does anything ever RUN it". These tests do not call it.
# --------------------------------------------------------------------------- #
class _ChickenScraper:
    """One matchable row, so a scrape has something a matcher should catch."""

    DEFAULT_POSTAL_CODE = "27401"

    def scrape(self, postal_code=None, include_coupons=True):
        row = {c: None for c in importers.DEAL_COLUMNS}
        row.update(item_name=CHICKEN, sub_category="Meat & Seafood",
                   deal_type="Weekly Ad", dollar_price=CHICKEN_PRICE,
                   sale_price=CHICKEN_PRICE, valid_from="2026-06-08",
                   valid_to="2026-06-16")
        return [row], {"id": 1}, {"total": 1}


@pytest.mark.parametrize("store", [
    "foodlion",                                # the store that had zero rows
    "brand-new-grocery-mart-9000",             # and any store added later
])
def test_scraping_matches_the_deals_it_writes(conn, monkeypatch, store):
    """THE REGRESSION. Nobody calls match_deals here -- ingest must."""
    monkeypatch.setitem(ingest.SCRAPERS, store, _ChickenScraper())
    ingest.run_scrape(store, postal_code="27401", conn=conn)

    match = matching.get_match(store, CHICKEN, conn=conn)
    assert match is not None, (
        f"{store}: deals were written but never matched, so they are "
        "invisible to $/g protein"
    )
    assert match["confidence"] == EXPECTED_CONFIDENCE


def test_scraping_reports_what_it_matched(conn, monkeypatch):
    """The summary carries the match counts, so a silent zero is visible."""
    monkeypatch.setitem(ingest.SCRAPERS, "foodlion", _ChickenScraper())
    result = ingest.run_scrape("foodlion", postal_code="27401", conn=conn)
    assert result["matches"]["matched"] >= 1


def test_a_scrape_still_succeeds_when_matching_fails(conn, monkeypatch):
    """The prices are the point; unmatched deals recover on the next run."""
    monkeypatch.setitem(ingest.SCRAPERS, "foodlion", _ChickenScraper())

    def boom(conn=None):
        raise sqlite3.OperationalError("matching exploded")

    monkeypatch.setattr(ingest.matching, "match_deals", boom)
    result = ingest.run_scrape("foodlion", postal_code="27401", conn=conn)

    assert result["matches"] is None
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM deals WHERE store='foodlion'"
    ).fetchone()["n"] == 1, "the scrape's rows must survive a matching failure"


def test_importing_csv_matches_the_deals_it_writes(conn, tmp_path):
    """The OTHER path that writes deals had the identical hole."""
    folder = tmp_path / "foodlion"
    folder.mkdir()
    header = ",".join(importers.DEAL_COLUMNS)
    blanks = "," * (len(importers.DEAL_COLUMNS) - 4)
    (folder / "deals.csv").write_text(
        f"{header}\n\"{CHICKEN}\",Meat & Seafood,Weekly Ad,{CHICKEN_PRICE}{blanks}\n",
        encoding="utf-8",
    )
    importers.import_dir(conn, tmp_path)

    assert matching.get_match("foodlion", CHICKEN, conn=conn) is not None
