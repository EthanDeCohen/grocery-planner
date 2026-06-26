"""Resolve per-OS locations for the local database and user data.

Uses platformdirs so the DB/settings live in the proper user-data folder
(%APPDATA% on Windows, ~/Library/Application Support on macOS) and survive
app updates. Override with the GROCERY_PLANNER_DB environment variable.
"""
from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "grocery-planner"
APP_AUTHOR = "grocery-planner"
DB_ENV_VAR = "GROCERY_PLANNER_DB"


def data_dir() -> Path:
    """Return the per-OS user-data directory, creating it if needed."""
    d = Path(user_data_dir(APP_NAME, APP_AUTHOR))
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    """Path to the SQLite database (honors the GROCERY_PLANNER_DB override)."""
    override = os.environ.get(DB_ENV_VAR)
    if override:
        p = Path(override).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    return data_dir() / "grocery_planner.sqlite3"
