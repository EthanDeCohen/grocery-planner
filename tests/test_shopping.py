"""GFP-112: a grocery list a client can actually shop from.

The point of this ticket, in the user's words: "what's the point of seeing this
with nothing to do about it." So the tests that matter are the ones proving the
list is ACTIONABLE -- whole packages, real prices, a period -- rather than the
amortised daily figure the bill deliberately produces.
"""
from __future__ import annotations

import csv
import io
from datetime import date, timedelta

import pytest

from grocery_planner import service
from grocery_planner.customers import Customer
from grocery_planner.service import shopping, shoppingfmt

FUTURE = (date.today() + timedelta(days=7)).isoformat()


def _client(weight_kg: float = 80.0) -> Customer:
    return Customer.create("Jane Doe", weight=weight_kg, weight_unit="kg")


def _deal(conn, item, price, *, store="harristeeter", protein=25.0,
          sold_by=None, uom=None, identifier=None, url=None, slug=None):
    """A deal that resolves to a real $/g protein, so it can reach a bill."""
    slug = slug or f"gfp112-{abs(hash(item)) % 10**6}"
    cur = conn.execute(
        "INSERT INTO foods(name, slug, category, source) VALUES (?, ?, 'Meat', 'usda')",
        (item, slug))
    food_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO food_nutrients(food_id, nutrient, amount_per_100g) "
        "VALUES (?, 'protein', ?)", (food_id, protein))
    conn.execute(
        "INSERT INTO deal_food_match(store, item_name, food_id, confidence, method) "
        "VALUES (?, ?, ?, 0.9, 'test')", (store, item, food_id))
    conn.execute(
        "INSERT INTO deals(store, item_name, deal_type, dollar_price, valid_to, source, "
        "postal_code, sold_by, price_per_unit_uom, product_identifier, "
        "product_identifier_ns, source_url) "
        "VALUES (?, ?, 'Weekly Ad', ?, ?, 'test', '27401', ?, ?, ?, ?, ?)",
        (store, item, price, FUTURE, sold_by, uom, identifier,
         "kroger.product_id" if identifier else None, url))
    conn.commit()


# --------------------------------------------------------------------------- #
# The core translation: amortised daily bill -> things you can buy
# --------------------------------------------------------------------------- #
def test_a_package_item_is_bought_in_whole_packs(conn):
    """You cannot buy 0.4 of a packet."""
    _deal(conn, "Chicken Breast 16 oz", 5.00, sold_by="UNIT")
    glist = service.grocery_list_for(_client(), days=7, conn=conn)

    item = glist.items[0]
    assert item.quantity_unit == "pack"
    assert item.quantity == int(item.quantity)      # a whole number
    assert item.quantity >= 1


def test_quantities_round_up_never_down(conn):
    """Rounding down produces a list that silently misses the target."""
    # One day, tiny target: the need is a fraction of one package.
    _deal(conn, "Chicken Breast 16 oz", 5.00, sold_by="UNIT")
    glist = service.grocery_list_for(_client(weight_kg=5.0), days=1, conn=conn)
    assert glist.items[0].quantity == 1.0           # not 0, not 0.1


def test_a_weight_sold_item_is_bought_by_weight_not_by_package(conn):
    """GFP-98's trap in a new place: price and size both describe ONE POUND."""
    _deal(conn, "Chicken Drumsticks, 1 lb", 1.49, sold_by="WEIGHT", uom="lb")
    item = service.grocery_list_for(_client(), days=7, conn=conn).items[0]

    assert item.quantity_unit == "lb"
    assert "lb" in item.quantity_label
    assert item.quantity > 1        # a week of protein is more than one pound


def test_the_cost_is_a_real_till_total_not_an_amortised_daily_figure(conn):
    """`bill.cost` is $/day and nobody can take that to a checkout."""
    _deal(conn, "Chicken Breast 16 oz", 5.00, sold_by="UNIT")
    glist = service.grocery_list_for(_client(), days=7, conn=conn)
    item = glist.items[0]

    assert item.estimated_cost == pytest.approx(item.shelf_price * item.quantity)
    assert glist.total_cost == pytest.approx(
        sum(i.estimated_cost for i in glist.items)
    )


def test_more_days_means_more_food_and_more_money(conn):
    _deal(conn, "Chicken Drumsticks, 1 lb", 1.49, sold_by="WEIGHT", uom="lb")
    week = service.grocery_list_for(_client(), days=7, conn=conn)
    fortnight = service.grocery_list_for(_client(), days=14, conn=conn)

    assert fortnight.items[0].quantity > week.items[0].quantity
    assert fortnight.total_cost > week.total_cost


# --------------------------------------------------------------------------- #
# Honesty
# --------------------------------------------------------------------------- #
def test_a_client_with_no_weight_gets_no_list_rather_than_a_guessed_one(conn):
    _deal(conn, "Chicken Breast 16 oz", 5.00)
    assert service.grocery_list_for(Customer.create("No Weight"), conn=conn) is None


def test_a_shortfall_is_carried_and_scaled_not_hidden(conn):
    """Available deals frequently cannot cover a full target."""
    glist = service.grocery_list_for(_client(), days=7, conn=conn)   # no deals at all
    assert glist.items == []
    assert not glist.is_complete
    assert glist.shortfall_grams > 0
    # Scaled to the period, not left as a daily figure.
    assert glist.shortfall_grams == pytest.approx(glist.target_grams_per_day * 7)


def test_the_list_records_when_it_was_generated(conn):
    """A grocery list with stale prices and no date on it is a trap."""
    _deal(conn, "Chicken Breast 16 oz", 5.00)
    glist = service.grocery_list_for(_client(), days=7, today="2026-08-03", conn=conn)
    assert glist.generated_on == "2026-08-03"
    assert glist.days == 7


def test_a_zero_or_negative_period_is_refused(conn):
    with pytest.raises(ValueError):
        service.grocery_list_for(_client(), days=0, conn=conn)


# --------------------------------------------------------------------------- #
# Grouping and provenance
# --------------------------------------------------------------------------- #
def test_items_are_grouped_by_store_because_you_shop_one_shop_at_a_time(conn):
    _deal(conn, "Chicken Breast 16 oz", 4.00, store="harristeeter")
    _deal(conn, "Chicken Thighs 16 oz", 9.00, store="wholefoods")
    _deal(conn, "Chicken Wings 16 oz", 5.00, store="harristeeter")

    glist = service.grocery_list_for(_client(), days=7, conn=conn)
    groups = glist.by_store()
    # One entry per store, not one per item.
    assert len(groups) == len({i.store for i in glist.items})
    for _label, items in groups:
        assert len({i.store for i in items}) == 1


def test_the_sku_and_link_reach_the_list(conn):
    """GFP-111's identifier and GFP-99's URL are why this list is actionable."""
    _deal(conn, "Chicken Breast 16 oz", 5.00, identifier="0020895500000",
          url="https://www.harristeeter.com/p/x/0020895500000")
    item = service.grocery_list_for(_client(), days=7, conn=conn).items[0]

    assert item.product_identifier == "0020895500000"
    assert item.product_identifier_ns == "kroger.product_id"
    assert item.source_url.startswith("https://www.harristeeter.com/")


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
@pytest.fixture
def rendered(conn):
    _deal(conn, "Chicken Drumsticks, 1 lb", 1.49, sold_by="WEIGHT", uom="lb",
          identifier="0020030200000",
          url="https://www.harristeeter.com/p/drumsticks/0020030200000")
    return service.grocery_list_for(_client(), days=7, today="2026-08-03", conn=conn)


def test_text_carries_store_item_quantity_price_and_a_bare_url(rendered):
    out = shoppingfmt.to_text(rendered)
    assert "Jane Doe" in out and "7 days from 2026-08-03" in out
    assert "HARRIS TEETER" in out
    assert "Chicken Drumsticks" in out
    assert "lb" in out and "$" in out
    assert "SKU 0020030200000" in out
    # A bare URL on its own line: most terminals linkify it, and it survives
    # copy/paste anywhere.
    assert "https://www.harristeeter.com/p/drumsticks/0020030200000" in out


def test_csv_quotes_item_names_that_contain_commas(rendered):
    """Real product names contain commas ("..., 1 lb") -- a naive join corrupts."""
    text = shoppingfmt.to_csv(rendered)
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == shoppingfmt.CSV_COLUMNS
    assert rows[1][1] == "Chicken Drumsticks, 1 lb"      # survived the round trip
    assert rows[1][7] == "0020030200000"                 # sku column


def test_html_has_real_clickable_anchors(rendered):
    """The whole reason HTML exists here -- the user called links a v1 imperative."""
    out = shoppingfmt.to_html(rendered)
    assert '<a href="https://www.harristeeter.com/p/drumsticks/0020030200000"' in out
    assert 'rel="noopener noreferrer"' in out
    assert "Buy now" not in out          # GFP-38's labelling rule
    assert shoppingfmt.LINK_TEXT in out


def test_html_escapes_names_that_contain_an_ampersand(conn):
    """"Bell & Evans" is a real brand; unescaped it corrupts the page."""
    # A size in the name, or savings.parse_size cannot price it and it never
    # reaches the bill at all -- which would make this test pass vacuously.
    _deal(conn, "Bell & Evans <b>Chicken</b> Breast 16 oz", 5.00)
    glist = service.grocery_list_for(_client(), days=7, conn=conn)
    assert glist.items, "the item must actually reach the list for this to test anything"
    out = shoppingfmt.to_html(glist)

    assert "Bell &amp; Evans" in out
    assert "&lt;b&gt;Chicken&lt;/b&gt;" in out      # markup neutralised, not executed


def test_an_item_with_no_link_simply_has_no_anchor(conn):
    """A missing link is not a reason to hide a real cheap item."""
    _deal(conn, "Chicken Breast 16 oz", 5.00)      # no url
    glist = service.grocery_list_for(_client(), days=7, conn=conn)

    assert "Chicken Breast" in shoppingfmt.to_html(glist)
    assert "<a href=" not in shoppingfmt.to_html(glist)
    assert "Chicken Breast" in shoppingfmt.to_text(glist)


def test_an_unknown_format_is_refused_by_name(rendered):
    with pytest.raises(ValueError, match="unknown format"):
        shoppingfmt.render(rendered, "ini")


def test_csv_is_written_with_a_bom_so_excel_on_windows_reads_it(rendered, tmp_path):
    """Plain UTF-8 gets mangled by Windows Excel; the BOM is harmless elsewhere."""
    path = shoppingfmt.write(rendered, tmp_path / "list.csv", "csv")
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")


def test_writing_creates_missing_directories(rendered, tmp_path):
    path = shoppingfmt.write(rendered, tmp_path / "nested" / "deep" / "l.html", "html")
    assert path.exists() and path.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_an_empty_list_says_so_in_every_format(conn):
    glist = service.grocery_list_for(_client(), days=7, conn=conn)   # no deals
    for fmt in shoppingfmt.RENDERERS:
        out = shoppingfmt.render(glist, fmt)
        assert out.strip(), f"{fmt} rendered nothing at all"
    assert "Nothing to buy" in shoppingfmt.to_text(glist)
    assert "Nothing to buy" in shoppingfmt.to_html(glist)
