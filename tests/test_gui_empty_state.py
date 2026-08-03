"""GFP-104: an empty database shows one plain message, not controls over nothing.

Reported from first use: on a fresh install the trends pane presented a bold
heading, a Store dropdown, a Range dropdown and a "no protein prices" line, all
over an empty pane. Controls that look operable but govern nothing invite a user
to fiddle with them instead of doing the one thing that would help.

The distinction these tests protect is that this is only true when there is NO
data at all. Every other empty case means "there IS data, just not this data",
and there the controls are exactly what the user needs — so they must survive.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

pytest.importorskip("PySide6")

from grocery_planner import db


def _seed_history(days: int = 3, store: str = "foodlion", start: int = 0) -> None:
    """Protein-resolvable history, so the pane has something real to show."""
    conn = db.connect()
    cur = conn.execute(
        "INSERT INTO foods(name, slug, category, source) "
        "VALUES ('Gfp104 chicken', 'gfp104-chicken', 'test', 'usda')"
    )
    food_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO food_nutrients(food_id, nutrient, amount_per_100g) "
        "VALUES (?, 'protein', 25.0)", (food_id,)
    )
    conn.execute(
        "INSERT INTO deal_food_match(store, item_name, food_id, confidence, method) "
        "VALUES (?, 'Chicken Breast 16 oz', ?, 0.9, 'test')", (store, food_id)
    )
    for index in range(days):
        offset = start + index
        conn.execute(
            "INSERT INTO price_history"
            "(store, postal_code, item_name, deal_type, dollar_price, source, captured_at) "
            "VALUES (?, '27401', 'Chicken Breast 16 oz', 'Weekly Ad', ?, 'test', ?)",
            (store, 5.0 + index * 0.25,
             (date.today() - timedelta(days=offset)).isoformat()),
        )
    conn.commit()


# --------------------------------------------------------------------------- #
# The reported problem
# --------------------------------------------------------------------------- #
def test_an_empty_database_hides_every_control(window):
    """The exact complaint: heading and dropdowns hanging over no data."""
    pane = window.trends
    assert not pane.has_data
    assert not pane.title.isVisibleTo(pane)
    assert not pane.selectors.isVisibleTo(pane)
    assert not pane.chart.isVisibleTo(pane)
    assert not pane.legend.isVisibleTo(pane)
    assert not pane.latest.isVisibleTo(pane)


def test_the_message_says_what_to_do_and_names_the_real_menu_path(window):
    text = window.trends.subtitle.text()
    assert "No price data yet" in text
    assert "Run scrape" in text and "Scrape all" in text
    assert window.trends.subtitle.isVisibleTo(window.trends)


def test_the_controls_come_back_as_soon_as_there_is_data(window):
    """No restart: the pane already reloads after a scrape."""
    pane = window.trends
    assert not pane.selectors.isVisibleTo(pane)

    _seed_history(days=3)
    pane.reload()

    assert pane.has_data
    assert pane.title.isVisibleTo(pane)
    assert pane.selectors.isVisibleTo(pane)
    assert pane.chart.isVisibleTo(pane)


# --------------------------------------------------------------------------- #
# The three explanations that must SURVIVE -- data exists, just not this data
# --------------------------------------------------------------------------- #
def test_one_day_of_history_keeps_its_controls_and_its_own_message(window):
    """'Come back tomorrow' is not 'there is nothing here'."""
    _seed_history(days=1)
    window.trends.reload()

    assert window.trends.has_data
    assert window.trends.selectors.isVisibleTo(window.trends)
    assert "Only 1 day" in window.trends.subtitle.text()
    assert "No price data yet" not in window.trends.subtitle.text()


def test_a_filter_that_empties_the_view_keeps_its_controls(window):
    """The user needs those very dropdowns to undo what they did."""
    _seed_history(days=2, start=40)      # real data, all outside a 7-day window
    window.trends.reload()
    window.trends.range_select.setCurrentIndex(
        window.trends.range_select.findData(7)
    )

    assert window.trends.has_data
    assert window.trends.selectors.isVisibleTo(window.trends)
    assert "No price data yet" not in window.trends.subtitle.text()


def test_history_that_resolves_no_protein_is_still_data(window):
    """price_history rows with no protein figure are not 'no data at all'.

    The pane must fall through to the service's own reason ("run a scrape"),
    which is about protein specifically, rather than claiming the database is
    empty when it demonstrably is not.
    """
    conn = db.connect()
    conn.execute(
        "INSERT INTO price_history"
        "(store, postal_code, item_name, deal_type, dollar_price, source, captured_at) "
        "VALUES ('foodlion', '27401', 'Unmatchable Thing', 'Weekly Ad', 3.0, 'test', ?)",
        (date.today().isoformat(),),
    )
    conn.commit()
    window.trends.reload()

    assert window.trends.has_data
    assert window.trends.selectors.isVisibleTo(window.trends)
    assert "No price data yet" not in window.trends.subtitle.text()


# --------------------------------------------------------------------------- #
# The service question behind it
# --------------------------------------------------------------------------- #
def test_has_price_history_is_about_the_whole_table_not_a_window(conn):
    """Unwindowed on purpose -- 'ever collected anything' is a different question."""
    from grocery_planner import service

    assert service.has_price_history(conn) is False
    conn.execute(
        "INSERT INTO price_history"
        "(store, postal_code, item_name, deal_type, dollar_price, source, captured_at) "
        "VALUES ('foodlion', '27401', 'Old Thing', 'Weekly Ad', 3.0, 'test', '2020-01-01')"
    )
    conn.commit()
    # Far outside any offered range, but the install HAS collected something.
    assert service.has_price_history(conn) is True


# --------------------------------------------------------------------------- #
# The roster, checked for the same class of problem -- it does not have it
# --------------------------------------------------------------------------- #
def test_the_roster_empty_state_is_already_one_clear_sentence(window):
    """The ticket asked me to check here too. This pane was already right.

    Hiding its search box as well (it governs nothing with zero clients) was
    tried and reverted: a hidden widget cannot take focus, which broke the
    Down-arrow behaviour GFP-36 deliberately built. The trends pane's problem
    was a wall of controls over nothing; one search field beside a named action
    is not that.
    """
    roster = window.roster
    assert "No clients yet" in roster.client_list.item(0).text()
    assert roster.add_btn.isVisibleTo(roster)
    assert roster.search_edit.isVisibleTo(roster)   # still focusable
