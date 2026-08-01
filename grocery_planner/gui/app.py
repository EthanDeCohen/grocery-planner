"""PySide6 desktop shell (GFP-14) — the first usable GUI slice of GFP-11.

A minimal window over the front-end-agnostic core (``grocery_planner.service``):
pick a store, run a scrape on a background thread, and view the resulting deals
in a table. No Excel and no CLI needed. The full GUI — formula editor, schedule
settings, per-job progress, Export to Excel — is GFP-11.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import (
    QAbstractTableModel,
    QDate,
    QModelIndex,
    QObject,
    Qt,
    QThread,
    Signal,
)
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
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

        # --- Action bar: pick a store, scrape it, refresh the view ---------- #
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Store:"))
        self.store_box = QComboBox()
        self._populate_stores()
        self.store_box.currentIndexChanged.connect(self.on_store_changed)
        bar.addWidget(self.store_box)

        self.scrape_btn = QPushButton("Run scrape now")
        self.scrape_btn.clicked.connect(self.on_scrape)
        bar.addWidget(self.scrape_btn)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.reload_deals)
        bar.addWidget(self.refresh_btn)
        bar.addStretch(1)
        layout.addLayout(bar)

        # --- Filter bar (GFP-17) -------------------------------------------- #
        # Every control here maps 1:1 onto a service.fetch_deals() parameter —
        # see current_filters(). Nothing filters rows in the model.
        filters = QHBoxLayout()

        filters.addWidget(QLabel("Category:"))
        self.category_box = QComboBox()
        # Category names run long ("... Brand Feature (price not listed)"); cap the
        # box so it can't starve the search field of width.
        self.category_box.setMinimumContentsLength(16)
        self.category_box.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.category_box.setMaximumWidth(240)
        self.category_box.currentIndexChanged.connect(self.reload_deals)
        filters.addWidget(self.category_box)

        filters.addWidget(QLabel("Type:"))
        self.type_box = QComboBox()
        for key, (label, _) in service.DEAL_TYPE_GROUPS.items():
            self.type_box.addItem(label, key)
        self.type_box.currentIndexChanged.connect(self.reload_deals)
        filters.addWidget(self.type_box)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search item or description…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumWidth(200)
        self.search_edit.textChanged.connect(self.reload_deals)
        filters.addWidget(self.search_edit, 1)

        self.on_sale_box = QCheckBox("On sale only")
        self.on_sale_box.toggled.connect(self.reload_deals)
        filters.addWidget(self.on_sale_box)

        self.loyalty_box = QCheckBox("Loyalty")
        self.loyalty_box.setToolTip("Only deals that require the store's loyalty card.")
        self.loyalty_box.toggled.connect(self.reload_deals)
        filters.addWidget(self.loyalty_box)

        # GFP-16: stale deals are hidden by default; unchecking greys them in.
        self.hide_expired_box = QCheckBox("Hide expired")
        self.hide_expired_box.setChecked(True)
        self.hide_expired_box.setToolTip("Hide deals whose valid-to date has passed.")
        self.hide_expired_box.toggled.connect(self.reload_deals)
        filters.addWidget(self.hide_expired_box)

        self.valid_on_box = QCheckBox("Valid on:")
        self.valid_on_box.setToolTip("Show only deals on offer on the chosen date.")
        self.valid_on_box.toggled.connect(self.on_valid_on_toggled)
        filters.addWidget(self.valid_on_box)
        self.valid_on_edit = QDateEdit(QDate.currentDate())
        self.valid_on_edit.setCalendarPopup(True)
        self.valid_on_edit.setDisplayFormat("yyyy-MM-dd")
        self.valid_on_edit.setEnabled(False)
        self.valid_on_edit.dateChanged.connect(self.reload_deals)
        filters.addWidget(self.valid_on_edit)

        self.reset_btn = QPushButton("Reset filters")
        self.reset_btn.clicked.connect(self.reset_filters)
        filters.addWidget(self.reset_btn)
        layout.addLayout(filters)

        self.table = QTableView()
        self.model = DealsTableModel()
        self.table.setModel(self.model)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        layout.addWidget(self.table)

        self._thread: QThread | None = None
        self._worker: ScrapeWorker | None = None
        self.on_store_changed()  # fills categories, sets scrape availability, loads rows

    # ----------------------------------------------------------------- #
    # Filter plumbing (GFP-17)
    # ----------------------------------------------------------------- #
    def _populate_stores(self) -> None:
        """Offer every store that has deals, plus any that can be scraped.

        CSV-only stores (Whole Foods today) belong in the filter even though
        no scraper exists for them yet.
        """
        keys = sorted(set(service.available_scrapers()) | set(service.stores_with_deals()))
        self.store_box.addItem("All stores", None)
        for key in keys:
            store = BY_KEY.get(key)
            self.store_box.addItem(store.display_name if store else key, key)
        if self.store_box.count() > 1:
            self.store_box.setCurrentIndex(1)

    def _populate_categories(self) -> None:
        """Refill the category list for the selected store, keeping the choice if it survives."""
        previous = self.category_box.currentData()
        self.category_box.blockSignals(True)
        self.category_box.clear()
        self.category_box.addItem("All categories", None)
        for name in service.deal_categories(store=self.current_store()):
            self.category_box.addItem(name, name)
        index = self.category_box.findData(previous)
        self.category_box.setCurrentIndex(max(index, 0))
        self.category_box.blockSignals(False)

    def current_filters(self) -> dict:
        """The filter bar's state as service.fetch_deals() keyword arguments.

        This is the whole contract between the widgets and the core: add a
        control, map it here, and the CLI flag of the same name behaves alike.
        """
        return {
            "store": self.current_store(),
            "category": self.category_box.currentData(),
            "deal_type": self.type_box.currentData() or "all",
            "search": self.search_edit.text().strip(),
            "on_sale": self.on_sale_box.isChecked(),
            "loyalty_only": self.loyalty_box.isChecked(),
            "hide_expired": self.hide_expired_box.isChecked(),
            "valid_on": (
                self.valid_on_edit.date().toString("yyyy-MM-dd")
                if self.valid_on_box.isChecked() else None
            ),
        }

    def on_store_changed(self) -> None:
        store = self.current_store()
        # Only stores with a registered scraper can be refreshed from the ad.
        self.scrape_btn.setEnabled(bool(store) and store in service.available_scrapers())
        self._populate_categories()
        self.reload_deals()

    def on_valid_on_toggled(self, checked: bool) -> None:
        self.valid_on_edit.setEnabled(checked)
        self.reload_deals()

    def reset_filters(self) -> None:
        """Clear every filter back to its default in one shot."""
        for widget in (self.category_box, self.type_box, self.search_edit,
                       self.on_sale_box, self.loyalty_box, self.hide_expired_box,
                       self.valid_on_box):
            widget.blockSignals(True)
        self.category_box.setCurrentIndex(0)
        self.type_box.setCurrentIndex(0)
        self.search_edit.clear()
        self.on_sale_box.setChecked(False)
        self.loyalty_box.setChecked(False)
        self.hide_expired_box.setChecked(True)
        self.valid_on_box.setChecked(False)
        self.valid_on_edit.setEnabled(False)
        for widget in (self.category_box, self.type_box, self.search_edit,
                       self.on_sale_box, self.loyalty_box, self.hide_expired_box,
                       self.valid_on_box):
            widget.blockSignals(False)
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
        self._populate_categories()  # a fresh ad can introduce new categories
        self.reload_deals()

    def _on_scrape_failed(self, message: str) -> None:
        self.statusBar().showMessage(f"Scrape failed: {message}", 10000)
        self.scrape_btn.setEnabled(True)

    def reload_deals(self) -> None:
        filters = self.current_filters()
        rows = service.fetch_deals(**filters)
        self.model.set_rows(rows)

        message = f"{len(rows)} deals for {self.store_box.currentText()}"
        # "of N" only means something once a filter has actually narrowed things.
        unfiltered = service.count_deals(store=filters["store"])
        if len(rows) != unfiltered:
            message += f" (of {unfiltered})"
        if filters["hide_expired"]:
            hidden = service.count_deals(**{**filters, "hide_expired": False}) - len(rows)
            if hidden:
                message += f" — {hidden} expired hidden"
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
