"""PySide6 desktop UI — the nutritionist front end (GFP-20).

Requires the optional ``gui`` extra (``pip install -e ".[gui]"``). Launch with
``python -m grocery_planner.gui`` or the ``gplan-gui`` console script.

Layout since GFP-35 retired the Deals/Formulas/Schedule tabs:

- :mod:`.app`      — the window shell, menu bar and CSV export; ``main()``.
- :mod:`.scrape`   — Data ▸ Run scrape…
- :mod:`.formulas` — Settings ▸ Formulas…
- :mod:`.schedule` — Settings ▸ Automatic refresh…
- :mod:`.widgets`  — helpers shared by the above.
"""
