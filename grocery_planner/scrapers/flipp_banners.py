# ######### decohen-partners ##########
# Protein Ledger
"""Flipp-sourced banners, registered from a table (GFP-165).

Why a table and not eleven modules
-----------------------------------
``foodlion``, ``harristeeter`` and ``giant_ad`` are each a thin module that
supplies a :class:`~grocery_planner.scrapers.base.StoreConfig` and delegates to
``base.scrape_store``. That is the right shape at two or three stores. The
2026-08-09 Flipp survey of Philadelphia and Greensboro found eleven more on our
target lists, and eleven more copies of the same twenty-five lines would be
duplication nobody could keep in step -- the failure GFP-90 records, where store
identity ended up living in three places because each integration added its own.

So a banner here is ONE ROW in :data:`BANNERS`. Adding a store is a line, which
is what GFP-207's onboarding runbook is trying to reach.

The registry is duck-typed -- ``scrapers/__init__.py`` reads ``STORE_KEY``,
``SOURCE``, ``MERCHANT``, ``DEFAULT_POSTAL_CODE``, ``scrape`` and optionally
``serves`` off whatever object it is given -- so an instance satisfies it
exactly as a module does. The existing three modules stay as they are: other
code imports them by name, and rewriting working integrations to prove a point
is not a change worth its risk.

What these are worth, measured before adding them
-------------------------------------------------
Every Flipp ad is thin, and it is thin in the same way. Measured live across
fourteen merchants on 2026-08-09: **2-8% of rows match a protein food, and
0-5% carry a machine-readable size**. Of the 905 rows in the live database that
reach a $/g-protein figure, catalogue sources supply 81% and every Flipp ad
combined supplies 23 rows -- 2.5%.

So these are NOT added for coverage. A weekly ad is the per-ZIP PRICE half of
GFP-248's join; the size and protein must come from a catalogue. Each row below
records the measured protein density so nobody re-litigates it, and two are
flagged as poor value on purpose rather than quietly dropped.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import base


@dataclass(frozen=True)
class Banner:
    """One Flipp-sourced store, and what it was measured to be worth."""

    config: base.StoreConfig
    display_name: str
    #: Percentage of this merchant's ad rows that matched a protein food when
    #: surveyed on 2026-08-09. Recorded, not enforced -- it is guidance for
    #: scrape budgeting, and a number that will drift with the ad each week.
    protein_density: float
    #: Free-text caveat shown to an operator choosing what to scrape.
    note: str = ""

    @property
    def key(self) -> str:
        return self.config.key


#: The banners found on Flipp for Philadelphia (19103) and Greensboro (27401),
#: limited to chains on the 2026-08-09 target lists. Merchant strings are
#: EXACTLY as Flipp labels them -- "Wegman's" carries an apostrophe and
#: "Lowes Foods" does not, and a near-miss silently matches nothing.
BANNERS: tuple[Banner, ...] = (
    Banner(base.StoreConfig("acme", "Acme Markets", "for U", "19103"),
           "ACME Markets", 4.6),
    Banner(base.StoreConfig("wegmans", "Wegman's", "Shoppers Club", "19103"),
           "Wegmans", 1.9,
           "Poor value: 1.9% protein density, the second-lowest measured."),
    Banner(base.StoreConfig("weis", "Weis Markets", "Preferred Shoppers", "19103"),
           "Weis Markets", 4.8),
    Banner(base.StoreConfig("hmart", "H Mart", "Smart Card", "19103"),
           "H Mart", 4.7),
    Banner(base.StoreConfig("lowesfoods", "Lowes Foods", "Fresh Rewards", "27401"),
           "Lowes Foods", 7.8,
           "Best of the additions: 7.8% protein and 5.3% with a size."),
    Banner(base.StoreConfig("publix", "Publix", "Club Publix", "27401"),
           "Publix", 8.1,
           "Highest protein density measured, but only 54.7% of rows are priced."),
    Banner(base.StoreConfig("aldi", "ALDI", "", "27401"),
           "ALDI", 4.5,
           "96% priced and 0% sized -- limited assortment, no size data at all."),
    Banner(base.StoreConfig("lidl", "Lidl", "myLidl", "27401"),
           "Lidl", 3.7,
           "99% priced and 0% sized -- same shape as ALDI."),
    Banner(base.StoreConfig("sprouts", "Sprouts Farmers Market", "", "27401"),
           "Sprouts Farmers Market", 3.2),
    Banner(base.StoreConfig("target", "Target", "Circle", "27401"),
           "Target", 1.6,
           "Poor value: 1.6% protein, the lowest measured -- a general "
           "merchandiser whose ad is mostly not grocery."),
)

#: Below this measured density a banner is unlikely to repay its scrape budget.
#: Not enforced anywhere: it is a number for an operator to weigh, and the
#: alternative -- silently refusing to scrape a store the user asked for -- is
#: the kind of hidden decision this project keeps out of the engine.
LOW_DENSITY = 2.0


class _FlippBanner:
    """A registry entry with the surface ``scrapers/__init__.py`` expects."""

    def __init__(self, banner: Banner) -> None:
        self._banner = banner
        self.STORE_KEY = banner.config.key
        self.SCRAPER_KEY = banner.config.key
        self.MERCHANT = banner.config.merchant_name
        self.DEFAULT_POSTAL_CODE = banner.config.default_postal_code
        self.SOURCE = "scrape"

    def scrape(
        self, postal_code: str | None = None, include_coupons: bool = True
    ) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
        return base.scrape_store(
            self._banner.config, postal_code=postal_code,
            include_coupons=include_coupons,
        )

    def serves(self, postal_code: str) -> bool | None:
        """GFP-257: does this merchant publish an ad here? Asked, never declared."""
        return base.serves_postal_code(self._banner.config, postal_code)

    def __repr__(self) -> str:                       # pragma: no cover - debugging
        return f"<FlippBanner {self.STORE_KEY}>"


MODULES: dict[str, _FlippBanner] = {
    b.key: _FlippBanner(b) for b in BANNERS
}

BY_KEY: dict[str, Banner] = {b.key: b for b in BANNERS}
