"""GUI model tests (GFP-14). Skipped where the optional ``gui`` extra is absent
(e.g. CI, which installs only ``.[dev]``)."""
import inspect

import pytest

from grocery_planner import service

pytest.importorskip("PySide6", reason="GUI extra not installed")

from PySide6.QtCore import Qt  # noqa: E402

from grocery_planner.gui.app import DEAL_HEADERS, DealsTableModel  # noqa: E402


def _cell(model, row, col):
    return model.data(model.index(row, col))


def test_model_shape_matches_headers():
    model = DealsTableModel([])
    assert model.rowCount() == 0
    assert model.columnCount() == len(DEAL_HEADERS)


def test_model_formats_store_and_money():
    rows = [{
        "store": "foodlion",
        "item_name": "Whole Milk",
        "sub_category": "Dairy",
        "deal_type": "Weekly Ad",
        "sale_price": 2.5,
        "dollar_price": None,
        "valid_to": "2026-07-10",
    }]
    model = DealsTableModel(rows)
    cols = {key: i for i, (key, _) in enumerate(DEAL_HEADERS)}

    assert _cell(model, 0, cols["store"]) == "Food Lion"       # key -> display name
    assert _cell(model, 0, cols["sale_price"]) == "$2.50"       # money formatting
    assert _cell(model, 0, cols["dollar_price"]) == ""          # None money -> blank
    assert _cell(model, 0, cols["item_name"]) == "Whole Milk"


def _row(**overrides):
    row = {
        "store": "foodlion", "item_name": "Whole Milk", "sub_category": "Dairy",
        "deal_type": "Weekly Ad", "sale_price": 2.5, "dollar_price": None,
        "valid_to": "2026-07-10", "expired": 0,
    }
    row.update(overrides)
    return row


def test_expired_rows_are_marked_and_greyed():
    """GFP-16: stale deals stay visible when unhidden, but must look stale."""
    model = DealsTableModel([_row(expired=1), _row(expired=0)])
    valid_to = {key: i for i, (key, _) in enumerate(DEAL_HEADERS)}["valid_to"]

    assert _cell(model, 0, valid_to) == "2026-07-10 (expired)"
    assert _cell(model, 1, valid_to) == "2026-07-10"
    assert model.data(model.index(0, 0), Qt.ForegroundRole) is not None
    assert model.data(model.index(1, 0), Qt.ForegroundRole) is None


def test_model_tolerates_rows_without_an_expired_column():
    model = DealsTableModel([{k: v for k, v in _row().items() if k != "expired"}])
    assert model.data(model.index(0, 0), Qt.ForegroundRole) is None


# --------------------------------------------------------------------------- #
# GFP-17 — the filter bar must stay in lockstep with the service layer
# --------------------------------------------------------------------------- #
@pytest.fixture
def window(env_db, monkeypatch):
    """A MainWindow over an isolated DB, rendered offscreen."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from grocery_planner.gui.app import MainWindow

    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    yield win
    win.close()
    app.processEvents()


def test_every_control_maps_to_a_real_service_param(window):
    """The point of GFP-17: no widget may filter in a way the CLI cannot."""
    accepted = set(inspect.signature(service.fetch_deals).parameters)
    assert set(window.current_filters()) <= accepted
    # And the mapping actually drives a query without error.
    assert isinstance(service.fetch_deals(**window.current_filters()), list)


def test_filters_default_to_current_deals_only(window):
    filters = window.current_filters()
    assert filters["hide_expired"] is True     # GFP-16 default carries over
    assert filters["deal_type"] == "all"
    assert filters["category"] is None
    assert filters["search"] == ""
    assert filters["valid_on"] is None         # date picker off until enabled


def test_reset_filters_restores_the_defaults(window):
    window.search_edit.setText("chicken")
    window.on_sale_box.setChecked(True)
    window.hide_expired_box.setChecked(False)
    window.valid_on_box.setChecked(True)
    assert window.current_filters()["search"] == "chicken"

    window.reset_filters()
    filters = window.current_filters()
    assert filters["search"] == ""
    assert filters["on_sale"] is False
    assert filters["hide_expired"] is True
    assert filters["valid_on"] is None


# --------------------------------------------------------------------------- #
# GFP-11 — formula editor, schedule pane, progress, export
# --------------------------------------------------------------------------- #
def _entries(list_widget):
    """Real rows, ignoring the unselectable "nothing here yet" placeholder."""
    return [
        list_widget.item(i).text()
        for i in range(list_widget.count())
        if list_widget.item(i).flags() != Qt.NoItemFlags
    ]


def test_empty_lists_explain_themselves(window):
    assert _entries(window.formula_list) == []
    assert window.formula_list.count() == 1          # the placeholder
    assert "No formulas yet" in window.formula_list.item(0).text()
    assert "No automatic refresh set" in window.schedule_list.item(0).text()


def test_window_has_the_three_panes(window):
    assert [window.tabs.tabText(i) for i in range(window.tabs.count())] == [
        "Deals", "Formulas", "Schedule"
    ]


def test_progress_bar_is_hidden_until_a_scrape_runs(window):
    assert not window.progress.isVisible()
    assert window.progress.minimum() == window.progress.maximum() == 0  # indeterminate


def test_formula_editor_saves_and_lists(window):
    window.formula_name.setText("value")
    window.formula_expression.setText("1 / unit_price")
    window.on_formula_save()

    assert "Saved" in window.formula_message.text()
    assert _entries(window.formula_list) == ["value  =  1 / unit_price"]

    window.formula_list.setCurrentRow(0)
    assert window.formula_name.text() == "value"
    assert window.formula_expression.text() == "1 / unit_price"


def test_formula_editor_refuses_an_expression_that_cannot_evaluate(window):
    window.formula_name.setText("broken")
    window.formula_expression.setText("1 / nope(")
    window.on_formula_save()

    assert "Not saved" in window.formula_message.text()
    assert _entries(window.formula_list) == []  # nothing persisted


def test_formula_editor_requires_both_fields(window):
    window.formula_name.setText("")
    window.formula_expression.setText("1 / unit_price")
    window.on_formula_save()
    assert "name and an expression" in window.formula_message.text()


def test_formula_delete(window):
    window.formula_name.setText("temp")
    window.formula_expression.setText("price * 2")
    window.on_formula_save()
    assert len(_entries(window.formula_list)) == 1

    window.formula_name.setText("temp")
    window.on_formula_delete()
    assert _entries(window.formula_list) == []


def test_schedule_pane_saves_and_removes(window):
    window.schedule_every.setText("8h")
    window.on_schedule_save()
    assert "every 8h" in window.schedule_message.text()
    assert len(_entries(window.schedule_list)) == 1

    window.on_schedule_remove()
    assert "Removed" in window.schedule_message.text()
    assert _entries(window.schedule_list) == []


def test_schedule_pane_rejects_a_bad_cadence(window):
    window.schedule_every.setText("later")
    window.on_schedule_save()
    assert "Not saved" in window.schedule_message.text()
    assert _entries(window.schedule_list) == []


def test_export_writes_the_filtered_view(window, tmp_path, monkeypatch):
    from grocery_planner import db as _db

    conn = _db.connect()
    conn.execute(
        "INSERT INTO deals(store, item_name, dollar_price, valid_to, source) "
        "VALUES ('foodlion', '16 oz. Peanut Butter', 4.00, '2099-01-01', 'scrape')"
    )
    conn.commit()
    window.reload_deals()

    target = tmp_path / "out.csv"
    monkeypatch.setattr(
        "grocery_planner.gui.app.QFileDialog.getSaveFileName",
        lambda *a, **k: (str(target), "CSV files (*.csv)"),
    )
    window.on_export()

    text = target.read_text(encoding="utf-8")
    assert "16 oz. Peanut Butter" in text
    assert "unit_price" in text.splitlines()[0]   # GFP-8 columns ride along
    assert "Exported" in window.statusBar().currentMessage()


def test_export_cancelled_writes_nothing(window, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "grocery_planner.gui.app.QFileDialog.getSaveFileName", lambda *a, **k: ("", "")
    )
    window.on_export()
    assert list(tmp_path.glob("*.csv")) == []


def test_scrape_button_disabled_for_stores_without_a_scraper(window):
    window.store_box.setCurrentIndex(0)  # "All stores"
    assert window.current_filters()["store"] is None
    assert not window.scrape_btn.isEnabled()

    scrapable = window.store_box.findData("foodlion")
    window.store_box.setCurrentIndex(scrapable)
    assert window.scrape_btn.isEnabled()
