"""Trader Joe's, from its own Magento 2 GraphQL endpoint (GFP-264).

``https://www.traderjoes.com/api/graphql`` is a stock Magento 2 Commerce
GraphQL API with the guard rails left off. Verified live on 2026-08-11:

* ``POST {"query":"{__typename}"}`` returns ``{"data":{"__typename":"Query"}}``.
  Arbitrary queries run -- there is **no persisted-query allow-list**, which is
  the single biggest difference from Sprouts (GFP-257), where every operation
  has to be a hash the server already knows and the hashes rot on each deploy.
* Introspection is **enabled** -- ``{__schema{types{name}}}`` returns 599 types.
  So every field name in this module was *read off the schema*, not guessed,
  and anyone maintaining it can re-read the schema rather than reverse-engineer
  a bundle.
* No auth of any kind. No cookie, no token, no browser step. ``Origin`` and
  ``Referer`` are sent because it is polite, not because anything checks them.

That makes this the cheapest source in the project to stand up -- cheaper even
than Sprouts, which at least needed a guest session minted first.

WHAT THIS SOURCE IS: a Sprouts-tier source, not a Flipp-tier one
----------------------------------------------------------------
The deciding question for any new store is whether it carries protein, because
that is what says whether rows can be priced per gram of protein directly or
have to go through a USDA matching pass first. **Trader Joe's carries it.**
``nutrition`` is a product attribute holding a full nutrition panel: serving
size, servings per container, calories, and a ``details`` list with a Protein
line. So this behaves like Kroger, Whole Foods and Sprouts -- the label is the
authority for this exact product -- and unlike Flipp, which has no nutrition at
all.

Measured over the full published catalogue on 2026-08-11 (2,454 products):

======================================  ======  =====================
                                        count   share of catalogue
======================================  ======  =====================
published products                       2,454  100%
priced                                   2,454  100%
carry a nutrition panel                  1,664  68%
...with a usable protein number          1,566  64%
...with a computable protein density     1,238  50%
======================================  ======  =====================

Half the catalogue arrives priced *and* with a label-derived protein density.
No USDA matching needed for those rows.

THE ATTRIBUTE INDIRECTION
-------------------------
Everything this module actually wants -- nutrition, size, per-store price --
lives in ``custom_attributesV2``, **not** as a field on ``ProductInterface``.
Introspecting ``SimpleProduct`` lists 58 fields and none of them is
``nutrition``; the same names appear in ``ProductAttributeFilterInput`` as
filter inputs, which is the clue that they are Magento custom attributes.
``attributesList(entityType:CATALOG_PRODUCT)`` enumerates all 80 of them.

Two consequences worth knowing before editing the query:

1. ``custom_attributesV2`` **requires its ``filters`` argument**. Omitting it
   returns ``"Internal server error"`` against that one field while the rest of
   the response succeeds -- a partial failure that is easy to miss because the
   HTTP status is still 200.
2. The filter takes only booleans (``is_visible_on_front`` and friends). There
   is **no way to request attributes by code**, so asking for ``nutrition``
   also drags in ``availability``, ``retail_price`` and ``promotion`` -- three
   per-store JSON blobs of ~20 KB each. That is ~60 KB per product of payload
   nobody asked for, and it is not optional. It is also, usefully, exactly
   where the per-store price lives, so this module reads the price out of those
   blobs rather than *additionally* selecting ``store_specific_info`` -- which
   carries the same facts again and measured 35% more wire (183 KB vs 119 KB
   per 24 products).

Attribute values are **JSON documents inside GraphQL ``String`` fields**. They
must be ``json.loads``-ed, and they are ``"[]"`` -- not ``null`` -- when empty.

THE PARTIAL-FAILURE TRAP: HTTP 200 with a server error inside
--------------------------------------------------------------
**132 of 2,454 published products (5.4%) come back with
``custom_attributesV2: null``** and an ``"Internal server error"`` entry in the
response's ``errors`` list. The HTTP status is 200 and ``data.products.items``
is fully populated, so nothing about the response looks wrong. Reproduced on
two separate full runs; 7 of 25 pages carried at least one.

Those products arrive with **no nutrition, no size and no per-store price** --
they still get a row, priced from the national figure, but they are
indistinguishable from products that genuinely have none of those things
unless someone counts them. That is precisely the shape of bug this project
keeps legislating against, and it caught this module's own first measurement
pass: "132 products have no size on file" was really "132 products failed
server-side", and the two readings are not the same fact.

So it is counted (``stats["products_missing_attributes"]``) and logged, and
:func:`parse_listing` records it per product rather than letting an empty
attribute map stand in for an absent one. It is *not* retried: it reproduced on
the same products across runs, so it looks like bad catalogue data rather than
a transient blip, and retrying 132 products every scrape would spend requests
to reach the same answer.

THE PANEL TRAP: 'Per Container' reuses the per-serving serving size
-------------------------------------------------------------------
This is the most dangerous thing in this module and the reason
:func:`select_panel` exists.

``nutrition`` is a *list* of panels. 289 of the 1,664 products with a panel
have more than one, and the second is almost always a whole-container restatement
of the first. The trap is that the ``serving_size`` **string is copied verbatim
into both panels while the amounts are not**::

    CHICKEN SHU MAI            servings_per_container: "Serves 3"
      panel 0  "Per Serving"    serving_size "6 pieces with 1 Tbsp. sauce (104g)"   protein 11 g
      panel 1  "Per Container"  serving_size "6 pieces with 1 Tbsp. sauce (104g)"   protein 33 g
                                             ^^^^^^^^^^^ identical ^^^^^^^^^^^

Reading protein off panel 1 and serving grams off the string gives
33/104*100 = 31.7 g per 100 g instead of the true 10.6 -- a **3x
overstatement** that would rank steamed dumplings alongside whey isolate and
put them at the top of every cheapest-protein list. Measured across all 209
products with a titled container panel, the container/serving calorie ratio was
above 1.05 in **209 of 209 cases** and never once 1.0, so this is the rule for
this source and not an occasional glitch.

This is the same *class* of bug as kroger.py's ``soldBy=WEIGHT`` trap and
sprouts.py's ``per lb`` trap -- two figures that look combinable and are
denominated differently -- and like those, it bites hardest on the
highest-protein items, which is precisely what makes it worth this much prose.

The defence is deliberately belt-and-braces: :func:`select_panel` takes the
lowest ``display_sequence`` **and** independently refuses any panel whose title
mentions a container or a package. Either rule alone would have worked on the
2026-08 data; both together survive one of them changing.

Multi-panel products also include baking mixes, where panel 1 is the *prepared*
food ("Per prepared portion") and includes eggs and butter the shopper has to
buy separately. Same rule, same answer: take panel 0.

THE 'less than' TRAP
--------------------
Protein arrives as a **string with its unit attached** -- ``"26 g"``, ``"26g"``
-- and 93 of 1,660 protein lines instead read ``"less than 1 g"`` (in four
different capitalisations). A ``float()`` raises on those; stripping the
non-digits yields ``1``, which *overstates* protein on an item that has
essentially none, and overstating protein understates cost per gram of protein,
which is the one direction of error this project must never make silently.

:func:`protein_grams` returns ``None`` for a bounded value. An upper bound is
not a measurement (savings.py rule 1), and the items concerned are oils and
jams whose loss costs the optimiser nothing.

THE 'Serves about' TRAP
-----------------------
``servings_per_container`` is prose, not a number. Of 1,664 panels only **6**
hold a bare numeral; the rest read ``"Serves 4"``, ``"Serves about 8"``,
``"Serves about 2.5"`` or ``"Servings varied"``. Sprouts' plain-number rule
would return ``None`` for 99.6% of this catalogue, so :func:`servings_per_container`
understands the ``Serves [about] N`` dialect -- and still refuses
``"Servings varied"`` rather than guessing 1.

THE VOLUME TRAP
---------------
Serving sizes are free text and several shapes are volumes:
``"1 Tbsp. (15mL)"``, ``"12 fl oz (360mL)"``, ``"2 Tbsp (30 mL)"``. Reading
``15`` as grams would invent a density for every beverage and condiment in the
catalogue. :func:`serving_grams` strips fluid-ounce spellings first and then
refuses outright when the only metric figure present is a volume. The same
question is asked again of the *package* size, where ``sales_uom_description``
is one of ``Fl Oz``/``mL``/``L``/``Pint``/``Qt`` for 444 of 2,454 products.

SIZE: two fields that are meaningless apart
-------------------------------------------
``sales_size`` is a bare decimal string (``"1.000000"``) and
``sales_uom_description`` is its unit (``"Oz"``, ``"Lb"``, ``"Fl Oz"``,
``"Each"``, ``"mL"``, ``"Bag"``, ``"Doz"``...). Neither means anything alone.
:func:`size_text` joins them into the one grammar the rest of the project
already speaks -- the string ``savings.parse_size`` reads -- rather than
growing a second size parser here.

Every published product that the server actually rendered carries both halves:
the only 132 without a size are exactly the 132 of the partial-failure trap
above, which is how that trap was found.

WHAT THIS SOURCE DOES *NOT* HAVE: a per-pound denomination
-----------------------------------------------------------
Kroger has ``soldBy``. Sprouts prints ``"per lb"``. **Trader Joe's has
neither**, and the schema has no field for it -- there is no ``soldBy``, no
``price_type``, nothing in the 80 attributes that distinguishes a rate from a
package.

The evidence says it does not need one. All 181 ``Lb`` products carry integer
sizes (153 of them exactly ``1.000000``) and are things like ``PASTA ORZO``
1 lb $0.99, ``ROASTED & SALTED ALMONDS 1 LB`` $6.49 and ``GROUND TURKEY`` 1 lb
$4.79 -- fixed packages at fixed prices, which is how Trader Joe's sells.
``sales_size`` is therefore read as a **package quantity throughout** and
``sold_by`` is ``"UNIT"`` for every row.

Flagging this as the assumption it is: it rests on the observed distribution,
not on a field that states it. If Trader Joe's ever starts pricing a case by
the pound, nothing in this response would say so, and the failure would be
silent. It is called out here so a future maintainer knows where to look.

PRICING IS STORE-SCOPED, AND THERE IS NO WAY TO ASK BY ZIP
-----------------------------------------------------------
Both halves of that sentence matter.

**It is store-scoped.** The ``retail_price`` attribute is a JSON list of
``{"store_code": "0750", "value": "7.99"}`` covering ~580 of the ~660 stores,
and the prices genuinely differ -- 8 of a 20-product sample had more than one
distinct price across stores. ``availability`` is a second such list and is
even more store-specific: 924 of 2,454 published products are simply not
carried at the Greensboro store. The ``price`` attribute is labelled, by
Trader Joe's own admin, **"Global TJ price"**, and there is a pseudo-store code
``"TJ"`` alongside the real ones -- so the national figure is a real, deliberate
fact, not a default that happens to be there.

**But no argument anywhere takes a location.** Introspected and confirmed:

* ``products(search, filter, pageSize, currentPage, sort)`` -- no postal code,
  no coordinates, no store argument.
* ``ProductAttributeFilterInput`` *does* accept ``store_code``, and it is a
  **no-op**. ``total_count`` is 28,323 with ``store_code:{eq:"TJ"}``, with
  ``{eq:"0750"}``, with ``{eq:"0003"}`` and with no filter at all. Filtering by
  it looks like it works and does nothing; this module never uses it.
* ``pickupLocations(area:{radius:100, search_term:"27401"})`` returns
  ``total_count: 0``. It is wired up but empty.
* ``availableStores`` returns exactly one entry, ``"Default Store View"`` --
  the Magento *store view*, which has nothing to do with a physical shop.

So the store must be chosen by *us* and the price read out of the blob. There
is no ZIP pinning to invent here and this module does not pretend otherwise.

Resolving a ZIP to a store code is a separate, and expensive, problem. The only
machine-readable store list Trader Joe's publishes is the locator at
``locations.traderjoes.com`` -- a different system (Soci local-pages, not
Magento). Its sitemap is one cheap 220 KB request and yields all 661 stores as
``/nc/greensboro/750/``, giving state, city and store number. **It does not
carry postal codes**, and neither do the state pages; only the individual store
page does. Building a national ZIP index would therefore cost 661 page fetches
of ~130 KB -- ~86 MB to answer one question -- so this module does not do it.

What it does instead:

* :data:`STORE_CODE_BY_POSTAL_CODE` pins the ZIPs that have been verified by
  hand, with the evidence recorded next to them.
* :func:`verify_store` asks the locator whether a pinned store still exists and
  what postal code it reports, so a closure or a renumbering fails **loudly**
  rather than silently pricing against a store that is gone. Same reasoning as
  sprouts.py's pinned-hash canary: a wrong answer that looks like a right
  answer is the failure mode worth engineering against.
* When no store code is known, :func:`scrape` falls back to the **national**
  price -- which is a real published figure, not a guess -- and says so, in
  every row's ``notes`` and in ``stats["pricing_scope"]``.

PACING
------
No throttling was observed at any point: the full 25-page catalogue walk ran at
pageSize 100 with a 0.5 s gap and returned 2,454 products in 98 s over 13.7 MB
with zero non-200s. That is *not* a licence to go faster. Responses are large
(~550 KB on the wire, ~5 s of server time each) and the endpoint is
unauthenticated, so :data:`CATALOGUE_BUDGET` deliberately starts at a 0.5 s
floor -- roughly the rate the measurement used, which is already known to be
sustainable -- rather than at :data:`~grocery_planner.scrapers.retry.GRAPHQL_BUDGET`'s
0.08 s, which was measured against a *different* host that had proved it could
take 36 req/s. Reusing a budget measured elsewhere would be assuming the answer.

NO SILENT CAPS
--------------
``limit``, and the default of dropping products the chosen store does not
stock, both bound what a run returns. Both are counted in ``stats``
(``limit_applied``, ``filtered_unavailable``) and logged, because a truncated
run that reads as a complete one is how a half-empty catalogue gets trusted.
A product whose availability is simply *unknown* at the chosen store is kept --
unknown is not absent, the same rule :func:`serves` follows.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx

from .. import db, logs, matching, savings, weight_basis
from . import foodfacts, base, retry

log = logs.get_logger(__name__)

STORE_KEY = "traderjoes"
MERCHANT = "Trader Joe's"
DEFAULT_POSTAL_CODE = "27401"
DEAL_TYPE = "Shelf Price"
PRODUCT_IDENTIFIER_NS = "traderjoes.sku"

BASE_URL = "https://www.traderjoes.com"
GRAPHQL_PATH = "/api/graphql"
PRODUCT_PATH = "/home/products/pdp/{url_key}"

#: The store locator. A completely separate system from the Magento catalogue
#: -- different host, different vendor (Soci local pages) -- and the only place
#: Trader Joe's publishes a machine-readable list of physical shops.
LOCATOR_URL = "https://locations.traderjoes.com"
LOCATOR_SITEMAP = f"{LOCATOR_URL}/sitemap.xml"

#: The pseudo-store code Trader Joe's uses for the national figure. It appears
#: in the same lists as the real four-digit codes, so anything walking those
#: lists has to know it is not a shop.
NATIONAL_STORE_CODE = "TJ"

#: Verified by hand against the locator on 2026-08-11 and recorded rather than
#: computed, because computing it would cost 661 page fetches (see the module
#: docstring). 27401 is the project's home ZIP; the nearest Trader Joe's is
#: store 750, 3721 Battleground Ave, Greensboro NC 27410 --
#: https://locations.traderjoes.com/nc/greensboro/750/ -- about five miles
#: away. `verify_store` re-checks this against the live locator.
STORE_CODE_BY_POSTAL_CODE = {"27401": "0750"}

#: How many products to ask for per GraphQL call. 100 was measured at ~550 KB
#: and ~5 s; larger pages were not tried because there is no reason to push an
#: endpoint that already answers the whole catalogue in 25 requests.
PAGE_SIZE = 100

#: Hard ceiling on pagination, so a server that keeps reporting more pages than
#: it serves cannot spin this forever. Well above the 25 pages the live
#: catalogue needs; if it is ever reached that is a bug, and it is reported.
MAX_PAGES = 200

#: Measured against www.traderjoes.com on 2026-08-11: 25 requests at ~2/s, zero
#: throttle signals, zero non-200s. The floor is set at the rate that was
#: actually proven sustainable rather than at the fastest rate that might work.
#: A distinct Budget rather than `retry.GRAPHQL_BUDGET` because that one's
#: 0.08 s floor was measured against Sprouts, and one host's tolerance is not
#: evidence about another's.
CATALOGUE_BUDGET = retry.Budget(
    name="traderjoes-graphql",
    min_interval=0.5,
    max_interval=30.0,
    cooldown_seconds=300.0,
)

#: Attribute codes this module reads. Documented as a set because the query
#: cannot ask for them by name -- see the attribute indirection note -- so this
#: is the only place that records which of the 80 attributes actually matter.
WANTED_ATTRIBUTES = frozenset({
    "nutrition", "sales_size", "sales_uom_description", "sales_uom_code",
    "retail_price", "availability", "item_title", "country_of_origin",
})

# The catalogue query. Every field here came from an introspection result:
# `products` and its arguments from the Query type, `custom_attributesV2` and
# its mandatory `filters` argument from ProductInterface, and the
# AttributeValue inline fragment from AttributeValueInterface's possibleTypes.
#
# `published:{eq:"1"}` is the live-catalogue gate: unfiltered `total_count` is
# 28,323 and includes years of delisted items, while published is 2,454 and
# matches what the website shows.
CATALOGUE_QUERY = """
query GroceryPlannerCatalogue($pageSize: Int!, $currentPage: Int!) {
  products(
    filter: { published: { eq: "1" } }
    pageSize: $pageSize
    currentPage: $currentPage
    sort: { sku: ASC }
  ) {
    total_count
    page_info { current_page total_pages }
    items {
      sku
      name
      url_key
      price_range { minimum_price { final_price { value currency } } }
      custom_attributesV2(filters: { is_visible_on_front: true }) {
        items { code ... on AttributeValue { value } }
      }
    }
  }
}
"""

# --- Parsing grammars -------------------------------------------------------

# Stripped before any mass is read: "12 FL OZ (360mL)" is a volume, and reading
# 12 as ounces of mass would invent a density out of nothing.
_FLUID = re.compile(r"\b(?:fl\.?\s*oz\.?|fluid\s+ounces?)\b", re.I)
# "(80g)", "40 g", "129g/4.5 oz". The lookbehind stops "1/16" being read as a
# mass and stops the "g" of a word being treated as a unit.
_GRAMS = re.compile(r"(?<![\w.])([0-9]+(?:\.[0-9]+)?)\s*(?:grammes?|grams?|g)\b", re.I)
_OUNCES = re.compile(r"(?<![\w.])([0-9]+(?:\.[0-9]+)?)\s*(?:ounces?|oz)\b", re.I)
# Metric volumes. Present so a volume can be positively identified rather than
# merely failing to match a mass -- "1 Tbsp. (15mL)" must be a refusal, not a
# fall-through into the ounce branch.
_MILLILITRES = re.compile(
    r"(?<![\w.])([0-9]+(?:\.[0-9]+)?)\s*(?:millilit(?:re|er)s?|ml|lit(?:re|er)s?|l)\b",
    re.I,
)
# "26 g" / "26g" -> 26. Applied only after the bounded-value guard below.
_LEADING_NUMBER = re.compile(r"([0-9]+(?:\.[0-9]+)?)")
# "less than 1 g" in any capitalisation. An upper bound, never a measurement.
_BOUNDED = re.compile(r"less\s+than", re.I)
# "Serves 4" / "Serves about 2.5" / "Servings: 6" / a bare "12".
_SERVINGS = re.compile(
    r"^\s*(?:serves|servings?)?\s*:?\s*(?:about|approx\.?|approximately)?\s*"
    r"([0-9]+(?:\.[0-9]+)?)\s*$",
    re.I,
)
# A store page URL in the locator sitemap: /nc/greensboro/750/.
_STORE_URL = re.compile(
    r"<loc>\s*(https://locations\.traderjoes\.com/([a-z]{2})/([^/<>\s]+)/([0-9]+)/)\s*</loc>",
    re.I,
)
_POSTAL_CODE = re.compile(r'"postalCode"\s*:\s*"?([0-9]{5})', re.I)


class TraderJoesError(RuntimeError):
    """Base for this module's failures."""


class CatalogueError(TraderJoesError):
    """The catalogue query failed or came back in a shape we do not understand.

    Raised rather than degrading to an empty list: zero products with no error
    is indistinguishable from "Trader Joe's stopped selling food", which is the
    shape of bug that lets a broken scraper sit unnoticed for weeks.
    """


class ThrottledError(TraderJoesError):
    """The endpoint returned 403/429. Never seen in measurement; see PACING."""


@dataclass(frozen=True)
class StoreRef:
    """One physical shop, as the locator describes it."""

    store_code: str          # zero-padded to match the catalogue blobs: "0750"
    number: str              # as the locator writes it: "750"
    state: str
    city: str
    url: str


@dataclass(frozen=True)
class Panel:
    """One nutrition panel. There may be several per product -- see the trap."""

    sequence: int
    title: str | None
    serving_size: str | None
    servings_per_container: str | None
    protein_per_serving: float | None
    calories: float | None = None


@dataclass(frozen=True)
class Listing:
    """One catalogue product, after the attribute indirection is unwound.

    ``price`` may legitimately be ``None``; so may every nutrition figure.
    """

    sku: str
    name: str | None
    title: str | None
    url_key: str | None
    national_price: float | None
    store_price: float | None
    available: bool | None    # None means the chosen store said nothing
    size_quantity: float | None
    size_uom: str | None
    country_of_origin: str | None
    panel: Panel | None
    #: False when the server failed to render this product's attributes at all
    #: -- see THE PARTIAL-FAILURE TRAP. Distinct from a product that simply has
    #: no nutrition, because "we could not ask" is not "there is none".
    has_attributes: bool = True


@dataclass
class _FoodFact:
    """Nutrition to land in foods/food_nutrients, keyed on the Trader Joe's SKU."""

    sku: str
    name: str
    category: str
    protein_per_100g: float
    item_name: str


# --------------------------------------------------------------------------- #
# Readiness
# --------------------------------------------------------------------------- #
def readiness() -> tuple[bool, str]:
    """Always ready -- this source needs no credential of any kind.

    A constant, exactly as sprouts.py's is, and for the same reason: the CLI
    and GUI branch on the *flag*, not on whether the attribute exists, so
    returning a constant here is what lets a no-setup store drop into the store
    table beside Whole Foods and Kroger with no special case. Trader Joe's is
    in fact the least demanding source in the project -- there is no login, no
    hand-minted cookie, no developer registration and no browser step, because
    the endpoint is unauthenticated (see the module docstring).
    """
    return True, "no credentials required (unauthenticated public API)"


# --------------------------------------------------------------------------- #
# Pure helpers -- no network, no DB, so every trap above is directly testable
# --------------------------------------------------------------------------- #
def attribute_map(product: dict[str, Any]) -> dict[str, str]:
    """``custom_attributesV2`` -> ``{code: value}``, ignoring non-scalar entries.

    Multi-select attributes come back as ``AttributeSelectedOptions`` with no
    ``value`` at all; the query does not select them and this drops them rather
    than storing a ``None`` that later code would have to re-check.
    """
    items = ((product.get("custom_attributesV2") or {}).get("items")) or []
    out: dict[str, str] = {}
    for item in items:
        code, value = item.get("code"), item.get("value")
        if code and value is not None:
            out[str(code)] = str(value)
    return out


def json_attribute(raw: str | None) -> list[dict[str, Any]]:
    """Decode a JSON-in-a-String attribute. Never raises.

    These arrive as ``"[]"`` when empty rather than ``null``, and a malformed
    one is a fact about that product, not a reason to abandon a 2,454-product
    scrape -- so a decode failure is an empty list, and the caller reads that
    as "this product told us nothing", which is true.
    """
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return [entry for entry in value if isinstance(entry, dict)] if isinstance(value, list) else []


def store_scoped_value(blob: list[dict[str, Any]], store_code: str | None) -> str | None:
    """Pull one store's entry out of a per-store attribute blob.

    ``None`` when no store code was chosen, or when this product simply has no
    row for that store -- the ``retail_price`` blob covers ~580 stores while
    ``availability`` covers ~648, so a store having one and not the other is
    ordinary and must not be read as a zero.
    """
    if not store_code:
        return None
    for entry in blob:
        if str(entry.get("store_code") or "") == store_code:
            value = entry.get("value")
            return None if value is None else str(value)
    return None


def store_price(raw: str | None) -> float | None:
    """A per-store shelf price, or ``None``.

    ``"0"`` and ``""`` are placeholders meaning "this store has no price on
    file", not a price of zero. Letting a 0.00 through would sort straight to
    the top of every cheapest-protein list -- savings.py rule 1, and the same
    guard ``price_per_gram_from_per_pound`` makes for the same reason.
    """
    price = base.price_to_float(raw)
    return price if price is not None and price > 0 else None


def is_available(raw: str | None) -> bool | None:
    """Whether the chosen store stocks this. ``None`` means it did not say.

    Three states, not two. 924 of 2,454 published products are explicitly not
    carried at the Greensboro store, but a product missing from the blob
    entirely is unknown -- and unknown is not absent, so the caller keeps it.
    """
    if raw is None or raw == "":
        return None
    return raw == "1"


def label_number(amount: Any) -> float | None:
    """The number out of a label field like ``"26 g"`` or ``"240 "``, or ``None``.

    THE 'less than' TRAP lives here. Every numeric field on a Trader Joe's
    panel is a string with its unit attached, and 93 of 1,660 protein lines
    read ``"less than 1 g"`` instead of a figure (in four different
    capitalisations). ``float()`` raises on those; stripping the non-digits
    yields ``1``, which *overstates* an item that has essentially none.

    An upper bound is not a measurement (savings.py rule 1), so it is refused.
    That refusal matters in a specific direction: overstating protein
    understates cost per gram of protein, which silently promotes an item up
    every ranking -- the one error this project must never make quietly.
    """
    if amount is None:
        return None
    text = str(amount).strip()
    if not text or _BOUNDED.search(text):
        return None
    match = _LEADING_NUMBER.search(text)
    if not match:
        return None
    value = float(match.group(1))
    return value if value >= 0 else None


def protein_grams(amount: Any) -> float | None:
    """Grams of protein from a label string, or ``None``.

    A thin alias for :func:`label_number` so the protein path -- the one that
    decides rankings -- reads by name at every call site. The affected
    ``"less than 1 g"`` products are oils and jams the optimiser loses nothing
    by skipping.
    """
    return label_number(amount)


def servings_per_container(value: Any) -> float | None:
    """The servings count, or ``None`` when the label does not give a number.

    The 'Serves about' trap. Only 6 of 1,664 live panels hold a bare numeral;
    the rest are prose. ``"Serves 4"``, ``"Serves about 2.5"`` and
    ``"Servings: 6"`` all carry a real count and are read. ``"Servings varied"``
    does not, and returns ``None`` rather than a defaulted 1 -- which would
    understate protein per package on exactly the variable-weight items where
    it matters most.
    """
    match = _SERVINGS.match(str(value or ""))
    if not match:
        return None
    count = float(match.group(1))
    return count if count > 0 else None


def serving_grams(serving_size: str | None) -> float | None:
    """Grams per serving, across every shape Trader Joe's prints.

    Handles ``"4 pieces(80g)"`` (no space), ``"1/4 Pizza (129g/4.5 oz)"`` and
    ``"3 oz (84g/about 1/6 pkg)"`` (metric first, then a second unit), and
    ``"1 oz (28g/about 1 inch cube)"``.

    A metric mass always wins over an imperial one when both are present,
    because it is the figure the label rounded *to*; taking the ounces would
    import a rounding error the label had already resolved.

    ``None`` when the panel gives no mass at all -- the VOLUME trap. A serving
    measured only in millilitres is a volume, and this refuses rather than
    reading the number as grams, which would invent a protein density for every
    beverage and condiment in the catalogue. That refusal is a fact about the
    product, not a parse failure to work around.
    """
    if not serving_size:
        return None
    text = _FLUID.sub(" ", serving_size)

    match = _GRAMS.search(text)
    if match:
        grams = float(match.group(1))
        return grams if grams > 0 else None

    # No mass in grams. If the only metric figure is a volume, say so, rather
    # than falling through to an ounce that is really a fluid ounce.
    if _MILLILITRES.search(text):
        return None

    match = _OUNCES.search(text)
    if match:
        grams = float(match.group(1)) * savings.GRAMS_PER_OZ
        return grams if grams > 0 else None
    return None


def size_text(quantity: Any, uom: str | None) -> str | None:
    """``("1.000000", "Lb")`` -> ``"1 lb"``. ``None`` if either half is missing.

    The two fields are meaningless apart, and 132 of 2,454 products have
    neither. The output is deliberately in the grammar ``savings.parse_size``
    already reads -- this module grows no second size parser, so a pound means
    the same thing here as everywhere else in the project.
    """
    if quantity is None or not uom:
        return None
    try:
        amount = float(str(quantity).strip())
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    return f"{amount:g} {str(uom).strip().lower()}"


def package_grams(size: str | None) -> float | None:
    """Grams in the package, or ``None`` if the size is not a mass.

    Delegates the grammar to ``savings.parse_size`` and only converts the
    result, so ``Fl Oz``/``mL``/``L``/``Pint``/``Qt`` sizes (444 of 2,454
    products) come back ``None`` instead of being read as weight, and so do
    counts (``Each``, ``Bag``, ``Doz``).
    """
    if not size:
        return None
    parsed = savings.parse_size(size)
    if parsed is None or parsed.base_unit != savings.WEIGHT or parsed.base_quantity <= 0:
        return None
    return parsed.base_quantity * savings.GRAMS_PER_OZ


def panel_is_whole_container(title: str | None) -> bool:
    """Does this panel restate the whole package rather than one serving?

    Half of the PANEL TRAP defence. Checked against the title independently of
    ``display_sequence`` so that either signal alone still catches it.
    """
    lowered = (title or "").lower()
    return any(word in lowered for word in ("container", "package", "pkg", "bottle"))


def select_panel(panels: Iterable[Panel]) -> Panel | None:
    """The per-serving panel, or ``None`` if there is nothing usable.

    THE PANEL TRAP, in one function. 289 products carry more than one panel and
    the extra one restates the whole container **while repeating the
    per-serving ``serving_size`` string verbatim**, so pairing its protein with
    that string overstates density by the servings count -- 3x on Chicken Shu
    Mai (see the module docstring).

    Two independent rules, both applied: discard anything whose title names a
    container, then take the lowest ``display_sequence`` of what is left. On
    the 2026-08 catalogue either rule alone sufficed; requiring both means one
    of them changing upstream degrades to the other rather than to a silent 3x
    error. Only one product in 2,454 has *nothing* left after the title filter.
    """
    candidates = [p for p in panels if not panel_is_whole_container(p.title)]
    if not candidates:
        return None
    return min(candidates, key=lambda p: p.sequence)


def parse_panels(raw: str | None) -> list[Panel]:
    """Decode the ``nutrition`` attribute into panels, in the order given."""
    panels: list[Panel] = []
    for index, entry in enumerate(json_attribute(raw)):
        try:
            sequence = int(str(entry.get("display_sequence") or index).strip())
        except (TypeError, ValueError):
            sequence = index
        protein = None
        for detail in entry.get("details") or []:
            if not isinstance(detail, dict):
                continue
            if str(detail.get("nutritional_item") or "").strip().lower() == "protein":
                protein = protein_grams(detail.get("amount"))
                break
        panels.append(Panel(
            sequence=sequence,
            title=entry.get("panel_title"),
            serving_size=entry.get("serving_size"),
            servings_per_container=entry.get("servings_per_container"),
            protein_per_serving=protein,
            calories=label_number(entry.get("calories_per_serving")),
        ))
    return panels


#: Nothing edible is more than 100 g of protein per 100 g. Pure whey isolate
#: reaches ~90; the highest figure in this catalogue is pork rinds at 64.
MAX_PLAUSIBLE_PROTEIN_PER_100G = 100.0


def plausible_density(density: float | None) -> float | None:
    """``density`` if it is physically possible, else ``None``.

    A last-line invariant shared with instacart_storefront.py, which produced
    677 and 307 g per 100 g on a real run before it had one. Both scrapers can
    reach the same nonsense the same way: the package route multiplies a
    per-serving protein figure by a servings count, so one wrong count -- or
    one panel that was already whole-package (THE PANEL TRAP) -- inflates the
    answer without any single input looking wrong.

    It **rejects rather than clamps**. Clamping to 100 would turn a data error
    into a plausible-looking figure that then sorts near the top of every
    cheapest-protein ranking, which is exactly the outcome the guard exists to
    prevent. ``None`` means no ``food_nutrients`` row, and the item is simply
    absent from protein rankings until the data is right -- an honest gap
    rather than a confident lie (savings.py rule 1).
    """
    if density is None or density <= 0 or density > MAX_PLAUSIBLE_PROTEIN_PER_100G:
        return None
    return density


def protein_per_100g(panel: Panel | None, size: str | None) -> float | None:
    """Protein density, by whichever of the two routes the label supports.

    1. **Serving mass.** ``protein / serving_grams * 100``. Preferred whenever
       the panel prints a metric serving weight, because both sides of the
       division are then per-serving and no other figure can contaminate it.
       This route covers 1,238 of the 1,664 products that carry a panel.
    2. **Whole package.** ``protein * servings / package_grams * 100``. Needs a
       numeric servings count *and* a package size that is a mass. Safe here
       only because :func:`select_panel` has already guaranteed ``protein``
       is per-serving -- running this against a container panel would multiply
       an already-whole-package figure by the servings count again.

    ``None`` when neither route is available, and ``None`` when the answer is
    physically impossible -- see :func:`plausible_density`. The caller then
    writes no ``food_nutrients`` row, rather than inventing a density.
    """
    if panel is None:
        return None
    protein = panel.protein_per_serving
    if protein is None or protein <= 0:
        return None

    grams = serving_grams(panel.serving_size)
    if grams:
        return plausible_density(protein / grams * 100.0)

    count = servings_per_container(panel.servings_per_container)
    pack = package_grams(size)
    if count and pack:
        return plausible_density(protein * count / pack * 100.0)
    return None


def display_item_name(listing_title: str | None, name: str | None, size: str | None) -> str:
    """The name to store, with the package size folded in.

    Two things happen here, both for the benefit of code elsewhere.

    ``item_title`` is preferred over ``name`` because the catalogue's ``name``
    is upper-case shelf-tag text (``"NATURAL CUBED CHICKEN BREAST"``) while
    ``item_title`` is the cased marketing title (``"Pesto Chicken Breast"``) --
    the keyword matcher and the UI both read better for it.

    The size is appended so ``savings.parse_size`` can read it back off
    ``item_name``, which is how price and size end up referring to the same
    quantity with no per-store branching downstream. Same call kroger.py makes,
    and the reason its size string is folded in there too.
    """
    label = (listing_title or name or "").strip()
    if size and not savings.parse_size(label):
        return f"{label}, {size}".strip(", ")
    return label


def product_page_url(url_key: str | None) -> str | None:
    return f"{BASE_URL}{PRODUCT_PATH.format(url_key=url_key)}" if url_key else None


def store_code_for(postal_code: str | None) -> str | None:
    """The pinned store code for a ZIP, or ``None`` if we have not verified one.

    ``None`` is a real and common answer -- see the module docstring on why a
    general ZIP lookup is not affordable -- and the caller falls back to the
    national price rather than guessing at a nearby store.
    """
    return STORE_CODE_BY_POSTAL_CODE.get((postal_code or "").strip())


def parse_store_sitemap(xml: str) -> dict[str, StoreRef]:
    """Locator sitemap -> ``{store_code: StoreRef}``.

    The store number in the URL (``750``) is zero-padded to four digits to
    match the ``store_code`` the catalogue blobs use (``0750``). That mapping
    is the join between the two systems and is the only reason a locator
    lookup can say anything about a price.
    """
    stores: dict[str, StoreRef] = {}
    for url, state, city, number in _STORE_URL.findall(xml):
        code = number.zfill(4)
        stores[code] = StoreRef(
            store_code=code, number=number, state=state.lower(), city=city, url=url
        )
    return stores


def parse_store_postal_code(html: str) -> str | None:
    """The postal code from a locator store page's schema.org PostalAddress."""
    match = _POSTAL_CODE.search(html)
    return match.group(1) if match else None


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class TraderJoesClient:
    """HTTP session for the catalogue API and the locator.

    Holds one :class:`~grocery_planner.scrapers.retry.Paced` for the GraphQL
    path. The locator is a handful of requests against a static CDN-backed site
    and is not paced separately; if that ever changes it should get its own
    budget rather than being folded into this one, for the reason retry.py sets
    out -- one host's two paths can be policed by completely different rules.
    """

    def __init__(
        self,
        client: httpx.Client | None = None,
        timeout: float = 60.0,
        pace: retry.Paced | None = None,
    ):
        # Injectable so tests drive it with a fake clock instead of real sleeps.
        self.pace = pace or retry.Paced(CATALOGUE_BUDGET)
        #: Set when pagination stopped early because the host started throttling
        #: mid-catalogue. Read by `scrape` into stats -- a partial catalogue that
        #: does not announce itself is indistinguishable from a shrinking one.
        self.truncated_by_throttling = False
        self._owned = client is None
        self._http = client or httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": base.user_agent(),
                "Accept": "application/json",
                "Accept-Language": "en-US,en;q=0.9",
                # Sent because it is polite, not because anything checks it.
                "Origin": BASE_URL,
                "Referer": f"{BASE_URL}/",
            },
        )

    def __enter__(self) -> "TraderJoesClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owned:
            self._http.close()

    def query(self, document: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run one GraphQL query. Raises on transport, throttle or GraphQL errors."""
        self.pace.wait()
        payload: dict[str, Any] = {"query": document}
        if variables:
            payload["variables"] = variables
        response = self._http.post(f"{BASE_URL}{GRAPHQL_PATH}", json=payload)

        if response.status_code in retry.THROTTLE_STATUS:
            if self.pace.record_throttled():
                self.pace.cool_off()
            raise ThrottledError(
                f"{response.status_code} from {GRAPHQL_PATH}. No throttling was "
                "seen when this source was measured, so this is new behaviour "
                "-- check the pacing before raising the rate."
            )
        self.pace.record_success()
        response.raise_for_status()
        body = response.json()

        # A Magento GraphQL response can be 200 with a populated `errors` list
        # and a partly-null `data` -- which is exactly what omitting
        # `custom_attributesV2`'s filters argument produces. Surfacing it is the
        # difference between "nutrition is missing" and "we asked wrongly".
        errors = body.get("errors") or []
        if errors and not body.get("data"):
            raise CatalogueError(
                "Trader Joe's GraphQL returned no data: "
                + "; ".join(str(e.get("message")) for e in errors[:3])
            )
        if errors:
            log.warning(
                "traderjoes: %d partial GraphQL error(s), first: %s",
                len(errors), errors[0].get("message"),
            )
        return body

    def catalogue(self, page_size: int = PAGE_SIZE) -> tuple[list[dict[str, Any]], int]:
        """Every published product. Returns ``(items, total_count)``.

        Pages until the server says it is done. ``published:{eq:"1"}`` is what
        separates the 2,454 live products from the 28,323 rows the unfiltered
        query returns, most of which are years of delisted items.
        """
        items: list[dict[str, Any]] = []
        total_count = 0
        page = 1
        while page <= MAX_PAGES:
            try:
                body = self.query(
                    CATALOGUE_QUERY, {"pageSize": page_size, "currentPage": page}
                )
            except ThrottledError:
                # Keep the pages already fetched. This is retry.py's founding
                # complaint in its other form: a blip at page 24 of 25 used to
                # discard 2,400 products, and "the longer and more valuable the
                # scrape, the more it loses" is exactly backwards.
                #
                # Nothing fetched yet means there is nothing to salvage, and an
                # empty return there would read as "Trader Joe's has no
                # products" -- so the first page re-raises.
                if not items:
                    raise
                self.truncated_by_throttling = True
                log.warning(
                    "traderjoes: throttled at page %d; keeping the %d products "
                    "already fetched rather than discarding the run",
                    page, len(items),
                )
                break
            products = (body.get("data") or {}).get("products")
            if products is None:
                raise CatalogueError(
                    "Trader Joe's GraphQL returned no `products` block. The "
                    "query shape has probably changed -- re-introspect rather "
                    "than treating this as an empty catalogue."
                )
            total_count = products.get("total_count") or total_count
            batch = products.get("items") or []
            items.extend(batch)
            total_pages = ((products.get("page_info") or {}).get("total_pages")) or 1
            if page >= total_pages or not batch:
                break
            page += 1
        else:
            # Ran out of pages rather than out of data: a bound was hit, so say
            # so instead of returning a quietly truncated catalogue.
            log.warning(
                "traderjoes: stopped at the %d-page ceiling with %d products; "
                "the catalogue may be truncated", MAX_PAGES, len(items),
            )
        return items, total_count

    def store_directory(self) -> dict[str, StoreRef]:
        """Every Trader Joe's the locator publishes, from its sitemap.

        One request, ~220 KB, 661 stores. Carries state, city and store number
        but **no postal codes** -- see the module docstring for why that makes
        a general ZIP lookup unaffordable.
        """
        response = self._http.get(LOCATOR_SITEMAP, headers={"Accept": "application/xml"})
        response.raise_for_status()
        return parse_store_sitemap(response.text)

    def store_postal_code(self, store: StoreRef) -> str | None:
        """The postal code a store page reports, or ``None`` if it has none."""
        response = self._http.get(store.url, headers={"Accept": "text/html"})
        response.raise_for_status()
        return parse_store_postal_code(response.text)


# --------------------------------------------------------------------------- #
# Row mapping
# --------------------------------------------------------------------------- #
def parse_listing(product: dict[str, Any], store_code: str | None) -> Listing | None:
    """One raw catalogue product -> :class:`Listing`. Pure.

    ``None`` only when the product has no SKU, which is the one field
    everything else is keyed on.
    """
    sku = str(product.get("sku") or "").strip()
    if not sku:
        return None
    # `null` means the server errored on this product's attributes; an empty
    # dict would lose that distinction. See THE PARTIAL-FAILURE TRAP.
    has_attributes = product.get("custom_attributesV2") is not None
    attributes = attribute_map(product)
    national = base.price_to_float(
        (((product.get("price_range") or {}).get("minimum_price") or {})
         .get("final_price") or {}).get("value")
    )
    return Listing(
        sku=sku,
        name=product.get("name"),
        title=attributes.get("item_title"),
        url_key=product.get("url_key"),
        national_price=national if national and national > 0 else None,
        store_price=store_price(
            store_scoped_value(json_attribute(attributes.get("retail_price")), store_code)
        ),
        available=is_available(
            store_scoped_value(json_attribute(attributes.get("availability")), store_code)
        ),
        size_quantity=attributes.get("sales_size"),
        size_uom=attributes.get("sales_uom_description"),
        country_of_origin=attributes.get("country_of_origin"),
        panel=select_panel(parse_panels(attributes.get("nutrition"))),
        has_attributes=has_attributes,
    )


def listing_to_row(
    listing: Listing,
    zip_code: str,
    now: datetime,
    store_code: str | None = None,
) -> tuple[dict[str, Any], _FoodFact | None]:
    """Map one product to a ``deals`` row plus (when computable) a food fact.

    Pure -- no DB, no network.
    """
    size = size_text(listing.size_quantity, listing.size_uom)
    price = listing.store_price if listing.store_price is not None else listing.national_price
    # Which of the two published figures this row is actually quoting. Recorded
    # per row rather than only per run, because a single scrape mixes them: 566
    # of 2,454 products had no price on file at the Greensboro store and fell
    # back to the national one.
    scope = "store" if listing.store_price is not None else "national"
    density = protein_per_100g(listing.panel, size)
    item_name = display_item_name(listing.title, listing.name, size)
    identifier, identifier_ns = base.product_identifier(listing.sku, PRODUCT_IDENTIFIER_NS)

    # Trader Joe's publishes no per-weight denomination and the schema has no
    # field for one -- see the module docstring. Every size is a package
    # quantity, so every row is sold by the unit.
    sold_by = "UNIT"

    notes = [
        "source=traderjoes_graphql",
        f"sku={listing.sku}",
        f"postal_code={zip_code}",
        f"pricing_scope={scope}",
    ]
    if store_code:
        notes.append(f"store_code={store_code}")
    if size:
        notes.append(f"size={size}")
    notes.append(f"sold_by={sold_by}")
    if listing.country_of_origin:
        notes.append(f"country_of_origin={listing.country_of_origin}")
    panel = listing.panel
    if panel is not None:
        if panel.protein_per_serving is not None:
            notes.append(f"protein_per_serving_g={panel.protein_per_serving:g}")
        grams = serving_grams(panel.serving_size)
        if grams is not None:
            notes.append(f"serving_grams={grams:g}")
        count = servings_per_container(panel.servings_per_container)
        if count is not None:
            notes.append(f"servings_per_container={count:g}")
    if density is not None:
        notes.append(f"protein_per_100g={density:.2f}")
    if listing.available is False:
        notes.append("availability=not_carried")
    elif listing.available is None and store_code:
        notes.append("availability=unknown")
    if not listing.has_attributes:
        # So a row with no size and no nutrition is legible as "the server
        # failed here", not as "this product has none". See the trap.
        notes.append("attributes_unavailable=true")
    if price is None:
        notes.append("price_missing=true")

    row = {
        "item_name": item_name,
        "sub_category": base.infer_sub_category(item_name, MERCHANT, price is not None),
        "deal_type": DEAL_TYPE if price is not None else f"{DEAL_TYPE} (price not listed)",
        "deal_description": f"${price:.2f}" if price is not None else "Trader Joe's shelf listing",
        "regular_price": None,
        "sale_price": price,
        "dollar_price": price,
        "discount_amount": None,
        "discount_percent": None,
        "valid_from": now.date().isoformat(),
        # A shelf price announces no expiry and `deals` is replaced wholesale on
        # every re-scrape, so a missing valid_to reads as "unknown", never
        # "expired" (GFP-16) -- same call as kroger.py and sprouts.py.
        "valid_to": None,
        "loyalty_required": "N",
        "notes": "; ".join(notes),
        "source_url": product_page_url(listing.url_key),
        # The catalogue does carry image attributes, but they are templated
        # paths that need a base-media URL this query does not fetch. None
        # rather than a guessed URL that would render broken (GFP-99).
        "image_url": None,
        "flipp_flyer_id": None,
        "flipp_item_id": None,
        "flipp_coupon_id": None,
        "sold_by": sold_by,
        "weight_basis": weight_basis.classify(sold_by, None, item_name),
        # Derived downstream from price and size by the shared engine, so that
        # the one definition of a pound stays in savings.py.
        "price_per_unit": None,
        "price_per_unit_uom": None,
        "product_identifier": identifier,
        "product_identifier_ns": identifier_ns,
    }

    fact = None
    if density and density > 0:
        fact = _FoodFact(
            sku=listing.sku,
            name=listing.title or listing.name or item_name,
            category=row["sub_category"],
            protein_per_100g=density,
            item_name=item_name,
        )
    return row, fact


# --------------------------------------------------------------------------- #
# Persistence -- same shape as kroger.py/sprouts.py, for the same reasons
# --------------------------------------------------------------------------- #
#: Where this figure came from, and the banner it is sold under. Passed
#: separately because they are not always equal -- see foodfacts.
FOOD_SOURCE = "traderjoes"
MATCH_METHOD = "traderjoes_label_direct"


def _upsert_food_fact(conn: sqlite3.Connection, fact: _FoodFact) -> None:
    """Record this retailer's own protein figure (GFP-302: one shared write).

    Trader Joe's keys on its own SKU.
    """
    foodfacts.upsert_food_fact(
        conn, FOOD_SOURCE, STORE_KEY, MATCH_METHOD,
        foodfacts.FoodFact(
            source_ref=fact.sku,
            name=fact.name,
            category=fact.category,
            protein_per_100g=fact.protein_per_100g,
            item_name=fact.item_name,
        ),
    )


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def verify_store(
    store_code: str | None = None,
    client: TraderJoesClient | None = None,
) -> tuple[bool, str]:
    """Prove a pinned store still exists, and report what ZIP it claims.

    Exists because :data:`STORE_CODE_BY_POSTAL_CODE` is a hand-verified pin that
    cannot self-heal (a general ZIP lookup is unaffordable -- see the module
    docstring), and a store that closes or gets renumbered would otherwise be
    invisible: the catalogue would simply stop carrying that code and every
    price would quietly fall back to the national figure. Same reasoning as
    sprouts.py's canary -- a wrong answer that looks like a right answer is the
    failure worth engineering against.

    Two requests: the locator sitemap and one store page.
    """
    code = store_code or STORE_CODE_BY_POSTAL_CODE.get(DEFAULT_POSTAL_CODE)
    if not code:
        return False, "no store code to verify"
    owned = client is None
    active = client or TraderJoesClient()
    try:
        directory = active.store_directory()
        store = directory.get(code)
        if store is None:
            return False, (
                f"store {code} is not in the locator's {len(directory)} stores. "
                "It may have closed or been renumbered -- re-check "
                f"{LOCATOR_URL} and update STORE_CODE_BY_POSTAL_CODE."
            )
        postal = active.store_postal_code(store)
    except (TraderJoesError, httpx.HTTPError, ValueError) as exc:
        # Could not ask. Not the same as "the pin is stale", and must not be
        # reported as one.
        return False, f"could not verify (transport): {exc}"
    finally:
        if owned:
            active.close()
    where = f"{store.city}, {store.state.upper()}"
    if not postal:
        return True, f"store {code} exists ({where}) but published no postal code"
    return True, f"store {code} OK -- {where} {postal}"


def serves(postal_code: str) -> bool | None:
    """Is there a Trader Joe's serving ``postal_code``? (GFP-257)

    Returns ``True`` only for a ZIP whose store has been verified by hand and
    pinned in :data:`STORE_CODE_BY_POSTAL_CODE`, and ``None`` -- never
    ``False`` -- for everything else.

    That is not laziness, it is the honest answer, and the module docstring
    sets out why at length. Trader Joe's exposes **no location argument
    anywhere**: ``products`` takes none, the ``store_code`` filter it does
    accept is a measured no-op, ``pickupLocations`` returns zero results for
    any area, and ``availableStores`` describes Magento store views rather than
    shops. The locator on a separate host lists all 661 stores but publishes no
    postal codes above the individual store page, so resolving one arbitrary
    ZIP would cost 661 page fetches.

    So the question genuinely cannot be put to this source at reasonable cost,
    and unknown is not absent -- returning ``False`` would tell availability.py
    that Trader Joe's is not in a market when all we actually know is that we
    did not look. ``None`` is treated permissively there, which is correct.
    """
    if store_code_for(postal_code):
        return True
    return None


def scrape(
    postal_code: str | None = None,
    limit: int | None = None,
    conn: sqlite3.Connection | None = None,
    client: TraderJoesClient | None = None,
    now: datetime | None = None,
    store_code: str | None = None,
    include_unavailable: bool = False,
    products: Iterable[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Scrape Trader Joe's shelf prices and return ``(rows, meta, stats)``.

    Matches the contract ``service/ingest.run_scrape`` expects of every
    scraper, and -- like kroger.py and sprouts.py -- also upserts
    ``foods``/``food_nutrients``/``deal_food_match`` for every product whose
    protein density is computable, so nutrition arrives with the price instead
    of needing a USDA matching pass.

    One pass, not two: unlike Sprouts, price and nutrition come back in the
    *same* response, because both are attributes of the same product. That is
    the whole benefit of an unrestricted GraphQL endpoint and is why this
    scraper is ~25 requests rather than ~46,000.

    ``store_code`` overrides the pinned lookup. When neither yields one, prices
    are the national "Global TJ price" figure and every row says so.

    ``include_unavailable`` keeps products the chosen store explicitly does not
    stock (924 of 2,454 at Greensboro). Off by default because a price for
    something you cannot buy is not a deal; the count is reported either way.
    """
    zip_code = postal_code or DEFAULT_POSTAL_CODE
    moment = now or datetime.now(timezone.utc)
    code = store_code or store_code_for(zip_code)

    owned_client = client is None
    active = client or TraderJoesClient()
    own = conn or db.connect()

    try:
        if products is not None:
            catalogue = list(products)
            total_count = len(catalogue)
        else:
            catalogue, total_count = active.catalogue()

        rows: list[dict[str, Any]] = []
        facts: dict[str, _FoodFact] = {}
        filtered_unavailable = 0
        unparsed = 0
        no_panel = 0
        missing_attributes = 0
        limit_applied = False

        for product in catalogue:
            listing = parse_listing(product, code)
            if listing is None:
                unparsed += 1
                continue
            if not listing.has_attributes:
                missing_attributes += 1
            # `is False` on purpose: an unknown availability keeps the product.
            if listing.available is False and not include_unavailable:
                filtered_unavailable += 1
                continue
            if listing.panel is None:
                no_panel += 1
            row, fact = listing_to_row(listing, zip_code, moment, code)
            rows.append(row)
            if fact is not None:
                facts[fact.sku] = fact
            if limit is not None and len(rows) >= limit:
                limit_applied = True
                break

        if limit_applied:
            log.info(
                "traderjoes: stopped at limit=%d of %d products; this run is "
                "deliberately partial", limit, total_count,
            )
        if missing_attributes:
            log.warning(
                "traderjoes: %d of %d product(s) came back with no attributes "
                "(server-side error, not absent data) -- they carry no size, "
                "nutrition or store price", missing_attributes, total_count,
            )
        if filtered_unavailable:
            log.info(
                "traderjoes: dropped %d product(s) store %s does not stock "
                "(pass include_unavailable=True to keep them)",
                filtered_unavailable, code,
            )

        for fact in facts.values():
            _upsert_food_fact(own, fact)
        if conn is None:
            own.commit()

        priced = sum(1 for r in rows if r["dollar_price"] is not None)
        store_priced = sum(1 for r in rows if "pricing_scope=store" in r["notes"])
        meta = {
            "name": f"{MERCHANT} shelf prices ({zip_code})",
            "id": code or NATIONAL_STORE_CODE,
            "store_code": code,
            "store_name": MERCHANT,
        }
        stats = {
            # Flipp-vintage field names, repurposed rather than forking the
            # CLI's formatting per store -- same call as kroger.py/sprouts.py.
            "weekly_ad": len(rows),
            "digital_coupons": 0,
            "no_price": len(rows) - priced,
            "bogo": 0,
            "expired_items": 0,
            "total": len(rows),
            "flyer_id": code or NATIONAL_STORE_CODE,
            "flyer_name": meta["name"],
            "flyer_status": "active",
            "valid_from": moment.date().isoformat(),
            "valid_to": moment.date().isoformat(),
            "priced": priced,
            "with_protein": len(facts),
            "sold_by_weight": 0,
            "products_seen": total_count,
            "no_nutrition_panel": no_panel,
            # Which figure the prices actually are. A run with store_priced=0
            # is quoting national prices throughout and must be legible as
            # such, not mistaken for a store-specific capture.
            "pricing_scope": "store" if code else "national",
            "store_priced": store_priced,
            "national_priced": priced - store_priced,
            # The no-silent-caps rule: every bound this run applied, counted.
            "filtered_unavailable": filtered_unavailable,
            "limit_applied": limit_applied,
            "unparsed_products": unparsed,
            # Whether pagination stopped early because the host throttled us.
            # A truncated catalogue must be legible as truncated, or the next
            # reader takes a partial run for a shrinking source -- and the
            # GFP-67 replace-guard is what decides whether it may land.
            "throttled_truncation": active.truncated_by_throttling,
            # Products the server failed to render attributes for. Counted
            # separately from `no_nutrition_panel` because "we could not ask"
            # and "there is none" are different facts -- see the trap.
            "products_missing_attributes": missing_attributes,
            **active.pace.stats(),
        }
        return rows, meta, stats
    finally:
        if owned_client:
            active.close()
