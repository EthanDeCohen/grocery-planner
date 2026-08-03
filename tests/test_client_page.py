"""Client detail page: the three columns and the wiring between them.

Covers GFP-52 (daily bill panel), GFP-38 (where-to-buy column) and GFP-37
(the page that joins them, and the recompute chain the ticket asks for).

Mirrors tests/test_gui.py's conventions: PySide6 is optional, everything runs
offscreen, and nothing is ever shown -- so visibility assertions use
``isVisibleTo`` rather than ``isVisible``.
"""
import pytest

pytest.importorskip("PySide6", reason="GUI extra not installed")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from grocery_planner import bill, db, preferences  # noqa: E402
from grocery_planner.customers import Customer, CustomerRepository  # noqa: E402
from grocery_planner.gui.client import ClientDetailPage  # noqa: E402
from grocery_planner.gui.wheretobuy import LINK_TEXT  # noqa: E402


@pytest.fixture
def page(env_db, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    widget = ClientDetailPage()
    yield widget
    widget.close()
    app.processEvents()


def _client(name="Ana Ruiz", weight=62.0, unit="kg"):
    return CustomerRepository.save(
        Customer.create(name, weight=weight, weight_unit=unit), conn=db.connect()
    )


def _food(name, category, protein_per_100g):
    conn = db.connect()
    cur = conn.execute(
        "INSERT INTO foods(name, slug, category, source) VALUES (?, ?, ?, 'usda')",
        (name, name.lower().replace(" ", "-"), category),
    )
    food_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO food_nutrients(food_id, nutrient, amount_per_100g) "
        "VALUES (?, 'protein', ?)", (food_id, protein_per_100g)
    )
    conn.commit()
    return food_id


def _deal(store, item_name, price, food_id, source_url=None):
    conn = db.connect()
    conn.execute(
        "INSERT INTO deal_food_match(store, item_name, food_id, confidence, method) "
        "VALUES (?, ?, ?, 0.9, 'test')", (store, item_name, food_id)
    )
    conn.execute(
        "INSERT INTO deals(store, item_name, dollar_price, valid_to, source, source_url) "
        "VALUES (?, ?, ?, '2099-01-01', 'scrape', ?)",
        (store, item_name, price, source_url),
    )
    conn.commit()


def _entries(list_widget):
    return [
        list_widget.item(i).text()
        for i in range(list_widget.count())
        if list_widget.item(i).flags() != Qt.NoItemFlags
    ]


def _where_to_buy_texts(pane):
    layout = pane.rows_layout
    return [
        layout.itemAt(i).widget().findChildren(type(pane.title))[-1].text()
        for i in range(layout.count())
    ]


# --------------------------------------------------------------------------- #
# GFP-37 — the page has three columns and loads a client into all of them
# --------------------------------------------------------------------------- #
def test_the_page_has_the_three_columns(page):
    assert page.columns.count() == 3
    assert page.columns.widget(0) is page.biometrics
    assert page.columns.widget(1) is page.bill_panel
    assert page.columns.widget(2) is page.where_to_buy


def test_loading_a_client_fills_every_column(page):
    food_id = _food("Test Chicken", "chicken", 25.0)
    _deal("foodlion", "Chicken Breast 16 oz", 5.00, food_id)
    ana = _client()

    assert page.show_client(ana.id) is True
    assert page.name_label.text() == "Ana Ruiz"
    assert "109 g/day" in page.target_label.text()
    assert page.biometrics.customer.id == ana.id
    assert page.bill_panel.customer_id == ana.id
    assert len(_entries(page.bill_panel.lines_list)) == 1
    assert page.where_to_buy.rows_layout.count() == 1


def test_a_missing_client_clears_every_column(page):
    food_id = _food("Test Chicken", "chicken", 25.0)
    _deal("foodlion", "Chicken Breast 16 oz", 5.00, food_id)
    ana = _client()
    page.show_client(ana.id)

    assert page.show_client(999999) is False
    assert page.biometrics.customer is None
    assert page.bill_panel.customer_id is None
    assert page.where_to_buy.rows_layout.count() == 0


def test_a_biometric_save_recomputes_the_bill_immediately(page):
    """GFP-37's acceptance criterion: an edit flows through to the bill."""
    food_id = _food("Test Chicken", "chicken", 25.0)
    _deal("foodlion", "Chicken Breast 16 oz", 5.00, food_id)
    ana = _client()
    page.show_client(ana.id)
    before = page.bill_panel.comparison.constrained.total_cost

    page.biometrics.weight_spin.setValue(100.0)   # 176 g/day instead of 99
    page.biometrics.on_save()

    assert "176 g/day" in page.target_label.text()
    assert page.bill_panel.comparison.constrained.total_cost > before


def test_renaming_a_client_updates_the_page_header(page):
    _client()
    ana = CustomerRepository.list(conn=db.connect())[0]
    page.show_client(ana.id)

    page.biometrics.name_edit.setText("Ana Ruiz-Marsh")
    page.biometrics.on_save()
    assert page.name_label.text() == "Ana Ruiz-Marsh"


# --------------------------------------------------------------------------- #
# GFP-52 — the daily bill panel
# --------------------------------------------------------------------------- #
def test_nothing_checked_shows_the_baseline_not_an_empty_basket(page):
    """preferences.py's rule reaching the screen."""
    food_id = _food("Test Chicken", "chicken", 25.0)
    _deal("foodlion", "Chicken Breast 16 oz", 5.00, food_id)
    ana = _client()
    page.show_client(ana.id)

    assert preferences.list_preferences(ana.id, conn=db.connect()) == []
    assert page.bill_panel.comparison.is_constrained is False
    assert len(_entries(page.bill_panel.lines_list)) == 1       # not empty
    assert "Cheapest way to hit the target" in page.bill_panel.comparison_label.text()


def test_a_checkbox_recomputes_and_persists_with_no_save_step(page):
    beef_id = _food("Test Beef", "beef", 25.0)
    chicken_id = _food("Test Chicken", "chicken", 25.0)
    _deal("foodlion", "Beef Roast 16 oz", 2.00, beef_id)
    _deal("foodlion", "Chicken Breast 16 oz", 8.00, chicken_id)
    ana = _client()
    page.show_client(ana.id)
    assert "Beef Roast 16 oz" in _entries(page.bill_panel.lines_list)[0]

    page.bill_panel._boxes["chicken"].setChecked(True)

    # Recomputed...
    assert "Chicken Breast 16 oz" in _entries(page.bill_panel.lines_list)[0]
    # ...and stored, with no Save button anywhere in this panel.
    assert preferences.list_preferences(ana.id, conn=db.connect()) == ["chicken"]
    assert page.bill_panel.comparison.is_constrained is True
    assert page.bill_panel.comparison.delta_cost > 0


def test_stored_preferences_are_reflected_when_a_client_loads(page):
    _food("Test Chicken", "chicken", 25.0)
    ana = _client()
    preferences.set_preferences(ana.id, ["chicken"], conn=db.connect())

    page.show_client(ana.id)
    assert page.bill_panel._boxes["chicken"].isChecked()
    assert not page.bill_panel._boxes["beef"].isChecked()


def test_the_categories_come_from_the_data_not_a_hard_coded_list(page):
    from grocery_planner import nutrition

    assert set(page.bill_panel._boxes) == set(
        nutrition.list_categories(conn=db.connect())
    )


def test_the_amortisation_note_is_always_on_screen(page):
    """A bare dollar figure must never be readable as a checkout total."""
    assert page.bill_panel.amortisation_label.text() == bill.AMORTIZATION_NOTE
    assert "not a same-day shopping total" in page.bill_panel.amortisation_label.text()


def test_a_starving_preference_shows_the_caveat_not_a_saving(page):
    """The GFP-49 trap, on screen: cheaper because it buys less protein."""
    beef_id = _food("Test Beef", "beef", 25.0)
    tofu_id = _food("Test Tofu", "tofu", 8.0)
    _deal("foodlion", "Beef Roast 16 oz", 4.00, beef_id)
    _deal("foodlion", "Tofu Block 4 oz", 2.00, tofu_id)
    ana = _client()
    page.show_client(ana.id)

    page.bill_panel._boxes["tofu"].setChecked(True)

    assert page.bill_panel.comparison.is_comparable is False
    assert page.bill_panel.caveat_label.isVisibleTo(page.bill_panel)
    assert "buys less protein" in page.bill_panel.caveat_label.text()
    assert "short" in page.bill_panel.footer.text()


def test_a_client_with_no_weight_gets_no_bill_rather_than_a_zero(page):
    dev = CustomerRepository.save(Customer.create("Dev Patel"), conn=db.connect())
    assert page.show_client(dev.id) is True          # the page still opens
    assert page.bill_panel.comparison is None
    assert page.bill_panel.headline.text() == "—"
    assert "no bill to compute" in page.bill_panel.comparison_label.text()


def test_each_line_carries_a_store_tag(page):
    food_id = _food("Test Chicken", "chicken", 25.0)
    _deal("harristeeter", "Chicken Breast 16 oz", 5.00, food_id)
    ana = _client()
    page.show_client(ana.id)

    assert _entries(page.bill_panel.lines_list)[0].startswith("[Harris Teeter]")


def test_unpriceable_deals_are_reported_in_the_footer(page):
    food_id = _food("Test Chicken", "chicken", 25.0)
    _deal("foodlion", "Chicken Breast 16 oz", 5.00, food_id)
    conn = db.connect()          # one connection: two would deadlock on the write
    conn.execute(
        "INSERT INTO deals(store, item_name, dollar_price, valid_to, source) "
        "VALUES ('foodlion', 'Mystery Value Pack', 3.00, '2099-01-01', 'scrape')"
    )
    conn.commit()
    ana = _client()
    page.show_client(ana.id)

    assert "could not be priced per gram of protein" in page.bill_panel.footer.text()


# --------------------------------------------------------------------------- #
# GFP-38 — where to buy
# --------------------------------------------------------------------------- #
def test_a_captured_link_renders_as_view_ad_never_buy_now(page):
    food_id = _food("Test Chicken", "chicken", 25.0)
    _deal("foodlion", "Chicken Breast 16 oz", 5.00, food_id,
          source_url="https://flipp.com/flyer/1/flyer_item/2")
    ana = _client()
    page.show_client(ana.id)

    texts = " ".join(_where_to_buy_texts(page.where_to_buy))
    assert LINK_TEXT == "View ad"
    assert "View ad" in texts
    assert "Buy" not in texts
    assert "flipp.com" in texts          # a real href, not a dead control
    assert page.where_to_buy.linked_count == 1


def test_a_missing_link_degrades_to_plain_text(page):
    """The ticket's criterion — and today the only path, since nothing populates
    source_url: the Flipp scraper is the sole writer and Kroger/Whole Foods
    write None."""
    food_id = _food("Test Chicken", "chicken", 25.0)
    _deal("foodlion", "Chicken Breast 16 oz", 5.00, food_id)   # no source_url
    ana = _client()
    page.show_client(ana.id)

    texts = " ".join(_where_to_buy_texts(page.where_to_buy))
    assert "no ad link captured" in texts
    assert "View ad" not in texts
    assert "<a href" not in texts
    assert page.where_to_buy.linked_count == 0


def test_where_to_buy_follows_a_preference_change(page):
    beef_id = _food("Test Beef", "beef", 25.0)
    chicken_id = _food("Test Chicken", "chicken", 25.0)
    _deal("foodlion", "Beef Roast 16 oz", 2.00, beef_id)
    _deal("harristeeter", "Chicken Breast 16 oz", 8.00, chicken_id)
    ana = _client()
    page.show_client(ana.id)
    assert "Food Lion" in " ".join(_where_to_buy_texts(page.where_to_buy))

    page.bill_panel._boxes["chicken"].setChecked(True)
    assert "Harris Teeter" in " ".join(_where_to_buy_texts(page.where_to_buy))


def test_re_rendering_does_not_leave_duplicate_rows_behind(page):
    """A detached-but-undeleted row stayed painted, showing each item twice."""
    food_id = _food("Test Chicken", "chicken", 25.0)
    _deal("foodlion", "Chicken Breast 16 oz", 5.00, food_id)
    ana = _client()

    page.show_client(ana.id)
    page.show_client(ana.id)
    page.show_client(ana.id)

    assert page.where_to_buy.rows_layout.count() == 1


def test_where_to_buy_empties_when_the_bill_does(page):
    _food("Test Tofu", "tofu", 8.0)
    ana = _client()
    page.show_client(ana.id)          # no deals at all
    assert page.where_to_buy.rows_layout.count() == 0
    assert "nowhere to buy" in page.where_to_buy.subtitle.text()
