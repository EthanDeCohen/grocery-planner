"""Instacart Storefront Pro -- the platform client behind Sprouts and ALDI.

GFP-265. ``sprouts.py`` (GFP-262) was written as a Sprouts scraper but its own
module docstring already said the truth: ``shop.sprouts.com`` is not Sprouts'
code, it is an **Instacart Storefront Pro** tenant, and "re-aiming it at another
banner is a slug change, not a rewrite". This module is that sentence cashed in.
Everything tenant-independent lives here; ``sprouts.py`` and ``aldi.py`` are
:class:`Tenant` records over it.

The claim was tested, not assumed. Measured 2026-08-11 against
``https://www.aldi.us``:

* the storefront returns **200 to plain httpx** -- no browser, no headless
  browser, no cookie to hand-mint. It sets the same ``__Host-instacart_sid``
  guest cookie ``shop.sprouts.com`` does.
* :func:`discover_persisted_queries`, **unmodified**, found **59** operation
  hashes in ALDI's storefront HTML.
* those hashes are not merely the same *shape* as Sprouts' -- for
  ``SimpleShopCollection`` the hash is byte-identical
  (``d438f50c...74501a``) on both tenants. Apollo hashes the query *document*,
  and the document ships in the shared platform bundle, so a hash is a property
  of the **Instacart deploy**, not of the banner. That is why the pinned
  ``ProductNutritionalInfo`` hash transfers between tenants (see PINNING below).

WHY THERE IS NO BROWSER HERE, AND WHY THAT IS NOT NEGOTIABLE
------------------------------------------------------------
A DOM-scraping approach was proposed for ALDI -- drive Chromium, scroll the
category pages, read ``inner_text()``, match product names against a hard-coded
brand list (``kirkwood``, ``clancy``, ``parkview``...), regex the prices out of
the text. It was not built, for two reasons that are worth writing down because
they will be proposed again:

1. **A browser does not ship.** Established by the GFP-4 Whole Foods spike: the
   distributed desktop app cannot carry a browser binary. A Playwright scraper
   could never leave a dev machine, so it is not a slower version of this -- it
   is a thing that never reaches a customer. (Project rule, GFP-265: any browser
   this project launches runs headless, always, precisely because a headed one
   cannot run in CI, in GFP-102's unattended scheduled scrape, or on a customer
   machine. Here the rule is satisfied vacuously -- there is no browser.)
2. **It throws away data the platform already hands over.** The DOM has a
   rendered price string; the platform has numeric ``protein``,
   ``servingSize``, ``servingsPerContainer`` and schema.org ``price``/``size``.
   And a brand allow-list silently drops every product whose brand is not on
   the list -- a coverage cap that never appears in ``stats``, which is the
   exact failure mode the no-silent-caps rule exists to prevent.

ACCESS SHAPE: persisted queries only
------------------------------------
Everything data-bearing is a GraphQL **GET** against ``/graphql``::

    /graphql?operationName=X
            &variables={...}
            &extensions={"persistedQuery":{"version":1,"sha256Hash":"..."}}

Arbitrary queries are refused -- a plain ``{"query":"{__typename}"}`` POST
returns ``PersistedQueryNotSupported``, and so does introspection. Only the
server's allow-list of hashes will run. Auth is a guest session that mints
itself on the first GET of the storefront.

THE ROT TRAP: the hashes rotate, and only *some* are discoverable
-----------------------------------------------------------------
``sha256Hash`` values rotate per Instacart deploy -- the same failure mode as
the Whole Foods ``buildId`` (GFP-4). Every page embeds a performance-timing blob
naming each operation it fired *together with the hash it used*, so
:meth:`StorefrontClient.discover` harvests those on every run. The blob is
**doubly URL-encoded**; one ``unquote`` pass leaves the braces still escaped and
the regex silently matches nothing, which is a quiet way to end up on stale
pins. Hence the two passes in :func:`discover_persisted_queries`.

**But the most important operation is not in any blob.**
``ProductNutritionalInfo`` -- the one that carries protein -- is fetched
client-side after hydration, so its hash appears in no HTML the server sends.
Nor can it be recovered from the JS: the bundles ship the query document
*stripped of its selection set*::

    {kind:"Document",definitions:[{kind:"OperationDefinition",
     operation:"query",name:{kind:"Name",value:"ProductNutritionalInfo"}}]}

Apollo's persisted-query link hashes the full document at runtime, and the full
document is not in the eagerly-loaded chunks, so the hash can be neither read
nor recomputed. It was captured by watching the network in a real browser, and
it is **pinned** per tenant in :attr:`Tenant.pinned_hashes`.

PINNING: per tenant by construction, shared by measurement
-----------------------------------------------------------
The pin lives on the :class:`Tenant`, not on this module, so two banners on
different Instacart deploys can hold different values without either one having
to know about the other. As measured on 2026-08-11 they do **not** differ:
Sprouts' pinned ``ProductNutritionalInfo`` hash was replayed against ALDI and
was **accepted** -- HTTP 200, no ``PERSISTED_QUERY_NOT_FOUND``, and a
well-formed ``ItemsProductNutritionalInfo`` envelope. That is consistent with
the ``SimpleShopCollection`` hash matching byte-for-byte, and with hashes being
a property of the deploy rather than the banner.

The value is nonetheless written out in full in each tenant module rather than
imported from a shared constant. Sharing the *literal* would encode "these are
always equal" as a fact, and it is only an observation: the two banners can be
moved onto different deploy trains at any time, and the day that happens the
symptom of a shared constant is one tenant silently losing all protein data. A
duplicated literal that two canaries check independently fails loudly instead,
which is the same reasoning the rename guards use (assert a relationship, not a
spelling -- and here the relationship is "each tenant's own pin answers its own
canary").

THE THROTTLE TRAP: /graphql is generous, product HTML is not
------------------------------------------------------------
These two paths are policed completely differently, and the difference decides
the design of every tenant's ``scrape``:

===================  ==========================  ==========================
path                 observed behaviour          verdict
===================  ==========================  ==========================
``/graphql``         37,500+ requests at ~36/s,  bulk-safe
                     zero errors, zero 429s
``/store/.../        ~2,300 pages, then a hard   bounded working set only
products/<slug>``    **403** on every subsequent
                     product page (the storefront
                     and /graphql kept serving)
===================  ==========================  ==========================

Measured on Sprouts. It is assumed to hold on every tenant until measured
otherwise, because assuming the generous direction is the mistake that gets an
IP blocked. Two consequences:

**The work is split by which path can carry it.** Nutrition -- the expensive,
per-product part -- goes through GraphQL. The page fetch is used *only* for
price, and *only* for products that already resolved a protein figure. A scrape
that gets nutrition but no price is degraded, not failed.

**Each path paces itself.** :class:`StorefrontClient` holds one
:class:`~grocery_planner.scrapers.retry.Paced` per path class
(:data:`~grocery_planner.scrapers.retry.GRAPHQL_BUDGET` and
:data:`~grocery_planner.scrapers.retry.PRODUCT_PAGE_BUDGET`). The pacing
counters land in ``stats`` so a run that was throttled into crawling says so.

THE SERVICE-TYPE TRAP -- found by generalising, and it was a live bug
---------------------------------------------------------------------
``SimpleShopCollection`` returns bare shop ids in an **unspecified order** and
with no indication of what each one is. The GFP-262 code took the first one.
Measured 2026-08-11 for postal code 27401:

===========  ================  ==========================================
tenant       ids, in order     service types
===========  ================  ==========================================
sprouts      515202, 5201,     **instore**, delivery, pickup
             5202
aldi         6823, 22443,      delivery, pickup, **instore**
             515201
===========  ================  ==========================================

All three of a tenant's shops are the **same physical store** -- for ALDI all
three carry ``retailerLocationId`` 124437, "ALDI - SBY 140 - Greensboro",
2965 Battleground Ave. They differ only in ``serviceType``. So "take the first"
was not a rule, it was a coincidence that happened to be right for the one
tenant it was written against and is wrong for the next one. Taking a delivery
shop would price a delivery basket -- a different number from the shelf price
this project exists to compare.

:meth:`StorefrontClient.shop_context` therefore asks ``ShopCollectionScoped``,
which *does* return ``serviceType``, and prefers :data:`PREFERRED_SERVICE_TYPE`.
``SimpleShopCollection`` is kept as a fallback for the case where the scoped
operation is not in a tenant's discovered set, and when that fallback is used
the resulting :class:`ShopContext` says so in :attr:`ShopContext.service_type`
(``None`` -- unknown, never guessed) so a caller can tell a verified in-store
shop from an assumed one.

Price and size come from schema.org
-----------------------------------
Product pages server-render a ``application/ld+json`` block about 12 KB in,
carrying ``name``, ``brand``, ``size``, ``offers.price`` and ``availability``.
Reading only the first :data:`HEAD_BYTES` of the response gets all of it for
~1/15th of the bandwidth of the full page.

Caveat, measured on ALDI and documented rather than papered over: the product
page renders under whatever shop the *session* defaults to, and it ignores a
``shop_id`` query parameter -- ALDI's page reported shop 6823 (delivery) for
every value tried, and returned the same $3.22 each time. So the JSON-LD price
is the storefront's canonical price for that banner and is **not** re-priced per
service type. It is the same number a shopper sees on the site, which is what
this project compares; it is not independently verified against a physical
shelf tag, and no code here claims it is.

THE WEIGHT TRAP -- the same one as GFP-98
-----------------------------------------
Fresh meat renders ``size: "per lb"`` with ``"$11.19 each (est.)"`` and "About
1.x lb", while ``servingsPerContainer`` describes the whole package. Multiplying
the two mixes denominators and understates cost several-fold, on precisely the
highest-protein items. :func:`protein_per_100g` therefore refuses to multiply
when :func:`size_is_weight` is true, and falls back to the density route
(serving grams) which is denominated correctly either way. This is the same rule
kroger.py applies to ``soldBy=WEIGHT``.

THE 'Varied' TRAP
-----------------
``servingsPerContainer`` is a **string** and is frequently non-numeric --
``"Varied"`` and ``""`` together account for ~15% of Sprouts panels. It must
never be coerced with a bare ``float()``; :func:`servings_per_container` returns
``None`` for anything that is not a plain number.

THE THREE SERVING-SIZE SHAPES
-----------------------------
See :data:`_FLUID`. Matching only the parenthesised-metric shape costs ~5x the
coverage of the density route (measured: 2,110 panels vs 10,825 of 15,163).

THE 'per lb' PRICING-UNIT TRAP
------------------------------
Kroger's ``size`` for a WEIGHT item literally reads ``"1 lb"``, already a
pricing unit. This platform writes the same fact as ``"per lb"``, which
``savings.parse_size`` does not understand -- it returns ``None``, so the size
would be silently lost on exactly the fresh-meat rows where price and size must
agree. :func:`pricing_unit_size` normalises the dialect here rather than
teaching the shared grammar a new form.

THE MISSING-SIZE TRAP -- see :func:`display_item_name`
------------------------------------------------------
Copying kroger.py's "fold the size in only for WEIGHT items" rule left every
UNIT row without a size in ``item_name``, which is the only place the ranking
looks. 32 of 155 real rows were rankable and ``gplan cheapest`` said "Nothing to
rank yet" while the rows sat there priced and with protein.

THE IMPOSSIBLE-DENSITY TRAP -- see :func:`plausible_density`
-------------------------------------------------------------
The whole-package route produced 677.3 and 306.7 g protein per 100 g on a real
run, because ``size`` was the per-unit weight while ``servingsPerContainer``
counted the multi-pack. Rejected rather than clamped, and counted in
``stats['density_rejected_implausible']``.

BOTH OF THOSE WERE INVISIBLE TO UNIT TESTS
-------------------------------------------
Neither bug raised, and every module was individually correct. They were found
by loading real rows and running the app, and both are the same species as the
service-type trap above: code that is right for the source it was written
against and silently wrong for the next one. The lesson recorded here for
whoever adds the third tenant -- **scrape a few hundred real rows and run
``gplan cheapest`` against them before believing the tests.**
"""
from __future__ import annotations

import json
import re
import sqlite3
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator

import httpx

from .. import matching, weight_basis
from . import base, retry

GRAPHQL_PATH = "/graphql"
STOREFRONT_PATH = "/store/{slug}/storefront"
PRODUCT_PATH = "/store/{slug}/products/{product_slug}"
SITEMAP_PATH = "/sitemaps/storefront_pro/{host_key}/sitemap.xml"

#: Only the first chunk of a product page is read: the JSON-LD block sits ~12 KB
#: in, the whole page is ~460 KB.
HEAD_BYTES = 30_000

#: The shop this project wants: shelf price, not a delivery basket. See the
#: service-type trap.
PREFERRED_SERVICE_TYPE = "instore"

#: The operations that must be pinned because discovery cannot find them.
PINNED_OPERATIONS = ("ProductNutritionalInfo",)

_LD_JSON = re.compile(
    r'<script type="application/ld\+json">\s*(\{.*?\})\s*</script>', re.S
)
# The storefront's embedded perf blob, after two URL-decode passes, reads
# `operationName=Foo&variables=...&extensions={"persistedQuery":{...,"sha256Hash":"..."}}`.
_OP_HASH = re.compile(
    r'operationName=(\w+).{0,600}?sha256Hash\W{1,10}([0-9a-f]{64})', re.S
)
_SITEMAP_LOC = re.compile(r"<loc>(.*?)</loc>", re.S)
_PLAIN_NUMBER = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*$")
# Serving sizes arrive in at least three shapes and the differences are not
# cosmetic -- matching only the first costs ~5x the coverage of the safe
# density route (measured: 2,110 panels vs 10,825 of 15,163):
#   "8 oz (227g)"          parenthesised metric
#   "3 OZ  85 Gram"        double-spaced, spelled out, no brackets
#   "1 POUCH  3.5 Ounce"   imperial only, needs converting
# Fluid measures are stripped FIRST, because "12 FL OZ. (1 PINT)" is a volume
# and reading 12 as ounces of mass would invent a density out of nothing.
_FLUID = re.compile(r"\b(?:fl\.?\s*oz\.?|fluid\s+ounces?)\b", re.I)
_SERVING_GRAMS = re.compile(
    r"(?<![\w.])([0-9]+(?:\.[0-9]+)?)\s*(?:grammes?|grams?|gm|g)\b", re.I
)
_SERVING_OUNCES = re.compile(
    r"(?<![\w.])([0-9]+(?:\.[0-9]+)?)\s*(?:ounces?|oz)\b", re.I
)
_RATE_UNIT = re.compile(r"\bper\s*(lb|pound|oz|ounce|kg)\b", re.I)


class StorefrontError(RuntimeError):
    """Base for this platform's failures."""


class QueryNotAllowedError(StorefrontError):
    """The server rejected a persisted query hash.

    Almost always means the hashes rotated and discovery did not run (or ran
    against a cached page). Raised rather than swallowed so a scrape that can
    no longer see the catalogue fails visibly instead of reporting zero
    products, which reads identically to "the store has nothing on offer".
    """


class ThrottledError(StorefrontError):
    """The product-page path returned 403 repeatedly. See the throttle trap."""


# --------------------------------------------------------------------------- #
# Tenant
# --------------------------------------------------------------------------- #
def _host_key_from(base_url: str) -> str:
    """``https://shop.sprouts.com`` -> ``shop_sprouts_com``.

    The sitemap path embeds the storefront host with separators flattened to
    underscores. Verified on both live tenants -- ``shop.sprouts.com`` ->
    ``shop_sprouts_com`` and ``www.aldi.us`` -> ``www_aldi_us`` -- and it is
    only a *default*: :attr:`Tenant.sitemap_host_key` overrides it, because a
    derivation that holds on two samples is a convenience, not a law, and the
    next banner may well be the counter-example.
    """
    host = urllib.parse.urlsplit(base_url).netloc
    return re.sub(r"[^0-9a-zA-Z]+", "_", host)


@dataclass(frozen=True)
class Tenant:
    """Everything that differs between two banners on this one platform.

    Deliberately data, not subclass hooks: the whole finding of GFP-265 is that
    adding a banner is a *configuration* change. If a tenant ever needs
    behaviour rather than values, that is the signal it is not really the same
    platform and deserves its own module.
    """

    #: ``deals.store``. Two modules MAY share one -- see scrapers/__init__.py.
    store_key: str
    #: Human-facing merchant name.
    merchant: str
    #: Scheme + host, no trailing slash.
    base_url: str
    #: The ``/store/<slug>/`` path segment and the ``retailerSlug`` argument.
    retailer_slug: str
    #: Instacart's numeric retailer id (Sprouts 279, ALDI 12). Reported in
    #: ``meta``; read back from the live storefront, never invented.
    retailer_id: str
    #: The pin that discovery cannot find. See PINNING.
    pinned_hashes: dict[str, str]
    #: A product with a stable, well-populated panel, used to prove the pin
    #: still works. Per tenant, because a Sprouts SKU means nothing at ALDI.
    #: ``None`` when the tenant has no panel to check -- see aldi.py.
    canary_product_id: str | None
    #: Cold-start defaults only; ``serves``/``scrape`` re-resolve per ZIP.
    default_shop_id: str
    default_zone_id: str = "430"
    default_postal_code: str = "27401"
    #: Overrides :func:`_host_key_from` when a banner does not follow it.
    sitemap_host_key: str | None = None
    #: ``deals.deal_type`` prefix.
    deal_type: str = "Storefront Price"
    #: ``product_identifier_ns`` -- the vocabulary the product id belongs to.
    #: Without it '21171551' as an ALDI id and as a Sprouts id are
    #: indistinguishable strings denoting unrelated products (GFP-111).
    product_identifier_ns: str = ""
    #: The ``source=`` note and the ``foods.source`` value.
    source_label: str = ""
    #: ``deal_description`` for a row whose price the storefront did not list.
    #: Explicit rather than derived from :attr:`merchant`, because a merchant
    #: name may already be qualified ("... (storefront)") and a derived string
    #: would then read back to the user as a stutter.
    priceless_description: str = ""

    def __post_init__(self) -> None:
        if not self.product_identifier_ns:
            object.__setattr__(
                self, "product_identifier_ns", f"{self.store_key}.product_id"
            )
        if not self.source_label:
            object.__setattr__(self, "source_label", f"{self.store_key}_storefront")
        if not self.priceless_description:
            object.__setattr__(
                self, "priceless_description", f"{self.merchant} storefront listing"
            )
        if self.sitemap_host_key is None:
            object.__setattr__(
                self, "sitemap_host_key", _host_key_from(self.base_url)
            )

    # -- derived URLs; all tenant state flows through these ------------------ #
    @property
    def storefront_path(self) -> str:
        return STOREFRONT_PATH.format(slug=self.retailer_slug)

    @property
    def sitemap_index(self) -> str:
        return self.base_url + SITEMAP_PATH.format(host_key=self.sitemap_host_key)

    def product_path(self, product_slug: str) -> str:
        return PRODUCT_PATH.format(
            slug=self.retailer_slug, product_slug=product_slug
        )

    def product_page_url(self, product_slug: str | None) -> str | None:
        return self.base_url + self.product_path(product_slug) if product_slug else None


@dataclass(frozen=True)
class ShopContext:
    """The tenant ids a query needs. Resolved per ZIP, never hard-coded."""

    shop_id: str
    zone_id: str
    postal_code: str
    #: ``"instore"``/``"delivery"``/``"pickup"``, or ``None`` when the fallback
    #: operation was used and the type is genuinely unknown. Never guessed --
    #: see the service-type trap.
    service_type: str | None = None
    #: The physical store behind the shop, when the platform reported one.
    retailer_location_id: str | None = None


@dataclass(frozen=True)
class Nutrition:
    """The subset of a nutrition panel this project acts on."""

    protein_per_serving: float | None
    serving_size: str | None
    servings_per_container: str | None
    calories: float | None = None


@dataclass(frozen=True)
class Listing:
    """What the product page's JSON-LD says. Price may legitimately be None."""

    product_id: str
    slug: str
    name: str | None
    brand: str | None
    category: str | None
    size: str | None
    price: float | None
    availability: str | None


@dataclass
class FoodFact:
    """Nutrition to land in foods/food_nutrients, keyed on the tenant's id."""

    product_id: str
    name: str
    category: str
    protein_per_100g: float
    item_name: str


# --------------------------------------------------------------------------- #
# Pure helpers -- no network, no DB, so the awkward cases are directly testable
# --------------------------------------------------------------------------- #
def product_id_from_slug(slug: str) -> str:
    """``"19793676-iconic-protein-powder-1-lb"`` -> ``"19793676"``."""
    return slug.split("-", 1)[0].strip()


def servings_per_container(value: Any) -> float | None:
    """The servings count, or ``None`` when it is not a plain number.

    See the 'Varied' trap: this field is free text and ``"Varied"``/``""`` are
    common. A bare ``float()`` here would raise on real data, and -- worse -- a
    ``try/except`` defaulting to ``1`` would quietly understate protein per
    package on every varied-weight item.
    """
    m = _PLAIN_NUMBER.match(str(value or ""))
    if not m:
        return None
    count = float(m.group(1))
    return count if count > 0 else None


def serving_grams(serving_size: str | None) -> float | None:
    """Grams per serving, across every shape the platform prints.

    A metric figure always wins over an imperial one when both are present
    (``"3 OZ  85 Gram"``), because it is the one the label rounded *to*.
    ``None`` means the panel gives no mass at all -- a volume (``"1 TBSP  15
    Millilitre"``) or a bare count (``"PER CONTAINER"``). That is a fact about
    the product, not a parse failure to work around.
    """
    if not serving_size:
        return None
    text = _FLUID.sub(" ", serving_size)

    m = _SERVING_GRAMS.search(text)
    if m:
        grams = float(m.group(1))
        return grams if grams > 0 else None

    m = _SERVING_OUNCES.search(text)
    if m:
        from .. import savings

        grams = float(m.group(1)) * savings.GRAMS_PER_OZ
        return grams if grams > 0 else None
    return None


def size_is_weight(size_text: str | None) -> bool:
    """Is the printed size a *rate* (per lb) rather than a package quantity?

    The WEIGHT trap. ``"per lb"`` means the price buys one pound, while the
    servings count still describes the whole cut, so the two must not be
    multiplied together.
    """
    if not size_text:
        return False
    return bool(_RATE_UNIT.search(size_text))


def package_grams(size_text: str | None) -> float | None:
    """Grams for a weight-denominated package size, via the shared grammar.

    Reuses ``savings.parse_size`` -- the same parser every other store's deals
    go through -- rather than growing a second size grammar here.
    """
    if not size_text or size_is_weight(size_text):
        return None
    # Imported lazily for the same reason wholefoods.py does it: to keep this
    # module's top-level imports to what every code path needs.
    from .. import savings

    size = savings.parse_size(size_text)
    if size is None or size.base_unit != savings.WEIGHT or size.base_quantity <= 0:
        return None
    return size.base_quantity * savings.GRAMS_PER_OZ


def protein_per_100g(nutrition: Nutrition, size_text: str | None) -> float | None:
    """Protein density, by whichever of the two routes the data supports.

    1. **Serving grams.** ``protein / serving_grams * 100``. Correct whatever
       the price denomination is, because both sides are per-serving.
    2. **Whole package.** ``protein * servings / package_grams * 100``. Needs a
       numeric servings count *and* a package size that is not a per-unit rate
       -- see :func:`size_is_weight` and the WEIGHT trap.

    ``None`` when neither route is available. That is the honest answer and the
    caller writes no ``food_nutrients`` row, rather than inventing a density.

    Either route can also produce an IMPOSSIBLE answer, and both are filtered
    through :func:`plausible_density` before being returned -- see that function
    for why the answer is ``None`` rather than a clamp.
    """
    return plausible_density(raw_protein_per_100g(nutrition, size_text))


def raw_protein_per_100g(
    nutrition: Nutrition, size_text: str | None
) -> float | None:
    """The density BEFORE the plausibility screen. ``None`` if no route applies.

    Split out from :func:`protein_per_100g` for one reason: a rejection has to
    be *countable*. Screening inside the calculation would make an impossible
    density and an absent one indistinguishable to the caller, and a source that
    quietly starts disagreeing with itself would degrade the ranking with
    nothing in ``stats`` to show for it -- the no-silent-caps rule. See
    :func:`rejected_density`.
    """
    protein = nutrition.protein_per_serving
    if protein is None or protein <= 0:
        return None

    grams = serving_grams(nutrition.serving_size)
    if grams:
        return protein / grams * 100.0

    count = servings_per_container(nutrition.servings_per_container)
    pack = package_grams(size_text)
    if count and pack:
        return protein * count / pack * 100.0
    return None


def rejected_density(nutrition: Nutrition, size_text: str | None) -> float | None:
    """The impossible value that WAS rejected, or ``None`` if nothing was.

    ``None`` covers both "the density is fine" and "there was no density to
    compute" -- the caller only needs to count the rejections, and the two
    non-rejection cases are not different kinds of nothing here.
    """
    raw = raw_protein_per_100g(nutrition, size_text)
    if raw is None or raw <= 0:
        return None
    return None if plausible_density(raw) is not None else raw


#: A food cannot be more than 100 g of protein per 100 g of food. Values above
#: this are not "high protein", they are arithmetic that disagrees with itself.
MAX_DENSITY_G_PER_100G = 100.0


def plausible_density(density: float | None) -> float | None:
    """The density, or ``None`` if it is physically impossible.

    Found by running the app against a real 155-row Sprouts scrape: two of 162
    foods came back at **677.3** and **306.7** g protein per 100 g. The next
    highest was 75.0.

    Both came from the whole-package route, where a label declares many servings
    against a small net weight (a jerky multi-pack, a seasoning blend): the
    servings count and the printed size are describing different things, so
    ``protein * servings / package_grams`` mixes denominators. Same family as
    the WEIGHT trap -- the inputs are individually fine and their product is
    nonsense.

    **It is rejected, not clamped.** Returning 100.0 would be inventing a
    number, against this codebase's rule 1, and it would still be wrong -- the
    real density is unknown, not maximal. It matters more than two rows suggests
    because the product ranks by *cheapest* cost per gram of protein: an
    inflated density does not sit harmlessly in the tail, it sorts to the top of
    the recommendation a customer acts on.

    The ceiling is the physical one rather than a tighter guess. Pure isolates
    genuinely reach ~90, and rejecting a real 92 to catch a fake 677 would be
    trading a true positive for nothing.
    """
    if density is None or density <= 0:
        return None
    return density if density <= MAX_DENSITY_G_PER_100G else None


def pricing_unit_size(size_text: str | None) -> str | None:
    """Turn a rate (``"per lb"``) into the quantity it buys (``"1 lb"``).

    See the 'per lb' trap. Normalising here, rather than teaching the shared
    grammar a new form, keeps the dialect difference contained to the module
    that has to speak it.
    """
    if not size_text:
        return None
    m = _RATE_UNIT.search(size_text)
    return f"1 {m.group(1).lower()}" if m else None


def display_item_name(name: str | None, size_text: str | None) -> str:
    """Fold the size into the name, because that is where the engine reads it.

    ``savings.parse_size`` looks for the size on ``item_name`` -- that is the
    only channel ``savings.cost_per_gram_protein`` has. A size recorded anywhere
    else (``notes``, a column) is invisible to the ranking.

    THIS FOLDS FOR BOTH DENOMINATIONS, AND KROGER'S RULE DOES NOT
    -------------------------------------------------------------
    ``kroger.py`` folds only for ``soldBy=WEIGHT``, and that is correct *there*:
    a Kroger UNIT item already carries its size inside ``description``, so
    appending it again would duplicate it.

    Copying that rule here was a bug. The Instacart JSON-LD splits them -- a
    clean ``name`` ("Greek Yogurt") and a separate ``size`` ("7.5 oz") -- so
    nothing puts the size back and every UNIT row reached ``deals`` unrankable.
    Measured on a real 155-row Sprouts run: **32 rankable**, and ``gplan
    cheapest`` answered "no protein with a usable size in the current offers"
    while the rows sat there priced, with protein, and with the size present in
    ``notes``. Unit tests could not see it; only running the app could.

    So: the pricing unit (``"1 lb"``) for a rate, the package size (``"7.5 oz"``)
    for a package. Both make price and size refer to the same quantity, which is
    the invariant the engine needs.

    Nothing is appended when the name ALREADY yields a weight-based size --
    doubling it up ("... 3 Pack, 3 fl oz") would leave the parser choosing
    between two answers, and it does not have to guess.
    """
    label = (name or "").strip()
    if not size_text:
        return label

    from .. import savings

    existing = savings.parse_size(label)
    if existing is not None and existing.base_unit == savings.WEIGHT:
        return label

    unit = pricing_unit_size(size_text) if size_is_weight(size_text) else size_text.strip()
    return f"{label}, {unit}".strip(", ") if unit else label


def parse_listing(slug: str, html_head: str) -> Listing | None:
    """Read the schema.org block out of the first chunk of a product page."""
    m = _LD_JSON.search(html_head)
    if not m:
        return None
    try:
        graph = json.loads(m.group(1))["@graph"][0]
    except (ValueError, KeyError, IndexError):
        return None
    offers = graph.get("offers") or {}
    return Listing(
        product_id=product_id_from_slug(slug),
        slug=slug,
        name=graph.get("name"),
        brand=(graph.get("brand") or {}).get("name"),
        category=graph.get("category"),
        size=graph.get("size"),
        price=base.price_to_float(offers.get("price")),
        availability=(offers.get("availability") or "").rsplit("/", 1)[-1] or None,
    )


def discover_persisted_queries(html: str) -> dict[str, str]:
    """Operation name -> sha256 hash, read out of a storefront page.

    The page embeds a performance-timing blob naming every operation it fired
    and the persisted-query hash it used. The blob is **doubly** URL-encoded,
    which is why this decodes twice before matching -- one pass leaves the
    braces and quotes still percent-escaped and the regex silently finds
    nothing, which is a quiet way to end up on stale fallbacks.

    Tenant-independent by construction, and demonstrated so: this exact
    function, unmodified, found 59 hashes in ALDI's storefront HTML on its
    first run (GFP-265).
    """
    decoded = urllib.parse.unquote(urllib.parse.unquote(html))
    return dict(_OP_HASH.findall(decoded))


def nutrition_from_payload(payload: dict[str, Any]) -> Nutrition | None:
    """Map a ``ProductNutritionalInfo`` response to :class:`Nutrition`.

    ``None`` when the product simply has no panel -- roughly two thirds of the
    Sprouts catalogue, and (measured, GFP-265) **all** of ALDI's. That is an
    absence, never an error: unpackaged produce and fresh-cut meat routinely
    carry none, and a whole tenant may publish none at all.
    """
    info = ((payload.get("data") or {}).get("productNutritionalInfo") or {})
    facts = info.get("nutritionalInfo")
    if not facts:
        return None
    protein = facts.get("protein")
    calories = facts.get("calories")
    return Nutrition(
        protein_per_serving=float(protein) if isinstance(protein, (int, float)) else None,
        serving_size=facts.get("servingSize"),
        servings_per_container=facts.get("servingsPerContainer"),
        calories=calories if isinstance(calories, (int, float)) else None,
    )


#: Real centroids for the ZIPs this project actually targets. Small on purpose:
#: every entry is a fact, and the alternative to a fact here is ``None``, not a
#: plausible-looking guess. See :func:`zip_centroid`.
ZIP_CENTROIDS: dict[str, dict[str, float]] = {
    # Greensboro NC -- the project's home ZIP.
    "27401": {"latitude": 36.0726, "longitude": -79.7920},
    # Philadelphia PA -- the other market the GFP-165 Flipp survey covered.
    "19103": {"latitude": 39.9526, "longitude": -75.1723},
}


class UnknownLocationError(StorefrontError):
    """No coordinates are known for this postal code, so the question cannot
    be put to the platform. Distinct from "no shop serves it" -- see
    :func:`zip_centroid`."""


def zip_centroid(postal_code: str, home: str = "27401") -> dict[str, float] | None:
    """Real coordinates for a ZIP, or ``None`` when we do not have them.

    THE FABRICATED-CENTROID TRAP (GFP-265, found live)
    ---------------------------------------------------
    GFP-262 returned the geographic centre of the contiguous US for every ZIP
    that was not 27401, on the stated reasoning that "the API requires a
    ``coordinates`` argument but selects the shop from the postal code, so an
    approximate centroid is sufficient".

    **The premise is false, and it was measured false.** The coordinates drive
    the selection and the postal code is largely ignored. Asking ALDI for
    99501 (Anchorage) with the US-centre point returns shop ``64623``, whose
    ``retailerLocationId`` is a store in Missouri. Asking with Anchorage's real
    coordinates returns nothing at all, which is the true answer -- ALDI does
    not operate in Alaska.

    So the fallback did not merely lose precision, it manufactured a positive:
    ``serves()`` answered ``True`` for **every ZIP in the country**, for both
    banners, and a scrape of a ZIP with no local store would have written
    another state's prices under that ZIP.

    ``None`` here means "cannot ask", and the caller turns it into
    :class:`UnknownLocationError` -> ``serves()`` returning ``None`` (unknown).
    Unknown is not absent and it is certainly not present. Adding a ZIP to
    :data:`ZIP_CENTROIDS` is how a market becomes answerable; a real geocoder
    would be the general fix and is not in this repo today.
    """
    known = ZIP_CENTROIDS.get(postal_code)
    if known is not None:
        return known
    # `home` is honoured for a tenant whose default ZIP is not in the table, so
    # a tenant can still answer for its own market without editing this module.
    return ZIP_CENTROIDS.get(home)


def _require_centroid(postal_code: str, home: str) -> dict[str, float]:
    coordinates = zip_centroid(postal_code, home) if postal_code == home else \
        ZIP_CENTROIDS.get(postal_code)
    if coordinates is None:
        raise UnknownLocationError(
            f"No coordinates known for postal code {postal_code!r}. This "
            "platform selects a shop by coordinates, not by postal code, so "
            "guessing one would silently return another market's store -- see "
            "zip_centroid's fabricated-centroid trap. Add the ZIP to "
            "ZIP_CENTROIDS (or wire a geocoder) to make this market answerable."
        )
    return coordinates


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class StorefrontClient:
    """Guest-session client for one Instacart Storefront Pro tenant.

    Opening it mints the session (a plain GET of the storefront is enough) and
    discovers the current persisted-query hashes in the same response, so the
    two things most likely to rot are both refreshed on every run.

    The tenant is held on the instance and every request path, every argument
    and every id is derived from it. Two clients over two tenants therefore
    cannot leak ids into one another's requests -- there is no module-level
    mutable state for them to share -- which is asserted directly in
    ``tests/test_instacart_storefront.py``.
    """

    def __init__(
        self,
        tenant: Tenant,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
        graphql_pace: retry.Paced | None = None,
        page_pace: retry.Paced | None = None,
    ):
        self.tenant = tenant
        # One pacer per path class, because this host polices the two paths by
        # completely different rules -- see retry.py's self-pacing note and the
        # throttle table above. Injectable so tests can drive them with a fake
        # clock instead of real sleeps.
        self.graphql_pace = graphql_pace or retry.Paced(retry.GRAPHQL_BUDGET)
        self.page_pace = page_pace or retry.Paced(retry.PRODUCT_PAGE_BUDGET)
        self._owned = client is None
        self._http = client or httpx.Client(
            base_url=tenant.base_url,
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": base.user_agent(),
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "x-client-identifier": "web",
            },
        )
        self.hashes: dict[str, str] = dict(tenant.pinned_hashes)
        self.discovered = False
        #: Whether :meth:`discover` has been *tried*, which is not the same as
        #: whether it found anything. Without the distinction, a storefront that
        #: yields no hashes would be re-fetched before every shop lookup.
        self._discovery_attempted = False

    def __enter__(self) -> "StorefrontClient":
        self.discover()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owned:
            self._http.close()

    def discover(self, canary_slug: str | None = None) -> dict[str, str]:
        """Mint the guest session and refresh the discoverable hashes.

        Refreshes whatever the storefront's perf blob names. Passing
        ``canary_slug`` additionally reads a product page, which names the
        item-detail operations the storefront does not -- but *not*
        :data:`PINNED_OPERATIONS`, which appear nowhere. Anything already
        pinned is therefore never overwritten by a partial discovery.
        """
        self._discovery_attempted = True
        pages = [self.tenant.storefront_path]
        if canary_slug:
            pages.append(self.tenant.product_path(canary_slug))
        found: dict[str, str] = {}
        for path in pages:
            response = self._http.get(path)
            response.raise_for_status()
            found.update(discover_persisted_queries(response.text))
        if found:
            # Discovery wins for anything it actually found -- that is the whole
            # point, and a pinned value for a *discoverable* operation (such as
            # ``SimpleShopCollection``) is only a cold-start default that a live
            # page should replace. The pin for ``ProductNutritionalInfo``
            # survives for free, because it appears in no page and so is never
            # in ``found``. Making pins unconditionally win here would look like
            # extra safety and would in fact freeze the one hash that rotates
            # most often.
            self.hashes.update(found)
            self.discovered = True
        return self.hashes

    def _query(self, operation: str, variables: dict[str, Any]) -> dict[str, Any]:
        sha = self.hashes.get(operation)
        if not sha:
            raise QueryNotAllowedError(
                f"No persisted-query hash for {operation!r} on "
                f"{self.tenant.store_key}. Discovery "
                f"{'ran but did not list it' if self.discovered else 'did not run'}; "
                "the storefront may have renamed or dropped the operation."
            )
        params = {
            "operationName": operation,
            "variables": json.dumps(variables, separators=(",", ":")),
            "extensions": json.dumps(
                {"persistedQuery": {"version": 1, "sha256Hash": sha}},
                separators=(",", ":"),
            ),
        }
        self.graphql_pace.wait()
        response = self._http.get(GRAPHQL_PATH, params=params)
        if response.status_code in retry.THROTTLE_STATUS:
            if self.graphql_pace.record_throttled():
                self.graphql_pace.cool_off()
            raise ThrottledError(
                f"{response.status_code} on {GRAPHQL_PATH} ({operation}). "
                "Measured behaviour says this path tolerates a high rate, so a "
                "throttle here is unusual -- check the pacing before raising it."
            )
        self.graphql_pace.record_success()
        response.raise_for_status()
        payload = response.json()
        for error in payload.get("errors") or ():
            code = ((error.get("extensions") or {}).get("code") or "")
            if "PERSISTED_QUERY" in str(code).upper():
                raise QueryNotAllowedError(
                    f"{operation} on {self.tenant.store_key}: {error.get('message')}. "
                    "The hashes rotate per Instacart deploy -- re-run discovery "
                    "against a fresh storefront response rather than editing the "
                    "tenant's pinned_hashes."
                )
        return payload

    # -- shop resolution ----------------------------------------------------- #
    def shop_context(self, postal_code: str) -> ShopContext | None:
        """Resolve the in-store shop serving ``postal_code``, or ``None``.

        Asks the source rather than assuming (GFP-257's rule): a shop id that is
        right for 27401 is not right for anywhere else, and a hard-coded one
        would silently return another city's prices.

        Prefers the ``instore`` service type. See the service-type trap for why
        "the first shop in the list" is not a rule -- it is a coincidence that
        holds for Sprouts and fails for ALDI.
        """
        # ``ShopCollectionScoped`` is discoverable, never pinned, so a client
        # that has not run discovery yet would silently drop to the fallback
        # and lose the service type -- degrading to exactly the bug this method
        # exists to fix, with no error. Discover first rather than depending on
        # the caller having remembered to.
        if "ShopCollectionScoped" not in self.hashes and not self._discovery_attempted:
            self.discover()
        scoped = self._scoped_shop_context(postal_code)
        if scoped is not None:
            return scoped
        return self._simple_shop_context(postal_code)

    def _scoped_shop_context(self, postal_code: str) -> ShopContext | None:
        if "ShopCollectionScoped" not in self.hashes:
            return None
        payload = self._query(
            "ShopCollectionScoped",
            {
                "retailerSlug": self.tenant.retailer_slug,
                "postalCode": postal_code,
                "coordinates": _require_centroid(
                    postal_code, self.tenant.default_postal_code
                ),
                "addressId": None,
                # FALSE, and the storefront itself sends True. The web UI wants
                # to show *something* rather than an empty page; this client
                # wants the truth. With True, ALDI answers 99501 (Anchorage)
                # with a canonical shop in Missouri instead of the correct empty
                # list -- a fabricated positive, and the second half of the
                # fabricated-centroid trap.
                "allowCanonicalFallback": False,
            },
        )
        shops = (((payload.get("data") or {}).get("shopCollection") or {}).get("shops") or [])
        chosen = None
        for shop in shops:
            if not str(shop.get("id") or "").strip():
                continue
            if shop.get("serviceType") == PREFERRED_SERVICE_TYPE:
                chosen = shop
                break
            chosen = chosen or shop
        if chosen is None:
            return None
        return ShopContext(
            shop_id=str(chosen["id"]).strip(),
            zone_id=self.tenant.default_zone_id,
            postal_code=postal_code,
            service_type=chosen.get("serviceType"),
            retailer_location_id=chosen.get("retailerLocationId"),
        )

    def _simple_shop_context(self, postal_code: str) -> ShopContext | None:
        """Fallback: ids only, no service type.

        ``service_type`` stays ``None`` rather than being filled in with a
        hopeful ``"instore"``. Unknown is not the same as verified, and a caller
        that cares can tell the difference.
        """
        payload = self._query(
            "SimpleShopCollection",
            {
                "postalCode": postal_code,
                "coordinates": _require_centroid(
                    postal_code, self.tenant.default_postal_code
                ),
                "retailerSlug": self.tenant.retailer_slug,
            },
        )
        shops = (((payload.get("data") or {}).get("shopCollection") or {}).get("shops") or [])
        for shop in shops:
            shop_id = str(shop.get("id") or "").strip()
            if shop_id:
                return ShopContext(
                    shop_id=shop_id,
                    zone_id=self.tenant.default_zone_id,
                    postal_code=postal_code,
                )
        return None

    # -- data ---------------------------------------------------------------- #
    def nutrition(self, product_id: str, shop_id: str) -> Nutrition | None:
        """The nutrition panel for one product, or ``None`` if it has none."""
        payload = self._query(
            "ProductNutritionalInfo", {"productId": product_id, "shopId": shop_id}
        )
        return nutrition_from_payload(payload)

    def product_slugs(self) -> Iterator[str]:
        """Every product slug in the published sitemap.

        The sitemap is the catalogue -- refreshed daily, and the slug carries
        the printed size (``...-25-oz``). Walking it is both cheaper and more
        complete than crawling the department nav. Measured: 46,359 entries
        across two files for Sprouts, 15,256 in one file for ALDI.
        """
        index = self._http.get(self.tenant.sitemap_index)
        index.raise_for_status()
        for sitemap_url in _SITEMAP_LOC.findall(index.text):
            if "/products/" not in sitemap_url:
                continue
            page = self._http.get(sitemap_url)
            page.raise_for_status()
            for loc in _SITEMAP_LOC.findall(page.text):
                _, sep, slug = loc.partition("/products/")
                if sep and slug:
                    yield slug

    def listing(self, slug: str) -> Listing | None:
        """Price and size for one product, from the page's JSON-LD.

        Reads only :data:`HEAD_BYTES` and abandons the rest of the response.
        Raises :class:`ThrottledError` on 403 so the caller can stop the price
        pass rather than walk into the wall -- see the throttle trap.
        """
        url = self.tenant.product_path(slug)
        self.page_pace.wait()
        with self._http.stream("GET", url) as response:
            if response.status_code in retry.THROTTLE_STATUS:
                # This is the path that actually hit the wall, and the 403 it
                # returns is a rate verdict rather than a credential one -- the
                # opt-in retry.py describes. Slowing down here is what keeps a
                # long scrape alive; the cool-off timer is the last resort.
                if self.page_pace.record_throttled():
                    self.page_pace.cool_off()
                raise ThrottledError(
                    f"{response.status_code} on {url}. The product-page path is "
                    "rate-policed far more tightly than /graphql; the pacer has "
                    "slowed itself down."
                )
            self.page_pace.record_success()
            response.raise_for_status()
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size >= HEAD_BYTES:
                    break
        head = b"".join(chunks).decode("utf-8", "replace")
        return parse_listing(slug, head)


# --------------------------------------------------------------------------- #
# Row mapping
# --------------------------------------------------------------------------- #
def listing_to_row(
    tenant: Tenant,
    listing: Listing,
    nutrition: Nutrition | None,
    zip_code: str,
    now: datetime,
) -> tuple[dict[str, Any], FoodFact | None]:
    """Map one product to a ``deals`` row plus (when computable) a food fact.

    Pure -- no DB, no network.
    """
    size_text = (listing.size or "").strip() or None
    by_weight = size_is_weight(size_text)
    # `sold_by` mirrors Kroger's vocabulary so weight_basis.classify and every
    # downstream consumer see one dialect rather than two.
    sold_by = "WEIGHT" if by_weight else "UNIT"
    density = protein_per_100g(nutrition, size_text) if nutrition else None
    item_name = display_item_name(listing.name, size_text)
    has_price = listing.price is not None
    identifier, identifier_ns = base.product_identifier(
        listing.product_id, tenant.product_identifier_ns
    )

    if has_price:
        deal_description = f"${listing.price:.2f}" + ("/lb" if by_weight else "")
    else:
        deal_description = tenant.priceless_description

    notes = [
        f"source={tenant.source_label}",
        f"product_id={listing.product_id}",
        f"postal_code={zip_code}",
    ]
    if listing.category:
        notes.append(f"category={listing.category}")
    if size_text:
        notes.append(f"size={size_text}")
    notes.append(f"sold_by={sold_by}")
    if nutrition and nutrition.protein_per_serving is not None:
        notes.append(f"protein_per_serving_g={nutrition.protein_per_serving:g}")
    if nutrition:
        grams = serving_grams(nutrition.serving_size)
        if grams is not None:
            notes.append(f"serving_grams={grams:g}")
        count = servings_per_container(nutrition.servings_per_container)
        if count is not None:
            notes.append(f"servings_per_container={count:g}")
    if density is not None:
        notes.append(f"protein_per_100g={density:.2f}")
    elif nutrition is not None:
        # An impossible density is dropped rather than clamped, but it is not
        # dropped SILENTLY: the figure that was refused is written to the row so
        # a bad label can be audited later without re-scraping.
        refused = rejected_density(nutrition, size_text)
        if refused is not None:
            notes.append(f"protein_per_100g_rejected={refused:.2f}")
    if listing.availability and listing.availability != "InStock":
        notes.append(f"availability={listing.availability}")
    if not has_price:
        notes.append("price_missing=true")

    row = {
        "item_name": item_name,
        "sub_category": listing.category or base.infer_sub_category(
            item_name, listing.brand or "", has_price
        ),
        "deal_type": tenant.deal_type if has_price
        else f"{tenant.deal_type} (price not listed)",
        "deal_description": deal_description,
        "regular_price": None,
        "sale_price": listing.price,
        "dollar_price": listing.price,
        "discount_amount": None,
        "discount_percent": None,
        "valid_from": now.date().isoformat(),
        # A shelf price announces no expiry and `deals` is replaced wholesale on
        # every re-scrape, so a missing valid_to reads as "unknown", never
        # "expired" (GFP-16) -- same call as kroger.py.
        "valid_to": None,
        "loyalty_required": "N",
        "notes": "; ".join(notes),
        "source_url": tenant.product_page_url(listing.slug),
        # The storefront's images are templated URLs behind the Apollo cache,
        # ~235 KB into the page. Not fetched, so this is honestly None rather
        # than a guessed URL that would render a broken thumbnail (GFP-99).
        "image_url": None,
        "flipp_flyer_id": None,
        "flipp_item_id": None,
        "flipp_coupon_id": None,
        "sold_by": sold_by,
        "weight_basis": weight_basis.classify(sold_by, None, item_name),
        # Per-unit price lives deep in the Apollo cache and is not fetched --
        # see the module docstring. The engine derives it from price and size.
        "price_per_unit": None,
        "price_per_unit_uom": None,
        "product_identifier": identifier,
        "product_identifier_ns": identifier_ns,
    }

    fact = None
    if listing.product_id and density and density > 0:
        fact = FoodFact(
            product_id=listing.product_id,
            name=listing.name or item_name,
            category=listing.category or "",
            protein_per_100g=density,
            item_name=item_name,
        )
    return row, fact


# --------------------------------------------------------------------------- #
# Persistence -- same shape as kroger.py/wholefoods.py, for the same reasons
# --------------------------------------------------------------------------- #
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def upsert_food_fact(
    conn: sqlite3.Connection, tenant: Tenant, fact: FoodFact
) -> None:
    now = _now_iso()
    source = tenant.store_key
    method = f"{tenant.store_key}_label_direct"
    conn.execute(
        "INSERT INTO foods(name, category, source, source_ref, slug, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(source, source_ref) DO UPDATE SET "
        "name=excluded.name, category=excluded.category, slug=excluded.slug, "
        "updated_at=excluded.updated_at",
        (fact.name, fact.category, source, fact.product_id,
         f"{source}-{fact.product_id}", now),
    )
    food_id = conn.execute(
        "SELECT id FROM foods WHERE source=? AND source_ref=?",
        (source, fact.product_id),
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO food_nutrients(food_id, nutrient, amount_per_100g, unit) "
        "VALUES (?, 'protein', ?, 'g') "
        "ON CONFLICT(food_id, nutrient) DO UPDATE SET "
        "amount_per_100g=excluded.amount_per_100g, unit=excluded.unit",
        (food_id, fact.protein_per_100g),
    )
    # match_source=MANUAL for the same reason as kroger.py and wholefoods.py:
    # the figure came off the retailer's own label for this exact product, so
    # the keyword auto-matcher must never be allowed to downgrade it. `method`
    # keeps the real provenance auditable.
    conn.execute(
        "INSERT INTO deal_food_match"
        "(store, item_name, food_id, confidence, method, match_source, updated_at) "
        "VALUES (?, ?, ?, 1.0, ?, ?, ?) "
        "ON CONFLICT(store, item_name) DO UPDATE SET "
        "food_id=excluded.food_id, confidence=1.0, method=excluded.method, "
        "match_source=excluded.match_source, updated_at=excluded.updated_at",
        (tenant.store_key, fact.item_name, food_id, method, matching.MANUAL, now),
    )


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def verify_pinned_hashes(
    tenant: Tenant, client: StorefrontClient | None = None
) -> tuple[bool, str]:
    """Prove the tenant's pinned nutrition query still runs. ``(ok, message)``.

    Exists because :data:`PINNED_OPERATIONS` cannot be self-healed (see the rot
    trap) and a rotation is otherwise invisible until a scrape comes back with
    no protein anywhere. Cheap enough to run as a health check: one product, one
    request.

    A tenant with no ``canary_product_id`` reports ``False`` with the reason,
    rather than reporting success it did not earn -- ALDI is that case, and it
    is the whole point that the difference is visible.
    """
    if not tenant.canary_product_id:
        return False, (
            f"{tenant.store_key}: no canary product -- this tenant publishes no "
            "nutrition panels, so the pin cannot be verified against it. See the "
            "tenant module's docstring."
        )
    owned = client is None
    active = client or StorefrontClient(tenant)
    try:
        if owned:
            active.discover()
        panel = active.nutrition(tenant.canary_product_id, tenant.default_shop_id)
    except QueryNotAllowedError as exc:
        return False, f"pinned hash rejected -- re-capture it: {exc}"
    except (StorefrontError, httpx.HTTPError, ValueError) as exc:
        # Could not ask. Not the same as "the pin is stale", and must not be
        # reported as one.
        return False, f"could not verify (transport): {exc}"
    finally:
        if owned:
            active.close()
    if panel is None or panel.protein_per_serving is None:
        return False, (
            f"canary product {tenant.canary_product_id} returned no protein. The "
            "hash still resolves, so either the canary changed or the panel "
            "shape did."
        )
    return True, f"pinned hashes OK (canary protein {panel.protein_per_serving:g} g)"


def serves(tenant: Tenant, postal_code: str) -> bool | None:
    """Is there a shop of this banner serving ``postal_code``? (GFP-257)

    ``None`` when the question cannot be put -- a network error, a rotated
    hash. Unknown is not absent, and availability.py treats it permissively.

    The geocode is checked FIRST, before any client is built, because a ZIP we
    cannot place is a question we cannot form -- and asking anyway meant minting
    a guest session over the network just to discover that. It made every
    unanswerable ZIP cost a live request, on a resolver that availability.py
    calls per store per ZIP.
    """
    # Membership, NOT `zip_centroid(...) is None` -- that helper deliberately
    # falls back to the home ZIP's coordinates, so it never returns None for a
    # caller that passes a home, and a guard written against it would never
    # fire. The question here is specifically "do we know where THIS ZIP is".
    if postal_code not in ZIP_CENTROIDS:
        return None
    try:
        with StorefrontClient(tenant) as client:
            return client.shop_context(postal_code) is not None
    except (StorefrontError, httpx.HTTPError, ValueError):
        return None


def scrape(
    tenant: Tenant,
    postal_code: str | None = None,
    limit: int | None = None,
    conn: sqlite3.Connection | None = None,
    client: StorefrontClient | None = None,
    now: datetime | None = None,
    slugs: Iterable[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Scrape one tenant's shelf prices and return ``(rows, meta, stats)``.

    Matches the contract ``service/ingest.run_scrape`` expects of every scraper,
    and -- like kroger.py and wholefoods.py -- also upserts
    ``foods``/``food_nutrients``/``deal_food_match`` for every product whose
    protein density is computable, so nutrition arrives with the price instead
    of needing a USDA matching pass.

    The two passes are deliberately asymmetric, for the reason set out in the
    throttle table: nutrition goes through ``/graphql``, which is bulk-safe, and
    only the products that came back with a protein figure are then priced from
    HTML, paced and with a 403 circuit-breaker.

    **A tenant that publishes no panels prices nothing.** That is a genuine
    consequence of the pass ordering, not a bug to route around, and it is made
    legible rather than silent: ``stats['no_nutrition_panel']`` equals
    ``products_seen`` and ``stats['priceable']`` is 0. For such a tenant, pass
    ``price_without_nutrition=True`` via :func:`scrape_prices_only` instead.
    """
    from .. import db

    zip_code = postal_code or tenant.default_postal_code
    moment = now or datetime.now(timezone.utc)

    owned_client = client is None
    active = client or StorefrontClient(tenant)
    if owned_client:
        active.discover()
    own = conn or db.connect()

    try:
        context = active.shop_context(zip_code)
        if context is None:
            raise StorefrontError(f"No {tenant.merchant} shop serves {zip_code}.")

        catalogue = list(slugs) if slugs is not None else list(active.product_slugs())

        # Pass 1 -- nutrition for the whole catalogue. Cheap and bulk-safe.
        panels: dict[str, tuple[str, Nutrition]] = {}
        no_panel = 0
        for slug in catalogue:
            product_id = product_id_from_slug(slug)
            panel = active.nutrition(product_id, context.shop_id)
            if panel is None or panel.protein_per_serving in (None, 0):
                no_panel += 1
                continue
            panels[product_id] = (slug, panel)

        # Pass 2 -- price, only for what pass 1 found protein for.
        priceable = list(panels.values())
        priceable_total = len(priceable)
        if limit is not None:
            priceable = priceable[:limit]

        rows, facts, skipped, refused, unlisted = _price_pass(
            active, priceable, zip_code, moment, tenant
        )

        for fact in facts.values():
            upsert_food_fact(own, tenant, fact)
        if conn is None:
            own.commit()

        stats = _stats(
            tenant, context, moment, rows, facts, active,
            products_seen=len(catalogue), no_panel=no_panel,
            priceable=priceable_total, skipped=skipped, limit=limit,
            refused_density=refused, unlisted=unlisted,
        )
        meta = {
            "name": f"{tenant.merchant} shelf prices ({zip_code})",
            "id": context.shop_id,
            "shop_id": context.shop_id,
            "retailer_id": tenant.retailer_id,
            "store_name": tenant.merchant,
        }
        stats["flyer_name"] = meta["name"]
        return rows, meta, stats
    finally:
        if owned_client:
            active.close()


def scrape_prices_only(
    tenant: Tenant,
    limit: int,
    postal_code: str | None = None,
    conn: sqlite3.Connection | None = None,
    client: StorefrontClient | None = None,
    now: datetime | None = None,
    slugs: Iterable[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Price a bounded slice of the catalogue, with no nutrition pass.

    For a tenant that publishes no nutrition panels, :func:`scrape` correctly
    returns nothing: its second pass only prices what the first pass found
    protein for. Rather than weaken that rule -- it is what stops a Sprouts run
    walking into the product-page wall -- a tenant with no panels takes this
    path instead and says so in its own module.

    ``limit`` is **required**, not optional, and that is deliberate. The
    product-HTML path returned a hard 403 after ~2,300 pages on Sprouts, and
    ALDI is assumed to be policed at least as tightly until measured otherwise.
    An unbounded full-catalogue HTML crawl is not a thing this project does, so
    the bound is not something a caller can forget to pass. It is reported in
    ``stats['price_limit']`` alongside ``stats['products_seen']`` so the
    coverage it costs is visible rather than silent.

    Rows land with no protein figure and therefore no ``food_nutrients`` row.
    They are still useful: a price with a parseable size is exactly what the
    Flipp-sourced stores contribute, and the USDA matching pass supplies the
    protein for those.

    **Which slice is priced is sitemap order, not a sample.** The bound is taken
    as ``catalogue[:limit]``, so a bounded run sees whatever the sitemap happens
    to list first rather than a representative cross-section of departments.
    That is a real limitation of a bounded crawl and is stated rather than
    hidden: with ``products_seen`` and ``price_limit`` both in ``stats``, a
    reader can see exactly how much of the catalogue the prices came from.
    Randomising the slice was rejected -- it would make consecutive runs price
    different products, and GFP-75's price history needs the same item observed
    over time far more than it needs breadth on any single run.
    """
    from .. import db

    zip_code = postal_code or tenant.default_postal_code
    moment = now or datetime.now(timezone.utc)

    owned_client = client is None
    active = client or StorefrontClient(tenant)
    if owned_client:
        active.discover()
    own = conn or db.connect()

    try:
        context = active.shop_context(zip_code)
        if context is None:
            raise StorefrontError(f"No {tenant.merchant} shop serves {zip_code}.")

        catalogue = list(slugs) if slugs is not None else list(active.product_slugs())
        priceable: list[tuple[str, Nutrition | None]] = [
            (slug, None) for slug in catalogue[:limit]
        ]

        rows, facts, skipped, refused, unlisted = _price_pass(
            active, priceable, zip_code, moment, tenant
        )

        for fact in facts.values():
            upsert_food_fact(own, tenant, fact)
        if conn is None:
            own.commit()

        stats = _stats(
            tenant, context, moment, rows, facts, active,
            products_seen=len(catalogue),
            # Every product is "no panel" here because none were asked for --
            # said plainly so the number is not misread as a measurement of the
            # catalogue's nutrition coverage.
            no_panel=len(catalogue),
            priceable=len(catalogue), skipped=skipped, limit=limit,
            refused_density=refused, unlisted=unlisted,
        )
        stats["nutrition_pass"] = "skipped (tenant publishes no panels)"
        meta = {
            "name": f"{tenant.merchant} shelf prices ({zip_code})",
            "id": context.shop_id,
            "shop_id": context.shop_id,
            "retailer_id": tenant.retailer_id,
            "store_name": tenant.merchant,
        }
        stats["flyer_name"] = meta["name"]
        return rows, meta, stats
    finally:
        if owned_client:
            active.close()


def _price_pass(
    active: StorefrontClient,
    priceable: list[tuple[str, Nutrition | None]],
    zip_code: str,
    moment: datetime,
    tenant: Tenant,
) -> tuple[list[dict[str, Any]], dict[str, FoodFact], int, int, int]:
    rows: list[dict[str, Any]] = []
    facts: dict[str, FoodFact] = {}
    skipped = 0
    refused = 0
    unlisted = 0
    for slug, panel in priceable:
        try:
            # Pacing, back-off and the cool-off timer all live in the client's
            # pacer -- this loop only decides what to do with a page it could
            # not get. Giving up on one product is not the same as giving up on
            # the run: the pacer has already slowed itself down, so the next one
            # may well succeed.
            listing = active.listing(slug)
        except ThrottledError:
            skipped += 1
            continue
        if listing is None:
            # The page served but carried no JSON-LD -- a delisted product, or a
            # page shape we do not read. Counted rather than dropped silently:
            # measured 4 of 12 on a live ALDI slice, which is far too large a
            # hole to leave invisible in a row count.
            unlisted += 1
            continue
        row, fact = listing_to_row(tenant, listing, panel, zip_code, moment)
        rows.append(row)
        if fact is not None:
            facts[fact.product_id] = fact
        elif panel is not None and rejected_density(panel, listing.size) is not None:
            refused += 1
    return rows, facts, skipped, refused, unlisted


def _stats(
    tenant: Tenant,
    context: ShopContext,
    moment: datetime,
    rows: list[dict[str, Any]],
    facts: dict[str, FoodFact],
    active: StorefrontClient,
    *,
    products_seen: int,
    no_panel: int,
    priceable: int,
    skipped: int,
    refused_density: int,
    unlisted: int,
    limit: int | None,
) -> dict[str, Any]:
    priced = sum(1 for r in rows if r["dollar_price"] is not None)
    by_weight = sum(1 for r in rows if r["sold_by"] == "WEIGHT")
    return {
        # Flipp-vintage field names, repurposed rather than forking the CLI's
        # formatting per store -- same call as kroger.py.
        "weekly_ad": len(rows),
        "digital_coupons": 0,
        "no_price": len(rows) - priced,
        "bogo": 0,
        "expired_items": 0,
        "total": len(rows),
        "flyer_id": context.shop_id,
        "flyer_status": "active",
        "valid_from": moment.date().isoformat(),
        "valid_to": moment.date().isoformat(),
        "priced": priced,
        "with_protein": len(facts),
        "sold_by_weight": by_weight,
        "products_seen": products_seen,
        "no_nutrition_panel": no_panel,
        # Every bound on coverage is named here, per the no-silent-caps rule.
        # `priceable` is how many products pass 1 qualified; `price_limit` is
        # the cap the caller imposed on pass 2; `skipped_throttled` is products
        # we gave up on; the pacer counters say how hard the run had to slow
        # down to finish at all.
        "priceable": priceable,
        "price_limit": limit,
        "skipped_throttled": skipped,
        # Products whose two nutrition declarations disagreed badly enough to
        # produce a physically impossible density (see `plausible_density`).
        # Surfaced because dropping them is right but dropping them quietly is
        # not: a source that starts disagreeing with itself would otherwise just
        # look like a source with slightly less protein data than last week.
        "density_rejected_implausible": refused_density,
        # Products whose page returned no schema.org block at all.
        "no_listing_data": unlisted,
        "hashes_discovered": active.discovered,
        # Which shop, and whether we could actually verify it is the in-store
        # one. See the service-type trap: a delivery shop prices a delivery
        # basket, and silently doing that is the bug this field makes visible.
        "shop_service_type": context.service_type,
        "shop_service_type_verified": context.service_type is not None,
        "retailer_location_id": context.retailer_location_id,
        **active.graphql_pace.stats(),
        **active.page_pace.stats(),
    }
