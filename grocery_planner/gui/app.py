"""PySide6 desktop shell (GFP-14) — the first usable GUI slice of GFP-11.

A minimal window over the front-end-agnostic core (``grocery_planner.service``):
pick a store, run a scrape on a background thread, and view the resulting deals
in a table. No Excel and no CLI needed. The full GUI — formula editor, schedule
settings, per-job progress, Export to Excel — is GFP-11.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt, QThread, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .. import service
from ..scrapers import SCRAPERS
from ..stores import BY_KEY

# (row key, column header). Mirrors service.fetch_deals()'s SELECT.
DEAL_HEADERS: list[tuple[str, str]] = [
    ("store", "Store"),
    ("item_name", "Item"),
    ("sub_category", "Category"),
    ("deal_type", "Type"),
    ("sale_price", "Sale"),
    ("dollar_price", "Price"),
    ("valid_to", "Valid to"),
]
_MONEY_KEYS = {"sale_price", "dollar_price"}
_EXPIRED_FG = QColor(150, 150, 150)  # greyed out: stale but still shown (GFP-16)


class DealsTableModel(QAbstractTableModel):
    """Read-only table model over a list of ``deals`` rows (sqlite3.Row / mapping)."""

    def __init__(self, rows: list | None = None) -> None:
        super().__init__()
        self._rows = list(rows or [])

    def set_rows(self, rows: list) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(DEAL_HEADERS)

    def _is_expired(self, row: int) -> bool:
        try:
            return bool(self._rows[row]["expired"])
        except (IndexError, KeyError):
            return False

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):  # noqa: N802
        if not index.isValid():
            return None
        if role == Qt.ForegroundRole:
            return QBrush(_EXPIRED_FG) if self._is_expired(index.row()) else None
        if role != Qt.DisplayRole:
            return None
        key = DEAL_HEADERS[index.column()][0]
        value = self._rows[index.row()][key]
        if key == "store":
            store = BY_KEY.get(value)
            return store.display_name if store else value
        if key in _MONEY_KEYS:
            return f"${value:.2f}" if isinstance(value, (int, float)) else ""
        if key == "valid_to" and value and self._is_expired(index.row()):
            return f"{value} (expired)"
        return "" if value is None else str(value)

    def headerData(self, section: int, orientation: int, role: int = Qt.DisplayRole):  # noqa: N802
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return DEAL_HEADERS[section][1]
        return None


class ScrapeWorker(QObject):
    """Runs a scrape off the UI thread; opens its own DB connection there."""

    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, store_key: str) -> None:
        super().__init__()
        self._store_key = store_key

    def run(self) -> None:
        try:
            result = service.run_scrape(self._store_key)
        except Exception as exc:  # surface any failure to the UI thread
            self.failed.emit(str(exc))
        else:
            self.finished.emit(result)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Grocery Planner")
        self.resize(920, 560)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Store:"))
        self.store_box = QComboBox()
        for key in service.available_scrapers():
            store = BY_KEY.get(key)
            self.store_box.addItem(store.display_name if store else key, key)
        self.store_box.currentIndexChanged.connect(self.reload_deals)
        bar.addWidget(self.store_box)

        self.scrape_btn = QPushButton("Run scrape now")
        self.scrape_btn.clicked.connect(self.on_scrape)
        bar.addWidget(self.scrape_btn)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.reload_deals)
        bar.addWidget(self.refresh_btn)

        # GFP-16: stale deals are hidden by default; unchecking greys them in.
        self.hide_expired_box = QCheckBox("Hide expired")
        self.hide_expired_box.setChecked(True)
        self.hide_expired_box.setToolTip("Hide deals whose valid-to date has passed.")
        self.hide_expired_box.toggled.connect(self.reload_deals)
        bar.addWidget(self.hide_expired_box)
        bar.addStretch(1)
        layout.addLayout(bar)

        self.table = QTableView()
        self.model = DealsTableModel()
        self.table.setModel(self.model)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        layout.addWidget(self.table)

        self._thread: QThread | None = None
        self._worker: ScrapeWorker | None = None
        self.reload_deals()

    def current_store(self) -> str | None:
        return self.store_box.currentData()

    def on_scrape(self) -> None:
        store = self.current_store()
        if not store:
            return
        self.scrape_btn.setEnabled(False)
        self.statusBar().showMessage(f"Scraping {self.store_box.currentText()} …")

        self._thread = QThread(self)
        self._worker = ScrapeWorker(store)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_scrape_done)
        self._worker.failed.connect(self._on_scrape_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _on_scrape_done(self, result: dict) -> None:
        stats = result.get("stats", {})
        self.statusBar().showMessage(
            f"Stored {stats.get('total', '?')} deals — "
            f"{stats.get('weekly_ad', '?')} weekly ad, "
            f"{stats.get('digital_coupons', '?')} coupons.",
            8000,
        )
        self.scrape_btn.setEnabled(True)
        self.reload_deals()

    def _on_scrape_failed(self, message: str) -> None:
        self.statusBar().showMessage(f"Scrape failed: {message}", 10000)
        self.scrape_btn.setEnabled(True)

    def reload_deals(self) -> None:
        store = self.current_store()
        hide_expired = self.hide_expired_box.isChecked()
        rows = service.fetch_deals(store=store, hide_expired=hide_expired)
        self.model.set_rows(rows)

        message = f"{len(rows)} deals for {self.store_box.currentText()}"
        if hide_expired:
            hidden = service.count_deals(store=store) - len(rows)
            if hidden:
                message += f" ({hidden} expired hidden)"
        else:
            stale = sum(1 for r in rows if r["expired"])
            if stale:
                message += f" — {stale} expired"
        self.statusBar().showMessage(message + ".")


def main() -> int:
    """Launch the desktop app; returns the Qt exit code."""
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
