"""GUI model tests (GFP-14). Skipped where the optional ``gui`` extra is absent
(e.g. CI, which installs only ``.[dev]``)."""
import pytest

pytest.importorskip("PySide6", reason="GUI extra not installed")

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
