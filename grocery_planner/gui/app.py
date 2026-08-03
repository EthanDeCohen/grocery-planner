"""PySide6 desktop shell — the menu-bar window the nutritionist GUI sits in.

GFP-35 retired the GFP-14/GFP-11 tab layout (Deals / Formulas / Schedule) and
its deal-browsing table: browsing raw supermarket deals is not the product, and
the tabs were in the way of the client roster and client detail page that are.
GFP-36 filled the centre it left empty. This module is the shell:

- the menu bar, which is where every control the tabs used to hold now lives,
- the roster/detail navigation stack,
- CSV export,
- ``main()``, still the ``gplan-gui`` entry point.

Every pane and dialog is its own module so the GFP-20 stories can be built side
by side without several people editing one file:

- :mod:`.roster`   — client roster, left of the main view (GFP-36)
- :mod:`.trends`   — price-trends chart, right of it (GFP-36)
- :mod:`.cheapest` — the cheapest-meat strip along the bottom (GFP-107)
- :mod:`.client`   — client detail page (GFP-37 fills the body)
- :mod:`.scrape`   — Data ▸ Run scrape…
- :mod:`.formulas` — Settings ▸ Formulas…
- :mod:`.schedule` — Settings ▸ Automatic refresh…

The GFP-17 filter bar went with the table. Filtered querying and export live on
in ``gplan list`` / ``gplan best`` / ``gplan export``, which take every filter
the bar had as a flag; the menu's Export writes the current (non-expired) deals.
"""
from __future__ import annotations

import sys

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QMainWindow,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import service
from .cheapest import CheapestMeatStrip
from .client import ClientDetailPage
from .formulas import FormulaDialog
from .roster import RosterPane
from .schedule import ScheduleDialog
from .scrape import ScrapeDialog
from .trends import TrendsPane

#: Roster gets the narrower half; the chart needs the width to be readable.
SPLIT_SIZES = (340, 580)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Grocery Planner")
        self.resize(920, 560)

        self._dialogs: dict[str, QDialog] = {}
        self._build_menus()
        self.setCentralWidget(self._build_central())
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

    def _build_central(self) -> QWidget:
        """Roster + trends, the client page stacked behind them, and the strip.

        A stack rather than a new window per client: the nutritionist moves
        between the roster and one client constantly during an intake call, and
        a second window to hunt for would be in the way. ``Alt+Left`` and the
        page's own back button both come back here.

        GFP-107's cheapest-meat strip sits BELOW the stack rather than inside
        either page, so it stays on screen while moving between the roster and a
        client. It answers "where is protein cheapest today", which is as
        relevant mid-consultation as it is on the roster — and putting it inside
        one page would make it vanish exactly when a client is on screen.
        """
        self.roster = RosterPane()
        self.roster.client_selected.connect(self.show_client)
        self.trends = TrendsPane()

        self.split = QSplitter()
        self.split.addWidget(self.roster)
        self.split.addWidget(self.trends)
        self.split.setSizes(list(SPLIT_SIZES))

        self.client_page = ClientDetailPage()
        self.client_page.back_requested.connect(self.show_roster)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.split)
        self.stack.addWidget(self.client_page)

        self.cheapest = CheapestMeatStrip()

        central = QWidget()
        column = QVBoxLayout(central)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self.stack, 1)     # the stack takes the slack...
        column.addWidget(self.cheapest)     # ...so the strip keeps its own height
        return central

    # ----------------------------------------------------------------- #
    # Navigation (GFP-36)
    # ----------------------------------------------------------------- #
    def show_client(self, customer_id: int) -> bool:
        """Open one client's detail page. Stays on the roster if they're gone."""
        if not self.client_page.show_client(customer_id):
            self.statusBar().showMessage("That client is no longer on file.", 8000)
            return False
        self.stack.setCurrentWidget(self.client_page)
        return True

    def show_roster(self) -> None:
        self.stack.setCurrentWidget(self.split)
        self.roster.reload()      # a rename or a new client shows up on return
        self.roster.search_edit.setFocus()

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
        self.trends.reload()             # a fresh day of prices moves the chart
        self.cheapest.reload()           # ...and changes what is cheapest today
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
