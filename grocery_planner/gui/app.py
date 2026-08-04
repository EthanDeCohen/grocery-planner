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

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QMainWindow,
    QSplitter,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import config, logs, service
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
        self._build_zip_corner()
        self.setCentralWidget(self._build_central())
        self.statusBar().showMessage(
            f"{service.count_deals(hide_expired=True)} current deals stored."
        )
        # NOT called here: see main(). Constructing the window must not touch
        # the network, or every GUI test would scrape.

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

        self.connect_wf_action = QAction("&Connect Whole Foods…", self)
        self.connect_wf_action.setStatusTip(
            "Choose your Whole Foods store so the app can see its prices."
        )
        self.connect_wf_action.triggered.connect(self.open_wholefoods_mint)
        data_menu.addAction(self.connect_wf_action)

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

        # GFP-148. Deliberately not named after a store: the loader identifies
        # which credential a file is from its contents, so one menu item covers
        # all of them and a new credential needs no new UI.
        #
        # TEMPORARY BY DESIGN. GFP-149 removes this in v2, when the hosted
        # server supplies the credential and there is nothing for a user to
        # load.
        settings_menu.addSeparator()
        self.load_credential_action = QAction("&Load credential…", self)
        self.load_credential_action.setStatusTip(
            "Install a credential file you were sent, so prices can be fetched."
        )
        self.load_credential_action.triggered.connect(self.open_load_credential)
        settings_menu.addAction(self.load_credential_action)

        # GFP-96. Present but DISABLED until a check finds something, so the
        # menu never offers an action that would do nothing -- and so the app
        # has somewhere to put the news other than a modal.
        help_menu = bar.addMenu("&Help")
        self.update_action = QAction("No updates available", self)
        self.update_action.setEnabled(False)
        self.update_action.setStatusTip(
            "Open the releases page in your browser. Nothing is downloaded "
            "or installed automatically."
        )
        self.update_action.triggered.connect(self.open_releases_page)
        help_menu.addAction(self.update_action)

    # ----------------------------------------------------------------- #
    # The ZIP, always on screen (GFP-122)
    # ----------------------------------------------------------------- #
    def _build_zip_corner(self) -> None:
        """Show the ZIP in the menu bar's corner, and let it be changed there.

        THE POINT IS VISIBILITY, not convenience. Every price in this app is
        for one ZIP, and until now that ZIP appeared nowhere in the interface:
        an install pointed at the wrong city looked exactly like one pointed at
        the right city. Putting it in the corner means a wrong ZIP is
        noticeable at a glance, on every screen, without anyone going looking.

        The menu bar corner rather than a toolbar because it is global -- it
        scopes the whole dataset, not the chart it would otherwise sit beside.
        """
        from PySide6.QtWidgets import QHBoxLayout, QLabel

        # HELD ON self, and that reference is load-bearing rather than tidy.
        # setCornerWidget does not keep the Python wrapper alive, so a corner
        # widget kept only in a local is garbage-collected once this method
        # returns -- destroying its C++ children with it. The control then
        # never appears, and touching self.zip_button raises "already deleted".
        #
        # Isolated by removing each guard in turn: the self reference is what
        # fixes it; parenting to the menu bar alone does not. Found by a
        # screenshot, and the regression test needs an explicit gc.collect()
        # to reproduce it deterministically.
        corner = QWidget(self.menuBar())
        self._zip_corner = corner
        row = QHBoxLayout(corner)
        row.setContentsMargins(0, 0, 8, 0)
        row.setSpacing(6)

        label = QLabel("ZIP")
        label.setStyleSheet("color: #666;")
        row.addWidget(label)

        self.zip_button = QPushButton(config.postal_code())
        self.zip_button.setFlat(True)
        self.zip_button.setStyleSheet("font-weight: 600;")
        self.zip_button.setStatusTip(
            "Prices are looked up for this ZIP code. Click to change it."
        )
        self.zip_button.clicked.connect(self.open_change_zip)
        row.addWidget(self.zip_button)

        self.menuBar().setCornerWidget(corner)

    def open_change_zip(self) -> None:
        """Change the ZIP every price is looked up for.

        Says plainly that stored prices are now for somewhere else. Silently
        swapping the ZIP under a screen full of prices from the old one would
        leave the user reading the wrong city's numbers with no hint of it --
        which is the same failure GFP-122 exists to prevent, arrived at from a
        different direction.
        """
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        from .firstrun import ZIP_PATTERN

        current = config.postal_code()
        text, ok = QInputDialog.getText(
            self, "Change ZIP code",
            "Look up prices for which ZIP code?", text=current,
        )
        if not ok:
            return
        candidate = text.strip()
        if not ZIP_PATTERN.match(candidate):
            QMessageBox.warning(
                self, "That is not a ZIP code", "A ZIP code is five digits."
            )
            return
        if candidate == current:
            return

        try:
            config.set_value("postal_code", candidate)
        except Exception as exc:            # noqa: BLE001
            QMessageBox.warning(self, "Could not save that ZIP", str(exc))
            return

        self.zip_button.setText(candidate)
        self.statusBar().showMessage(
            f"ZIP changed to {candidate}. Prices already stored are still for "
            f"{current} -- run a scrape to refresh them.", 20000
        )

    def open_load_credential(self) -> None:
        """Install a credential file the user was sent (GFP-148).

        Says what to do next rather than refreshing anything: a credential
        changes what a scrape CAN do, not what is currently on screen. Quietly
        reloading the charts would show identical numbers and read as though
        nothing had happened.
        """
        from . import loadcredential

        installed = loadcredential.load(self)
        if installed is None:
            return
        self.statusBar().showMessage(
            f"Credential loaded ({installed.name}). "
            "Run a scrape to pull prices with it.", 12000
        )

    def open_releases_page(self) -> None:
        """Hand off to the browser, and stop there.

        This is the whole of GFP-96's action surface. The app tells you an
        update exists and opens the page; a person downloads it and runs the
        installer. Nothing here writes an executable, and nothing here should
        ever learn how -- a silent self-update is a remote-code-execution path
        by design, which is a much bigger trust ask on a tool holding
        health-adjacent data about third parties than on a text editor.
        """
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        from .. import updates

        QDesktopServices.openUrl(QUrl(getattr(self, "_update_url", updates.RELEASES_PAGE)))

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
    # First-run / new-day refresh (GFP-105)
    # ----------------------------------------------------------------- #
    def open_wholefoods_mint(self):
        """GFP-80: mint a Whole Foods session in an embedded browser.

        Imported HERE rather than at module scope: Qt WebEngine is 195 MB of
        Chromium, and the overwhelming majority of launches never open this
        window. (The one import that cannot be deferred is in main(), because
        Qt requires it before the QApplication exists.)
        """
        from .. import config
        from . import wholefoods as mint_ui

        if not mint_ui.webengine_available():
            self.statusBar().showMessage(
                "This build does not include the embedded browser needed to "
                "connect Whole Foods.", 12000
            )
            return

        postal_code = config.postal_code()
        dialog = mint_ui.mint(postal_code, parent=self)
        if dialog is None:
            self.statusBar().showMessage("Whole Foods was not connected.", 8000)
            return None
        self.statusBar().showMessage(
            f"Whole Foods connected for {postal_code}. "
            "Run a scrape to pull its prices.", 12000
        )
        return dialog

    def mention_update(self, message: str, url: str) -> None:
        """Say a newer version exists, once, quietly, and never again.

        A status-bar line and a menu item. NOT a modal: this is a local-first
        desktop tool for somebody doing their actual job, and a blocking update
        nag is the wrong shape. The message has no timeout because unlike the
        other status-bar messages it is not about something the user just did,
        so there is nothing for it to become stale against -- and it is
        replaced the moment they do anything else.
        """
        self.statusBar().showMessage(f"{message}  Get it from the Help menu.")
        self.update_action.setText("Update available…")
        self.update_action.setEnabled(True)
        self._update_url = url

    def maybe_auto_refresh(self) -> bool:
        """Fetch this week's prices on first run, or on a new day.

        Reported from first use: a fresh install showed an empty app, and the
        only way to fill it was to find Data ▸ Run scrape and click through
        every store by hand. Nothing told the user that.

        Deliberately reuses the GFP-103 scrape dialog rather than scraping
        quietly in the background. That dialog already shows a row per store
        with its own progress and result, which satisfies the two constraints
        that matter here: the user can SEE it happening, and they can close it.
        Automatic network activity that cannot be seen or stopped is not
        acceptable on someone else's machine.

        Whether a refresh is due is decided by ``service.refresh_decision`` and
        NOT by this method, so GFP-102's background timer and this cannot reach
        different answers and double-scrape.

        Returns True when a refresh was started, for tests and for callers that
        want to know.
        """
        if not config.auto_refresh():
            # GFP-85 gave this a real home. It was an environment variable
            # placeholder when GFP-105 shipped, which the code said at the time;
            # `auto_refresh: false` in config.json is now the supported way, and
            # GROCERY_PLANNER_AUTO_REFRESH still overrides it because the
            # config layer gives every setting an environment override.
            return False

        decision = service.refresh_decision()
        if not decision.due or not decision.stores:
            return False

        self.statusBar().showMessage(decision.explanation, 15000)
        dialog = self.open_scrape()
        started = [store for store in decision.stores if dialog.start(store)]
        return bool(started)

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


class _UpdateCheck(QThread):
    """Ask GitHub whether a newer release exists, off the UI thread.

    A thread rather than a direct call because this runs while the user is
    trying to start working, and the check has a 5-second timeout. Blocking the
    event loop for five seconds on a bad connection would make the update check
    the slowest thing about opening the app -- for a feature nobody asked for.

    Emits nothing at all when there is no update, when the check fails, or when
    the user has turned it off. Silence is the normal outcome.
    """

    found = Signal(str, str)        # message, url

    def run(self) -> None:          # pragma: no cover -- exercised by hand
        from .. import updates
        result = updates.check_quietly()
        if result is not None:
            self.found.emit(result.message, result.url)


def main() -> int:
    """Launch the desktop app; returns the Qt exit code."""
    # GFP-86: the GUI is the unattended-est path of all -- a scheduled refresh
    # fires with nobody watching. console=False because a windowed build has no
    # console to write to.
    logs.setup(console=False)
    logs.get_logger(__name__).info("gui starting")

    # GFP-80: Qt requires QtWebEngineCore to be imported BEFORE a
    # QApplication exists. It cannot be deferred to the moment the user opens
    # the minting window -- by then it is too late and the window fails to
    # open, which is the worst possible time to discover it.
    #
    # The import itself is cheap; it does not start Chromium. That happens
    # when a QWebEngineView is first constructed, which is still deferred to
    # the dialog. A build without WebEngine (CLI-only, or size-trimmed) simply
    # carries on without the feature rather than failing to start.
    try:
        from PySide6 import QtWebEngineCore  # noqa: F401
    except ImportError:
        logs.get_logger(__name__).info(
            "Qt WebEngine is not in this build; Whole Foods cannot be "
            "connected from the app."
        )

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    # GFP-105 lives HERE and not in MainWindow.__init__ on purpose. Building a
    # window must never touch the network: with the call in the constructor,
    # every GUI test -- and anything else that instantiates MainWindow --
    # would fire real scrapes. Launching the application is the event that
    # justifies fetching prices; constructing a widget is not.
    #
    # After show(), so the window is already on screen when the scrape dialog
    # appears over it rather than the app seeming to open into a dialog.
    # GFP-122. BEFORE the refresh, and that ordering is the whole ticket:
    # postal_code defaults to 27401 (the developer's ZIP), and GFP-105 would
    # otherwise make the very first act of a new install a confident scrape of
    # the wrong city -- with nothing on screen to suggest it.
    if config.is_first_run():
        from .firstrun import ask

        chosen = ask(window)
        if chosen:
            window.zip_button.setText(chosen)
    window.maybe_auto_refresh()
    # GFP-96: passive, and in main() for the same reason as the refresh above
    # -- constructing a window must never touch the network. Held on the
    # window so Python does not collect the thread mid-flight.
    window.update_check = _UpdateCheck(window)
    window.update_check.found.connect(window.mention_update)
    window.update_check.start()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
