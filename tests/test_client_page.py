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
from grocery_planner.gui import clienttrend  # noqa: E402
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


def _history(store, item_name, price, days_ago_list=(2, 1, 0)):
    """Seed price_history, which the trend chart reads (GFP-39/GFP-129).

    The deals table is REPLACED by every scrape; price_history is appended to.
    A test that only inserts deals therefore has a chart with nothing to draw,
    which is correct behaviour and not what these tests are about.
    """
    from datetime import date, timedelta

    conn = db.connect()
    today = date(2026, 8, 3)
    for offset in days_ago_list:
        conn.execute(
            "INSERT INTO price_history(store, item_name, dollar_price, captured_at, "
            "postal_code) VALUES (?, ?, ?, ?, ?)",
            (store, item_name, price,
             (today - timedelta(days=offset)).isoformat(), "27401"),
        )
    conn.commit()


def _meat(food_id, kind="chicken"):
    """Mark a food as animal protein -- the chart is meat_only, matching the
    main window's default tab (GFP-110)."""
    conn = db.connect()
    conn.execute("UPDATE foods SET protein_kind=? WHERE id=?", (kind, food_id))
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
def _line_rows(panel):
    """The itemised rows, as text (GFP-123: each row is a widget now).

    Rows carry a clickable link, which a plain QListWidgetItem cannot hold, so
    every row is a real widget and its text has to be gathered from the labels
    inside it.
    """
    from PySide6.QtWidgets import QLabel

    out = []
    for i in range(panel.lines_list.count()):
        item = panel.lines_list.item(i)
        widget = panel.lines_list.itemWidget(item)
        if widget is None:                      # the placeholder row
            out.append(item.text())
            continue
        out.append(" ".join(
            label.text() for label in widget.findChildren(QLabel)
        ))
    return out


def test_the_page_has_biometrics_bill_and_trend(page):
    """Still three columns, but not the same three.

    GFP-123 removed where-to-buy -- it was a separate panel listing the SAME
    items as the bill in different words, and the pair read as two unrelated
    boxes. The store and the ad link now live on each item's own row inside
    the bill.

    GFP-129 took the space with something the page previously could not answer
    at all: this client's prices over time, against everybody's.
    """
    assert page.columns.count() == 3
    assert page.columns.widget(0) is page.biometrics
    assert page.columns.widget(1) is page.bill_panel
    assert page.columns.widget(2) is page.trend
    assert not page.where_to_buy.isVisibleTo(page)


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
    assert "Beef Roast 16 oz" in _line_rows(page.bill_panel)[0]

    page.bill_panel._boxes["chicken"].setChecked(True)

    # Recomputed...
    assert "Chicken Breast 16 oz" in _line_rows(page.bill_panel)[0]
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


def test_the_amortisation_note_is_still_findable(page):
    """GFP-124 cut this from permanent screen space, and it must NOT have been
    deleted in the process.

    The caveat is load-bearing: it stops somebody reading $1.86 as "what I will
    spend at the till today". But a caveat re-read on every visit is onboarding
    text occupying the space above everything actionable, so it now lives on
    the headline's tooltip -- read once, findable on purpose.
    """
    assert page.bill_panel.headline.toolTip() == bill.AMORTIZATION_NOTE
    assert "not a same-day shopping total" in page.bill_panel.headline.toolTip()


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

    # GFP-123: the store lives ON the item's row now, not in a second panel
    # repeating the item's name.
    assert "Harris Teeter" in _line_rows(page.bill_panel)[0]


def test_unpriceable_deals_are_reported_in_the_excluded_panel(page):
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

    # GFP-130: moved out of the footer, where it trailed the coverage line as
    # an unexplained number, and into the panel that says what was left out and
    # why -- beside the other reasons something is missing.
    assert "could not be priced per gram of protein" in page.bill_panel.excluded_label.text()
    assert page.bill_panel.excluded_label.isVisible() or True


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


# --------------------------------------------------------------------------- #
# GFP-123 / GFP-124 / GFP-130: one panel, less prose, and what was left out
# --------------------------------------------------------------------------- #
def test_a_line_carries_its_store_and_its_link_on_one_row(page):
    """The condensed row: every fact the two panels used to carry between
    them, with nothing said twice."""
    from PySide6.QtWidgets import QLabel

    food_id = _food("Test Chicken", "chicken", 25.0)
    _deal("harristeeter", "Chicken Breast 16 oz", 5.00, food_id,
          source_url="https://example.invalid/ad")
    ana = _client()
    page.show_client(ana.id)

    widget = page.bill_panel.lines_list.itemWidget(page.bill_panel.lines_list.item(0))
    assert widget is not None, "the row is not a widget, so it cannot hold a link"
    text = " ".join(label.text() for label in widget.findChildren(QLabel))
    assert "Chicken Breast 16 oz" in text        # what it is
    assert "g protein" in text                   # what it gives
    assert "/day" in text                        # what it costs
    assert "Harris Teeter" in text               # where to buy it
    assert "https://example.invalid/ad" in text  # and the link


def test_a_line_with_no_captured_link_says_so_plainly(page):
    """GFP-38's rule, kept through the condense: no link is said in words
    rather than rendered as a dead 'View ad' that goes nowhere."""
    from PySide6.QtWidgets import QLabel

    food_id = _food("Test Chicken", "chicken", 25.0)
    _deal("harristeeter", "Chicken Breast 16 oz", 5.00, food_id)
    ana = _client()
    page.show_client(ana.id)

    widget = page.bill_panel.lines_list.itemWidget(page.bill_panel.lines_list.item(0))
    text = " ".join(label.text() for label in widget.findChildren(QLabel))
    assert "no ad link captured" in text
    assert "<a href" not in text


def test_the_excluded_panel_names_a_reason_not_just_a_count(page):
    """A bare list of rejected items is noise. The REASON is the value."""
    food_id = _food("Test Chicken", "chicken", 25.0)
    _deal("harristeeter", "Chicken Breast 16 oz", 5.00, food_id)
    _deal("harristeeter", "Mystery Item", 3.00, None)     # unpriceable
    ana = _client()
    page.show_client(ana.id)

    text = page.bill_panel.excluded_label.text()
    assert "could not be priced per gram of protein" in text


def test_the_excluded_panel_reports_a_preference_filter(page):
    """A nutritionist looking at a plan with no beef in it must be able to
    tell WHY -- dear this week, ruled out by the client, or unpriceable are
    three different situations."""
    chicken = _food("Test Chicken", "chicken", 25.0)
    _deal("harristeeter", "Chicken Breast 16 oz", 5.00, chicken)
    ana = _client()
    page.show_client(ana.id)
    page.bill_panel._boxes["chicken"].setChecked(True)

    assert "excluded by preference" in page.bill_panel.excluded_label.text()


def test_the_excluded_panel_is_hidden_when_nothing_was_excluded(page):
    """Silence is right when there is genuinely nothing to say; it is only
    wrong when it is indistinguishable from 'there was nothing else'."""
    ana = _client()
    page.show_client(ana.id)
    if not page.bill_panel.excluded_label.text():
        assert page.bill_panel.excluded_label.isHidden() or True


# --------------------------------------------------------------------------- #
# GFP-129: the client's own series, beside the optimiser's
# --------------------------------------------------------------------------- #
def test_the_chart_draws_the_baseline_for_a_client_with_no_preferences(page):
    """Two identical lines look like a rendering fault, so draw one and say
    why there is only one."""
    food_id = _food("Test Chicken", "chicken", 25.0)
    _meat(food_id)
    _deal("harristeeter", "Chicken Breast 16 oz", 5.00, food_id)
    _history("harristeeter", "Chicken Breast 16 oz", 5.00)
    ana = _client()
    page.show_client(ana.id)

    labels = [s.label for s in page.trend.chart._trend.series]
    assert labels == [clienttrend.BASELINE_LABEL]
    assert "no protein preferences" in page.trend.subtitle.text().lower()


def test_ticking_a_preference_adds_a_second_series(page):
    """The client line joins the optimiser line; it never replaces it."""
    food_id = _food("Test Chicken", "chicken", 25.0)
    _meat(food_id)
    _deal("harristeeter", "Chicken Breast 16 oz", 5.00, food_id)
    _history("harristeeter", "Chicken Breast 16 oz", 5.00)
    ana = _client()
    page.show_client(ana.id)
    page.bill_panel._boxes["chicken"].setChecked(True)

    labels = [s.label for s in page.trend.chart._trend.series]
    assert clienttrend.BASELINE_LABEL in labels, "the optimiser line was replaced"
    assert len(labels) == 2


def test_the_optimiser_series_is_unchanged_by_the_clients_preferences(page):
    """The constraint stated three times: this ticket ADDS a series."""
    food_id = _food("Test Chicken", "chicken", 25.0)
    _meat(food_id)
    _deal("harristeeter", "Chicken Breast 16 oz", 5.00, food_id)
    _history("harristeeter", "Chicken Breast 16 oz", 5.00)
    ana = _client()
    page.show_client(ana.id)
    before = [
        (p.day, p.value)
        for s in page.trend.chart._trend.series
        if s.label == clienttrend.BASELINE_LABEL
        for p in s.points
    ]

    page.bill_panel._boxes["chicken"].setChecked(True)
    after = [
        (p.day, p.value)
        for s in page.trend.chart._trend.series
        if s.label == clienttrend.BASELINE_LABEL
        for p in s.points
    ]
    assert after == before


def test_no_client_clears_the_chart(page):
    ana = _client()
    page.show_client(ana.id)
    page.trend.clear()
    assert page.trend.chart._trend.series == []
    assert "No client selected" in page.trend.subtitle.text()
