"""GFP-107: the cheapest-meat strip along the bottom of the main window."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

pytest.importorskip("PySide6")

from grocery_planner import db, service
from grocery_planner.gui.cheapest import describe

FUTURE = (date.today() + timedelta(days=7)).isoformat()


def _seed(store="harristeeter", price=4.00, sold_by=None, uom=None,
          category="Meat", item="Chicken Thighs 16 oz") -> None:
    conn = db.connect()
    row = conn.execute(
        "SELECT id FROM foods WHERE slug = 'gfp107-gui'").fetchone()
    if row is None:
        cur = conn.execute(
            "INSERT INTO foods(name, slug, category, source) "
            "VALUES ('Gui Chicken', 'gfp107-gui', ?, 'usda')", (category,))
        food_id = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO food_nutrients(food_id, nutrient, amount_per_100g) "
            "VALUES (?, 'protein', 25.0)", (food_id,))
    else:
        food_id = int(row["id"])
    conn.execute(
        "INSERT OR IGNORE INTO deal_food_match"
        "(store, item_name, food_id, confidence, method) VALUES (?, ?, ?, 0.9, 'test')",
        (store, item, food_id))
    conn.execute(
        "INSERT INTO deals(store, item_name, deal_type, dollar_price, valid_to, "
        "source, postal_code, sold_by, price_per_unit_uom) "
        "VALUES (?, ?, 'Weekly Ad', ?, ?, 'test', '27401', ?, ?)",
        (store, item, price, FUTURE, sold_by, uom))
    conn.commit()


def test_the_strip_is_below_the_stack_so_it_survives_opening_a_client(window):
    """Inside a page it would vanish exactly when a client is on screen."""
    assert window.cheapest is not None
    assert window.cheapest.parent() is window.centralWidget()
    assert window.stack.parent() is window.centralWidget()


def test_the_strip_lists_a_store_with_its_kind_and_price(window):
    _seed()
    window.cheapest.reload()

    assert len(window.cheapest.items) == 1
    text = window.cheapest.body.text()
    assert "Harris Teeter" in text
    assert "(chicken)" in text
    assert "Chicken Thighs" in text


def test_a_weight_sold_item_says_what_the_price_buys(window):
    """GFP-98: $1.49 buys ONE POUND, not the package."""
    _seed(price=1.49, sold_by="WEIGHT", uom="lb")
    window.cheapest.reload()
    assert "$1.49 per lb" in window.cheapest.body.text()


def test_a_unit_sold_item_carries_no_denomination_suffix(window):
    _seed(price=6.99, sold_by="UNIT")
    window.cheapest.reload()
    text = window.cheapest.body.text()
    assert "$6.99" in text and "per" not in text.split("$6.99")[1][:12]


def test_an_empty_database_points_at_the_scrape_menu(window):
    strip = window.cheapest
    assert strip.items == []
    assert "No price data yet" in strip.body.text()
    assert "Run scrape" in strip.body.text()
    assert strip.title.isHidden()


def test_history_but_no_rankable_meat_says_something_different(window):
    """Two silences, told apart: no data at all vs nothing rankable this week.

    Telling someone to scrape when a scrape has already run and simply found no
    meat with a usable size would send them to fix nothing.
    """
    conn = db.connect()
    conn.execute(
        "INSERT INTO price_history"
        "(store, postal_code, item_name, deal_type, dollar_price, source, captured_at) "
        "VALUES ('foodlion', '27401', 'Unmatchable', 'Weekly Ad', 3.0, 'test', ?)",
        (date.today().isoformat(),))
    conn.commit()
    window.cheapest.reload()

    text = window.cheapest.body.text()
    assert "nothing to rank" in text.lower()
    assert "No price data yet" not in text


def test_describe_omits_the_parenthetical_when_the_kind_is_unknown(window):
    """A missing label is not a reason to hide a real cheap price."""
    item = service.CheapestProtein(
        store="x", label="X", item_name="Mystery Cut", kind=None,
        cost_per_gram_protein=0.02, price=5.0, protein_grams=250.0)
    rendered = describe(item)
    assert "Mystery Cut" in rendered
    assert "(" not in rendered.replace("(Pack", "")


# --------------------------------------------------------------------------- #
# GFP-118 -- the strip is clickable
#
# This panel answers "where is protein cheapest right now" and is the first
# thing read, yet it was the one place that could not be clicked through while
# the grocery list could.
# --------------------------------------------------------------------------- #
def _linked_item(url="https://www.harristeeter.com/p/drumsticks/0020030200000"):
    return service.CheapestProtein(
        store="harristeeter", label="Harris Teeter",
        item_name="Chicken Drumsticks Value Pack, 1 lb", kind="chicken",
        cost_per_gram_protein=0.0169, price=1.49, protein_grams=88.0,
        sold_by="WEIGHT", price_per_unit_uom="lb",
        source_url=url, product_identifier="0020030200000")


def test_a_linked_item_renders_a_real_anchor(window):
    rendered = describe(_linked_item())
    assert '<a href="https://www.harristeeter.com/p/drumsticks/0020030200000"' in rendered
    assert "View product" in rendered


def test_the_label_never_says_buy_now(window):
    """GFP-38's rule: a product page is not a checkout this app drives."""
    assert "Buy now" not in describe(_linked_item())


def test_an_item_with_no_url_degrades_to_plain_text(window):
    """A dead control would be worse than the plain text it replaces."""
    rendered = describe(_linked_item(url=None))
    assert "<a href=" not in rendered
    assert "Chicken Drumsticks" in rendered


def test_a_url_is_escaped_so_it_cannot_break_the_markup(window):
    rendered = describe(_linked_item(url='https://x.test/p?a=1&b="2"'))
    assert "&amp;" in rendered and '&quot;' in rendered


def test_the_strip_opens_links_externally_rather_than_swallowing_the_click(window):
    """Anchors that render blue and do nothing are worse than no anchor."""
    assert window.cheapest.body.openExternalLinks() is True


def test_a_live_row_carries_its_url_and_sku_through_the_service(window):
    _seed(price=1.49, sold_by="WEIGHT", uom="lb")
    conn = db.connect()
    conn.execute(
        "UPDATE deals SET source_url = ?, product_identifier = ? "
        "WHERE item_name = 'Chicken Thighs 16 oz'",
        ("https://www.harristeeter.com/p/x/1", "0020030200000"))
    conn.commit()
    window.cheapest.reload()

    item = window.cheapest.items[0]
    assert item.source_url == "https://www.harristeeter.com/p/x/1"
    assert item.product_identifier == "0020030200000"
    assert "<a href=" in window.cheapest.body.text()
