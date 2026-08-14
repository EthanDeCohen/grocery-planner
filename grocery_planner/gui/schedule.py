# ######### decohen-partners ##########
# Protein Ledger
"""Automatic-refresh settings (GFP-35) — the GFP-11 Schedule tab, now a dialog.

Same GFP-7 machinery as before: a per-store cadence stored in the database
plus the recent job history, reachable from Settings ▸ Automatic refresh…
instead of a tab.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
)

from .. import db, jobs, scheduler, service
from ..stores import BY_KEY
from .widgets import placeholder

JOB_HISTORY_LIMIT = 15


class ScheduleDialog(QDialog):
    """Set the automatic refresh cadence per store and show what has run."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Automatic refresh")
        self.resize(680, 480)
        layout = QVBoxLayout(self)

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

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.reload_schedules()

    # ----------------------------------------------------------------- #
    def reload_schedules(self) -> None:
        conn = db.connect()
        self.schedule_list.clear()
        rows = scheduler.list_schedules(conn)
        if not rows:
            self.schedule_list.addItem(placeholder(
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
        history = jobs.recent_jobs(conn, limit=JOB_HISTORY_LIMIT)
        if not history:
            self.jobs_list.addItem(placeholder("Nothing has run automatically yet."))
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
