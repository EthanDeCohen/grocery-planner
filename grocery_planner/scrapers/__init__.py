"""Store scrapers. ``base`` owns the shared Flipp/Wishabi client (GFP-6); each
Flipp-sourced store module supplies its
:class:`~grocery_planner.scrapers.base.StoreConfig`. ``wholefoods`` (GFP-4) is
a different shape of source (the retailer's own storefront, not a Flipp
flyer) and is plain ``httpx`` against that storefront instead -- see its
module docstring.

``SCRAPERS`` is the registry the CLI dispatches on — adding a store is: create a
thin module (see ``foodlion``/``harristeeter``/``wholefoods``) and list it here.

``kroger`` (GFP-98) is the first module where the registry key and the store
are NOT the same thing: it is a second source for a store that already has one.
``harristeeter`` is the Flipp weekly ad, ``harristeeter-api`` is Kroger's shelf
price API for the same physical shop. They complement each other -- Flipp
carries BOGO and coupon promotions the API does not, the API carries sizes and
nutrition Flipp never has -- so neither may evict the other.

A module may therefore declare:

- ``SCRAPER_KEY`` -- the CLI/registry name. Defaults to ``STORE_KEY``.
- ``STORE_KEY``   -- the ``deals.store`` value. Two modules MAY share one.
- ``SOURCE``      -- the ``deals.source`` value. Defaults to ``"scrape"``.
  This is what keeps two feeds for one store from overwriting each other:
  ``service/ingest.run_scrape`` scopes its replace to
  ``(store, source, postal_code)``.
"""
from . import (
    flipp_banners, foodlion, foodlion_catalog, giant, giant_ad, harristeeter,
    kroger, wholefoods,
)

_MODULES = (
    foodlion, foodlion_catalog, giant, giant_ad, harristeeter, kroger,
    wholefoods,
)

SCRAPERS = {getattr(m, "SCRAPER_KEY", m.STORE_KEY): m for m in _MODULES}
# GFP-165: the banners found by the 2026-08-09 Flipp survey, registered from a
# table rather than as eleven near-identical modules. They satisfy the same
# duck-typed surface -- see flipp_banners for why that is the right shape here
# and why the three hand-written modules above were left alone.
SCRAPERS.update(flipp_banners.MODULES)


def store_key_for(module, default: str | None = None) -> str:
    """The ``deals.store`` value a scraper module writes under.

    Falls back to ``default`` (the registry key it was looked up by) when the
    module declares no ``STORE_KEY``, so a stand-in object -- a test double, or
    a future scraper that simply doesn't need the distinction -- keeps working
    without having to know this convention exists.
    """
    return getattr(module, "STORE_KEY", None) or default or ""


def source_for(module) -> str:
    """The ``deals.source`` value a scraper module writes under."""
    return getattr(module, "SOURCE", None) or "scrape"
