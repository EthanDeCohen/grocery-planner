"""Whole Foods Market storefront scraper (GFP-4).

Whole Foods is a different shape of source than Food Lion/Harris Teeter: those
are Flipp flyer aggregators (``scrapers/base.py``); Whole Foods is the retailer's
own storefront, driven by search, not a weekly ad. Its payload is dramatically
better for this product's purpose (see ``docs/spikes/GFP-70-whole-foods.md``,
merged on main): 100% of results carry a real shelf price, and roughly 40%
carry everything needed to compute cost-per-gram-of-protein *directly* --
price, a machine-readable size, and full nutrition facts including protein
grams -- with no USDA matching step at all. That is a ~300x improvement over
the 2-of-1,573 deals (0.13%) the Flipp-sourced pipeline can currently price
per gram of protein.

Architecture -- decided by GFP-70, not reopened here
------------------------------------------------------
The spike found a fresh ``httpx`` client cannot get past Whole Foods' session
bootstrap on its own: a JS-computed ``x-amzn-csrf`` header gates the calls
that mint the real cookies, and that header is not derivable from any
response the page ever sends a plain client. Reverse-engineering it is not a
sane bet against a private endpoint that could change on the next deploy. But
the spike's key finding is that the browser is needed only ONCE, to mint a
session -- cookies minted that way then serve unlimited further ``httpx``-only
searches, confirmed against a query neither the browser nor httpx had run
before.

So: minting is an out-of-band, human, one-time-ish step (see "Minting the
session cookie" below), never something this product does at runtime.
Playwright/Chromium do NOT ship with grocery-planner (not in pyproject.toml)
-- the Mac build is CLI-only (PyInstaller cannot cross-compile) and could
never run a browser anyway. This module is pure ``httpx``, exactly like
``scrapers/base.py``'s Flipp client, reading one already-minted cookie value
out of a small JSON config file.

The one cookie that matters: ``wfm_store_d8``
-----------------------------------------------
The spike verified, across six controlled cookie subsets plus a zero-cookie
control, that ``wfm_store_d8`` ALONE is necessary and sufficient for search
from a plain httpx client. The Amazon-domain ``session-id``/``session-token``/
``ubid-main`` cookies are not needed. Nominal cookie expiry observed: 365 days.

``wfm_store_d8``'s value is itself an encoded JSON blob carrying (among
other things) the pinned store's ``id`` (authoritative) and a ``deliveryZip``
field. **The encoding is not stable** -- the GFP-70 spike recorded a
URL-encoded value, and by 2026-08-02 the live cookie was base64 instead
(GFP-93), so :func:`_decode_store_cookie` accepts base64, URL-encoded and
bare JSON and you should paste the raw Value without worrying about which
one you got. The spike found the blob's ``name``/``state``/``geometry`` fields are
STALE placeholders that never update -- never trust those for store identity,
only ``id`` (this module doesn't need store identity beyond the ZIP check
below, so it never reads them at all).

Config file format
--------------------
A small JSON file lives at ``<user-data-dir>/wholefoods_session.json``
(``grocery_planner/paths.py::data_dir()`` -- the same folder
``grocery_planner.sqlite3`` lives in; run ``gplan db-path`` and look
alongside it)::

    {
      "wfm_store_d8": "<paste the cookie's raw Value here, exactly as shown>",
      "minted_at": "2026-08-01"
    }

``wfm_store_d8`` is the only key this module reads. ``minted_at`` is optional
and purely for a human's own bookkeeping (the nominal 365-day expiry above) --
this module does not act on it; the payload-shape check below is what
actually detects a dead cookie.

Minting the session cookie (out-of-band, human, one-time-ish step)
----------------------------------------------------------------------
1. Open https://www.wholefoodsmarket.com/ in a normal, real browser (Chrome,
   Edge, Firefox -- any modern browser; this is a one-off manual step, not
   something grocery-planner automates).
2. Let the site's own store-locator flow pin a store to the ZIP you want this
   scraper to use (e.g. type that ZIP into the "choose your store" prompt the
   site shows on first load). This is what determines the ZIP baked into the
   cookie -- see "ZIP handling" below.
3. Open DevTools -> Application (Chrome) / Storage (Firefox) -> Cookies ->
   https://www.wholefoodsmarket.com.
4. Find the cookie named ``wfm_store_d8``. Copy its raw *Value* column exactly
   as shown -- it is URL-encoded JSON; do NOT decode it by hand, this module
   decodes it itself.
5. Paste that value into ``wholefoods_session.json`` as shown above, next to
   the database (``gplan db-path`` shows the folder).
6. Re-run whenever the scraper reports the cookie is dead (see "Failing
   loudly on a dead cookie" below) -- nominal lifetime is ~365 days per the
   spike, but that is the cookie's own advertised expiry, not a guarantee
   about server-side session state, which the spike did not measure.

ZIP handling (GFP-53)
------------------------
The cookie's decoded blob carries a ``deliveryZip`` field. Every call to
:func:`scrape` computes the ZIP it is about to use exactly the way
``service/ingest.py::run_scrape`` does (``postal_code`` argument, else
``DEFAULT_POSTAL_CODE``) and refuses outright (:class:`ZipMismatchError`) if
that disagrees with the cookie's own ``deliveryZip``. Silently scraping under
the wrong ZIP would attribute one location's prices to another -- exactly the
harm GFP-53 exists to prevent. There is no "just use the cookie's ZIP
instead" fallback: the point is that the ZIP the caller *asked for* and the
ZIP the session was actually minted for must agree, not that either one wins
by default.

Failing loudly on a dead cookie (do not confuse with "no results")
------------------------------------------------------------------------
The spike documented the exact signature of an invalid/expired session: the
response's ``pageProps`` comes back as a "loading" shell --
``pageProps.pageType == "loading"`` with ``productsInfo`` (and
``searchResults``) both ``null`` -- rather than an HTTP error. :func:`scrape`
checks every page of results for this shape and raises
:class:`SessionExpiredError` with a message that says exactly that ("cookie
expired, re-mint required"), never lets it fall through as "this store has
nothing right now". A *genuine* zero-product search result is a different,
valid shape (``productsInfo`` present but empty) and is allowed to reach
``service/ingest.py``'s GFP-67 replace-guards (:class:`EmptyScrapeError` /
:class:`ImplausibleCollapseError`), which already handle "a scrape returned
suspiciously few/no rows" generically for every store -- this module
deliberately does not duplicate that check.

Auto-discovering ``buildId``
-------------------------------
Whole Foods' storefront is a Next.js app; every page embeds a ``"buildId":
"..."`` token that Next.js's data-fetching endpoints require, and it rotates
on every site deploy. This module never hard-codes it: the first request of
every :func:`scrape` call is a plain HTML search page fetch, which yields the
current ``buildId`` (regexed out of the HTML) *and* (if the session is alive)
that first query's own results in the same request, read out of the page's
``__NEXT_DATA__`` script tag. Every subsequent query in the same run reuses
that ``buildId`` against the lighter ``_next/data/.../search.json`` JSON
endpoint. This is what makes the scraper self-healing across deploys: no
version pin ever goes stale.

Extracted per product
------------------------
- ``name``, ``asin``.
- ``offerDetails.price.priceAmount`` (the real shelf price) and
  ``offerDetails.unitPrice`` (pre-computed per-unit price; ``baseUnit``
  varies -- ``each``/``pound``/``ounce``).
- A size string from ``variationsList`` -- specifically the
  ``variationNodeList`` entry whose own ``asin`` matches this product's, since
  the list otherwise mixes in sibling package sizes. Butcher-counter items
  sold by weight (ground chicken, bone-in cuts, ...) legitimately have no
  ``variationsList`` at all; that is not an error, just a different (and
  arguably more honest) product shape.
- ``nutritionFacts.servingSize``, ``servingsPerContainer``, and the protein
  gram figure out of ``macronutrients`` (the entry whose ``name`` is
  "Protein").

Where the nutrition data lands, and why
-------------------------------------------
Whole Foods hands over protein grams *directly*, per exact product (via its
``asin``) -- there is no USDA-matching problem to solve here at all, unlike
the free-text Flipp ad copy ``grocery_planner/matching.py`` has to guess at.
Rather than inventing a parallel "Whole Foods nutrition" table, this module
reuses exactly what GFP-23/GFP-25 already built for this purpose:

- ``foods`` + ``food_nutrients`` (``grocery_planner/nutrition.py``) already
  model "a food and its protein-per-100g" -- precisely the fact a serving's
  protein grams + serving size compute directly
  (``protein_g / serving_grams * 100``, a scale-invariant density, which is
  the *correct* way to fold in ``servingsPerContainer`` -- see the callout
  below -- rather than treating a per-serving figure as if it were
  per-package). Each Whole Foods product becomes one ``foods`` row: ``source=
  'wholefoods'``, ``source_ref=<asin>`` (``UNIQUE(source, source_ref)`` makes
  re-scraping the same product idempotent), ``slug='wholefoods-<asin>'`` (the
  GFP-68 stable-identity convention), ``category`` set from the search query
  that found it (one of the existing six: beef/pork/chicken/fish/tofu/whey --
  see ``DEFAULT_QUERIES`` below -- so it plugs directly into the existing
  client protein-category preferences, GFP-30, with no new vocabulary).
- ``deal_food_match`` (GFP-25) links the deal to that food. Since Whole
  Foods' own catalog data already tells us with certainty which food a row
  is -- no keyword guessing required -- this module writes that link with
  ``confidence=1.0``, ``method='wholefoods_direct'``. It deliberately sets
  ``match_source='manual'`` (not because a human reviewed it -- ``method``
  keeps that auditable/distinguishable from a real human correction) purely
  so ``grocery_planner/matching.py``'s keyword-rule auto-matcher (which scans
  every store's deals indiscriminately) can never downgrade a Whole-Foods-
  certain match to one of its own lower-confidence keyword guesses on a
  later ``gplan match`` run -- the same protection ``match_source='manual'``
  already gives a nutritionist's manual correction.

This means ``grocery_planner/savings.py``'s existing, store-agnostic
``cost_per_gram_protein`` chain (price -> ``parse_size(item_name)`` -> grams
-> ``deal_food_match`` -> ``food_nutrients``) resolves automatically for any
Whole Foods product whose package weight is known (see "Package weight and
GFP-73" below) with ZERO new code in that module -- exactly the "reuse what
exists" the ticket asked for.

Package weight, item naming, and GFP-73 (servingsPerContainer)
--------------------------------------------------------------------
``savings.cost_per_gram_protein`` needs a *package* weight in grams; a raw
per-serving protein figure divided straight into a per-package price is
exactly the GFP-73 bug (a multi-serving item's real cost-per-gram-of-protein
is far higher than that naive division suggests). This module resolves a
package weight two ways, in priority order:

1. **A real printed size** (``variationsList``'s ``dimensionValue``, e.g.
   "1.5 Pound (Pack of 1)") -- the strongest source, an actual label weight.
   When this is available, it is appended to ``item_name`` (e.g. "Bell &
   Evans Boneless Skinless Chicken Breast, 1.5 Pound (Pack of 1)") so
   ``savings.parse_size`` -- the exact same size-parsing this app already
   uses for every other store -- picks it up with no special-casing.
2. **``servingSize`` x ``servingsPerContainer``** (GFP-73's callout) as a
   fallback for butcher-counter items with no ``variationsList`` at all. This
   IS the correct way to use ``servingsPerContainer`` -- multiplying it back
   out to a package weight, never treating the per-serving protein figure as
   if it already described the whole package. This estimate is deliberately
   NOT appended into ``item_name``: it is a computed estimate, not a number
   Whole Foods printed on a label, and ``savings.py``'s rule 1 ("a size we
   cannot read is ``None``, never a guess") means this module should not
   manufacture what looks like a real printed size for the shared parser to
   trust. The ``foods``/``food_nutrients``/``deal_food_match`` rows still
   land normally for these products (so the protein density fact is not
   lost) -- only the generic per-name-parsed cost-per-gram-protein path is
   left unresolved for them, which is visible/auditable via
   ``notes`` (``package_weight_source=serving_estimate``), not silent.

If neither source yields a weight-based size, the food/nutrition rows still
land (a nutritionist gets the fact either way); only the automatic
cost-per-gram-of-protein chain doesn't fire for that specific row.

Scope of search: which categories, and why
-----------------------------------------------
Whole Foods has no "weekly ad" to fetch wholesale the way Flipp does --
everything is search-driven. :data:`DEFAULT_QUERIES` runs one search per the
same six categories the rest of this app's protein-matching already
recognizes (GFP-23/GFP-30: beef, pork, chicken, fish, tofu, whey), a modest,
paced set of requests (see "Being a polite client" below), not an attempt to
mirror the entire storefront.

Being a polite client
-------------------------
Whole Foods' search endpoints are an unauthenticated, undocumented part of a
real retailer's storefront, not a public API with a rate-limit contract. This
module: identifies itself with the same plain, honest User-Agent the Flipp
client already uses (``scrapers/base.py::USER_AGENT``) rather than
impersonating a browser; issues one HTTP request per bootstrap + one per
remaining query (roughly a dozen total for the default query list, well
inside what the spike's own manual probing already did without incident);
and reuses one ``httpx.Client`` (and the one bootstrapped ``buildId``) across
every query in a run rather than reconnecting per request.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import sqlite3
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx

from .. import credentials, db, matching
from . import base

STORE_KEY = "wholefoods"
MERCHANT = "Whole Foods Market"
DEFAULT_POSTAL_CODE = "27401"
DEAL_TYPE = "Storefront Price"

# GFP-111: the vocabulary this module's `product_identifier` values belong to.
# An ASIN is Amazon's identifier, which is what the Whole Foods storefront is
# built on -- so the namespace is named for the vocabulary, not for the shop
# (GFP-32), exactly as `kroger.product_id` is named for the API that issues it
# rather than for Harris Teeter.
#
# Stored verbatim as TEXT: an ASIN is alphanumeric ('B09439SKW3') and has no
# numeric reading at all.
PRODUCT_IDENTIFIER_NS = "wholefoods.asin"

# unitPrice.baseUnit values that mean "this price is per unit WEIGHT", i.e. the
# shopper pays this multiplied by however much the cut weighs (GFP-98). Whole
# Foods uses "pound" for butcher-counter items; "each" is a package price.
_WEIGHT_BASE_UNITS = frozenset({"pound", "lb", "lbs", "ounce", "oz", "kg", "kilogram"})

BASE_URL = "https://www.wholefoodsmarket.com"
SEARCH_HTML_PATH = "/grocery/search"

# GFP-115: where a product page lives, built from the asin.
#
# UNLIKE KROGER, this is CONSTRUCTED rather than read from the payload. The
# Whole Foods product payload carries no URL field at all -- verified by
# dumping a live product and walking every string in it recursively; the only
# URLs anywhere are `productImages` (Amazon media). So the shape had to be
# established and then confirmed, which the ticket insists on because a guessed
# pattern that 404s is worse than no link (GFP-38 chose plain text over a dead
# control for exactly this reason).
#
# Confirmed 2026-08-03 in a real browser: /grocery/product/B0787WTY4C returns
# "Bell & Evans Boneless Skinless Chicken Breast", $6.99/lb, 27 g protein per
# 4 oz -- the same product this scraper stores, at the same 27401 ZIP.
# `/product/<asin>` also works but 302s here, so the canonical form is used.
#
# As with Kroger, an automated check is NOT possible: httpx is refused by these
# storefronts, so no test may fetch this URL.
PRODUCT_PAGE_PATH = "/grocery/product/{asin}"
SEARCH_DATA_PATH = "/_next/data/{build_id}/grocery/search.json"

# From the GFP-97 credential registry, not restated, so the filename cannot
# drift from what the seam looks at.
SESSION_FILENAME = credentials.WHOLEFOODS.filename

# One search per existing protein category (GFP-23/GFP-30 vocabulary) -- see
# the "Scope of search" section above for why these six and not an attempt to
# mirror the whole storefront.
DEFAULT_QUERIES: tuple[tuple[str, str], ...] = (
    ("ground beef", "beef"),
    ("sirloin steak", "beef"),
    ("pork chops", "pork"),
    ("pork tenderloin", "pork"),
    ("chicken breast", "chicken"),
    ("chicken thigh", "chicken"),
    ("salmon", "fish"),
    ("shrimp", "fish"),
    ("tofu", "tofu"),
    ("whey protein", "whey"),
)


class SessionMissingError(RuntimeError):
    """No ``wholefoods_session.json`` (or no usable ``wfm_store_d8`` in it).

    See the module docstring's "Minting the session cookie" section -- this
    is the out-of-band human step that produces the file this error is
    asking for.
    """


class SessionExpiredError(RuntimeError):
    """The stored cookie is present but Whole Foods no longer honors it.

    Raised specifically on the "loading shell" payload signature the GFP-70
    spike documented (``pageProps.pageType == 'loading'`` with
    ``productsInfo`` null) -- see the module docstring. This is NEVER raised
    for a legitimate empty search result (that is a different, valid shape);
    conflating the two would misreport "the cookie is dead" as "Whole Foods
    has nothing" or vice versa.
    """


class PageStructureError(RuntimeError):
    """Whole Foods' page markup no longer contains what this scraper expects
    (e.g. no ``buildId`` or no ``__NEXT_DATA__`` script). Distinct from a
    dead cookie: this means the SITE changed shape, not the session."""


class ZipMismatchError(RuntimeError):
    """The requested postal code disagrees with the cookie's own ``deliveryZip``.

    See the module docstring's "ZIP handling" section (GFP-53) -- refusing
    here is what stops one ZIP's prices from silently being stored/labeled as
    another's.
    """


@dataclass(frozen=True)
class Session:
    """The one fact this module needs from the config file, plus an optional
    human bookkeeping field this module never acts on (see the module
    docstring's config format section)."""

    wfm_store_d8: str
    minted_at: str | None = None


def session_path() -> Path:
    """Where the human-minted cookie config lives (next to the database).

    Resolved through the GFP-97 credential seam rather than computed here, so
    there is one definition of where this credential comes from. Still a
    function on this module because callers and tests already use it.
    """
    return credentials.LocalFileProvider().path(credentials.WHOLEFOODS)


def readiness(session_file: Path | None = None) -> tuple[bool, str]:
    """Is this registered scraper actually usable right now?

    Registered-in-``SCRAPERS`` and ready-to-scrape are NOT the same thing for
    this store (unlike the Flipp-sourced ones, which need no setup at all):
    Whole Foods needs the out-of-band, human session-mint step described in
    this module's docstring before a scrape can do anything useful.
    ``service/ingest.py::available_scrapers()`` calls this (any scraper
    module MAY define it; one that doesn't is simply always ready) so the
    CLI/GUI can decline to offer this store as scrapable -- instead of
    presenting it as ready-to-go and only failing on click/run -- and so
    ``gplan scrape``/``gplan schedule set`` can reject it with a clear,
    friendly message up front rather than an unhandled
    :class:`SessionMissingError` traceback.

    Deliberately a cheap existence check, not full validation -- malformed
    JSON or a missing ``wfm_store_d8`` key inside an existing file still
    report "ready" here; :func:`load_session` (run at actual scrape time) is
    what does full validation and raises the detailed, authoritative error.
    Keeping this check cheap and side-effect-light is what makes it safe to
    call from places like `gplan stores` that list every store on every
    invocation.
    """
    target = session_file or session_path()
    if target.exists():
        return True, ""
    return False, (
        f"needs a one-time session cookie at {target} -- see the "
        "'Minting the session cookie' section of this module's docstring "
        "(grocery_planner/scrapers/wholefoods.py)"
    )


def load_session(path: Path | None = None) -> Session:
    """Read and validate the session config file.

    ``path`` is overridable purely for tests; production callers always use
    the default (:func:`session_path`).
    """
    target = path or session_path()
    if not target.exists():
        raise SessionMissingError(
            f"No Whole Foods session file at {target}. Whole Foods requires a "
            "one-time, out-of-band session cookie minted by hand in a real "
            "browser -- see the 'Minting the session cookie' section of "
            "grocery_planner/scrapers/wholefoods.py's module docstring for "
            "the exact steps."
        )
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SessionMissingError(
            f"{target} is not valid JSON: {exc}. Expected "
            '{"wfm_store_d8": "<cookie value>"}.'
        ) from exc

    cookie = str((raw or {}).get("wfm_store_d8") or "").strip()
    if not cookie:
        raise SessionMissingError(
            f"{target} exists but has no non-empty 'wfm_store_d8' value. See "
            "the module docstring's minting instructions."
        )
    minted_at = raw.get("minted_at")
    return Session(wfm_store_d8=cookie, minted_at=str(minted_at) if minted_at else None)


def _b64_candidate(value: str) -> str | None:
    """``value`` decoded from base64, or ``None`` if it is not base64 at all.

    Whole Foods pads inconsistently, so the missing ``=`` are restored before
    decoding. ``validate=False`` (the default) would silently skip characters
    that are not in the base64 alphabet and hand back plausible-looking
    garbage, so this validates strictly and lets a non-base64 value fail here
    rather than turn into a confusing JSON error later.
    """
    stripped = value.strip()
    if not stripped:
        return None
    padded = stripped + "=" * (-len(stripped) % 4)
    try:
        return base64.b64decode(padded, validate=True).decode("utf-8")
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None


def _decode_store_cookie(value: str) -> dict[str, Any]:
    """Decode ``wfm_store_d8``'s value into its JSON blob.

    Three encodings are accepted, because the cookie's own shape has already
    changed once under us:

    1. **base64** -- what the live cookie actually carries as of 2026-08-02
       (GFP-93). A real minted value read straight out of Chrome began
       ``eyJpZCI6IjEwNDI2Iiwi``, i.e. base64 for ``{"id":"10426",...}``, with
       no percent-encoding anywhere in it.
    2. **URL-decoded** -- what the GFP-70 spike recorded. Kept so an older
       session file, or a future revert on Whole Foods' side, still loads.
    3. **as-is JSON** -- in case a human pasted a value some tool had already
       decoded for them.

    Order matters only for speed, not correctness: the three forms are
    mutually exclusive in practice (base64 of a JSON object never parses as
    JSON, and vice versa).
    """
    candidates = (_b64_candidate(value), urllib.parse.unquote(value), value)
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            data = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    raise SessionExpiredError(
        "Could not parse the wfm_store_d8 cookie value as JSON (tried "
        "base64, URL-decoded and as-is). Either the pasted value is not the "
        "real cookie Value, or Whole Foods changed the cookie's shape -- "
        "re-mint following the module docstring's steps."
    )


def _check_zip(cookie_data: dict[str, Any], requested_zip: str) -> None:
    # deliveryZip ONLY -- never name/state/geometry. Confirmed against a real
    # minted cookie during this ticket's review: it decoded to
    # deliveryZip="27401" (correct) alongside name="Lamar"/state="TX" and
    # Austin, TX coordinates (stale placeholders -- see the module
    # docstring's cookie-shape section, also GFP-74). Comparing against
    # anything but deliveryZip here would compare against those stale
    # fields instead of the real, live-updating one.
    cookie_zip = str(cookie_data.get("deliveryZip") or "").strip()
    if cookie_zip and requested_zip and cookie_zip != requested_zip:
        raise ZipMismatchError(
            f"Requested postal code {requested_zip!r} does not match the ZIP "
            f"baked into the Whole Foods session cookie ({cookie_zip!r}). "
            "Whole Foods' storefront is pinned per-session (GFP-70 spike) -- "
            "scraping under a different ZIP than the one the cookie was "
            "minted for would silently attribute one location's prices to "
            "another (GFP-53). Re-mint the session for the ZIP you actually "
            "want (see the module docstring), or pass --postal-code "
            f"{cookie_zip} to match the session you already have."
        )


# --------------------------------------------------------------------------- #
# HTTP client
# --------------------------------------------------------------------------- #
_BUILD_ID_RE = re.compile(r'"buildId"\s*:\s*"([^"]+)"')
_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)


def _extract_build_id(html: str) -> str:
    match = _BUILD_ID_RE.search(html)
    if not match:
        raise PageStructureError(
            "Could not find a Next.js buildId (\"buildId\":\"...\") in the "
            "Whole Foods storefront HTML. This is a site-markup change, not "
            "a cookie problem -- the buildId token is what makes this "
            "scraper self-healing across deploys, and its absence means "
            "that assumption no longer holds."
        )
    return match.group(1)


def _extract_next_data(html: str) -> dict[str, Any]:
    match = _NEXT_DATA_RE.search(html)
    if not match:
        raise PageStructureError(
            "Could not find a __NEXT_DATA__ script tag in the Whole Foods "
            "search page HTML. Site-markup change, not a cookie problem."
        )
    try:
        return json.loads(match.group(1))
    except ValueError as exc:
        raise PageStructureError(
            f"__NEXT_DATA__ script tag did not contain valid JSON: {exc}"
        ) from exc


def _is_loading_shell(page_props: dict[str, Any] | None) -> bool:
    """The GFP-70-documented signature of a dead/invalid cookie -- see the
    module docstring's "Failing loudly on a dead cookie" section."""
    if not page_props:
        return False
    return (
        page_props.get("pageType") == "loading"
        and page_props.get("productsInfo") is None
    )


class WholeFoodsClient:
    """Thin httpx wrapper: one search-page HTML fetch (bootstraps buildId),
    then one ``_next/data`` JSON fetch per remaining query. No Playwright, no
    browser -- see the module docstring."""

    def __init__(self, cookie_value: str, timeout: float = 30.0):
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": base.USER_AGENT},
            cookies={"wfm_store_d8": cookie_value},
            follow_redirects=True,
        )

    def __enter__(self) -> "WholeFoodsClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def bootstrap(self, query: str) -> tuple[str, dict[str, Any]]:
        """Fetch the plain HTML search page for ``query``.

        Returns ``(build_id, page_props)`` -- one request that both discovers
        the current ``buildId`` and (if the session is alive) this query's
        own results, read out of ``__NEXT_DATA__``.
        """
        resp = self._client.get(BASE_URL + SEARCH_HTML_PATH, params={"k": query})
        resp.raise_for_status()
        html = resp.text
        build_id = _extract_build_id(html)
        next_data = _extract_next_data(html)
        page_props = ((next_data.get("props") or {}).get("pageProps")) or {}
        return build_id, page_props

    def search(self, build_id: str, query: str) -> dict[str, Any]:
        """Fetch one query's results via the lighter ``_next/data`` JSON route."""
        resp = self._client.get(
            BASE_URL + SEARCH_DATA_PATH.format(build_id=build_id),
            params={"k": query},
        )
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, dict):
            raise PageStructureError(f"Unexpected Whole Foods response for {query!r}")
        return payload.get("pageProps") or {}


# --------------------------------------------------------------------------- #
# Payload -> row/food extraction
# --------------------------------------------------------------------------- #
def _iter_products(products_info: Any) -> list[dict[str, Any]]:
    """``productsInfo`` is a plain list of product dicts -- confirmed against
    a real, live search (30 results, matching the GFP-70 spike's transcribed
    example byte-for-byte) during this ticket's review. The dict-keyed-by-
    asin branch is kept as a defensive fallback rather than removed -- it
    costs nothing and this is a private, undocumented endpoint free to
    change shape without notice."""
    if isinstance(products_info, list):
        return [p for p in products_info if isinstance(p, dict)]
    if isinstance(products_info, dict):
        return [p for p in products_info.values() if isinstance(p, dict)]
    return []


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


_LEADING_NUMBER = re.compile(r"([\d.]+)")


def _extract_servings_per_container(nutrition_facts: dict[str, Any]) -> float | None:
    text = nutrition_facts.get("servingsPerContainer")
    if not text:
        return None
    match = _LEADING_NUMBER.match(str(text).strip())
    return float(match.group(1)) if match else None


def _extract_protein_grams(nutrition_facts: dict[str, Any]) -> float | None:
    for macro in nutrition_facts.get("macronutrients") or []:
        if str(macro.get("name") or "").strip().lower() != "protein":
            continue
        amount = macro.get("amount")
        if not amount:
            return None
        match = _LEADING_NUMBER.match(str(amount).strip())
        return float(match.group(1)) if match else None
    return None


def product_page_url(asin: str | None) -> str | None:
    """The storefront product page for an asin, or ``None`` (GFP-115).

    ``None`` for a missing asin rather than a link to a bare host: a link to
    the homepage is not a link to the product, and the where-to-buy column
    already degrades to plain text when there is nothing to point at.
    """
    cleaned = (asin or "").strip()
    return f"{BASE_URL}{PRODUCT_PAGE_PATH.format(asin=cleaned)}" if cleaned else None


def product_image_url(product: dict[str, Any]) -> str | None:
    """The first product image, or ``None`` (GFP-115).

    ``productImages`` is a plain list of absolute Amazon media URLs, unlike
    Kroger's perspective/size tree, so there is no "front" to prefer -- the
    first entry is what the storefront leads with.

    Carried, never fetched: this app does no network I/O to paint a window
    (see gui/wheretobuy.py).
    """
    for candidate in product.get("productImages") or []:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _find_own_size_text(product: dict[str, Any]) -> str | None:
    """The ``variationsList`` size string for THIS product's own asin.

    The list mixes in sibling package sizes under other asins (see the
    module docstring); reading the wrong node would silently attribute one
    size to another product's price, so this matches on asin rather than
    just taking the first entry. Falls back to the first node only if this
    exact asin isn't present for some reason -- better than no size at all.
    """
    asin = product.get("asin")
    variations = product.get("variationsList") or []
    for group in variations:
        for node in group.get("variationNodeList") or []:
            if node.get("asin") == asin:
                dimension = node.get("dimensionValue")
                return str(dimension) if dimension else None
    for group in variations:
        nodes = group.get("variationNodeList") or []
        if nodes and nodes[0].get("dimensionValue"):
            return str(nodes[0]["dimensionValue"])
    return None


def _weight_grams(size_text: str | None) -> float | None:
    """Grams for a weight-based size string, or ``None`` (not weight-based /
    unreadable). Reuses ``savings.parse_size`` -- the same size grammar every
    other store's deals already go through -- rather than a second parser."""
    if not size_text:
        return None
    # Imported lazily: grocery_planner.savings imports grocery_planner.matching
    # and .nutrition, neither of which import scrapers, so there's no real
    # import cycle -- deferred purely to keep this module's top-level imports
    # limited to what every code path here needs.
    from .. import savings

    size = savings.parse_size(size_text)
    if size is None or size.base_unit != savings.WEIGHT or size.base_quantity <= 0:
        return None
    return size.base_quantity * savings.GRAMS_PER_OZ


def _protein_per_100g(serving_size_text: str | None, protein_per_serving: float | None) -> float | None:
    if not serving_size_text or protein_per_serving is None or protein_per_serving <= 0:
        return None
    serving_grams = _weight_grams(serving_size_text)
    if not serving_grams:
        return None
    return protein_per_serving / serving_grams * 100.0


def _package_weight_grams(
    size_text: str | None, serving_size_text: str | None, servings_per_container: float | None
) -> tuple[float | None, str]:
    """``(grams, source)`` -- see the module docstring's "Package weight and
    GFP-73" section for why a real printed size always wins over the
    serving-based estimate, and why the estimate is never faked into
    ``item_name``."""
    printed = _weight_grams(size_text)
    if printed:
        return printed, "variationsList"
    serving_grams = _weight_grams(serving_size_text)
    if serving_grams and servings_per_container:
        return serving_grams * servings_per_container, "serving_estimate"
    return None, "unknown"


def _display_item_name(name: str, size_text: str | None, weight_source: str) -> str:
    """Only a REAL printed size gets folded into the name text
    ``savings.parse_size`` reads -- see the GFP-73 section on why an
    estimated size never does."""
    if weight_source == "variationsList" and size_text:
        return f"{name}, {size_text}"
    return name


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class _FoodFact:
    asin: str
    name: str
    category: str
    protein_per_100g: float
    item_name: str


def product_to_row(
    product: dict[str, Any], category: str, zip_code: str, now: datetime
) -> tuple[dict[str, Any], _FoodFact | None]:
    """Map one Whole Foods product dict to a DB-ready ``deals`` row, plus (when
    computable) the food fact to land in ``foods``/``food_nutrients``.

    Pure function, no DB/network access -- directly testable, same shape as
    ``scrapers/base.py::flyer_item_to_row``.
    """
    asin = str(product.get("asin") or "").strip()
    raw_name = str(product.get("name") or "").strip()

    offer = product.get("offerDetails") or {}
    price = _to_float((offer.get("price") or {}).get("priceAmount"))
    unit_price_info = offer.get("unitPrice") or {}
    unit_price_amount = _to_float(unit_price_info.get("priceAmount"))
    unit_price_base = str(unit_price_info.get("baseUnit") or "").strip()

    size_text = _find_own_size_text(product)
    nutrition_facts = product.get("nutritionFacts") or {}
    serving_size_text = nutrition_facts.get("servingSize")
    servings_per_container = _extract_servings_per_container(nutrition_facts)
    protein_per_serving = _extract_protein_grams(nutrition_facts)
    protein_per_100g = _protein_per_100g(serving_size_text, protein_per_serving)

    _package_weight, weight_source = _package_weight_grams(
        size_text, serving_size_text, servings_per_container
    )

    item_name = _display_item_name(raw_name, size_text, weight_source)
    has_price = price is not None
    identifier, identifier_ns = base.product_identifier(asin, PRODUCT_IDENTIFIER_NS)

    desc_parts: list[str] = []
    if has_price:
        desc_parts.append(f"${price:.2f}")
        if unit_price_amount is not None and unit_price_base:
            desc_parts.append(f"(${unit_price_amount:.2f}/{unit_price_base})")
    deal_description = " ".join(desc_parts) if desc_parts else "Whole Foods storefront listing"

    notes = [
        "source=wholefoods_storefront",
        f"asin={asin}",
        f"category={category}",
        f"postal_code={zip_code}",
        f"package_weight_source={weight_source}",
    ]
    if size_text:
        notes.append(f"size={size_text}")
    if unit_price_amount is not None and unit_price_base:
        notes.append(f"unit_price={unit_price_amount}/{unit_price_base}")
    if serving_size_text:
        notes.append(f"serving_size={serving_size_text}")
    if servings_per_container is not None:
        notes.append(f"servings_per_container={servings_per_container:g}")
    if protein_per_serving is not None:
        notes.append(f"protein_per_serving_g={protein_per_serving:g}")
    if protein_per_100g is not None:
        notes.append(f"protein_per_100g={protein_per_100g:.2f}")
    if not has_price:
        notes.append("price_missing=true")

    row = {
        "item_name": item_name,
        "sub_category": base.infer_sub_category(item_name, "", has_price),
        "deal_type": DEAL_TYPE if has_price else f"{DEAL_TYPE} (price not listed)",
        "deal_description": deal_description,
        "regular_price": None,
        "sale_price": price,
        "dollar_price": price,
        "discount_amount": None,
        "discount_percent": None,
        "valid_from": now.date().isoformat(),
        # Open-ended on purpose: a live storefront price carries no announced
        # expiry the way a dated weekly ad does. `deals` is fully replaced on
        # every re-scrape anyway (service/ingest.py::run_scrape), and a
        # missing valid_to is "unknown", never "expired" (see
        # service/deals.py::is_expired) -- exactly the honest state here.
        "valid_to": None,
        "loyalty_required": "N",
        "notes": "; ".join(notes),
        # GFP-115: both from the payload's own asin/images, both None when it
        # carries neither -- the where-to-buy column degrades to plain text
        # rather than rendering a dead control.
        "source_url": product_page_url(asin),
        "image_url": product_image_url(product),
        "flipp_flyer_id": None,
        "flipp_item_id": None,
        "flipp_coupon_id": None,
        # GFP-98. Whole Foods' unitPrice.baseUnit already distinguishes a
        # per-pound butcher price from a per-package one, so these come for
        # free here rather than staying NULL like the Flipp rows -- and the UI
        # tag (GFP-36/37/38/48/50/52) then works for two sources, not one.
        "sold_by": "WEIGHT" if unit_price_base.lower() in _WEIGHT_BASE_UNITS else (
            "UNIT" if unit_price_base else None
        ),
        "price_per_unit": unit_price_amount,
        "price_per_unit_uom": unit_price_base or None,
        # GFP-111: the ASIN already in hand a few lines above, written at scrape
        # time into a real column -- the same value `notes` records for
        # provenance, but somewhere nothing downstream has to string-parse that
        # blob to find it. `notes` is unchanged. NULL/NULL when the storefront
        # omitted the ASIN: an unidentified listing cannot be ordered, and a
        # stand-in would be a fabricated SKU.
        "product_identifier": identifier,
        "product_identifier_ns": identifier_ns,
    }

    food_fact = None
    if asin and protein_per_100g and protein_per_100g > 0:
        food_fact = _FoodFact(
            asin=asin, name=raw_name or item_name, category=category,
            protein_per_100g=protein_per_100g, item_name=item_name,
        )
    return row, food_fact


# --------------------------------------------------------------------------- #
# Persistence: foods / food_nutrients / deal_food_match (see the module
# docstring's "Where the nutrition data lands" section for why).
# --------------------------------------------------------------------------- #
def _upsert_food_fact(conn: sqlite3.Connection, fact: _FoodFact) -> None:
    slug = f"wholefoods-{fact.asin}"
    now = _now_iso()
    conn.execute(
        "INSERT INTO foods(name, category, source, source_ref, slug, updated_at) "
        "VALUES (?, ?, 'wholefoods', ?, ?, ?) "
        "ON CONFLICT(source, source_ref) DO UPDATE SET "
        "name=excluded.name, category=excluded.category, slug=excluded.slug, "
        "updated_at=excluded.updated_at",
        (fact.name, fact.category, fact.asin, slug, now),
    )
    food_id = conn.execute(
        "SELECT id FROM foods WHERE source='wholefoods' AND source_ref=?", (fact.asin,)
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO food_nutrients(food_id, nutrient, amount_per_100g, unit) "
        "VALUES (?, 'protein', ?, 'g') "
        "ON CONFLICT(food_id, nutrient) DO UPDATE SET "
        "amount_per_100g=excluded.amount_per_100g, unit=excluded.unit",
        (food_id, fact.protein_per_100g),
    )
    # match_source=MANUAL is deliberate, not a typo -- see the module
    # docstring's "Where the nutrition data lands" section: it is what stops
    # matching.match_deals()'s keyword auto-matcher from ever downgrading a
    # Whole-Foods-certain match. method stays auditable as the real story.
    conn.execute(
        "INSERT INTO deal_food_match"
        "(store, item_name, food_id, confidence, method, match_source, updated_at) "
        f"VALUES (?, ?, ?, 1.0, 'wholefoods_direct', '{matching.MANUAL}', ?) "
        "ON CONFLICT(store, item_name) DO UPDATE SET "
        "food_id=excluded.food_id, confidence=1.0, method='wholefoods_direct', "
        f"match_source='{matching.MANUAL}', updated_at=excluded.updated_at",
        (STORE_KEY, fact.item_name, food_id, now),
    )


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def scrape(
    postal_code: str | None = None,
    queries: Iterable[tuple[str, str]] | None = None,
    conn: sqlite3.Connection | None = None,
    session_file: Path | None = None,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Scrape Whole Foods search results and return ``(deal_rows, meta, stats)``.

    Matches the interface ``service/ingest.py::run_scrape`` expects from
    every registered scraper (see ``scrapers/foodlion.py``/``harristeeter.py``
    for the Flipp-store shape of the same contract). Also -- unlike those two
    -- upserts ``foods``/``food_nutrients``/``deal_food_match`` for every
    product with full nutrition data, using ``conn`` (defaulting to
    ``db.connect()``, same convention as ``matching.py``/``nutrition.py``/
    ``savings.py``) since Whole Foods' own catalog data resolves the
    deal-to-food link directly rather than needing the separate ``gplan
    match`` step Flipp-sourced stores rely on.

    Raises :class:`SessionMissingError`/:class:`SessionExpiredError` for a
    missing/dead cookie, :class:`ZipMismatchError` for a ZIP that disagrees
    with the cookie (GFP-53), and :class:`PageStructureError` if the site's
    markup no longer matches what this scraper expects. A genuine zero-result
    scrape is returned normally and left to
    ``service/ingest.py``'s GFP-67 replace-guards, not raised here.
    """
    session = load_session(session_file)
    cookie_data = _decode_store_cookie(session.wfm_store_d8)
    requested_zip = postal_code or DEFAULT_POSTAL_CODE
    _check_zip(cookie_data, requested_zip)

    query_list = list(queries) if queries is not None else list(DEFAULT_QUERIES)
    if not query_list:
        raise ValueError("wholefoods.scrape() needs at least one (query, category) pair")

    when = now or datetime.now(timezone.utc)
    own = conn or db.connect()

    pages: list[tuple[str, dict[str, Any]]] = []
    with WholeFoodsClient(session.wfm_store_d8) as client:
        first_query, first_category = query_list[0]
        build_id, first_page_props = client.bootstrap(first_query)
        pages.append((first_category, first_page_props))
        for query, category in query_list[1:]:
            pages.append((category, client.search(build_id, query)))

    rows: list[dict[str, Any]] = []
    seen_asins: set[str] = set()
    products_seen = no_price = with_nutrition = 0

    for category, page_props in pages:
        if _is_loading_shell(page_props):
            raise SessionExpiredError(
                "Whole Foods returned a 'loading' page shell "
                "(pageProps.pageType == 'loading', productsInfo is null) -- "
                "the documented signature of a dead/expired session cookie "
                "(see the GFP-70 spike). This is NOT 'no results' -- the "
                "wfm_store_d8 cookie needs to be re-minted. See the "
                "'Minting the session cookie' section of this module's "
                "docstring."
            )
        for product in _iter_products(page_props.get("productsInfo")):
            products_seen += 1
            asin = str(product.get("asin") or "").strip()
            if asin and asin in seen_asins:
                continue  # the same product surfaced under >1 search query
            if asin:
                seen_asins.add(asin)

            row, food_fact = product_to_row(product, category, requested_zip, when)
            if not row["item_name"]:
                continue
            if row["sale_price"] is None:
                no_price += 1
            rows.append(row)
            if food_fact is not None:
                _upsert_food_fact(own, food_fact)
                with_nutrition += 1

    own.commit()

    meta = {
        "name": f"{MERCHANT} storefront search ({requested_zip})",
        "id": cookie_data.get("id"),
        "build_id": build_id,
    }
    stats = {
        # Field names mirror scrapers/base.py::scrape_store's stats shape so
        # the existing CLI output (cli.py::scrape) needs no store-specific
        # branching -- "weekly_ad"/"digital_coupons"/"bogo" are Flipp-vintage
        # names this store doesn't literally have, repurposed rather than
        # forking the CLI's formatting per store.
        "weekly_ad": len(rows),
        "digital_coupons": 0,
        "no_price": no_price,
        "bogo": 0,
        "expired_items": 0,
        "total": len(rows),
        "flyer_id": cookie_data.get("id"),
        "flyer_name": meta["name"],
        "flyer_status": "active",
        "valid_from": when.date().isoformat(),
        "valid_to": when.date().isoformat(),
        "products_seen": products_seen,
        "with_full_nutrition": with_nutrition,
        "queries": [q for q, _cat in query_list],
    }
    return rows, meta, stats
