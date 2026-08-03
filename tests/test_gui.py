"""GUI tests. Skipped where the optional ``gui`` extra is absent (e.g. CI,
which installs only ``.[dev]``).

Rewritten for GFP-35, which retired the Deals/Formulas/Schedule tabs and the
deal-browsing table. What the old tests guarded — formula editing, schedule
editing, export, a scrape that only offers scrapable stores — is guarded here
against the dialogs those tabs became, plus the ticket's own acceptance
criterion: every one of those capabilities is still reachable from the menu bar.
"""
import pytest

pytest.importorskip("PySide6", reason="GUI extra not installed")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QTabWidget  # noqa: E402

from grocery_planner.gui import app as gui_app  # noqa: E402
from grocery_planner.gui.formulas import FormulaDialog  # noqa: E402
from grocery_planner.gui.schedule import ScheduleDialog  # noqa: E402
from grocery_planner.gui.scrape import ScrapeDialog  # noqa: E402


@pytest.fixture
def window(env_db, monkeypatch):
    """A MainWindow over an isolated DB, rendered offscreen."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    win = gui_app.MainWindow()
    yield win
    win.close()
    app.processEvents()


def _entries(list_widget):
    """Real rows, ignoring the unselectable "nothing here yet" placeholder."""
    return [
        list_widget.item(i).text()
        for i in range(list_widget.count())
        if list_widget.item(i).flags() != Qt.NoItemFlags
    ]


def _menu_actions(window, title):
    """The action texts of one top-level menu, minus separators."""
    for action in window.menuBar().actions():
        if action.text().replace("&", "") == title:
            return [
                a.text().replace("&", "")
                for a in action.menu().actions()
                if not a.isSeparator()
            ]
    raise AssertionError(f"no {title!r} menu")


# --------------------------------------------------------------------------- #
# GFP-35 — the tabs and the deal table are gone, and nothing went with them
# --------------------------------------------------------------------------- #
def test_the_tab_layout_is_gone(window):
    assert not isinstance(window.centralWidget(), QTabWidget)
    assert not hasattr(window, "tabs")


def test_the_deal_browsing_table_is_gone(window):
    for attribute in ("table", "model", "store_box", "category_box", "search_edit"):
        assert not hasattr(window, attribute), attribute
    assert not hasattr(gui_app, "DealsTableModel")
    assert not hasattr(gui_app, "DEAL_HEADERS")


def test_every_retired_control_is_reachable_from_the_menu_bar(window):
    """The GFP-35 acceptance criterion, asserted directly."""
    titles = [a.text().replace("&", "") for a in window.menuBar().actions()]
    assert titles == ["File", "Data", "Settings"]

    assert "Export deals…" in _menu_actions(window, "File")
    assert "Run scrape…" in _menu_actions(window, "Data")
    assert _menu_actions(window, "Settings") == ["Formulas…", "Automatic refresh…"]


def test_menu_actions_open_their_dialogs(window):
    window.scrape_action.trigger()
    window.formulas_action.trigger()
    window.schedule_action.trigger()

    assert isinstance(window._dialogs["scrape"], ScrapeDialog)
    assert isinstance(window._dialogs["formulas"], FormulaDialog)
    assert isinstance(window._dialogs["schedule"], ScheduleDialog)


def test_reopening_a_dialog_reuses_the_same_instance(window):
    first = window.open_formulas()
    assert window.open_formulas() is first


# --------------------------------------------------------------------------- #
# Formulas (GFP-11 behaviour, GFP-35 location)
# --------------------------------------------------------------------------- #
@pytest.fixture
def formulas_dialog(window):
    return window.open_formulas()


def test_empty_formula_list_explains_itself(formulas_dialog):
    assert _entries(formulas_dialog.formula_list) == []
    assert formulas_dialog.formula_list.count() == 1          # the placeholder
    assert "No formulas yet" in formulas_dialog.formula_list.item(0).text()


def test_formula_editor_saves_and_lists(formulas_dialog):
    formulas_dialog.formula_name.setText("value")
    formulas_dialog.formula_expression.setText("1 / unit_price")
    formulas_dialog.on_formula_save()

    assert "Saved" in formulas_dialog.formula_message.text()
    assert _entries(formulas_dialog.formula_list) == ["value  =  1 / unit_price"]

    formulas_dialog.formula_list.setCurrentRow(0)
    assert formulas_dialog.formula_name.text() == "value"
    assert formulas_dialog.formula_expression.text() == "1 / unit_price"


def test_formula_editor_refuses_an_expression_that_cannot_evaluate(formulas_dialog):
    formulas_dialog.formula_name.setText("broken")
    formulas_dialog.formula_expression.setText("1 / nope(")
    formulas_dialog.on_formula_save()

    assert "Not saved" in formulas_dialog.formula_message.text()
    assert _entries(formulas_dialog.formula_list) == []  # nothing persisted


def test_formula_editor_requires_both_fields(formulas_dialog):
    formulas_dialog.formula_name.setText("")
    formulas_dialog.formula_expression.setText("1 / unit_price")
    formulas_dialog.on_formula_save()
    assert "name and an expression" in formulas_dialog.formula_message.text()


def test_formula_editor_still_accepts_the_protein_target_formula(formulas_dialog):
    """GFP-64 in its new home: protein_factor editing must survive GFP-35."""
    formulas_dialog.formula_name.setText("protein_target_daily")
    formulas_dialog.formula_expression.setText("weight_kg * protein_factor")
    formulas_dialog.on_formula_save()
    assert "Saved" in formulas_dialog.formula_message.text()


def test_formula_delete(formulas_dialog):
    formulas_dialog.formula_name.setText("temp")
    formulas_dialog.formula_expression.setText("price * 2")
    formulas_dialog.on_formula_save()
    assert len(_entries(formulas_dialog.formula_list)) == 1

    formulas_dialog.formula_name.setText("temp")
    formulas_dialog.on_formula_delete()
    assert _entries(formulas_dialog.formula_list) == []


def test_ranking_previews_in_the_dialog_now_the_deals_table_is_gone(formulas_dialog):
    from grocery_planner import db

    conn = db.connect()
    conn.execute(
        "INSERT INTO deals(store, item_name, dollar_price, valid_to, source) "
        "VALUES ('foodlion', '16 oz. Peanut Butter', 4.00, '2099-01-01', 'scrape')"
    )
    conn.commit()

    formulas_dialog.formula_name.setText("value")
    formulas_dialog.formula_expression.setText("1 / unit_price")
    formulas_dialog.on_formula_save()
    formulas_dialog.on_formula_rank()

    assert any("Peanut Butter" in row for row in _entries(formulas_dialog.ranked_list))
    assert "Top 1 deals" in formulas_dialog.formula_message.text()


def test_ranking_an_unsaved_formula_says_so(formulas_dialog):
    formulas_dialog.formula_name.setText("never-saved")
    formulas_dialog.on_formula_rank()
    assert "Save 'never-saved' first" in formulas_dialog.formula_message.text()


# --------------------------------------------------------------------------- #
# Automatic refresh (GFP-7/GFP-11 behaviour, GFP-35 location)
# --------------------------------------------------------------------------- #
@pytest.fixture
def schedule_dialog(window):
    return window.open_schedule()


def test_empty_schedule_list_explains_itself(schedule_dialog):
    assert _entries(schedule_dialog.schedule_list) == []
    assert "No automatic refresh set" in schedule_dialog.schedule_list.item(0).text()


def test_schedule_dialog_saves_and_removes(schedule_dialog):
    schedule_dialog.schedule_every.setText("8h")
    schedule_dialog.on_schedule_save()
    assert "every 8h" in schedule_dialog.schedule_message.text()
    assert len(_entries(schedule_dialog.schedule_list)) == 1

    schedule_dialog.on_schedule_remove()
    assert "Removed" in schedule_dialog.schedule_message.text()
    assert _entries(schedule_dialog.schedule_list) == []


def test_schedule_dialog_rejects_a_bad_cadence(schedule_dialog):
    schedule_dialog.schedule_every.setText("later")
    schedule_dialog.on_schedule_save()
    assert "Not saved" in schedule_dialog.schedule_message.text()
    assert _entries(schedule_dialog.schedule_list) == []


# --------------------------------------------------------------------------- #
# Run scrape (GFP-14 behaviour, GFP-35 location)
# --------------------------------------------------------------------------- #
@pytest.fixture
def scrape_dialog(window):
    return window.open_scrape()


def test_scrape_dialog_offers_only_stores_with_a_scraper(scrape_dialog):
    from grocery_planner import service

    offered = [scrape_dialog.store_box.itemData(i)
               for i in range(scrape_dialog.store_box.count())]
    assert offered == list(service.available_scrapers())
    assert None not in offered              # no "All stores" row to mis-click
    assert scrape_dialog.scrape_btn.isEnabled()


def test_there_are_no_rows_until_a_scrape_runs(scrape_dialog):
    """GFP-103: the single shared progress bar became one bar per run."""
    assert scrape_dialog._rows == {}
    assert scrape_dialog.running_stores == []


def test_force_is_off_by_default_and_reaches_the_worker(scrape_dialog):
    """GFP-71 left run_tracked_scrape(force=) with no GUI control; this is it."""
    from grocery_planner.gui.scrape import ScrapeWorker

    assert not scrape_dialog.force_box.isChecked()
    assert ScrapeWorker("foodlion")._force is False

    scrape_dialog.force_box.setChecked(True)
    assert ScrapeWorker("foodlion", force=scrape_dialog.force_box.isChecked())._force


def test_a_finished_scrape_reaches_the_status_bar(window, scrape_dialog):
    scrape_dialog._row_for("foodlion")
    scrape_dialog._on_done("foodlion", {"stats": {"total": 7, "weekly_ad": 5,
                                                  "digital_coupons": 2}})
    assert "Stored 7 deals" in scrape_dialog._rows["foodlion"].status.text()
    assert "Stored 7 deals" in window.statusBar().currentMessage()
    # GFP-103: with several runs in flight a bare summary names no store.
    assert "Food Lion" in window.statusBar().currentMessage()
    assert not scrape_dialog._rows["foodlion"].running


def test_a_failed_scrape_reaches_the_status_bar(window, scrape_dialog):
    scrape_dialog._row_for("foodlion")
    scrape_dialog._on_failed("foodlion", "flyer 404")
    assert "Scrape failed: flyer 404" in window.statusBar().currentMessage()
    assert "Scrape failed: flyer 404" in scrape_dialog._rows["foodlion"].status.text()
    assert scrape_dialog.scrape_btn.isEnabled()   # re-armed, not left dead


# --------------------------------------------------------------------------- #
# Export (GFP-11 behaviour, GFP-35 location)
# --------------------------------------------------------------------------- #
def test_export_writes_the_current_deals(window, tmp_path, monkeypatch):
    from grocery_planner import db

    conn = db.connect()
    conn.execute(
        "INSERT INTO deals(store, item_name, dollar_price, valid_to, source) "
        "VALUES ('foodlion', '16 oz. Peanut Butter', 4.00, '2099-01-01', 'scrape')"
    )
    conn.commit()

    target = tmp_path / "out.csv"
    monkeypatch.setattr(
        "grocery_planner.gui.app.QFileDialog.getSaveFileName",
        lambda *a, **k: (str(target), "CSV files (*.csv)"),
    )
    window.export_action.trigger()

    text = target.read_text(encoding="utf-8")
    assert "16 oz. Peanut Butter" in text
    assert "unit_price" in text.splitlines()[0]   # GFP-8 columns ride along
    assert "Exported" in window.statusBar().currentMessage()


def test_export_leaves_out_expired_deals(window, tmp_path, monkeypatch):
    """Exporting prices that are no longer on offer is worse than exporting none."""
    from grocery_planner import db

    conn = db.connect()
    conn.execute(
        "INSERT INTO deals(store, item_name, dollar_price, valid_to, source) "
        "VALUES ('foodlion', 'Stale Bread', 1.00, '2000-01-01', 'scrape')"
    )
    conn.commit()

    target = tmp_path / "out.csv"
    monkeypatch.setattr(
        "grocery_planner.gui.app.QFileDialog.getSaveFileName",
        lambda *a, **k: (str(target), "CSV files (*.csv)"),
    )
    window.on_export()
    assert "Stale Bread" not in target.read_text(encoding="utf-8")


def test_export_cancelled_writes_nothing(window, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "grocery_planner.gui.app.QFileDialog.getSaveFileName", lambda *a, **k: ("", "")
    )
    window.on_export()
    assert list(tmp_path.glob("*.csv")) == []


# --------------------------------------------------------------------------- #
# GFP-36 — client roster, price trends, and navigation between them
# --------------------------------------------------------------------------- #
def _add(name, weight=None, unit=None, **kwargs):
    from grocery_planner import db
    from grocery_planner.customers import Customer, CustomerRepository

    return CustomerRepository.save(
        Customer.create(name, weight=weight, weight_unit=unit, **kwargs),
        conn=db.connect(),
    )


def test_the_centre_is_the_roster_and_the_trends_pane(window):
    assert window.stack.currentWidget() is window.split
    assert window.split.widget(0) is window.roster
    assert window.split.widget(1) is window.trends


def test_empty_roster_explains_itself(window):
    assert _entries(window.roster.client_list) == []
    assert "No clients yet" in window.roster.client_list.item(0).text()


def test_roster_lists_clients_with_their_protein_target(window):
    _add("Ana Ruiz", 62.0, "kg")
    window.roster.reload()
    assert _entries(window.roster.client_list) == ["Ana Ruiz — 99 g/day  (62 kg)"]


def test_roster_shows_the_weight_in_the_unit_it_was_entered_in(window):
    """GFP-28's rule reaches the screen: never silently redisplay lb as kg."""
    _add("Ben Okafor", 195.0, "lb")
    window.roster.reload()
    assert "(195 lb)" in _entries(window.roster.client_list)[0]


def test_a_client_with_no_weight_gets_no_invented_target(window):
    _add("Dev Patel")
    window.roster.reload()
    assert _entries(window.roster.client_list) == ["Dev Patel — weight not on file"]


def test_roster_search_filters(window):
    _add("Ana Ruiz", 62.0, "kg")
    _add("Ben Okafor", 88.0, "kg")

    window.roster.search_edit.setText("ruiz")
    assert len(_entries(window.roster.client_list)) == 1

    window.roster.search_edit.setText("zzz")
    assert _entries(window.roster.client_list) == []
    assert "No clients match" in window.roster.client_list.item(0).text()


# --- keyboard navigation (the ticket names it) ----------------------------- #
def _press(widget, key):
    """Deliver a real key press to a widget, event filters and all."""
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    QApplication.sendEvent(widget, QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier))


def test_down_from_the_search_field_steps_into_the_list(window):
    _add("Ana Ruiz", 62.0, "kg")
    _add("Ben Okafor", 88.0, "kg")
    roster = window.roster
    roster.reload()
    roster.search_edit.setFocus()

    _press(roster.search_edit, Qt.Key_Down)
    # focusWidget(), not hasFocus(): the window is never activated offscreen,
    # so nothing holds real keyboard focus — but the window still tracks which
    # widget it would go to, which is what this behaviour is about.
    assert window.focusWidget() is roster.client_list
    assert roster.client_list.currentRow() == 0


def test_down_in_an_empty_roster_leaves_the_search_field_alone(window):
    """Nothing to step into, so the key stays with the field it was typed in."""
    roster = window.roster
    roster.search_edit.setFocus()

    _press(roster.search_edit, Qt.Key_Down)
    assert window.focusWidget() is roster.search_edit


def test_enter_on_a_single_match_opens_that_client(window):
    ana = _add("Ana Ruiz", 62.0, "kg")
    _add("Ben Okafor", 88.0, "kg")

    window.roster.search_edit.setText("ruiz")
    window.roster.on_search_entered()

    assert window.stack.currentWidget() is window.client_page
    assert window.client_page.customer_id == ana.id


def test_enter_on_several_matches_moves_into_the_list_instead_of_guessing(window):
    _add("Ana Ruiz", 62.0, "kg")
    _add("Ana Silva", 70.0, "kg")

    window.roster.search_edit.setText("ana")
    window.roster.on_search_entered()

    assert window.stack.currentWidget() is window.split   # nothing was opened
    assert window.focusWidget() is window.roster.client_list


def test_the_placeholder_row_is_never_selectable(window):
    """Enter on "No clients yet" must not emit a selection."""
    seen = []
    window.roster.client_selected.connect(seen.append)
    window.roster.on_item_activated(window.roster.client_list.item(0))
    assert seen == []


# --- navigation ------------------------------------------------------------ #
def test_selecting_a_client_opens_the_detail_page(window):
    ana = _add("Ana Ruiz", 62.0, "kg")
    window.roster.reload()
    window.roster.client_selected.emit(ana.id)

    assert window.stack.currentWidget() is window.client_page
    assert window.client_page.name_label.text() == "Ana Ruiz"
    assert "99 g/day" in window.client_page.target_label.text()


def test_back_returns_to_the_roster(window):
    ana = _add("Ana Ruiz", 62.0, "kg")
    window.roster.reload()
    window.show_client(ana.id)

    window.client_page.back_btn.click()
    assert window.stack.currentWidget() is window.split


def test_opening_a_deleted_client_stays_on_the_roster(window):
    from grocery_planner import db
    from grocery_planner.customers import CustomerRepository

    ana = _add("Ana Ruiz", 62.0, "kg")
    CustomerRepository.delete(ana.id, conn=db.connect())

    assert window.show_client(ana.id) is False
    assert window.stack.currentWidget() is window.split
    assert "no longer on file" in window.statusBar().currentMessage()


def test_a_client_with_no_weight_gets_no_target_on_the_detail_page(window):
    dev = _add("Dev Patel")
    window.show_client(dev.id)
    assert "no protein target" in window.client_page.target_label.text()


# --- add client ------------------------------------------------------------ #
def test_add_client_dialog_saves_and_converts_pounds(window):
    from grocery_planner.customers import KG_PER_LB
    from grocery_planner.gui.roster import AddClientDialog

    dialog = AddClientDialog(window)
    dialog.name_edit.setText("Ben Okafor")
    dialog.weight_spin.setValue(195.0)
    dialog.unit_box.setCurrentIndex(dialog.unit_box.findData("lb"))
    dialog.on_save()

    assert dialog.saved is not None
    # Stored canonical, echoed back in the unit that was typed.
    assert dialog.saved.weight_kg == pytest.approx(195.0 * KG_PER_LB)
    assert dialog.saved.weight_display == pytest.approx(195.0)


def test_add_client_requires_a_name(window):
    from grocery_planner.gui.roster import AddClientDialog

    dialog = AddClientDialog(window)
    dialog.weight_spin.setValue(70.0)
    dialog.on_save()

    assert dialog.saved is None
    assert "needs a name" in dialog.message.text()


def test_a_weightless_client_can_still_be_added(window):
    """Intake often starts before a weigh-in; the roster must not require one."""
    from grocery_planner.gui.roster import AddClientDialog

    dialog = AddClientDialog(window)
    dialog.name_edit.setText("Dev Patel")
    dialog.on_save()                      # weight left at 0 == "not on file"

    assert dialog.saved.weight_kg is None
    assert dialog.saved.weight_unit is None


def test_adding_a_client_clears_the_search_so_they_are_visible(window, monkeypatch):
    from PySide6.QtWidgets import QDialog

    from grocery_planner.gui import roster as roster_module

    _add("Ana Ruiz", 62.0, "kg")
    window.roster.search_edit.setText("zzz")   # a filter that hides everything

    def fake_exec(self):
        self.name_edit.setText("Ben Okafor")
        self.on_save()
        return QDialog.Accepted

    monkeypatch.setattr(roster_module.AddClientDialog, "exec", fake_exec)
    window.roster.on_add_client()

    assert window.roster.search_edit.text() == ""
    assert len(_entries(window.roster.client_list)) == 2
    assert "Added Ben Okafor" in window.roster.message.text()


# --- trends ---------------------------------------------------------------- #
def _seed_protein_history(days):
    """One priced, protein-resolvable item per day for Food Lion."""
    from datetime import date, timedelta

    from grocery_planner import db

    conn = db.connect()
    cur = conn.execute(
        "INSERT INTO foods(name, slug, category, source) "
        "VALUES ('Test chicken', 'test-chicken-gui', 'chicken', 'usda')"
    )
    food_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO food_nutrients(food_id, nutrient, amount_per_100g) "
        "VALUES (?, 'protein', 25.0)", (food_id,)
    )
    conn.execute(
        "INSERT INTO deal_food_match(store, item_name, food_id, confidence, method) "
        "VALUES ('foodlion', 'Chicken Breast 16 oz', ?, 0.9, 'test')", (food_id,)
    )
    for offset in range(days):
        conn.execute(
            "INSERT INTO price_history"
            "(store, postal_code, item_name, deal_type, dollar_price, source, captured_at) "
            "VALUES ('foodlion', '27401', 'Chicken Breast 16 oz', 'Weekly Ad', ?, 'test', ?)",
            (5.0 + offset * 0.25,
             (date.today() - timedelta(days=offset)).isoformat()),
        )
    conn.commit()


def test_trends_pane_hides_the_chart_when_there_is_nothing_to_plot(window):
    """The ticket's second acceptance criterion."""
    assert not window.trends.trend.is_plottable
    assert not window.trends.chart.isVisible()
    assert "No protein prices on record yet" in window.trends.subtitle.text()


def test_trends_pane_plots_once_there_are_enough_days(window):
    _seed_protein_history(days=4)
    window.trends.reload()

    assert window.trends.trend.is_plottable
    assert window.trends.chart.isVisibleTo(window.trends)
    assert "Lower is better" in window.trends.subtitle.text()
    assert "foodlion" in [s.key for s in window.trends.trend.plottable]


def test_the_latest_prices_are_listed_even_when_the_chart_cannot_be_drawn(window):
    """Refusing to plot is not a reason to withhold the number we do have."""
    _seed_protein_history(days=1)
    window.trends.reload()

    assert not window.trends.trend.is_plottable
    assert "Only 1 day" in window.trends.subtitle.text()
    assert "Food Lion" in window.trends.latest.text()
    assert window.trends.latest.isVisible() or window.trends.latest.isVisibleTo(window.trends)


def test_one_series_needs_no_legend_box(window):
    _seed_protein_history(days=4)
    window.trends.reload()
    assert not window.trends.legend.isVisibleTo(window.trends)


def test_series_colour_follows_the_store_not_its_rank(window):
    """A store that becomes cheapest must not repaint itself a different colour."""
    chart = window.trends.chart
    before = {key: chart.colour_for(key).name()
              for key in ("foodlion", "wholefoods", "harristeeter")}
    assert len(set(before.values())) == 3          # distinct per store

    _seed_protein_history(days=4)
    window.trends.reload()
    after = {key: chart.colour_for(key).name()
             for key in ("foodlion", "wholefoods", "harristeeter")}
    assert after == before


def test_endpoint_labels_are_pushed_apart_when_values_nearly_coincide():
    """Two stores a fraction of a cent apart must not print on top of each other."""
    from grocery_planner.gui.trends import _spread

    spread = _spread([100.0, 101.0, 102.0], gap=14.0, top=0.0, bottom=300.0)
    assert all(b - a >= 14.0 - 1e-9 for a, b in zip(sorted(spread), sorted(spread)[1:]))


def test_spread_keeps_labels_inside_the_plot(window):
    from grocery_planner.gui.trends import _spread

    spread = _spread([0.0, 1.0, 2.0], gap=20.0, top=0.0, bottom=50.0)
    assert min(spread) >= 0.0 - 1e-9
    assert max(spread) <= 50.0 + 1e-9


def test_spread_preserves_input_order(window):
    from grocery_planner.gui.trends import _spread

    spread = _spread([200.0, 100.0, 150.0], gap=14.0, top=0.0, bottom=300.0)
    assert spread[0] > spread[2] > spread[1]   # same relative order as the input
