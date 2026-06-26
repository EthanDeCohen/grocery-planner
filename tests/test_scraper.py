"""Offline scraper logic: price/unit normalization and item->row mapping.

No network — exercises the pure functions only.
"""
from grocery_planner.scrapers import base, foodlion


def test_normalize_price():
    assert base.normalize_price("1.990") == "1.99"
    assert base.normalize_price("2.00") == "2"
    assert base.normalize_price("") == ""
    assert base.normalize_price(None) == ""
    assert base.normalize_price("BOGO") == "BOGO"  # non-numeric passes through


def test_infer_unit():
    assert foodlion.infer_unit("Bananas per lb", "0.59") == "lb"
    assert foodlion.infer_unit("Large Eggs dozen", "2.99") == "dozen"
    assert foodlion.infer_unit("Generic Item", "1.00") == "each"
    assert foodlion.infer_unit("No price item", "") == ""


def test_item_to_row_priced():
    item = {"id": 1, "name": "Boneless Chicken Breast", "brand": "Tyson", "price": "1.99"}
    flyer = {"id": 99, "valid_from": "2026-06-10T00:00:00", "valid_to": "2026-06-16T00:00:00"}
    row = foodlion._item_to_row(item, flyer)
    assert row["item_name"] == "Boneless Chicken Breast"
    assert row["sale_price"] == 1.99
    assert row["deal_type"] == "Weekly Ad"
    assert row["valid_to"] == "2026-06-16"
    assert "flipp_flyer_id=99" in row["notes"]


def test_item_to_row_no_price():
    item = {"id": 2, "name": "Mystery Feature", "brand": "", "price": None}
    flyer = {"id": 99, "valid_from": "2026-06-10T00:00:00", "valid_to": "2026-06-16T00:00:00"}
    row = foodlion._item_to_row(item, flyer)
    assert row["sale_price"] is None
    assert row["deal_type"] == "Weekly Ad (price not listed)"
    assert "price_missing=true" in row["notes"]


def test_pick_weekly_flyer():
    from datetime import datetime
    flyers = [
        {"merchant": "Food Lion", "name": "Weekly Ad",
         "valid_from": "2026-06-10T00:00:00", "valid_to": "2026-06-16T00:00:00", "id": 1},
        {"merchant": "Harris Teeter", "name": "Weekly Ad",
         "valid_from": "2026-06-10T00:00:00", "valid_to": "2026-06-16T00:00:00", "id": 2},
    ]
    picked = base.pick_weekly_flyer(flyers, "Food Lion", datetime(2026, 6, 12))
    assert picked["id"] == 1
    assert base.pick_weekly_flyer(flyers, "Publix", datetime(2026, 6, 12)) is None
