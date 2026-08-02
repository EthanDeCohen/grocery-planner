"""GFP-36: cheapest $/g protein per store per day, from ``price_history``.

No Qt here on purpose — the trend is front-end-agnostic core, so the numbers
behind the chart are testable without an event loop, same as every other
``service`` submodule.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from grocery_planner import service
from grocery_planner.service import trends

PROTEIN_PER_100G = 25.0
# A 16 oz package at 25 g protein/100 g == 453.59 g == ~113.4 g of protein.
GRAMS_IN_16OZ = 16 * 28.349523125 * PROTEIN_PER_100G / 100.0


@pytest.fixture
def priced(conn):
    """A food with a known protein figure, matched to one item per store."""
    # No fixed id: db.connect() seeds curated foods (GFP-50), so the table is
    # not empty and hard-coding one would collide.
    cur = conn.execute(
        "INSERT INTO foods(name, slug, category, source) "
        "VALUES ('Test chicken', 'test-chicken-gfp36', 'chicken', 'usda')"
    )
    food_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO food_nutrients(food_id, nutrient, amount_per_100g) "
        "VALUES (?, 'protein', ?)", (food_id, PROTEIN_PER_100G)
    )
    conn.commit()

    def match(store: str, item_name: str) -> None:
        conn.execute(
            "INSERT INTO deal_food_match(store, item_name, food_id, confidence, method) "
            "VALUES (?, ?, ?, 0.9, 'test')", (store, item_name, food_id)
        )
        conn.commit()

    return match


def _observe(conn, store, item_name, price, day, postal_code="27401"):
    conn.execute(
        "INSERT INTO price_history"
        "(store, postal_code, item_name, deal_type, dollar_price, source, captured_at) "
        "VALUES (?, ?, ?, 'Weekly Ad', ?, 'test', ?)",
        (store, postal_code, item_name, price, day),
    )
    conn.commit()


def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


# --------------------------------------------------------------------------- #
# The series itself
# --------------------------------------------------------------------------- #
def test_empty_database_is_not_plottable_and_says_why(conn):
    trend = service.protein_price_trend(conn=conn)
    assert trend.series == []
    assert not trend.is_plottable
    assert trend.observed_days == 0
    assert "No protein prices on record yet" in trend.reason


def test_one_day_of_history_is_not_a_trend_and_says_so_differently(conn, priced):
    """The ticket's acceptance criterion: degrade *honestly*, not identically.

    "Nothing has been scraped" and "come back tomorrow" are different
    situations and must not produce the same message.
    """
    priced("foodlion", "Chicken Breast 16 oz")
    _observe(conn, "foodlion", "Chicken Breast 16 oz", 5.00, _days_ago(0))

    trend = service.protein_price_trend(conn=conn)
    assert trend.observed_days == 1
    assert not trend.is_plottable
    assert "Only 1 day" in trend.reason
    assert "No protein prices on record yet" not in trend.reason
    # ...but the price we DO know is still available to show.
    assert trend.series[0].latest.cost_per_gram_protein == pytest.approx(
        5.00 / GRAMS_IN_16OZ, rel=1e-6
    )


def test_two_days_make_a_plottable_series(conn, priced):
    priced("foodlion", "Chicken Breast 16 oz")
    _observe(conn, "foodlion", "Chicken Breast 16 oz", 6.00, _days_ago(1))
    _observe(conn, "foodlion", "Chicken Breast 16 oz", 5.00, _days_ago(0))

    trend = service.protein_price_trend(conn=conn)
    assert trend.is_plottable
    assert trend.reason == ""
    assert [p.day for p in trend.series[0].points] == [_days_ago(1), _days_ago(0)]


def test_the_cheapest_item_wins_the_day_not_the_last_one_seen(conn, priced):
    """A nutritionist buys the best option, so the minimum is the honest number."""
    priced("foodlion", "Chicken Breast 16 oz")
    priced("foodlion", "Chicken Thighs 16 oz")
    _observe(conn, "foodlion", "Chicken Breast 16 oz", 9.00, _days_ago(0))
    _observe(conn, "foodlion", "Chicken Thighs 16 oz", 4.00, _days_ago(0))

    point = service.protein_price_trend(conn=conn).series[0].latest
    assert point.item_name == "Chicken Thighs 16 oz"
    assert point.price == 4.00


def test_an_item_with_no_protein_figure_contributes_nothing_not_zero(conn, priced):
    """Rule 1 of savings.py: absent stays absent. A zero would win every day."""
    priced("foodlion", "Chicken Breast 16 oz")
    _observe(conn, "foodlion", "Chicken Breast 16 oz", 5.00, _days_ago(0))
    _observe(conn, "foodlion", "Mystery Flyer Item", 0.50, _days_ago(0))  # unmatched

    point = service.protein_price_trend(conn=conn).series[0].latest
    assert point.item_name == "Chicken Breast 16 oz"


def test_a_zero_or_negative_price_is_not_an_observation(conn, priced):
    priced("foodlion", "Chicken Breast 16 oz")
    _observe(conn, "foodlion", "Chicken Breast 16 oz", 0.0, _days_ago(0))
    assert service.protein_price_trend(conn=conn).observed_days == 0


def test_history_outside_the_window_is_excluded(conn, priced):
    priced("foodlion", "Chicken Breast 16 oz")
    _observe(conn, "foodlion", "Chicken Breast 16 oz", 5.00, _days_ago(200))
    _observe(conn, "foodlion", "Chicken Breast 16 oz", 4.00, _days_ago(1))
    _observe(conn, "foodlion", "Chicken Breast 16 oz", 3.00, _days_ago(0))

    trend = service.protein_price_trend(days=90, conn=conn)
    assert trend.observed_days == 2
    assert service.protein_price_trend(days=365, conn=conn).observed_days == 3


# --------------------------------------------------------------------------- #
# Multiple stores
# --------------------------------------------------------------------------- #
def test_stores_are_separate_series_led_by_the_cheapest(conn, priced):
    for store in ("foodlion", "wholefoods"):
        priced(store, "Chicken Breast 16 oz")
        for offset, price in ((1, 8.00), (0, 7.00 if store == "foodlion" else 3.00)):
            _observe(conn, store, "Chicken Breast 16 oz", price, _days_ago(offset))

    trend = service.protein_price_trend(conn=conn)
    assert [s.store for s in trend.series] == ["wholefoods", "foodlion"]
    assert len(trend.plottable) == 2


def test_one_store_can_be_plottable_while_another_is_not(conn, priced):
    priced("foodlion", "Chicken Breast 16 oz")
    priced("wholefoods", "Chicken Breast 16 oz")
    _observe(conn, "foodlion", "Chicken Breast 16 oz", 8.00, _days_ago(1))
    _observe(conn, "foodlion", "Chicken Breast 16 oz", 7.00, _days_ago(0))
    _observe(conn, "wholefoods", "Chicken Breast 16 oz", 3.00, _days_ago(0))

    trend = service.protein_price_trend(conn=conn)
    assert trend.is_plottable
    assert [s.store for s in trend.plottable] == ["foodlion"]
    # The one-point store still reports its latest price; it just isn't a line.
    assert [s.store for s in trend.series if not s.is_plottable] == ["wholefoods"]


def test_the_store_filter_narrows_to_one_series(conn, priced):
    for store in ("foodlion", "wholefoods"):
        priced(store, "Chicken Breast 16 oz")
        _observe(conn, store, "Chicken Breast 16 oz", 5.00, _days_ago(0))

    trend = service.protein_price_trend(store="foodlion", conn=conn)
    assert [s.store for s in trend.series] == ["foodlion"]


# --------------------------------------------------------------------------- #
# The probe-price optimisation must not change the arithmetic
# --------------------------------------------------------------------------- #
def test_the_cached_resolver_matches_a_direct_per_row_computation(conn, priced):
    """The whole point of the probe price is that it is only faster, not different."""
    from grocery_planner import savings

    priced("foodlion", "Chicken Breast 16 oz")
    _observe(conn, "foodlion", "Chicken Breast 16 oz", 6.49, _days_ago(0))

    direct = savings.cost_per_gram_protein(6.49, "Chicken Breast 16 oz", "foodlion", conn=conn)
    point = service.protein_price_trend(conn=conn).series[0].latest
    assert point.cost_per_gram_protein == pytest.approx(
        direct.cost_per_gram_protein, rel=1e-9
    )


def test_the_window_default_matches_what_retention_promises_to_keep(conn):
    """A 90-day axis over 30 days of retained history would be a quiet lie."""
    from grocery_planner.records import RETENTION_FLOOR_DAYS

    assert trends.DEFAULT_WINDOW_DAYS == RETENTION_FLOOR_DAYS
