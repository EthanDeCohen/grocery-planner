"""Offline scraper logic: normalization, classification, and item/coupon -> row.

No network — exercises the pure functions in the shared Flipp library only.
"""
from datetime import datetime, timedelta, timezone

import pytest

from grocery_planner.scrapers import base

STORE = base.FOOD_LION


def test_normalize_price():
    assert base.normalize_price("1.990") == "1.99"
    assert base.normalize_price("2.00") == "2"
    assert base.normalize_price("$3.50") == "3.5"
    assert base.normalize_price("") == ""
    assert base.normalize_price(None) == ""
    assert base.normalize_price("BOGO") == "BOGO"  # non-numeric passes through


def test_price_to_float():
    assert base.price_to_float("1.99") == 1.99
    assert base.price_to_float("$2") == 2.0
    assert base.price_to_float("") is None
    assert base.price_to_float(None) is None
    assert base.price_to_float("BOGO") is None


def test_infer_unit():
    assert base.infer_unit("Bananas per lb", "0.59") == "lb"
    assert base.infer_unit("Large Eggs dozen", "2.99") == "dozen"
    assert base.infer_unit("Generic Item", "1.00") == "each"
    assert base.infer_unit("No price item", "") == ""


def test_flyer_item_to_row_priced():
    item = {"id": 1, "name": "Boneless Chicken Breast", "brand": "Tyson", "price": "1.99"}
    flyer = {"id": 99, "valid_from": "2026-06-10T00:00:00", "valid_to": "2026-06-16T00:00:00"}
    row = base.flyer_item_to_row(item, flyer, STORE)
    assert row["item_name"] == "Boneless Chicken Breast"
    assert row["sale_price"] == 1.99
    assert row["dollar_price"] == 1.99
    assert row["deal_type"] == "Weekly Ad"
    assert row["valid_to"] == "2026-06-16"
    assert "flipp_flyer_id=99" in row["notes"]
    assert "loyalty=MVP" in row["notes"]


def test_flyer_item_to_row_no_price():
    item = {"id": 2, "name": "Mystery Feature", "brand": "", "price": None}
    flyer = {"id": 99, "valid_from": "2026-06-10T00:00:00", "valid_to": "2026-06-16T00:00:00"}
    row = base.flyer_item_to_row(item, flyer, STORE)
    assert row["sale_price"] is None
    assert row["deal_type"] == "Weekly Ad (price not listed)"
    assert "price_missing=true" in row["notes"]


def test_flyer_item_dollar_price_from_text():
    # No structured price, but the ad text names a dollar amount.
    item = {"id": 3, "name": "Cereal", "brand": "Kellogg's", "price": None}
    flyer = {"id": 99, "valid_from": "2026-06-10T00:00:00", "valid_to": "2026-06-16T00:00:00",
             "name": "Weekly"}
    row = base.flyer_item_to_row(item, flyer, STORE)
    # Description is "Kellogg's" (brand only) -> no dollar; dollar_price stays None.
    assert row["dollar_price"] is None


def test_pick_weekly_flyer():
    flyers = [
        {"merchant": "Food Lion", "name": "Weekly Ad",
         "valid_from": "2026-06-10T00:00:00", "valid_to": "2026-06-16T00:00:00", "id": 1},
        {"merchant": "Harris Teeter", "name": "Weekly Ad",
         "valid_from": "2026-06-10T00:00:00", "valid_to": "2026-06-16T00:00:00", "id": 2},
    ]
    picked = base.pick_weekly_flyer(flyers, "Food Lion", datetime(2026, 6, 12))
    assert picked["id"] == 1
    assert base.pick_weekly_flyer(flyers, "Publix", datetime(2026, 6, 12)) is None


# --------------------------------------------------------------------------- #
# GFP-16 — deal freshness
# --------------------------------------------------------------------------- #
def _flyer(id_, valid_from, valid_to, merchant="Food Lion", name="Weekly Ad"):
    return {"id": id_, "merchant": merchant, "name": name,
            "valid_from": valid_from, "valid_to": valid_to}


LAST_WEEK = _flyer(1, "2026-06-03T00:00:00", "2026-06-09T23:59:59")
THIS_WEEK = _flyer(2, "2026-06-10T00:00:00", "2026-06-16T23:59:59")
NEXT_WEEK = _flyer(3, "2026-06-17T00:00:00", "2026-06-23T23:59:59")
NOW = datetime(2026, 6, 12)


def test_pick_weekly_flyer_prefers_the_active_ad_over_an_older_one():
    picked = base.pick_weekly_flyer([LAST_WEEK, THIS_WEEK, NEXT_WEEK], "Food Lion", NOW)
    assert picked["id"] == THIS_WEEK["id"]


def test_pick_weekly_flyer_never_returns_an_expired_flyer():
    # The GFP-16 bug: with no active ad, the old code served last week's.
    assert base.pick_weekly_flyer([LAST_WEEK], "Food Lion", NOW) is None


def test_pick_weekly_flyer_falls_forward_to_the_next_ad():
    picked = base.pick_weekly_flyer([LAST_WEEK, NEXT_WEEK], "Food Lion", NOW)
    assert picked["id"] == NEXT_WEEK["id"]


def test_flyer_status():
    assert base.flyer_status(THIS_WEEK, NOW) == "active"
    assert base.flyer_status(LAST_WEEK, NOW) == "expired"
    assert base.flyer_status(NEXT_WEEK, NOW) == "upcoming"
    assert base.flyer_status(_flyer(4, None, None), NOW) == "unknown"


def test_is_expired():
    assert base.is_expired("2026-06-09T23:59:59", NOW)
    assert not base.is_expired("2026-06-16T23:59:59", NOW)
    # An absent or unparseable date is unknown, not expired — never drop those.
    assert not base.is_expired(None, NOW)
    assert not base.is_expired("not a date", NOW)


def test_date_comparison_survives_naive_vs_aware_mismatch():
    """Flipp sends offset-aware stamps while ``now`` may be naive (or vice versa)."""
    aware_now = datetime(2026, 6, 12, tzinfo=timezone(timedelta(hours=-4)))
    assert base.is_active("2026-06-10T00:00:00", "2026-06-16T23:59:59", aware_now)
    assert base.is_active("2026-06-10T00:00:00-04:00", "2026-06-16T23:59:59-04:00", NOW)
    assert base.is_expired("2026-06-09T23:59:59-04:00", NOW)


class _FakeFlippClient:
    """Stands in for the HTTP client so scrape_store can be exercised offline."""

    payload: dict = {}
    items: list = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def fetch_data(self, postal_code):
        return self.payload

    def fetch_flyer_items(self, flyer_id):
        return self.items


def _fake_flipp(monkeypatch, payload, items):
    _FakeFlippClient.payload, _FakeFlippClient.items = payload, items
    monkeypatch.setattr(base, "FlippClient", _FakeFlippClient)


def test_scrape_store_drops_items_that_lapsed_before_the_flyer(monkeypatch):
    _fake_flipp(
        monkeypatch,
        {"flyers": [THIS_WEEK], "coupons": []},
        [
            {"id": 1, "name": "Fresh Chicken", "price": "1.99"},
            {"id": 2, "name": "Stale Special", "price": "0.99",
             "valid_to": "2026-06-11T23:59:59"},  # its own end date already passed
        ],
    )
    rows, flyer, stats = base.scrape_store(STORE, include_coupons=False, now=NOW)

    assert [r["item_name"] for r in rows] == ["Fresh Chicken"]
    assert stats["expired_items"] == 1
    assert stats["flyer_status"] == "active"
    assert flyer["id"] == THIS_WEEK["id"]


def test_scrape_store_refuses_to_store_a_stale_flyer(monkeypatch):
    _fake_flipp(monkeypatch, {"flyers": [LAST_WEEK], "coupons": []}, [])
    with pytest.raises(RuntimeError, match="has expired"):
        base.scrape_store(STORE, now=NOW)


def test_no_flyer_message_distinguishes_expired_from_missing():
    expired = base._no_flyer_message([LAST_WEEK], STORE, "27401", NOW)
    assert "expired" in expired and "Refusing to store stale deals" in expired

    missing = base._no_flyer_message([], STORE, "27401", NOW)
    assert "none matched" in missing


def test_store_registry():
    from grocery_planner.scrapers import SCRAPERS

    # GFP-4: Whole Foods joined the registry alongside the two Flipp-sourced
    # stores. Updated here (not just added to) because this is an exact-
    # equality assertion that necessarily changes the moment a new store is
    # registered -- flagged in the GFP-4 PR description per the ticket's
    # "don't touch other test files unless you truly must" instruction.
    # GFP-98: 'harristeeter-api' is the Kroger shelf-price API for the SAME
    # physical store as 'harristeeter' (the Flipp weekly ad). Two entries, one
    # shop, on purpose -- see scrapers/__init__.py.
    assert set(SCRAPERS) == {
        "foodlion", "harristeeter", "harristeeter-api", "wholefoods",
    }
    # Every registered module exposes the thin store-scraper surface the CLI uses.
    for key, mod in SCRAPERS.items():
        assert mod.MERCHANT and mod.DEFAULT_POSTAL_CODE
        assert callable(mod.scrape)
        # The registry key is the module's SCRAPER_KEY when it declares one,
        # otherwise its STORE_KEY. Only a second-source module needs the two to
        # differ, so the assertion is about the registry being self-consistent
        # rather than about the names being equal.
        assert getattr(mod, "SCRAPER_KEY", mod.STORE_KEY) == key


def test_a_second_source_for_one_store_does_not_evict_the_first():
    """GFP-98: 'harristeeter' and 'harristeeter-api' share a store but not a source.

    If these ever collapse to the same (STORE_KEY, SOURCE) pair, run_scrape's
    DELETE — scoped to (store, source, postal_code) — would make each scrape
    wipe the other's rows, silently halving the data with no error anywhere.
    """
    from grocery_planner import scrapers as pkg
    from grocery_planner.scrapers import SCRAPERS

    flipp, api = SCRAPERS["harristeeter"], SCRAPERS["harristeeter-api"]
    assert pkg.store_key_for(flipp) == pkg.store_key_for(api) == "harristeeter"
    assert pkg.source_for(flipp) != pkg.source_for(api)
    assert pkg.source_for(flipp) == "scrape"
    assert pkg.source_for(api) == "kroger-api"


def test_store_key_for_falls_back_to_the_registry_key():
    # A stand-in without STORE_KEY (a test double, or a scraper that simply
    # doesn't need the distinction) must not have to know this convention.
    from grocery_planner import scrapers as pkg

    assert pkg.store_key_for(object(), "foodlion") == "foodlion"
    assert pkg.source_for(object()) == "scrape"


def test_harris_teeter_config():
    assert base.HARRIS_TEETER.key == "harristeeter"
    assert base.HARRIS_TEETER.merchant_name == "Harris Teeter"
    assert base.HARRIS_TEETER.loyalty_name == "VIC"


def test_infer_coupon_deal_type():
    assert base.infer_coupon_deal_type("Buy One Get One Free", "") == "Bogo"
    assert base.infer_coupon_deal_type("BOGO on chips", "") == "Bogo"
    assert base.infer_coupon_deal_type("Save $2 on cheese", "amountoff") == "Digital Coupon"
    assert base.infer_coupon_deal_type("20% off produce", "percentoff") == "Percent Off Coupon"


def test_is_grocery_coupon():
    assert base.is_grocery_coupon({"categories": ["Grocery"]})
    assert base.is_grocery_coupon({"sale_story": "Save on chicken"})
    assert not base.is_grocery_coupon({"categories": ["Electronics"], "sale_story": "Save on TVs"})


def test_coupon_to_row_amount_off():
    now = datetime(2026, 6, 12)
    coupon = {
        "coupon_id": "c1", "brand": "Oscar Mayer", "coupon_type": "amountoff",
        "sale_story": "Save $2.00", "promotion_text": "Save $2.00 on bacon",
        "dollars_off": "2.00", "valid_from": "2026-06-10T00:00:00",
        "valid_to": "2026-06-16T00:00:00",
    }
    assert base.is_active(coupon["valid_from"], coupon["valid_to"], now)
    row = base.coupon_to_row(coupon, STORE)
    assert row["item_name"] == "Oscar Mayer"
    assert row["sub_category"] == "Digital Coupon"
    assert row["deal_type"] == "Digital Coupon"
    assert row["discount_amount"] == 2.0
    assert row["dollar_price"] == 2.0
    assert row["sale_price"] is None
    assert "source=digital_coupon" in row["notes"]


def test_coupon_to_row_percent_off():
    coupon = {
        "coupon_id": "c2", "brand": "", "coupon_type": "percentoff",
        "sale_story": "20% off", "promotion_text": "20% off produce",
        "percent_off": "0.2",
    }
    row = base.coupon_to_row(coupon, STORE)
    assert row["deal_type"] == "Percent Off Coupon"
    assert row["discount_percent"] == 20.0
    assert row["item_name"] == "20% off produce"  # falls back to promotion_text
