"""PySide6 desktop shell — the menu-bar window the nutritionist GUI sits in.

GFP-35 retired the GFP-14/GFP-11 tab layout (Deals / Formulas / Schedule) and
its deal-browsing table: browsing raw supermarket deals is not the product,
and the tabs were in the way of the client roster (GFP-36) and client detail
page (GFP-37) that are. The central widget is deliberately empty here — GFP-36
fills it — and this module is now only the shell:

- the menu bar, which is where every control the tabs used to hold now lives,
- CSV export,
- ``main()``, still the ``gplan-gui`` entry point.

Each retired tab became its own dialog module so the GFP-20 panes can be built
side by side without three people editing one file:

- :mod:`.scrape`   — Data ▸ Run scrape…
- :mod:`.formulas` — Settings ▸ Formulas…
- :mod:`.schedule` — Settings ▸ Automatic refresh…

The GFP-17 filter bar went with the table. Filtered querying and export live on
in ``gplan list`` / ``gplan best`` / ``gplan export``, which take every filter
the bar had as a flag; the menu's Export writes the current (non-expired) deals.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QLabel,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)

from .. import service
from .formulas import FormulaDialog
from .schedule import ScheduleDialog
from .scrape import ScrapeDialog

PLACEHOLDER_TEXT = (
    "The client roster lands here.\n\n"
    "Everything the app can do today is on the menu bar:\n"
    "Data ▸ Run scrape…   ·   Settings ▸ Formulas…   ·   "
    "Settings ▸ Automatic refresh…   ·   File ▸ Export deals…"
)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Grocery Planner")
        self.resize(920, 560)

        self._dialogs: dict[str, QDialog] = {}
        self._build_menus()
        self.setCentralWidget(self._build_placeholder())
        self.statusBar().showMessage(
            f"{service.count_deals(hide_expired=True)} current deals stored."
        )

    # ----------------------------------------------------------------- #
    # Menu bar — the one place a control can live now the tabs are gone
    # ----------------------------------------------------------------- #
    def _build_menus(self) -> None:
        bar = self.menuBar()

        file_menu = bar.addMenu("&File")
        self.export_action = QAction("&Export deals…", self)
        self.export_action.setShortcut(QKeySequence("Ctrl+E"))
        self.export_action.setStatusTip("Save the current deals to a CSV file.")
        self.export_action.triggered.connect(self.on_export)
        file_menu.addAction(self.export_action)
        file_menu.addSeparator()
        self.quit_action = QAction("&Quit", self)
        self.quit_action.setShortcut(QKeySequence.Quit)
        self.quit_action.triggered.connect(self.close)
        file_menu.addAction(self.quit_action)

        data_menu = bar.addMenu("&Data")
        self.scrape_action = QAction("&Run scrape…", self)
        self.scrape_action.setShortcut(QKeySequence("Ctrl+R"))
        self.scrape_action.setStatusTip("Pull a fresh weekly ad for one store.")
        self.scrape_action.triggered.connect(self.open_scrape)
        data_menu.addAction(self.scrape_action)

        settings_menu = bar.addMenu("&Settings")
        self.formulas_action = QAction("&Formulas…", self)
        self.formulas_action.setStatusTip(
            "Edit the scoring formulas, including the daily protein target."
        )
        self.formulas_action.triggered.connect(self.open_formulas)
        settings_menu.addAction(self.formulas_action)
        self.schedule_action = QAction("&Automatic refresh…", self)
        self.schedule_action.setStatusTip("Set how often each store re-scrapes.")
        self.schedule_action.triggered.connect(self.open_schedule)
        settings_menu.addAction(self.schedule_action)

    def _build_placeholder(self) -> QWidget:
        """The empty centre, saying so plainly rather than looking broken."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addStretch(1)
        self.placeholder_label = QLabel(PLACEHOLDER_TEXT)
        self.placeholder_label.setAlignment(Qt.AlignCenter)
        self.placeholder_label.setWordWrap(True)
        layout.addWidget(self.placeholder_label)
        layout.addStretch(1)
        return page

    # ----------------------------------------------------------------- #
    # Dialogs — one instance each, reused so state survives a close/reopen
    # ----------------------------------------------------------------- #
    def _open(self, key: str, factory) -> QDialog:
        dialog = self._dialogs.get(key)
        if dialog is None:
            dialog = self._dialogs[key] = factory(self)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        return dialog

    def open_formulas(self) -> QDialog:
        return self._open("formulas", FormulaDialog)

    def open_schedule(self) -> QDialog:
        dialog = self._open("schedule", ScheduleDialog)
        dialog.reload_schedules()  # a run may have happened since it was last open
        return dialog

    def open_scrape(self) -> QDialog:
        first_time = "scrape" not in self._dialogs
        dialog = self._open("scrape", ScrapeDialog)
        if first_time:
            dialog.completed.connect(self._on_scrape_completed)
        return dialog

    def _on_scrape_completed(self, summary: str) -> None:
        self.statusBar().showMessage(summary, 10000)
        schedule = self._dialogs.get("schedule")
        if schedule is not None:
            schedule.reload_schedules()  # the run is on the job record either way

    # ----------------------------------------------------------------- #
    # Export (GFP-11)
    # ----------------------------------------------------------------- #
    def on_export(self) -> None:
        """Write the current deals to CSV.

        CSV, not .xlsx: GFP-13 retired the Excel dependency and a CSV opens in
        Excel anyway. Expired rows are left out — exporting prices that are no
        longer on offer is worse than exporting nothing. ``gplan export`` takes
        the full set of filters when a narrower slice is wanted.
        """
        path, _selected = QFileDialog.getSaveFileName(
            self, "Export deals", "deals.csv", "CSV files (*.csv)"
        )
        if not path:
            return
        try:
            written = service.export_deals(path, hide_expired=True)
        except OSError as exc:
            self.statusBar().showMessage(f"Export failed: {exc}", 10000)
            return
        self.statusBar().showMessage(f"Exported {written} deals to {path}.", 8000)


def main() -> int:
    """Launch the desktop app; returns the Qt exit code."""
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
