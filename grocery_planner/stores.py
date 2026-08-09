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
]

BY_KEY: dict[str, Store] = {s.key: s for s in STORES}
