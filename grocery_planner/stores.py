"""Registry of tracked stores: display name <-> data folder <-> key.

Mirrors the README store mapping. The scrapers and CSV importer both key off
this list so adding a store is a one-line change here.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Store:
    key: str          # stable id, also the data/<folder> name
    display_name: str
    data_folder: str


STORES: list[Store] = [
    Store("foodlion", "Food Lion", "foodlion"),
    Store("wholefoods", "Whole Foods", "wholefoods"),
    Store("harristeeter", "Harris Teeter", "harristeeter"),
    # GFP-247: the Philadelphia PRISM banner. The GIANT Company
    # (giantfoodstores.com), not Giant Food of Landover MD.
    Store("giant", "GIANT", "giant"),
    # GFP-165: banners found by the 2026-08-09 Flipp survey of Philadelphia
    # (19103) and Greensboro (27401). Registered from a table in
    # scrapers/flipp_banners.py; listed here so a deal can be attributed to a
    # display name. Ordered as surveyed, not by preference.
    Store("acme", "ACME Markets", "acme"),
    Store("wegmans", "Wegmans", "wegmans"),
    Store("weis", "Weis Markets", "weis"),
    Store("hmart", "H Mart", "hmart"),
    Store("lowesfoods", "Lowes Foods", "lowesfoods"),
    Store("publix", "Publix", "publix"),
    Store("aldi", "ALDI", "aldi"),
    Store("lidl", "Lidl", "lidl"),
    Store("sprouts", "Sprouts Farmers Market", "sprouts"),
    Store("target", "Target", "target"),
]

BY_KEY: dict[str, Store] = {s.key: s for s in STORES}
