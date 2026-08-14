# ######### decohen-partners ##########
# Protein Ledger
"""Ask for the ZIP code before the app does anything with prices (GFP-122).

**Why this is worth a modal**, when GFP-96 refused one for update news: that
rule is about unsolicited interruptions *during work*. This is the setup that
has to happen once, and the app cannot do its job without the answer.

**What goes wrong without it.** ``postal_code`` defaults to ``27401``, which is
the developer's ZIP. GFP-105 auto-refreshes on first run, so the very first
thing a new install does is fetch a different city's prices — confidently,
completely, and with nothing on screen suggesting anything is wrong. A wrong
ZIP does not produce an error; it produces a plausible answer to a question
nobody asked. That is the worst failure shape this app has, which is why it is
worth interrupting someone once to prevent.

The dialog cannot be dismissed into a *worse* state than not asking: closing it
keeps the existing default and writes the config file anyway, so the question
is asked exactly once either way. Nagging somebody every launch would be the
unsolicited interruption GFP-96 was right about.
"""
from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from .. import config

#: Five digits. Matches ``config._postal_code``'s own rule deliberately -- if
#: this were looser the dialog would accept something the config layer then
#: rejected, and the user would be told "invalid" by a component they cannot
#: see after the one they can see accepted it.
ZIP_PATTERN = re.compile(r"^\d{5}$")


class FirstRunDialog(QDialog):
    """One question, asked once."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Welcome to Grocery Planner")
        self.setModal(True)

        layout = QVBoxLayout(self)

        heading = QLabel("Which ZIP code do you shop in?")
        heading.setStyleSheet("font-size: 15px; font-weight: 600;")
        layout.addWidget(heading)

        explanation = QLabel(
            "Grocery prices are different in every area, so the app needs to "
            "know which one to look up. You can change it any time — it is "
            "shown in the top-right corner of the main window."
        )
        explanation.setWordWrap(True)
        explanation.setStyleSheet("color: #666;")
        layout.addWidget(explanation)

        self.zip_input = QLineEdit()
        self.zip_input.setPlaceholderText("12345")
        self.zip_input.setMaxLength(5)
        self.zip_input.setAlignment(Qt.AlignCenter)
        self.zip_input.setStyleSheet("font-size: 20px; padding: 6px;")
        # Deliberately NOT pre-filled with the 27401 default. A box already
        # containing a plausible ZIP invites Enter, which is exactly the
        # silent-wrong-city outcome this dialog exists to prevent.
        self.zip_input.textChanged.connect(self._validate)
        layout.addWidget(self.zip_input)

        self.problem = QLabel("")
        self.problem.setStyleSheet("color: #b3261e;")
        layout.addWidget(self.problem)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self.buttons.button(QDialogButtonBox.Ok).setText("Start")
        self.buttons.button(QDialogButtonBox.Cancel).setText("Skip for now")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self._validate("")

    def _validate(self, text: str) -> None:
        ok = bool(ZIP_PATTERN.match(text.strip()))
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(ok)
        # Silent while they are still typing: telling somebody "123 is not a
        # ZIP code" after three of five digits is scolding them for not having
        # finished.
        self.problem.setText(
            "" if ok or len(text.strip()) < 5 else "A ZIP code is five digits."
        )

    def postal_code(self) -> str:
        return self.zip_input.text().strip()


def ask(parent: QWidget | None = None) -> str | None:
    """Ask for the ZIP and save it. Returns what was saved, or ``None``.

    ``None`` means the user skipped, and the default stands. The config file is
    written either way, so :func:`config.is_first_run` becomes False and this
    is not asked again -- being nagged every launch is worse than a default
    somebody declined to change.
    """
    dialog = FirstRunDialog(parent)
    accepted = dialog.exec() == QDialog.Accepted
    chosen = dialog.postal_code() if accepted else None

    if chosen:
        try:
            config.set_value("postal_code", chosen)
            return chosen
        except Exception:                       # noqa: BLE001
            # A ZIP that passed ZIP_PATTERN and still failed the config layer
            # means the two rules have drifted. Fall through to writing the
            # defaults so the app is at least in a consistent state.
            pass

    config.write_defaults()
    return None
