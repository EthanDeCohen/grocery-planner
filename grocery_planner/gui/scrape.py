"""Run a scrape (GFP-35) — the old Deals-tab action bar, now a dialog.

The store picker and "Run scrape now" button lived beside the deal table that
GFP-35 retires, so they move to Data ▸ Run scrape… . The scrape itself is
unchanged: a background thread over ``jobs.run_tracked_scrape``, so a GUI run
lands in ``gplan jobs`` exactly like a scheduled one (GFP-7).

The "Force" box is the control GFP-71 left a seam for: ``run_tracked_scrape``
has taken ``force`` since then, but nothing in the GUI could set it, so a
tripped GFP-67 replace-guard had no escape hatch outside the CLI.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from .. import jobs, service
from ..stores import BY_KEY


class ScrapeWorker(QObject):
    """Runs a scrape off the UI thread; opens its own DB connection there."""

    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, store_key: str, force: bool = False) -> None:
        super().__init__()
        self._store_key = store_key
        self._force = force

    def run(self) -> None:
        try:
            # Tracked, so a GUI scrape lands in `gplan jobs` like a scheduled
            # one and an interrupted run is visible after a crash (GFP-7).
            result = jobs.run_tracked_scrape(self._store_key, force=self._force)
        except Exception as exc:  # surface any failure to the UI thread
            self.failed.emit(str(exc))
        else:
            self.finished.emit(result)


class ScrapeDialog(QDialog):
    """Pick a store, run its scrape, and watch it finish."""

    #: Emitted with a one-line summary once a run ends, pass or fail, so the
    #: main window can put it in the status bar and refresh anything open.
    completed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Run scrape")
        self.resize(520, 160)
        layout = QVBoxLayout(self)

        row = QHBoxLayout()
        row.addWidget(QLabel("Store:"))
        self.store_box = QComboBox()
        # Only stores with a registered scraper: the rest arrive by CSV import.
        for key in service.available_scrapers():
            store = BY_KEY.get(key)
            self.store_box.addItem(store.display_name if store else key, key)
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
        layout.addLayout(row)

        # Indeterminate: the sources give no progress signal, so a moving bar
        # is the honest amount of information — "working", not a fake percentage.
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)

        self.message = QLabel("")
        self.message.setWordWrap(True)
        layout.addWidget(self.message, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._thread: QThread | None = None
        self._worker: ScrapeWorker | None = None
        self.store_box.currentIndexChanged.connect(self._sync_button)
        self._sync_button()

    # ----------------------------------------------------------------- #
    def current_store(self) -> str | None:
        return self.store_box.currentData()

    def _sync_button(self) -> None:
        self.scrape_btn.setEnabled(bool(self.current_store()))

    def on_scrape(self) -> None:
        store = self.current_store()
        if not store:
            return
        self.scrape_btn.setEnabled(False)
        self.progress.show()
        self.message.setText(f"Scraping {self.store_box.currentText()} …")

        self._thread = QThread(self)
        self._worker = ScrapeWorker(store, force=self.force_box.isChecked())
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
        summary = (
            f"Stored {stats.get('total', '?')} deals — "
            f"{stats.get('weekly_ad', '?')} weekly ad, "
            f"{stats.get('digital_coupons', '?')} coupons."
        )
        self._finish(summary)

    def _on_scrape_failed(self, message: str) -> None:
        self._finish(f"Scrape failed: {message}")

    def _finish(self, summary: str) -> None:
        self.message.setText(summary)
        self.scrape_btn.setEnabled(True)
        self.progress.hide()
        self.completed.emit(summary)
