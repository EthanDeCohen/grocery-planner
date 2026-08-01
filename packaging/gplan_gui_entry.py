"""Frozen-GUI entry point. See ``gplan_entry.py``; same rules apply."""
from multiprocessing import freeze_support

from grocery_planner.gui.app import main

if __name__ == "__main__":
    freeze_support()
    raise SystemExit(main())
