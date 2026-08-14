# ######### decohen-partners ##########
# Protein Ledger
"""Run a scrape (GFP-35) — the old Deals-tab action bar, now a dialog.

The store picker and "Run scrape now" button lived beside the deal table that
GFP-35 retires, so they move to Data ▸ Run scrape… . The scrape itself is
unchanged: a background thread over ``jobs.run_tracked_scrape``, so a GUI run
lands in ``gplan jobs`` exactly like a scheduled one (GFP-7).

The "Force" box is the control GFP-71 left a seam for: ``run_tracked_scrape``
has taken ``force`` since then, but nothing in the GUI could set it, so a
tripped GFP-67 replace-guard had no escape hatch outside the CLI.

**GFP-103 — every run gets its own row, and rows never disappear.** This dialog
originally held one worker, one progress bar and one label, on the assumption
that only one scrape could be in flight. That assumption was defeated by its own
store picker (changing store re-enabled the button mid-run), and starting a
second scrape then replaced the first everywhere: on screen, and — far worse —
in ``self._worker``, which was the *only* reference keeping a running worker
alive, since ``ScrapeWorker`` has no Qt parent. A user watching four stores
scrape saw each one erase the last.

So runs are now a **collection**, not a slot:

* :class:`ScrapeRow` — one per run, with its own indicator and its own result.
  A finished row keeps its result on screen; nothing is ever replaced.
* :attr:`ScrapeDialog._runs` holds the worker and thread for the whole life of
  the run, keyed by store, so nothing can be collected mid-flight.
* Results are written to the row of the store they belong to, so a slow scrape
  finishing after a fast one cannot attribute its numbers to the wrong store.
* Re-running is refused **per store**, not by disabling the whole dialog — the
  point is that several stores run at once.

"Scrape all" exists because the first-run experience was otherwise: open the
dialog, pick a store, run it, pick the next, run it, four times over. Nothing
here may assume how many stores there are — ``available_scrapers()`` stays the
source of truth, same rule as GFP-32.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import jobs, service
from ..stores import BY_KEY


def _display(store_key: str) -> str:
    store = BY_KEY.get(store_key)
    return store.display_name if store else store_key


class ScrapeWorker(QObject):
    """Runs a scrape off the UI thread; opens its own DB connection there."""

    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, store_key: str, force: bool = False) -> None:
        super().__init__()
        self._store_key = store_key
        self._force = force

    @property
    def store_key(self) -> str:
        return self._store_key

    def run(self) -> None:
        try:
            # Tracked, so a GUI scrape lands in `gplan jobs` like a scheduled
            # one and an interrupted run is visible after a crash (GFP-7).
            result = jobs.run_tracked_scrape(self._store_key, force=self._force)
        except Exception as exc:  # surface any failure to the UI thread
            self.failed.emit(str(exc))
        else:
            self.finished.emit(result)


class ScrapeRow(QFrame):
    """One run: which store, whether it is still going, and how it ended."""

    def __init__(self, store_key: str, parent=None) -> None:
        super().__init__(parent)
        self.store_key = store_key
        self.setFrameShape(QFrame.NoFrame)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)

        self.name = QLabel(_display(store_key))
        font = self.name.font()
        font.setBold(True)
        self.name.setFont(font)
        self.name.setMinimumWidth(130)
        layout.addWidget(self.name)

        # Indeterminate: the sources give no progress signal, so a moving bar
        # is the honest amount of information -- "working", not a fake percentage.
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setMaximumWidth(120)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)

        self.status = QLabel("Scraping…")
        self.status.setWordWrap(True)
        self.status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout.addWidget(self.status, 1)

    @property
    def running(self) -> bool:
        # isHidden(), not isVisible(): the latter is False whenever an ancestor
        # is unshown, which would report every row idle in an unopened dialog.
        return not self.progress.isHidden()

    def finish(self, summary: str) -> None:
        """Stop the indicator and keep the result on screen for good."""
        self.progress.hide()
        self.status.setText(summary)


class ScrapeDialog(QDialog):
    """Pick a store (or all of them), run scrapes, and watch each one finish."""

    #: Emitted with a one-line summary once a run ends, pass or fail, so the
    #: main window can put it in the status bar and refresh anything open.
    completed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Run scrape")
        self.resize(620, 320)
        layout = QVBoxLayout(self)

        row = QHBoxLayout()
        row.addWidget(QLabel("Store:"))
        self.store_box = QComboBox()
        # Stores that have a registered scraper AND a branch serving this ZIP
        # (GFP-257). Readiness alone used to be the only filter, which is how
        # a Greensboro user was offered ACME Markets -- a Northeast chain with
        # no store within several hundred miles. `run_scrape` already refused
        # those, so the only thing the old list bought was a wasted click and a
        # confusing result. The rest arrive by CSV import.
        self.plan = service.scrapers_for_postal_code()
        for key in self.plan.keys:
            self.store_box.addItem(_display(key), key)
        row.addWidget(self.store_box, 1)

        self.force_box = QCheckBox("Force")
        self.force_box.setToolTip(
            "Replace the stored deals even if the new scrape looks empty or "
            "implausibly small. Use only when you know the ad really shrank."
        )
        row.addWidget(self.force_box)

        self.scrape_btn = QPushButton("Run scrape now")
        self.scrape_btn.clicked.connect(self.on_scrape)
        row.addWidget(self.scrape_btn)

        self.scrape_all_btn = QPushButton("Scrape all")
        self.scrape_all_btn.setToolTip(
            "Run every store that is ready to scrape, each as its own row."
        )
        self.scrape_all_btn.clicked.connect(self.on_scrape_all)
        row.addWidget(self.scrape_all_btn)
        layout.addLayout(row)

        # Rows accumulate, so they scroll: the dialog must stay usable as
        # stores are added rather than growing until it is taller than a screen.
        self.rows_host = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_host)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.addStretch(1)

        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        scroller.setWidget(self.rows_host)
        layout.addWidget(scroller, 1)

        self.message = QLabel("")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)

        # What the ZIP filter removed, said out loud. A dialog that silently
        # drops seven of nineteen stores is indistinguishable from one that
        # never supported them, and the user cannot tell a deliberate filter
        # from a missing feature -- the no-silent-caps rule again.
        self.scope = QLabel(self.plan.summary)
        self.scope.setWordWrap(True)
        self.scope.setVisible(bool(self.plan.summary))
        layout.addWidget(self.scope)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        #: store key -> its row. Finished rows stay here so a re-run reuses them.
        self._rows: dict[str, ScrapeRow] = {}
        #: store key -> (thread, worker) for runs IN FLIGHT. This is what keeps
        #: a worker alive: ScrapeWorker has no Qt parent, so dropping the last
        #: Python reference to one mid-run is a use-after-free (GFP-103).
        self._runs: dict[str, tuple[QThread, ScrapeWorker]] = {}
        #: Threads still alive, tracked SEPARATELY from ``_runs`` on purpose: a
        #: run leaves ``_runs`` the moment its result signal fires, but the
        #: thread is only stopping at that point, not stopped. Joining ``_runs``
        #: would therefore join nothing and let a live thread reach teardown.
        self._threads: list[QThread] = []

        self.store_box.currentIndexChanged.connect(self._sync_buttons)
        self._sync_buttons()

    # ----------------------------------------------------------------- #
    def current_store(self) -> str | None:
        return self.store_box.currentData()

    def is_running(self, store_key: str) -> bool:
        return store_key in self._runs

    @property
    def running_stores(self) -> list[str]:
        return sorted(self._runs)

    def _sync_buttons(self) -> None:
        """Enablement is PER STORE — several stores running at once is the point.

        The old dialog disabled the button on start and re-enabled it whenever
        the picker changed, which both blocked nothing and hid nothing. Here the
        selected store's own state decides.
        """
        store = self.current_store()
        self.scrape_btn.setEnabled(bool(store) and not self.is_running(store))
        idle = [key for key in self._scrapable() if not self.is_running(key)]
        self.scrape_all_btn.setEnabled(bool(idle))

    def _scrapable(self) -> list[str]:
        """Stores ready to scrape AND serving this ZIP, asked fresh.

        Fresh because both halves move: readiness can change mid-session
        (GFP-4, a session gets minted) and so can the ZIP (the user edits it in
        settings). "Scrape all" has to go through the same filter as the combo
        box or the button quietly means something different from the list above
        it -- which is how it would still have run ACME for a Greensboro user
        even after the dropdown stopped offering it (GFP-257).
        """
        self.plan = service.scrapers_for_postal_code()
        if hasattr(self, "scope"):
            self.scope.setText(self.plan.summary)
            self.scope.setVisible(bool(self.plan.summary))
        return list(self.plan.keys)

    # ----------------------------------------------------------------- #
    def _row_for(self, store_key: str) -> ScrapeRow:
        """This store's row, reused across runs so the list cannot grow forever."""
        row = self._rows.get(store_key)
        if row is None:
            row = self._rows[store_key] = ScrapeRow(store_key, self.rows_host)
            # Before the trailing stretch, so rows stack top-down in start order.
            self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
        else:
            row.progress.show()
            row.status.setText("Scraping…")
        return row

    def on_scrape(self) -> None:
        store = self.current_store()
        if store:
            self.start(store)

    def on_scrape_all(self) -> None:
        """Every ready store at once, each as its own row.

        Already-running stores are skipped rather than refused: pressing this
        while one store is going should top up the rest, not fail.
        """
        started = [key for key in self._scrapable() if self.start(key)]
        if not started:
            self.message.setText("Every store is already scraping.")

    def start(self, store_key: str) -> bool:
        """Begin one store's scrape. Returns False if it was already running."""
        if self.is_running(store_key):
            return False

        row = self._row_for(store_key)
        self.message.setText("")

        thread = QThread(self)
        worker = ScrapeWorker(store_key, force=self.force_box.isChecked())
        worker.moveToThread(thread)
        # Held for the whole run -- see the _runs docstring. Registered BEFORE
        # start() so a scrape that fails instantly still finds its own entry.
        self._runs[store_key] = (thread, worker)

        thread.started.connect(worker.run)
        # Bound with a default argument rather than a closure over the loop
        # variable, so "Scrape all" cannot route every result to the last store.
        worker.finished.connect(lambda result, key=store_key: self._on_done(key, result))
        worker.failed.connect(lambda message, key=store_key: self._on_failed(key, message))
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(lambda t=thread: self._release_thread(t))
        thread.finished.connect(thread.deleteLater)
        self._threads.append(thread)
        thread.start()

        row.progress.show()
        self._sync_buttons()
        return True

    # ----------------------------------------------------------------- #
    def wait_for_runs(self, msecs: int = 15_000) -> bool:
        """Join every in-flight thread. ``True`` if they all stopped in time.

        A ``QThread`` parented to this dialog is destroyed with it, and Qt
        aborts the process outright when that happens to a *running* thread
        ("QThread: Destroyed while thread is still running"). So teardown has to
        join rather than assume.

        ``quit()`` only ends a thread's event loop and cannot interrupt the
        blocking scrape inside ``ScrapeWorker.run``, so this really does wait
        for the network call to return — hence the bound. Overrunning it is not
        a disaster: every run is a tracked job (GFP-7), so an abandoned one is
        recoverable and visible in ``gplan jobs`` rather than lost.
        """
        threads = list(self._threads)
        for thread in threads:
            thread.quit()
        return all(thread.wait(msecs) for thread in threads)

    def _release_thread(self, thread: QThread) -> None:
        """Forget a thread once Qt says it has actually finished."""
        if thread in self._threads:
            self._threads.remove(thread)

    def closeEvent(self, event) -> None:  # noqa: N802
        # The main window caches this dialog (`_dialogs`), so closing normally
        # only hides it and runs continue. This matters on real teardown --
        # app exit -- where the dialog is destroyed for good.
        self.wait_for_runs()
        super().closeEvent(event)

    def _on_done(self, store_key: str, result: dict) -> None:
        stats = result.get("stats", {})
        self._finish(store_key, (
            f"Stored {stats.get('total', '?')} deals — "
            f"{stats.get('weekly_ad', '?')} weekly ad, "
            f"{stats.get('digital_coupons', '?')} coupons."
        ))

    def _on_failed(self, store_key: str, message: str) -> None:
        self._finish(store_key, f"Scrape failed: {message}")

    def _finish(self, store_key: str, summary: str) -> None:
        self._runs.pop(store_key, None)
        row = self._rows.get(store_key)
        if row is not None:
            row.finish(summary)
        self._sync_buttons()
        # The status bar wants to know which store this was: with several runs
        # in flight, a bare "Stored 812 deals" names nothing.
        self.completed.emit(f"{_display(store_key)}: {summary}")
