# ######### decohen-partners ##########
# Protein Ledger
"""PySide6 desktop UI — the nutritionist front end (GFP-20).

Requires the optional ``gui`` extra (``pip install -e ".[gui]"``). Launch with
``python -m grocery_planner.gui`` or the ``gplan-gui`` console script.

Layout since GFP-35 retired the Deals/Formulas/Schedule tabs:

- :mod:`.app`      — the window shell, menu bar, navigation and CSV export; ``main()``.
- :mod:`.roster`   — client roster pane, left of the main view (GFP-36).
- :mod:`.trends`   — price-trends chart, right of it (GFP-36).
- :mod:`.client`   — client detail page, joining its three columns (GFP-37).
- :mod:`.biometrics` — client biometrics and protein headline (GFP-51).
- :mod:`.billpanel`  — daily protein bill and preference checkboxes (GFP-52).
- :mod:`.wheretobuy` — where each bill line can be bought (GFP-38).
- :mod:`.scrape`   — Data ▸ Run scrape…
- :mod:`.formulas` — Settings ▸ Formulas…
- :mod:`.schedule` — Settings ▸ Automatic refresh…
- :mod:`.widgets`  — helpers shared by the above.
"""
