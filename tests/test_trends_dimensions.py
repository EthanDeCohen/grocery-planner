"""GFP-40: two metrics over two dimensions, from one definition.

GFP-36 built the headline question — cheapest $/g protein per store per day —
and ``tests/test_trends.py`` covers it. This file covers what GFP-40 added on
top: the **food** dimension, the plain **price** metric and the scope rule that
keeps it honest, the ZIP filter, and the gap rule stated as an executable
assertion rather than a docstring promise.

No Qt here, same as ``test_trends.py``: the numbers behind the chart are core,
so they are testable without an event loop.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from grocery_planner import service

PROTEIN_PER_100G = 25.0


@pytest.fixture
def catalog(conn):
    """Build named foods and match items to them, for by-food series."""
    def make(name: str, slug: str, protein_per_100g: float | None = PROTEIN_PER_100G):
        cur = conn.execute(
            "INSERT INTO foods(name, slug, category, source) "
            "VALUES (?, ?, 'test', 'usda')", (name, slug)
        )
        food_id = int(cur.lastrowid)
        # None means "a real food with no protein figure on record" -- the case
        # that separates food attribution from the protein chain.
        if protein_per_100g is not None:
            conn.execute(
                "INSERT INTO food_nutrients(food_id, nutrient, amount_per_100g) "
                "VALUES (?, 'protein', ?)", (food_id, protein_per_100g)
            )
        conn.commit()

        def match(store: str, item_name: str) -> None:
            conn.execute(
                "INSERT INTO deal_food_match"
                "(store, item_name, food_id, confidence, method) "
                "VALUES (?, ?, ?, 0.9, 'test')", (store, item_name, food_id)
            )
            conn.commit()

        return match

    return make


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
# The food dimension
# --------------------------------------------------------------------------- #
def test_grouping_by_food_makes_each_food_its_own_series(conn, catalog):
    catalog("Test chicken", "gfp40-chicken")("foodlion", "Chicken Breast 16 oz")
    catalog("Test salmon", "gfp40-salmon", 20.0)("foodlion", "Salmon Fillet 16 oz")
    for day in (1, 0):
        _observe(conn, "foodlion", "Chicken Breast 16 oz", 5.00, _days_ago(day))
        _observe(conn, "foodlion", "Salmon Fillet 16 oz", 12.00, _days_ago(day))

    trend = service.price_trend(dimension=service.Dimension.FOOD, conn=conn)
    assert trend.dimension is service.Dimension.FOOD
    # Keys are slugs (stable, machine-readable); labels are what a user reads.
    assert sorted(s.key for s in trend.series) == ["gfp40-chicken", "gfp40-salmon"]
    assert {s.key: s.label for s in trend.series} == {
        "gfp40-chicken": "Test chicken", "gfp40-salmon": "Test salmon",
    }


def test_a_food_series_folds_every_store_together(conn, catalog):
    """One food, two stores: the cheapest of the two is that food's price."""
    chicken = catalog("Test chicken", "gfp40-chicken")
    chicken("foodlion", "Chicken Breast 16 oz")
    chicken("wholefoods", "Chicken Breast 16 oz")
    _observe(conn, "foodlion", "Chicken Breast 16 oz", 9.00, _days_ago(0))
    _observe(conn, "wholefoods", "Chicken Breast 16 oz", 4.00, _days_ago(0))

    trend = service.price_trend(dimension=service.Dimension.FOOD, conn=conn)
    assert len(trend.series) == 1
    # ...and the point remembers WHICH store won, which is the whole use of it.
    assert trend.series[0].latest.store == "wholefoods"
    assert trend.series[0].latest.price == 4.00


def test_an_item_with_no_matched_food_joins_no_food_series(conn, catalog):
    """A label-claim item has a protein figure but no food -- absent, not lumped.

    Separate stores on purpose: a by-store series keeps only the day's cheapest
    item, so putting both in one store would test which is cheaper rather than
    whether the unmatched one is attributable to a food at all.
    """
    catalog("Test chicken", "gfp40-chicken")("wholefoods", "Chicken Breast 16 oz")
    _observe(conn, "wholefoods", "Chicken Breast 16 oz", 5.00, _days_ago(0))
    _observe(conn, "foodlion", "Protein Shake 30G Protein", 2.00, _days_ago(0))

    by_food = service.price_trend(dimension=service.Dimension.FOOD, conn=conn)
    assert [s.key for s in by_food.series] == ["gfp40-chicken"]
    assert all(
        p.item_name != "Protein Shake 30G Protein"
        for s in by_food.series for p in s.points
    )
    # It is not lost everywhere though -- grouped by store, the claim counts.
    by_store = service.protein_price_trend(conn=conn)
    assert any(
        p.item_name == "Protein Shake 30G Protein"
        for s in by_store.series for p in s.points
    )


# --------------------------------------------------------------------------- #
# The price metric, and why it must be scoped
# --------------------------------------------------------------------------- #
def test_an_unscoped_price_series_is_refused_not_quietly_wrong(conn):
    """The cheapest item in a whole ad measures package size, not price."""
    with pytest.raises(service.UnscopedPriceTrendError):
        service.price_trend(metric=service.Metric.PRICE, conn=conn)


def test_a_price_series_scoped_to_a_food_is_the_observed_dollars(conn, catalog):
    catalog("Test chicken", "gfp40-chicken")("foodlion", "Chicken Breast 16 oz")
    _observe(conn, "foodlion", "Chicken Breast 16 oz", 6.00, _days_ago(1))
    _observe(conn, "foodlion", "Chicken Breast 16 oz", 5.00, _days_ago(0))

    trend = service.price_trend(
        metric=service.Metric.PRICE, food="gfp40-chicken", conn=conn
    )
    assert trend.metric is service.Metric.PRICE
    assert [p.value for p in trend.series[0].points] == [6.00, 5.00]


def test_grouping_by_food_scopes_a_price_series_on_its_own(conn, catalog):
    """`--by food` is itself a scope, so it needs no `--food` to be legal."""
    catalog("Test chicken", "gfp40-chicken")("foodlion", "Chicken Breast 16 oz")
    _observe(conn, "foodlion", "Chicken Breast 16 oz", 5.00, _days_ago(0))

    trend = service.price_trend(
        metric=service.Metric.PRICE, dimension=service.Dimension.FOOD, conn=conn
    )
    assert trend.series[0].latest.value == 5.00


def test_a_food_with_a_price_but_no_protein_still_has_a_price_series(conn, catalog):
    """Food attribution must not require the protein chain to close.

    A matched food with no protein figure on record has no $/g protein -- but
    it does have a price, and dropping it from a price series would be the
    tool hiding data it holds.
    """
    catalog("Proteinless food", "gfp40-proteinless", None)("foodlion", "Mystery Item 16 oz")
    _observe(conn, "foodlion", "Mystery Item 16 oz", 3.00, _days_ago(0))

    trend = service.price_trend(
        metric=service.Metric.PRICE, food="gfp40-proteinless", conn=conn
    )
    assert trend.series[0].latest.value == 3.00
    # ...while the protein trend correctly refuses to invent a $/g figure.
    assert service.protein_price_trend(conn=conn).observed_days == 0


# --------------------------------------------------------------------------- #
# Filters
# --------------------------------------------------------------------------- #
def test_a_food_filter_accepts_a_slug_or_a_name_in_any_case(conn, catalog):
    catalog("Test chicken", "gfp40-chicken")("foodlion", "Chicken Breast 16 oz")
    catalog("Test salmon", "gfp40-salmon", 20.0)("foodlion", "Salmon Fillet 16 oz")
    _observe(conn, "foodlion", "Chicken Breast 16 oz", 5.00, _days_ago(0))
    _observe(conn, "foodlion", "Salmon Fillet 16 oz", 12.00, _days_ago(0))

    for spelling in ("gfp40-chicken", "Test chicken", "TEST CHICKEN"):
        trend = service.price_trend(food=spelling, conn=conn)
        items = [p.item_name for s in trend.series for p in s.points]
        assert items == ["Chicken Breast 16 oz"], spelling


def test_an_unknown_food_is_an_error_not_an_empty_chart(conn, catalog):
    """A typo must not read as 'this food has never been on sale'."""
    catalog("Test chicken", "gfp40-chicken")
    with pytest.raises(service.UnknownFoodError):
        service.price_trend(food="chikcen", conn=conn)


def test_the_postal_code_filter_keeps_two_zips_from_becoming_one_series(conn, catalog):
    """Two ZIPs are two markets (GFP-53), not extra points in one line."""
    catalog("Test chicken", "gfp40-chicken")("foodlion", "Chicken Breast 16 oz")
    _observe(conn, "foodlion", "Chicken Breast 16 oz", 5.00, _days_ago(1), "27401")
    _observe(conn, "foodlion", "Chicken Breast 16 oz", 9.00, _days_ago(0), "10001")

    here = service.protein_price_trend(postal_code="27401", conn=conn)
    assert [p.price for s in here.series for p in s.points] == [5.00]
    assert service.protein_price_trend(conn=conn).observed_days == 2


# --------------------------------------------------------------------------- #
# Gaps
# --------------------------------------------------------------------------- #
def test_a_fortnight_with_no_scrape_is_a_gap_not_a_zero(conn, catalog):
    """The ticket's acceptance criterion, stated as an executable assertion."""
    catalog("Test chicken", "gfp40-chicken")("foodlion", "Chicken Breast 16 oz")
    for day in (14, 0):                       # nothing at all in between
        _observe(conn, "foodlion", "Chicken Breast 16 oz", 5.00, _days_ago(day))

    points = service.protein_price_trend(conn=conn).series[0].points
    assert [p.day for p in points] == [_days_ago(14), _days_ago(0)]
    assert len(points) == 2                   # no invented days between them
    assert all(p.value > 0 for p in points)   # and no zero-valued filler


# --------------------------------------------------------------------------- #
# Store-agnosticism (GFP-32)
# --------------------------------------------------------------------------- #
def test_an_unregistered_store_still_gets_a_series(conn, catalog):
    """Nothing here may branch on store identity; an unknown key labels as itself."""
    catalog("Test chicken", "gfp40-chicken")("brand-new-store", "Chicken Breast 16 oz")
    _observe(conn, "brand-new-store", "Chicken Breast 16 oz", 5.00, _days_ago(0))

    series = service.protein_price_trend(conn=conn).series[0]
    assert series.key == "brand-new-store"
    assert series.label == "brand-new-store"


# --------------------------------------------------------------------------- #
# The shorthand and the general form must not drift apart
# --------------------------------------------------------------------------- #
def test_the_protein_shorthand_is_exactly_the_general_form(conn, catalog):
    catalog("Test chicken", "gfp40-chicken")("foodlion", "Chicken Breast 16 oz")
    _observe(conn, "foodlion", "Chicken Breast 16 oz", 6.00, _days_ago(1))
    _observe(conn, "foodlion", "Chicken Breast 16 oz", 5.00, _days_ago(0))

    shorthand = service.protein_price_trend(conn=conn)
    general = service.price_trend(
        metric=service.Metric.PROTEIN, dimension=service.Dimension.STORE, conn=conn
    )
    assert shorthand == general
