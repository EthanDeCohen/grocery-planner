# ######### decohen-partners ##########
# Protein Ledger
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
    # GFP-264. The only new store of this round that is not a second feed for a
    # banner already here -- Sprouts and ALDI reuse their existing rows, since
    # `sprouts-storefront`/`aldi-storefront` are extra SOURCES for one shop, not
    # extra shops. Without this entry `gplan stores` and the GUI's store table
    # would have no row to hang Trader Joe's deals on and the store would be
    # invisible in the UI while its scraper ran perfectly.
    Store("traderjoes", "Trader Joe's", "traderjoes"),
    # GFP-270. Walmart is a new shop; Publix is NOT -- its extra feed is a
    # second SOURCE for the banner already listed above, the same relationship
    # `sprouts-storefront` has to `sprouts`. (That feed was `publix-catalog`
    # until GFP-304 replaced it with `publix-storefront`.) Without this row Walmart's deals
    # would render under the raw key ("walmart") in the cheapest strip and the
    # store table, which is how a store ends up looking like a bug.
    Store("walmart", "Walmart", "walmart"),
]

BY_KEY: dict[str, Store] = {s.key: s for s in STORES}
