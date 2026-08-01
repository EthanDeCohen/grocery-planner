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


def test_scrape_button_disabled_for_stores_without_a_scraper(window):
    window.store_box.setCurrentIndex(0)  # "All stores"
    assert window.current_filters()["store"] is None
    assert not window.scrape_btn.isEnabled()

    scrapable = window.store_box.findData("foodlion")
    window.store_box.setCurrentIndex(scrapable)
    assert window.scrape_btn.isEnabled()
