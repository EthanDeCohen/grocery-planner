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


def test_the_empty_centre_says_what_it_is_and_where_the_controls_went(window):
    text = window.placeholder_label.text()
    assert "client roster" in text
    for pointer in ("Run scrape", "Formulas", "Automatic refresh", "Export deals"):
        assert pointer in text


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


def test_progress_bar_is_hidden_until_a_scrape_runs(scrape_dialog):
    assert not scrape_dialog.progress.isVisible()
    assert scrape_dialog.progress.minimum() == scrape_dialog.progress.maximum() == 0


def test_force_is_off_by_default_and_reaches_the_worker(scrape_dialog):
    """GFP-71 left run_tracked_scrape(force=) with no GUI control; this is it."""
    from grocery_planner.gui.scrape import ScrapeWorker

    assert not scrape_dialog.force_box.isChecked()
    assert ScrapeWorker("foodlion")._force is False

    scrape_dialog.force_box.setChecked(True)
    assert ScrapeWorker("foodlion", force=scrape_dialog.force_box.isChecked())._force


def test_a_finished_scrape_reaches_the_status_bar(window, scrape_dialog):
    scrape_dialog._on_scrape_done({"stats": {"total": 7, "weekly_ad": 5,
                                             "digital_coupons": 2}})
    assert "Stored 7 deals" in scrape_dialog.message.text()
    assert "Stored 7 deals" in window.statusBar().currentMessage()
    assert not scrape_dialog.progress.isVisible()


def test_a_failed_scrape_reaches_the_status_bar(window, scrape_dialog):
    scrape_dialog._on_scrape_failed("flyer 404")
    assert "Scrape failed: flyer 404" in window.statusBar().currentMessage()
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
