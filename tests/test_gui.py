"""GUI model tests (GFP-14). Skipped where the optional ``gui`` extra is absent
(e.g. CI, which installs only ``.[dev]``)."""
import pytest

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
