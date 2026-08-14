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

``sprouts`` and ``aldi`` are a third shape again: two tenants of ONE Instacart
Storefront Pro client (``instacart_storefront``), reached by persisted GraphQL
queries with a guest session and **no credential of any kind**. Their
``readiness()`` is therefore a constant. See that module's docstring for the
traps that shape it -- rotating query hashes of which the important one cannot
be auto-discovered, and a product-page path rate-policed far more tightly than
``/graphql``.

Both are SECOND sources for banners that already have a Flipp one, so both
carry a distinct ``SCRAPER_KEY`` (``sprouts-storefront``, ``aldi-storefront``)
and a distinct ``SOURCE``. That is not a stylistic choice: a hand-written module
whose key collides with a banner is silently OVERWRITTEN by the banner below,
because ``SCRAPERS.update(flipp_banners.MODULES)`` is last-write-wins. It
happened to ``sprouts`` and cost a live debugging session -- the module imported
fine and passed all its own tests while being unreachable from the CLI.
``tests/test_scraper_registry.py`` now asserts that relationship.

``traderjoes`` is a fourth: a public Magento 2 GraphQL API that accepts
arbitrary queries with introspection enabled, so it needs neither persisted
hashes nor a browser. It is the only store with no collision, hence no
``SCRAPER_KEY`` of its own.

A module may therefore declare:

- ``SCRAPER_KEY`` -- the CLI/registry name. Defaults to ``STORE_KEY``.
- ``STORE_KEY``   -- the ``deals.store`` value. Two modules MAY share one.
- ``SOURCE``      -- the ``deals.source`` value. Defaults to ``"scrape"``.
  This is what keeps two feeds for one store from overwriting each other:
  ``service/ingest.run_scrape`` scopes its replace to
  ``(store, source, postal_code)``.
"""
from . import (
    aldi, flipp_banners, foodlion, foodlion_catalog, giant, giant_ad,
    harristeeter, kroger, lidl_catalogue, publix, sprouts, traderjoes,
    walmart, wegmans_api, wholefoods,
)

_MODULES = (
    aldi, foodlion, foodlion_catalog, giant, giant_ad, harristeeter, kroger,
    lidl_catalogue, publix, sprouts, traderjoes, walmart, wegmans_api,
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
