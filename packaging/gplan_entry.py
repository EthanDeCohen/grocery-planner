"""Frozen-CLI entry point.

PyInstaller needs a real script to start from; ``gplan = grocery_planner.cli:app``
in pyproject only helps pip-installed use. Keep this file trivial — anything
worth testing belongs in the package, not in the launcher.
"""
from multiprocessing import freeze_support

from grocery_planner.cli import app

if __name__ == "__main__":
    freeze_support()  # harmless without multiprocessing, essential if it appears
    app()
