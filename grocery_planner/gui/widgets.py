"""Small Qt helpers shared by the desktop shell and its dialogs (GFP-35).

Kept out of :mod:`grocery_planner.gui.app` so the dialogs split out of the old
tab layout can use it without importing the main window — that would be a
cycle, since the window imports the dialogs.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidgetItem


def placeholder(text: str) -> QListWidgetItem:
    """An unselectable "nothing here yet" row, so an empty list explains itself."""
    item = QListWidgetItem(text)
    item.setFlags(Qt.NoItemFlags)
    return item
