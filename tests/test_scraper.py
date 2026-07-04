"""Offline scraper logic: normalization, classification, and item/coupon -> row.

No network — exercises the pure functions in the shared Flipp library only.
"""
from datetime import datetime

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
