# ######### decohen-partners ##########
# Protein Ledger
"""Resolve per-OS locations for the local database and user data.

Uses platformdirs so the DB/settings live in the proper user-data folder and
survive app updates. Override with the GROCERY_PLANNER_DB environment variable.

The real resolved locations, verified rather than assumed -- GFP-102's manual
removal checklist and packaging/INSTALL.md both quote them, and a checklist
naming the wrong directory is worse than none:

    Windows   %LOCALAPPDATA%\\grocery-planner\\grocery-planner
    macOS     ~/Library/Application Support/grocery-planner

platformdirs uses LOCALAPPDATA, not APPDATA, on Windows -- so this is
deliberately NOT the roaming profile. Roaming a multi-megabyte SQLite database
across a domain login is not something to do to somebody.
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
