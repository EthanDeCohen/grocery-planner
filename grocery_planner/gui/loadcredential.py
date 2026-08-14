# ######### decohen-partners ##########
# Protein Ledger
"""Settings ▸ Load credential… (GFP-148).

**Why this exists, and why it is temporary.** v1 ships the Kroger API key, but
a user who is emailed a credential file still has to get it into a per-OS data
directory whose path is different on every platform and is inside a hidden
Library folder on macOS. Asking a nutritionist to do that in Terminal is not a
setup step, it is a support call. This turns it into: save the attachment,
click a menu item, choose the file.

**It does not say "Kroger".** :func:`credentials.identify` works out which
credential a document is from the keys it contains, matched against each
spec's declared ``keys``. So the menu item is "Load credential…", one dialog
covers every credential the app has, and adding a new one to ``SPECS`` teaches
this screen about it for free.

GFP-149 removes this whole path in v2, once the hosted server supplies the
credential instead. That is why :func:`credentials.install` records what it
placed: the removal has to be able to delete what this dialog wrote without
touching a credential the user made themselves.

**Nothing here displays the credential.** The file is read, identified by its
key NAMES, and written. A value never reaches a widget, a log line or an
exception message -- a secret on screen is a secret in a screenshot.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from .. import credentials

#: What the file picker offers. Kroger's is .config, Whole Foods' and the
#: licence are .json, and people rename email attachments, so "All files" has
#: to be reachable or the dialog will hide the very file it is asking for.
FILE_FILTER = (
    "Credential files (*.config *.json *.txt *.ini);;All files (*)"
)


def _friendly(name: str) -> str:
    """A credential's name as a person would say it."""
    return {
        "kroger": "Harris Teeter (Kroger) prices",
        "wholefoods": "Whole Foods prices",
        "licence": "your licence key",
    }.get(name, name)


def load(parent: QWidget | None = None) -> credentials.CredentialSpec | None:
    """Ask for a credential file and install it. Returns which one, or None.

    ``None`` means the user cancelled, or something went wrong and has already
    been reported -- the caller only needs to know whether to refresh.
    """
    chosen, _selected_filter = QFileDialog.getOpenFileName(
        parent,
        "Choose the credential file you were sent",
        str(Path.home()),
        FILE_FILTER,
    )
    if not chosen:
        return None

    path = Path(chosen)
    try:
        document = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        QMessageBox.warning(
            parent, "Could not read that file",
            f"{path.name} could not be opened.\n\n{exc}",
        )
        return None

    spec = credentials.identify(document)
    if spec is None:
        # Deliberately does not quote the file's contents back. It is either a
        # secret or somebody's unrelated document, and neither belongs on
        # screen.
        QMessageBox.warning(
            parent, "That is not a credential file",
            f"{path.name} is not a credential this app recognises.\n\n"
            "Look for the file you were emailed -- it is a small text file, "
            "usually named kroger-env.config.",
        )
        return None

    # Replacing is the normal reason to be here (an expired or rotated
    # credential), so this confirms rather than refuses -- but it does say
    # plainly that something is being replaced.
    existing = credentials.LocalFileProvider().path(spec)
    if existing.exists():
        answer = QMessageBox.question(
            parent, "Replace the existing credential?",
            f"A credential for {_friendly(spec.name)} is already set up.\n\n"
            "Replace it with this file?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return None

    try:
        installed = credentials.install(document, source=f"loaded:{path.name}")
    except credentials.CredentialError as exc:
        QMessageBox.warning(parent, "Could not load that credential", str(exc))
        return None
    except OSError as exc:
        QMessageBox.warning(
            parent, "Could not save the credential",
            f"The file could not be written to {credentials.data_dir()}.\n\n{exc}",
        )
        return None

    QMessageBox.information(
        parent, "Credential loaded",
        f"Set up {_friendly(installed.name)}.\n\n"
        "Prices will use it from the next refresh.",
    )
    return installed
