"""GFP-41: the trends pane's store and time-range selectors.

The selectors must *narrow the query*, not filter an already-drawn chart, so
what the pane plots and what ``gplan trends --store X --days N`` prints stay the
same numbers. These tests drive the widgets and assert on the resulting trend.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

pytest.importorskip("PySide6")

from grocery_planner import db
from grocery_planner.gui.trends import ALL_STORES, range_choices, widest_range


def _seed(store: str, item: str, slug: str, offsets, protein=25.0) -> None:
    """One protein-resolvable item for ``store``, priced on each day offset."""
    conn = db.connect()
    row = conn.execute("SELECT id FROM foods WHERE slug = ?", (slug,)).fetchone()
    if row is None:
        cur = conn.execute(
            "INSERT INTO foods(name, slug, category, source) "
            "VALUES (?, ?, 'test', 'usda')", (slug, slug)
        )
        food_id = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO food_nutrients(food_id, nutrient, amount_per_100g) "
            "VALUES (?, 'protein', ?)", (food_id, protein)
        )
    else:
        food_id = int(row["id"])
    conn.execute(
        "INSERT INTO deal_food_match(store, item_name, food_id, confidence, method) "
        "VALUES (?, ?, ?, 0.9, 'test')", (store, item, food_id)
    )
    for offset in offsets:
        conn.execute(
            "INSERT INTO price_history"
            "(store, postal_code, item_name, deal_type, dollar_price, source, captured_at) "
            "VALUES (?, '27401', ?, 'Weekly Ad', ?, 'test', ?)",
            (store, item, 5.0 + offset * 0.25,
             (date.today() - timedelta(days=offset)).isoformat()),
        )
    conn.commit()


def _select_store(pane, key: str) -> None:
    pane.store_select.setCurrentIndex(pane.store_select.findData(key))


def _select_days(pane, days: int) -> None:
    pane.range_select.setCurrentIndex(pane.range_select.findData(days))


# --------------------------------------------------------------------------- #
# The range cap -- an axis may not promise more history than retention keeps
# --------------------------------------------------------------------------- #
def test_no_offered_range_outlives_what_retention_promises_to_keep():
    """A 'last 365 days' option over a 90-day floor would be a quiet lie."""
    from grocery_planner import config

    kept = config.history_retention_days()
    assert range_choices(), "the selector must offer at least one range"
    # GFP-42: the cap is now the CONFIGURED retention, not the floor -- the
    # floor is merely the least a user may set it to. Offering a 365-day axis
    # is honest exactly when 365 days are kept.
    assert all(days <= kept for days, _ in range_choices())
    assert widest_range() <= kept


def test_the_default_range_is_the_services_default(window):
    from grocery_planner import service

    assert window.trends.selected_days == service.DEFAULT_WINDOW_DAYS


# --------------------------------------------------------------------------- #
# The store selector is built from data, not from the store registry
# --------------------------------------------------------------------------- #
def test_the_store_list_offers_only_stores_that_have_history(window):
    """A registry-driven list offers stores that plot nothing, which reads as a bug."""
    _seed("foodlion", "Chicken Breast 16 oz", "gfp41-chicken", [1, 0])
    window.trends.reload()

    keys = [
        window.trends.store_select.itemData(i)
        for i in range(window.trends.store_select.count())
    ]
    assert keys == [ALL_STORES, "foodlion"]
    # "wholefoods" is a registered store, but nothing has been captured for it.
    assert "wholefoods" not in keys


def test_selecting_a_store_narrows_the_series(window):
    _seed("foodlion", "Chicken Breast 16 oz", "gfp41-chicken", [1, 0])
    _seed("wholefoods", "Chicken Breast 16 oz", "gfp41-chicken", [1, 0])
    window.trends.reload()
    assert len(window.trends.trend.series) == 2

    _select_store(window.trends, "foodlion")
    assert window.trends.selected_store == "foodlion"
    assert [s.key for s in window.trends.trend.series] == ["foodlion"]


def test_all_stores_means_no_filter_not_a_store_called_empty_string(window):
    _seed("foodlion", "Chicken Breast 16 oz", "gfp41-chicken", [1, 0])
    window.trends.reload()

    _select_store(window.trends, ALL_STORES)
    assert window.trends.selected_store is None
    assert [s.key for s in window.trends.trend.series] == ["foodlion"]


# --------------------------------------------------------------------------- #
# The range selector
# --------------------------------------------------------------------------- #
def test_narrowing_the_range_drops_older_points(window):
    # 20 days, not 40: GFP-42 moved the default window from 90 to 30 (90 is no
    # longer a range the selector offers). The oldest point has to sit inside
    # the default window or this starts testing the default instead of the
    # narrowing it is named for.
    _seed("foodlion", "Chicken Breast 16 oz", "gfp41-chicken", [20, 3, 1, 0])
    window.trends.reload()
    assert window.trends.trend.observed_days == 4

    _select_days(window.trends, 7)
    assert window.trends.trend.days == 7
    assert window.trends.trend.observed_days == 3   # the 20-day-old point is out


# --------------------------------------------------------------------------- #
# Selections must survive a reload -- app.py reloads this pane after a scrape
# --------------------------------------------------------------------------- #
def test_a_reload_keeps_the_users_selections(window):
    _seed("foodlion", "Chicken Breast 16 oz", "gfp41-chicken", [1, 0])
    _seed("wholefoods", "Chicken Breast 16 oz", "gfp41-chicken", [1, 0])
    window.trends.reload()

    _select_store(window.trends, "wholefoods")
    _select_days(window.trends, 7)

    window.trends.reload()   # as happens after a scrape finishes

    assert window.trends.selected_store == "wholefoods"
    assert window.trends.selected_days == 7


def test_repopulating_the_store_list_does_not_recurse(window):
    """Rebuilding a QComboBox emits currentIndexChanged, which is wired to reload."""
    _seed("foodlion", "Chicken Breast 16 oz", "gfp41-chicken", [1, 0])

    calls = {"n": 0}
    original = window.trends._refresh_store_choices

    def counting():
        calls["n"] += 1
        assert calls["n"] < 5, "reload recursed through the selector's own signal"
        original()

    window.trends._refresh_store_choices = counting
    window.trends.reload()
    assert calls["n"] == 1


def test_a_store_that_leaves_the_list_falls_back_to_all_stores(window):
    """Plotting a different store under the name the user picked would be worse."""
    _seed("foodlion", "Chicken Breast 16 oz", "gfp41-chicken", [1, 0])
    window.trends.reload()
    _select_store(window.trends, "foodlion")

    conn = db.connect()
    conn.execute("DELETE FROM price_history WHERE store = 'foodlion'")
    conn.commit()
    window.trends.reload()

    assert window.trends.selected_store is None
    assert window.trends.store_select.currentText() == "All stores"


# --------------------------------------------------------------------------- #
# The empty state stays honest once a filter can be what emptied it
# --------------------------------------------------------------------------- #
def test_an_empty_database_still_says_run_a_scrape(window):
    # GFP-104 rewrote this case: a wholly empty database gets one plain message
    # and no controls, instead of a protein-specific reason under two dropdowns.
    assert "No price data yet" in window.trends.subtitle.text()


def test_a_filter_that_empties_the_chart_says_so_rather_than_run_a_scrape(window):
    """Telling someone to scrape when they need to widen a dropdown fixes nothing."""
    _seed("foodlion", "Chicken Breast 16 oz", "gfp41-chicken", [40, 39])
    window.trends.reload()
    _select_days(window.trends, 7)

    text = window.trends.subtitle.text()
    assert "There is history further back" in text
    assert "widen the range" in text.lower()
    assert "No protein prices on record yet" not in text
