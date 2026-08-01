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
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from simpleeval import simple_eval

from .. import db, formulas, jobs, scheduler, service
from ..scrapers import SCRAPERS
from ..stores import BY_KEY

# Stand-in values used to validate a formula before it is saved: every variable
# score_deals() supplies, so a typo is caught at Save rather than at Rank.
_FORMULA_PROBE = {
    "price": 1.0, "sale_price": 1.0, "unit_price": 1.0,
    "quantity": 1.0, "saved_percent": 1.0,
}


def _placeholder(text: str) -> QListWidgetItem:
    """An unselectable "nothing here yet" row, so an empty list explains itself."""
    item = QListWidgetItem(text)
    item.setFlags(Qt.NoItemFlags)
    return item

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
            # Tracked, so a GUI scrape lands in `gplan jobs` like a scheduled
            # one and an interrupted run is visible after a crash (GFP-7).
            result = jobs.run_tracked_scrape(self._store_key)
        except Exception as exc:  # surface any failure to the UI thread
            self.failed.emit(str(exc))
        else:
            self.finished.emit(result)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Grocery Planner")
        self.resize(920, 560)

        # Deals stay the front door; formulas and schedule are their own tabs so
        # the browsing view never gets crowded out (GFP-11).
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        central = QWidget()
        self.tabs.addTab(central, "Deals")
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

        self.export_btn = QPushButton("Export…")
        self.export_btn.setToolTip("Save the deals currently shown to a CSV file.")
        self.export_btn.clicked.connect(self.on_export)
        bar.addWidget(self.export_btn)

        # Indeterminate: Flipp gives no progress signal, so a moving bar is the
        # honest amount of information — "working", not a fake percentage.
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setMaximumWidth(140)
        self.progress.hide()
        bar.addWidget(self.progress)
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

        self._build_formulas_tab()
        self._build_schedule_tab()

        self._thread: QThread | None = None
        self._worker: ScrapeWorker | None = None
        self.on_store_changed()  # fills categories, sets scrape availability, loads rows

    # ----------------------------------------------------------------- #
    # Formulas tab (GFP-11) — the GFP-8 scoring engine, made editable
    # ----------------------------------------------------------------- #
    def _build_formulas_tab(self) -> None:
        page = QWidget()
        self.tabs.addTab(page, "Formulas")
        layout = QVBoxLayout(page)

        layout.addWidget(QLabel(
            "Expressions scored against each deal (price, unit_price, quantity, "
            "saved_percent) and your profile values."
        ))

        self.formula_list = QListWidget()
        self.formula_list.setMaximumHeight(220)
        self.formula_list.currentItemChanged.connect(self.on_formula_selected)
        layout.addWidget(self.formula_list)

        form = QHBoxLayout()
        form.addWidget(QLabel("Name:"))
        self.formula_name = QLineEdit()
        self.formula_name.setMaximumWidth(200)
        form.addWidget(self.formula_name)
        form.addWidget(QLabel("Expression:"))
        self.formula_expression = QLineEdit()
        self.formula_expression.setPlaceholderText("1 / unit_price")
        form.addWidget(self.formula_expression, 1)
        layout.addLayout(form)

        actions = QHBoxLayout()
        self.formula_save_btn = QPushButton("Save")
        self.formula_save_btn.clicked.connect(self.on_formula_save)
        actions.addWidget(self.formula_save_btn)
        self.formula_delete_btn = QPushButton("Delete")
        self.formula_delete_btn.clicked.connect(self.on_formula_delete)
        actions.addWidget(self.formula_delete_btn)
        self.formula_rank_btn = QPushButton("Rank deals with this")
        self.formula_rank_btn.clicked.connect(self.on_formula_rank)
        actions.addWidget(self.formula_rank_btn)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.formula_message = QLabel("")
        self.formula_message.setWordWrap(True)
        layout.addWidget(self.formula_message)
        self.reload_formulas()

    def reload_formulas(self) -> None:
        self.formula_list.clear()
        rows = formulas.list_formulas(db.connect())
        if not rows:
            self.formula_list.addItem(_placeholder(
                "No formulas yet — name one below and Save."
            ))
            return
        for row in rows:
            item = QListWidgetItem(f"{row['name']}  =  {row['expression']}")
            item.setData(Qt.UserRole, (row["name"], row["expression"]))
            self.formula_list.addItem(item)

    def on_formula_selected(self, current, _previous=None) -> None:
        if current is None or current.data(Qt.UserRole) is None:
            return  # the placeholder row carries no formula
        name, expression = current.data(Qt.UserRole)
        self.formula_name.setText(name)
        self.formula_expression.setText(expression)

    def on_formula_save(self) -> None:
        name = self.formula_name.text().strip()
        expression = self.formula_expression.text().strip()
        if not name or not expression:
            self.formula_message.setText("Give the formula a name and an expression.")
            return
        # Validate before storing: a formula that cannot evaluate is not saved.
        try:
            simple_eval(expression, names=_FORMULA_PROBE)
        except Exception as exc:
            self.formula_message.setText(f"Not saved — {type(exc).__name__}: {exc}")
            return
        formulas.set_formula(db.connect(), name, expression)
        self.formula_message.setText(f"Saved {name!r}.")
        self.reload_formulas()

    def on_formula_delete(self) -> None:
        name = self.formula_name.text().strip()
        if not name:
            return
        conn = db.connect()
        conn.execute("DELETE FROM formulas WHERE name=?", (name,))
        conn.commit()
        self.formula_name.clear()
        self.formula_expression.clear()
        self.formula_message.setText(f"Deleted {name!r}.")
        self.reload_formulas()

    def on_formula_rank(self) -> None:
        """Score the current deal selection with this formula and show it."""
        name = self.formula_name.text().strip()
        if not name:
            return
        try:
            ranked = service.best_deals(
                limit=200, score_with=name, **self.current_filters()
            )
        except KeyError:
            self.formula_message.setText(f"Save {name!r} first.")
            return
        self.model.set_rows(ranked)
        self.tabs.setCurrentIndex(0)
        self.statusBar().showMessage(
            f"{len(ranked)} deals ranked by {name!r}. Refresh returns to the full list."
        )

    # ----------------------------------------------------------------- #
    # Schedule tab (GFP-11 over GFP-7)
    # ----------------------------------------------------------------- #
    def _build_schedule_tab(self) -> None:
        page = QWidget()
        self.tabs.addTab(page, "Schedule")
        layout = QVBoxLayout(page)

        layout.addWidget(QLabel(
            "Automatic refresh. The cadence is stored in the database, so it "
            "survives restarts; run `gplan schedule run` to keep it ticking."
        ))

        form = QHBoxLayout()
        form.addWidget(QLabel("Store:"))
        self.schedule_store_box = QComboBox()
        for key in service.available_scrapers():
            store = BY_KEY.get(key)
            self.schedule_store_box.addItem(store.display_name if store else key, key)
        form.addWidget(self.schedule_store_box)

        form.addWidget(QLabel("Every:"))
        self.schedule_every = QLineEdit("12h")
        self.schedule_every.setMaximumWidth(90)
        self.schedule_every.setToolTip("Interval such as 30m, 6h or 2d.")
        form.addWidget(self.schedule_every)

        self.schedule_save_btn = QPushButton("Save schedule")
        self.schedule_save_btn.clicked.connect(self.on_schedule_save)
        form.addWidget(self.schedule_save_btn)
        self.schedule_remove_btn = QPushButton("Remove")
        self.schedule_remove_btn.clicked.connect(self.on_schedule_remove)
        form.addWidget(self.schedule_remove_btn)
        form.addStretch(1)
        layout.addLayout(form)

        self.schedule_list = QListWidget()
        layout.addWidget(self.schedule_list)

        layout.addWidget(QLabel("Recent automatic runs:"))
        self.jobs_list = QListWidget()
        layout.addWidget(self.jobs_list, 1)

        self.schedule_message = QLabel("")
        self.schedule_message.setWordWrap(True)
        layout.addWidget(self.schedule_message)
        self.reload_schedules()

    def reload_schedules(self) -> None:
        conn = db.connect()
        self.schedule_list.clear()
        rows = scheduler.list_schedules(conn)
        if not rows:
            self.schedule_list.addItem(_placeholder(
                "No automatic refresh set — pick a store and an interval above."
            ))
        for row in rows:
            upcoming = scheduler.next_run(row["kind"], row["expression"])
            last = jobs.last_success(conn, row["store"])
            parts = [f"{row['store']} — {scheduler.describe(row['kind'], row['expression'])}"]
            parts.append(f"next {upcoming:%Y-%m-%d %H:%M}" if upcoming else "next unknown")
            parts.append(
                f"last success {last:%Y-%m-%d %H:%M}" if last else "never run"
            )
            self.schedule_list.addItem("; ".join(parts))
        self.jobs_list.clear()
        history = jobs.recent_jobs(conn, limit=15)
        if not history:
            self.jobs_list.addItem(_placeholder("Nothing has run automatically yet."))
        for row in history:
            self.jobs_list.addItem(
                f"[{row['status']}] {row['source']} — {(row['started_at'] or '')[:16]}"
                f" — {row['message'] or row['last_checkpoint'] or ''}"
            )

    def on_schedule_save(self) -> None:
        store = self.schedule_store_box.currentData()
        expression = self.schedule_every.text().strip()
        try:
            scheduler.set_schedule(db.connect(), store, scheduler.INTERVAL, expression)
        except (scheduler.ScheduleError, service.UnknownStoreError) as exc:
            self.schedule_message.setText(f"Not saved — {exc}")
            return
        self.schedule_message.setText(f"{store} will refresh every {expression}.")
        self.reload_schedules()

    def on_schedule_remove(self) -> None:
        store = self.schedule_store_box.currentData()
        removed = scheduler.remove_schedule(db.connect(), store)
        self.schedule_message.setText(
            f"Removed the schedule for {store}." if removed else f"No schedule for {store}."
        )
        self.reload_schedules()

    # ----------------------------------------------------------------- #
    # Export (GFP-11)
    # ----------------------------------------------------------------- #
    def on_export(self) -> None:
        """Write the current filtered view to CSV.

        CSV, not .xlsx: GFP-13 retired the Excel dependency and a CSV opens in
        Excel anyway.
        """
        path, _selected = QFileDialog.getSaveFileName(
            self, "Export deals", "deals.csv", "CSV files (*.csv)"
        )
        if not path:
            return
        try:
            written = service.export_deals(path, **self.current_filters())
        except OSError as exc:
            self.statusBar().showMessage(f"Export failed: {exc}", 10000)
            return
        self.statusBar().showMessage(f"Exported {written} deals to {path}.", 8000)

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
        self.progress.show()
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
        self.progress.hide()
        self._populate_categories()  # a fresh ad can introduce new categories
        self.reload_deals()
        self.reload_schedules()      # the run shows up in the job history

    def _on_scrape_failed(self, message: str) -> None:
        self.statusBar().showMessage(f"Scrape failed: {message}", 10000)
        self.scrape_btn.setEnabled(True)
        self.progress.hide()
        self.reload_schedules()      # the failure is on the record too

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
