"""GFP-109 (Overall / Animal protein tabs) and GFP-110 (paged Latest known).

GFP-109 exists because the unrestricted answer was arithmetically right and
practically useless: the cheapest $/g protein at Whole Foods was a pancake mix
with added whey. Nobody puts that on a client's shopping list, so a headline
naming it is not a headline.

GFP-110 keeps the "Latest known" list two entries tall however many stores
exist, so it cannot push the chart out of the pane as stores and per-client ZIPs
(GFP-53) multiply.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

pytest.importorskip("PySide6")

from grocery_planner import db, service
from grocery_planner.gui.trends import (
    DEFAULT_TAB,
    LATEST_PAGE_SIZE,
    TAB_ANIMAL,
    TAB_LABELS,
    TAB_OVERALL,
)


def _food(conn, name: str, slug: str, category: str, protein=25.0) -> int:
    cur = conn.execute(
        "INSERT INTO foods(name, slug, category, source) VALUES (?, ?, ?, 'usda')",
        (name, slug, category),
    )
    food_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO food_nutrients(food_id, nutrient, amount_per_100g) "
        "VALUES (?, 'protein', ?)", (food_id, protein)
    )
    conn.commit()
    return food_id


def _observe(conn, store: str, item: str, food_id: int, price: float, days=(1, 0)) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO deal_food_match"
        "(store, item_name, food_id, confidence, method) VALUES (?, ?, ?, 0.9, 'test')",
        (store, item, food_id),
    )
    for offset in days:
        conn.execute(
            "INSERT INTO price_history"
            "(store, postal_code, item_name, deal_type, dollar_price, source, captured_at) "
            "VALUES (?, '27401', ?, 'Weekly Ad', ?, 'test', ?)",
            (store, item, price, (date.today() - timedelta(days=offset)).isoformat()),
        )
    conn.commit()


@pytest.fixture
def pancake_and_chicken(env_db):
    """The reported situation: a cheap non-meat protein beating real chicken.

    Depends on env_db so db.connect() lands in the isolated test database --
    without it these would write to the developer's real one.
    """
    conn = db.connect()
    mix = _food(conn, "Protein Pancake Mix", "gfp109-mix", "Plant Protein", protein=30.0)
    chicken = _food(conn, "Chicken Breast", "gfp109-chicken", "Meat", protein=25.0)
    # The mix is cheaper per gram of protein, so it wins the Overall tab.
    _observe(conn, "wholefoods", "Protein Pancake Mix 16 oz", mix, 3.00)
    _observe(conn, "wholefoods", "Boneless Chicken Breast 16 oz", chicken, 6.00)
    return conn


# --------------------------------------------------------------------------- #
# GFP-109 — the service filter, which all three front ends share
# --------------------------------------------------------------------------- #
def test_meat_only_excludes_a_cheaper_non_meat_protein(pancake_and_chicken):
    """The exact reported symptom, at the level that fixes it everywhere."""
    conn = pancake_and_chicken

    overall = service.protein_price_trend(conn=conn)
    assert "Pancake" in overall.series[0].latest.item_name

    animal = service.protein_price_trend(meat_only=True, conn=conn)
    assert "Chicken" in animal.series[0].latest.item_name
    assert all(
        "Pancake" not in p.item_name for s in animal.series for p in s.points
    )


def test_meat_only_is_more_expensive_here_and_that_is_the_point(pancake_and_chicken):
    """Filtering must not be mistaken for finding a better price.

    The animal-protein answer is dearer than the overall one. That is correct:
    the cheap option was not a meat. A filter that only ever lowered the number
    would be doing something else.
    """
    conn = pancake_and_chicken
    overall = service.protein_price_trend(conn=conn).series[0].latest.value
    animal = service.protein_price_trend(meat_only=True, conn=conn).series[0].latest.value
    assert animal > overall


def test_a_label_claim_has_no_matched_food_so_it_is_not_meat(conn):
    """GFP-69 rows carry a protein figure but no food, so nothing classifies them.

    An on-pack "30G Protein" claim is not evidence of meat, and letting one
    through would put a protein shake on an animal-protein ranking.
    """
    for offset in (1, 0):
        conn.execute(
            "INSERT INTO price_history"
            "(store, postal_code, item_name, deal_type, dollar_price, source, captured_at) "
            "VALUES ('foodlion', '27401', 'Protein Shake 30G Protein', 'Weekly Ad', "
            "2.00, 'test', ?)",
            ((date.today() - timedelta(days=offset)).isoformat(),),
        )
    conn.commit()

    assert service.protein_price_trend(conn=conn).observed_days == 2
    assert service.protein_price_trend(meat_only=True, conn=conn).observed_days == 0


def test_the_default_is_unchanged_so_no_existing_caller_shifts(pancake_and_chicken):
    """Tabs were chosen over a changed default precisely so nothing is demoted."""
    conn = pancake_and_chicken
    assert service.protein_price_trend(conn=conn) == \
        service.protein_price_trend(meat_only=False, conn=conn)


# --------------------------------------------------------------------------- #
# GFP-109 — the tabs
# --------------------------------------------------------------------------- #
def test_animal_protein_leads_and_is_the_default(window):
    """Meat is what the tool is for, so it must not be the second tab.

    The overall view's honest-but-useless answer (a pancake mix winning on $/g
    protein) is exactly what a nutritionist should not be shown first.
    """
    pane = window.trends
    assert pane.tabs.count() == 2
    assert [pane.tabs.tabText(i) for i in range(2)] == list(TAB_LABELS)
    assert pane.tabs.tabText(0) == "Animal protein"
    assert pane.tabs.currentIndex() == DEFAULT_TAB == TAB_ANIMAL
    assert pane.meat_only is True


def test_the_default_tab_shows_meat_and_overall_shows_the_pancake_mix(window,
                                                                     pancake_and_chicken):
    """Both directions, since the default moved: meat first, everything second."""
    pane = window.trends
    pane.reload()
    assert pane.meat_only is True
    assert "Chicken" in pane.trend.series[0].latest.item_name

    pane.tabs.setCurrentIndex(TAB_OVERALL)
    assert pane.meat_only is False
    assert "Pancake" in pane.trend.series[0].latest.item_name


def test_the_caption_names_the_subset_being_ranked(window, pancake_and_chicken):
    """A chart ranking meat under a caption saying "protein" is the quiet lie."""
    pane = window.trends
    pane.reload()
    assert "animal protein" in pane.subtitle.text()

    pane.tabs.setCurrentIndex(TAB_OVERALL)
    assert "animal protein" not in pane.subtitle.text()


def test_an_empty_animal_tab_points_at_the_overall_tab_not_at_the_date_range(window):
    """Telling someone to widen a range they never narrowed fixes nothing."""
    live = db.connect()
    mix = _food(live, "Protein Pancake Mix", "gfp109-mix2", "Plant Protein", protein=30.0)
    _observe(live, "wholefoods", "Protein Pancake Mix 16 oz", mix, 3.00)
    

    pane = window.trends
    pane.reload()                       # already on the Animal tab by default

    text = pane.subtitle.text()
    assert "No animal protein" in text
    assert "Overall protein tab" in text
    assert "widen the range" not in text


# --------------------------------------------------------------------------- #
# GFP-110 — paging the Latest known list
# --------------------------------------------------------------------------- #
def _seed_stores(count: int) -> None:
    """`count` stores, each with its own plottable series."""
    conn = db.connect()
    food = _food(conn, "Paging Chicken", "gfp110-chicken", "Meat")
    for index in range(count):
        _observe(conn, f"store{index}", f"Chicken Breast {index} 16 oz", food,
                 5.00 + index)


def test_a_single_page_shows_no_arrows_at_all(window):
    """A control that can never do anything is what GFP-104 removed from here."""
    _seed_stores(2)
    pane = window.trends
    pane.reload()

    assert len(pane.latest_entries(pane.trend)) == 2
    assert pane.prev_btn.isHidden() and pane.next_btn.isHidden()


def test_more_than_two_stores_pages_two_at_a_time(window):
    _seed_stores(5)
    pane = window.trends
    pane.reload()

    entries = pane.latest_entries(pane.trend)
    assert len(entries) == 5
    assert pane.page_count(len(entries)) == 3          # 2 + 2 + 1
    assert not pane.prev_btn.isHidden()
    # First page: cannot go back, can go forward.
    assert not pane.prev_btn.isEnabled()
    assert pane.next_btn.isEnabled()
    assert "1/3" in pane.latest.text()


def test_the_arrows_walk_the_pages_and_stop_at_the_ends(window):
    _seed_stores(5)
    pane = window.trends
    pane.reload()

    pane.next_btn.click()
    assert "2/3" in pane.latest.text()
    assert pane.prev_btn.isEnabled() and pane.next_btn.isEnabled()

    pane.next_btn.click()
    assert "3/3" in pane.latest.text()
    # Disabled, NOT hidden -- the arrows must not jump around mid-use.
    assert not pane.next_btn.isEnabled()
    assert not pane.next_btn.isHidden()

    pane.prev_btn.click()
    assert "2/3" in pane.latest.text()


def test_each_page_shows_at_most_the_page_size(window):
    _seed_stores(5)
    pane = window.trends
    pane.reload()

    for expected in (LATEST_PAGE_SIZE, LATEST_PAGE_SIZE, 1):
        assert pane.latest.text().count("<b>") == expected
        if pane.next_btn.isEnabled():
            pane.next_btn.click()


def test_the_page_survives_a_reload(window):
    """app.py reloads this pane after every scrape; losing the page is silent."""
    _seed_stores(5)
    pane = window.trends
    pane.reload()
    pane.next_btn.click()
    assert "2/3" in pane.latest.text()

    pane.reload()
    assert "2/3" in pane.latest.text()


def test_a_page_that_no_longer_exists_clamps_instead_of_rendering_blank(window):
    _seed_stores(5)
    pane = window.trends
    pane.reload()
    pane.next_btn.click()
    pane.next_btn.click()
    assert "3/3" in pane.latest.text()

    conn = db.connect()
    conn.execute("DELETE FROM price_history WHERE store IN ('store3', 'store4')")
    conn.commit()
    pane.reload()

    assert "2/2" in pane.latest.text()
    assert pane.latest.text().count("<b>") > 0        # not a blank page


def test_switching_tab_resets_the_page(window):
    """Page 3 of the overall list means nothing in a shorter animal list."""
    _seed_stores(5)
    pane = window.trends
    pane.reload()
    pane.next_btn.click()
    assert "2/3" in pane.latest.text()

    pane.tabs.setCurrentIndex(TAB_OVERALL)
    assert pane._latest_page == 0


def test_an_empty_animal_tab_blames_the_range_when_the_range_is_the_cause(window):
    """Which narrowing emptied the view decides which message is honest.

    Caught by an existing GFP-41 test: with the Animal tab as the default, a
    result emptied by the DATE RANGE was being blamed on the meat filter, which
    sends the user to an Overall tab that is just as empty. The pane now checks
    whether dropping only the meat filter would find anything, rather than
    asserting "there is other protein on record" without looking.
    """
    live = db.connect()
    chicken = _food(live, "Range Chicken", "gfp109-range", "Meat")
    _observe(live, "wholefoods", "Chicken Breast 16 oz", chicken, 5.00, days=(40, 39))

    pane = window.trends
    pane.reload()
    pane.range_select.setCurrentIndex(pane.range_select.findData(7))

    text = pane.subtitle.text()
    assert "widen the range" in text
    assert "Overall protein tab" not in text
