"""Harris Teeter via Kroger's public developer API (GFP-98).

Harris Teeter is a Kroger banner, and Kroger publishes a *documented, public*
API. That makes this the first source in the project whose terms can be
complied with rather than worked around: no scraped browser artefact, no cookie
to mint, no undocumented endpoint. Auth is OAuth2 client credentials, tokens
last 30 minutes, and the documented budget is 10,000 Products calls/day.

Measured by the GFP-77 spike against 670 real products at store 09700347
(Greensboro): 100% priced, 100% with a machine-readable size, 82% with protein
grams. It resolves cost-per-gram-of-protein for ~74% of products, against
0.3% for the Flipp-sourced Harris Teeter feed -- roughly a 230x improvement for
the same physical store.

Finding the store: the chain code is ``HART``
---------------------------------------------
Do NOT look up Harris Teeter by name in ``/v1/chains``. The only entries there
that *read* as Harris Teeter are ``HARRIS TEETER FUEL`` and ``HARRIS TEETER
FUEL CENTER`` -- petrol stations, which return **zero** grocery locations. A
name search produces a confident false negative; the GFP-77 spike initially
concluded "not in the catalog" on exactly that basis and was wrong.

Grocery stores come back under the opaque code ``HART``. So this module
resolves a location by asking ``/v1/locations`` for stores near a ZIP and
reading the ``chain`` field *back*, rather than filtering by a name it thinks
it knows. That is also robust to Kroger renaming the code.

THE TRAP: ``soldBy=WEIGHT`` prices PER POUND
-------------------------------------------
This is the single most important thing in this module.

A ``soldBy=WEIGHT`` item prices per unit weight, while ``servingsPerPackage``
describes the *whole cut*. Multiplying the two mixes denominators. The spike hit
it on a real record::

    Pork Loin Whole
      soldBy              WEIGHT      <- price is PER POUND
      size                1 lb        <- the PRICING unit, not the package
      price.promo         2.49        <- $2.49 per lb
      servingsPerPackage  30          <- the WHOLE loin (30 x 112g = 7.4 lb)
      servingSize         112 g

Naively multiplying gives 720 g of protein for $2.49 -- $0.0035/g, and
impossible, since 453 g of pork cannot contain 720 g of protein. It understates
cost about 7x. That matters enormously because 30% of the catalog is
``soldBy=WEIGHT`` and it is precisely the fresh meat: the highest-protein
items, which would then dominate every recommendation while being wrong.

**How this module avoids it: ``servingsPerPackage`` is never used to compute
cost per gram of protein. At all.**

Instead protein is stored as a *density* -- ``protein_per_100g``, derived from
``servingSize`` -- and the ``size`` string (which is the PRICED quantity in
both cases: the package for UNIT, one pound for WEIGHT) is folded into
``item_name`` where ``savings.parse_size`` reads it. Price and size then always
refer to the same quantity, so the existing engine computes both denominations
correctly with no branching. This is deliberate: per GFP-32's rule for stores,
the fewer places that branch on a special case, the fewer places it can be got
wrong.

``servingsPerPackage`` is used for exactly one thing -- estimating a package
weight for a ``soldBy=UNIT`` item whose ``size`` string is unparseable -- and
is ignored outright for WEIGHT items. Same shape as ``wholefoods.py``'s
``serving_estimate`` fallback.

``sold_by`` is still persisted, because the *display* layer must tag a
per-weight price (GFP-36/37/38/48/50/52). The engine does not need it; the UI
does.

Credentials
-----------
An INI file, by default ``<user-data-dir>/kroger-env.config`` (the folder
``gplan db-path`` prints), overridable with ``$GROCERY_PLANNER_KROGER_CONFIG``::

    [kroger]
    client_id = ...
    client_secret = ...

Section header optional -- a bare ``key = value`` file is accepted too. Get
credentials by registering an application at https://developer.kroger.com.

**Never commit this file.** ``.gitignore`` carries patterns for it, but a
secret in git history is not fixed by a later delete commit -- it survives in
every clone and fork, and the only real remedy is rotating the credential.
GFP-97 tracks moving credential loading behind a provider seam so a hosted
token broker can replace the local file without touching callers.
"""
from __future__ import annotations

import configparser
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx

from .. import credentials, db, matching, savings, weight_basis
from . import base, retry

# The CLI/registry name. Distinct from STORE_KEY because this is a SECOND
# source for a store that already has one: `harristeeter` is the Flipp weekly
# ad, this is the shelf-price API. They complement each other (Flipp carries
# BOGO/coupon promotions the API does not) so neither should evict the other.
SCRAPER_KEY = "harristeeter-api"

# The `deals.store` value. The same physical store as the Flipp feed, on
# purpose -- a nutritionist shops at Harris Teeter, not at "Harris Teeter's
# Kroger API". Keeping the store identity shared is also what lets GFP-75's
# records treat an observation from either feed as the same item.
STORE_KEY = "harristeeter"

# The `deals.source` value. This is what keeps the two feeds from clobbering
# each other: service/ingest.run_scrape scopes its replace to
# (store, source, postal_code).
SOURCE = "kroger-api"

MERCHANT = "Harris Teeter (Kroger API)"
DEFAULT_POSTAL_CODE = "27401"
DEAL_TYPE = "Shelf Price"

# GFP-111: the vocabulary this module's `product_identifier` values belong to.
# Named after the SOURCE, not the store -- these ids are Kroger's, issued by
# the API, and would be the same numbers if this feed were pointed at any other
# Kroger banner. Naming it after `harristeeter` would make that a lie the first
# time a second banner is added, and would put store identity into shared data
# (GFP-32).
#
# Kroger's productId is zero-padded ('0020895500000'), so it is stored verbatim
# as TEXT -- read as a number it becomes 20895500000 and no longer matches
# anything Kroger will accept.
PRODUCT_IDENTIFIER_NS = "kroger.product_id"

# GFP-99: where a product page lives. The API returns `productPageURI` as a
# ROOT-RELATIVE path ("/p/<slug>/<productId>?cid=..."), so it needs a host.
#
# This host is Harris Teeter's because this module IS the Harris Teeter feed
# (SCRAPER_KEY/MERCHANT above) -- a banner's own storefront, not a generic
# Kroger one. Verified 2026-08-02 by opening a built URL in a real browser:
# https://www.harristeeter.com/p/harris-teeter-boneless-chicken-breast-value-pack/0020895500000
# resolves to the real product page ("$1.99/lb", UPC 0020895500000, 26 g
# protein per 4 oz -- which also cross-checks the protein density this scraper
# stores). Note that `httpx` gets a read timeout on the same URL: the
# storefront refuses unknown clients, so an automated check CANNOT confirm
# these links and must not be added as a test.
PRODUCT_PAGE_HOST = "https://www.harristeeter.com"

# The API appends a campaign tag identifying our own API client
# ("?cid=dis.api.tpi_products-api_...decohenpartners-bbcg"). Stripped
# deliberately: the bare path was verified to work, and a link handed to a
# nutritionist's client should not carry our developer account's tracking
# parameter. Everything before the "?" is the durable part.
PRODUCT_PAGE_TRACKING_PARAM = "cid"

# Re-exported from the GFP-97 credential registry rather than restated, so the
# filename and the override variable cannot drift from what the seam actually
# looks at. Existing callers and tests keep importing them from here.
CONFIG_FILENAME = credentials.KROGER.filename
CONFIG_ENV_VAR = credentials.KROGER.env_var

TOKEN_URL = "https://api.kroger.com/v1/connect/oauth2/token"
API_BASE = "https://api.kroger.com/v1"
TOKEN_SCOPE = "product.compact"

# Harris Teeter's grocery chain code. Used to RECOGNISE a store in a location
# response, never to filter the request -- see the module docstring.
GROCERY_CHAIN_CODES = ("HART",)

# Search terms, chosen for protein relevance rather than coverage: this is a
# cost-per-gram-of-protein tool, so a term earns its place by returning things
# a protein target can be met with. 50 results each, ~20 calls per scrape,
# comfortably inside the documented 10,000/day.
DEFAULT_QUERIES: tuple[tuple[str, str], ...] = (
    ("chicken breast", "Meat"), ("chicken thigh", "Meat"), ("ground beef", "Meat"),
    ("steak", "Meat"), ("pork", "Meat"), ("turkey", "Meat"), ("bacon", "Meat"),
    ("salmon", "Seafood"), ("tuna", "Seafood"), ("shrimp", "Seafood"),
    ("eggs", "Dairy"), ("greek yogurt", "Dairy"), ("cottage cheese", "Dairy"),
    ("milk", "Dairy"), ("cheese", "Dairy"),
    ("tofu", "Plant Protein"), ("tempeh", "Plant Protein"), ("beans", "Plant Protein"),
    ("lentils", "Plant Protein"), ("peanut butter", "Plant Protein"),
    ("protein powder", "Supplements"), ("protein bar", "Supplements"),
)

PAGE_SIZE = 50
PROTEIN_NUTRIENT_CODE = "PRO-"

# Grams per unit, for the units Kroger states explicitly in servingSize's
# unitOfMeasure. Only weight units appear here: a serving measured in
# millilitres cannot be converted to grams without a density we do not have,
# so it yields None rather than a guess.
# The ounce and pound figures come from savings rather than being restated:
# the pound is DERIVED from the ounce there, so the two can never drift apart
# the way three hand-copied literals in three modules could.
_GRAMS_PER_UNIT = {
    "gram": 1.0, "grm": 1.0, "g": 1.0,
    "milligram": 0.001, "mgm": 0.001,
    "kilogram": 1000.0, "kgm": 1000.0,
    "ounce": savings.GRAMS_PER_OZ, "onz": savings.GRAMS_PER_OZ, "oz": savings.GRAMS_PER_OZ,
    "pound": savings.GRAMS_PER_LB, "lbr": savings.GRAMS_PER_LB, "lb": savings.GRAMS_PER_LB,
}


class KrogerError(RuntimeError):
    """Base class for this scraper's failures."""


class CredentialsMissingError(KrogerError):
    """No usable client_id/client_secret. Not a transient failure."""


class AuthFailedError(KrogerError):
    """Kroger rejected the credentials. Distinct from 'no credentials'."""


class NoStoreFoundError(KrogerError):
    """No Harris Teeter grocery store near the requested ZIP."""


@dataclass(frozen=True)
class Credentials:
    client_id: str
    client_secret: str


@dataclass(frozen=True)
class BearerToken:
    """An access token minted somewhere else -- the broker (GFP-101).

    **Why this exists at all.** If the broker handed out the client_id and
    client_secret, they would land in the cache on every customer's disk, and a
    secret on N disks is a secret. Minting the token server-side means the
    secret never leaves the broker: what a customer holds is a bearer token
    that expires in half an hour and buys nothing but grocery prices.

    So a client built from one of these must NOT try to exchange credentials it
    does not have. :meth:`KrogerClient.token` returns this verbatim.
    """

    access_token: str
    #: Seconds of life the issuer claimed. Recorded rather than acted on -- the
    #: caching decision belongs to :mod:`grocery_planner.broker`, and a token
    #: that expires mid-scrape should surface as the 401 it is.
    expires_in: float | None = None


def _missing_message(target: Path | str) -> str:
    """The 'no credentials' text, shared by every path that has to say it."""
    return (
        f"No Kroger credentials at {target}. Register an application at "
        "https://developer.kroger.com and write its client_id/client_secret "
        "to that file as INI:\n\n    [kroger]\n    client_id = ...\n"
        "    client_secret = ...\n\nNever commit it."
    )


@dataclass(frozen=True)
class _FoodFact:
    """Nutrition to land in foods/food_nutrients, keyed on Kroger's productId."""

    product_id: str
    name: str
    category: str
    protein_per_100g: float
    item_name: str


# --------------------------------------------------------------------------- #
# Credentials
# --------------------------------------------------------------------------- #
def config_path() -> Path:
    """Where the credential file lives, via the GFP-97 provider seam.

    Kept as a function on this module because callers and tests already use
    it; it now delegates rather than resolving the path itself, so there is
    one definition of "where do Kroger credentials come from".
    """
    return credentials.LocalFileProvider().path(credentials.KROGER)


def load_credentials(path: Path | None = None) -> Credentials:
    """Read client_id/client_secret from the INI document, or raise.

    Accepts the document with or without a ``[section]`` header, because an
    "env config" is as likely to be written as bare ``key = value`` lines, and
    failing on that would be a pointless papercut.

    The *document* is obtained through the GFP-97 credential seam
    (:func:`grocery_planner.credentials.fetch`), so a future token broker can
    supply it without touching this function; the INI parsing and the error
    messages stay here, where the format is actually known. ``path`` still
    reads a specific file directly -- tests rely on it, and it stays the
    escape hatch for "check this exact file".
    """
    auth = load_auth(path)
    if isinstance(auth, BearerToken):
        raise CredentialsMissingError(
            "The credential broker supplied an access token rather than a "
            "client_id/client_secret pair, and by design: the secret stays on "
            "the broker. Use load_auth() and hand the result to KrogerClient."
        )
    return auth


def load_auth(path: Path | None = None) -> Credentials | BearerToken:
    """Whatever this install authenticates to Kroger with.

    Two shapes, because there are two legitimate sources (GFP-101):

    * an **INI document** with client_id/client_secret -- a local file, the
      only shape that existed before the broker;
    * a **JSON document** with ``access_token`` -- a token the broker minted,
      so that the secret behind it never reaches this machine.

    Which one arrives is decided entirely by the credential provider. Sniffing
    the document rather than asking the provider what it is keeps the GFP-97
    seam to its single method: a provider returns a document and the consumer,
    which is the only party that knows these formats, parses it.
    """
    if path is not None:
        if not path.exists():
            raise CredentialsMissingError(_missing_message(path))
        text = path.read_text(encoding="utf-8-sig")
        target: Path | str = path
    else:
        try:
            text = credentials.fetch(credentials.KROGER.name)
        except credentials.CredentialsMissingError as exc:
            raise CredentialsMissingError(str(exc)) from exc
        except credentials.CredentialError as exc:
            # A broker that could not be reached. Deliberately NOT turned into
            # CredentialsMissingError: "nothing is configured" tells a user to
            # go and set something up, when in fact the right advice is to try
            # again later.
            raise KrogerError(str(exc)) from exc
        target = credentials.provider().describe(credentials.KROGER)

    token = _as_token(text)
    if token is not None:
        return token

    text = text.strip()
    if not text.lstrip().startswith("["):
        text = "[kroger]\n" + text

    parser = configparser.ConfigParser()
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        raise CredentialsMissingError(f"{target} is not readable as INI: {exc}") from exc

    for section in parser.sections():
        values = {k.lower(): v for k, v in parser.items(section)}
        client_id = values.get("client_id") or values.get("clientid")
        secret = values.get("client_secret") or values.get("clientsecret")
        if client_id and secret:
            return Credentials(
                client_id=client_id.strip().strip('"').strip("'"),
                client_secret=secret.strip().strip('"').strip("'"),
            )
    raise CredentialsMissingError(
        f"{target} has no client_id/client_secret pair. Sections found: "
        f"{parser.sections() or '(none)'}."
    )


def _as_token(document: str) -> BearerToken | None:
    """``document`` read as an OAuth2 token response, or ``None``.

    Returns None for anything that is not JSON carrying a non-empty
    ``access_token`` -- an INI credential file is not a near miss to be
    guessed at, it is simply the other format.
    """
    try:
        parsed = json.loads(document)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    value = str(parsed.get("access_token") or "").strip()
    if not value:
        return None
    expires = parsed.get("expires_in")
    return BearerToken(
        access_token=value,
        expires_in=float(expires) if isinstance(expires, (int, float)) else None,
    )


def readiness(config_file: Path | None = None) -> tuple[bool, str]:
    """Whether a scrape could run right now, and why not.

    Same contract as ``wholefoods.readiness`` -- deliberately a cheap existence
    check rather than full validation, so `gplan stores` stays fast and the
    detailed error belongs to the scrape itself.
    """
    if config_file is not None:
        if not config_file.exists():
            return False, (
                f"needs Kroger API credentials at {config_file} "
                "(register at developer.kroger.com)"
            )
        return True, ""

    # Asked through the provider rather than by looking for a file: under the
    # broker there IS no local file and there is not meant to be, so a file
    # check would report a perfectly working install as unconfigured (GFP-101).
    entry = credentials.status(credentials.KROGER.name)[0]
    if not entry.configured:
        return False, f"needs Kroger API credentials at {entry.location}"
    return True, ""


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
class KrogerClient:
    """Thin authenticated client. One token per instance, reused across calls.

    Tokens last 30 minutes and a scrape takes seconds, so there is no refresh
    logic here on purpose -- an expiry mid-scrape would be a surprise worth
    seeing rather than papering over.
    """

    def __init__(
        self, credentials: Credentials | BearerToken, timeout: float = 45.0
    ):
        self._credentials = credentials
        self._http = httpx.Client(timeout=timeout)
        # GFP-108: set by a caller that wants to SHOW a retry happening -- the
        # GFP-103 scrape row, so it says "retrying" instead of sitting on
        # "Scraping..." with no explanation. None means retry silently.
        self._on_retry = None

        # A brokered token is already the answer, so the OAuth2 exchange below
        # never runs -- there is no client_secret on this machine to run it
        # with, which is the point of brokering (GFP-101).
        self._token: str | None = (
            credentials.access_token if isinstance(credentials, BearerToken) else None
        )

    def __enter__(self) -> "KrogerClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def token(self) -> str:
        if self._token:
            return self._token
        # GFP-108: the token exchange is retried too. A 503 here failed the
        # whole scrape before a single product had been asked for -- and a
        # 401 still is NOT retried, because bad credentials stay bad.
        resp = retry.request(
            lambda: self._http.post(
                TOKEN_URL,
                auth=(self._credentials.client_id, self._credentials.client_secret),
                data={"grant_type": "client_credentials", "scope": TOKEN_SCOPE},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ),
            what="Kroger token",
            on_retry=self._on_retry,
        )
        if resp.status_code != 200:
            raise AuthFailedError(
                f"Kroger rejected the credentials ({resp.status_code}). Check "
                f"{config_path()}, and that the application at "
                f"developer.kroger.com is still active. Response: {resp.text[:200]}"
            )
        self._token = resp.json()["access_token"]
        return self._token

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        # GFP-108. This is THE call the ticket was raised about: a full
        # Harris Teeter run is ~20 of these, and one 503 at request 18 used to
        # discard the 17 that had already worked.
        resp = retry.request(
            lambda: self._http.get(
                f"{API_BASE}{path}",
                params=params,
                headers={"Authorization": f"Bearer {self.token()}",
                         "Accept": "application/json"},
            ),
            what=f"Kroger {path}",
            on_retry=self._on_retry,
        )
        if resp.status_code == 429:
            raise KrogerError(
                f"Kroger rate limit hit on {path}. The documented budget is "
                "10,000 Products calls and 1,600 Locations calls per day."
            )
        if resp.status_code != 200:
            raise KrogerError(f"GET {path} -> {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    def locations_near(self, postal_code: str, radius_miles: int = 25) -> list[dict[str, Any]]:
        payload = self._get("/locations", {
            "filter.zipCode.near": postal_code,
            "filter.radiusInMiles": radius_miles,
            "filter.limit": 200,
        })
        return payload.get("data", []) or []

    def products(self, term: str, location_id: str, limit: int = PAGE_SIZE) -> list[dict[str, Any]]:
        payload = self._get("/products", {
            "filter.term": term,
            "filter.locationId": location_id,
            "filter.limit": limit,
        })
        return payload.get("data", []) or []


def pick_store(locations: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """The nearest Harris Teeter GROCERY store from a locations response.

    Recognises by the ``chain`` field the response actually carries, never by
    asking the API for a chain name -- see the module docstring for why a name
    lookup returns a confident false negative here. The name check is a
    secondary net in case Kroger retires the ``HART`` code.
    """
    for location in locations:
        chain = str(location.get("chain") or "").strip().upper()
        if chain in GROCERY_CHAIN_CODES:
            return location
    for location in locations:
        name = str(location.get("name") or "")
        chain = str(location.get("chain") or "").upper()
        # "HARRIS TEETER FUEL"/"FUEL CENTER" are petrol stations, not groceries.
        if "harris teeter" in name.lower() and "FUEL" not in chain:
            return location
    return None


# --------------------------------------------------------------------------- #
# Pure extraction -- no network, no DB
# --------------------------------------------------------------------------- #
def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def first_item(product: dict[str, Any]) -> dict[str, Any]:
    items = product.get("items") or []
    return items[0] if items else {}


def extract_price(item: dict[str, Any]) -> tuple[float | None, float | None]:
    """``(effective_price, regular_price)``. Promo wins when present."""
    price = item.get("price") or {}
    regular = _to_float(price.get("regular"))
    promo = _to_float(price.get("promo"))
    return (promo or regular), regular


def extract_price_per_unit(item: dict[str, Any], sold_by: str | None) -> tuple[float | None, str | None]:
    """Kroger's own per-unit price, and what it is per.

    Kept verbatim rather than recomputed: it is the retailer's arithmetic over
    the retailer's package size, which beats anything derived from a size
    string we had to parse. For a WEIGHT item the headline price already IS a
    per-pound price, so the unit is a pound.
    """
    price = item.get("price") or {}
    per_unit = _to_float(price.get("promoPerUnitEstimate")) or _to_float(
        price.get("regularPerUnitEstimate")
    )
    if per_unit is None:
        return None, None
    if (sold_by or "").upper() == "WEIGHT":
        return per_unit, "lb"
    return per_unit, "each"


def _nutrition_block(product: dict[str, Any]) -> dict[str, Any] | None:
    blocks = product.get("nutritionInformation") or []
    return blocks[0] if blocks else None


def extract_protein_per_serving(product: dict[str, Any]) -> float | None:
    """Grams of protein per SERVING (never per package -- see GFP-73)."""
    block = _nutrition_block(product)
    for nutrient in (block or {}).get("nutrients", []) or []:
        if nutrient.get("code") == PROTEIN_NUTRIENT_CODE:
            return _to_float(nutrient.get("quantity"))
    return None


def product_page_url(product: dict[str, Any]) -> str | None:
    """The storefront product page for one API product, or ``None`` (GFP-99).

    Built from the API's own ``productPageURI`` rather than assembled from a
    slug and an id. That distinction is the ticket's requirement and it matters:
    a guessed URL shape that 404s is worse than the plain text it replaces
    (GFP-38 deliberately chose plain text over a dead control), whereas this
    path is what Kroger itself says the product lives at.

    The campaign tag is stripped -- see :data:`PRODUCT_PAGE_TRACKING_PARAM`.

    ``None`` whenever the payload does not carry a URI, or carries something
    that is not a root-relative path. An absolute URL from the API would mean
    the contract changed under us, and silently prefixing a host onto it would
    produce nonsense like "https://www.harristeeter.comhttps://..." -- so it is
    refused rather than patched over (savings.py rule 1).
    """
    uri = (product.get("productPageURI") or "").strip()
    if not uri or not uri.startswith("/"):
        return None
    path = uri.split("?", 1)[0]
    return f"{PRODUCT_PAGE_HOST}{path}" if path else None


def product_image_url(product: dict[str, Any]) -> str | None:
    """The best front-of-pack image for one API product, or ``None`` (GFP-99).

    ``images`` is a list of perspectives ("front", "back", ...), each with
    several ``sizes``. Front is preferred because it is the pack a shopper
    recognises on a shelf; any perspective is better than none.

    Only carried, never fetched: this app does no network I/O to paint a window
    (see gui/wheretobuy.py). Storing the URL costs nothing and leaves the
    caching/offline decision to whoever renders it.
    """
    images = product.get("images") or []
    if not isinstance(images, list):
        return None

    def first_url(image: dict[str, Any]) -> str | None:
        for size in image.get("sizes") or []:
            url = (size or {}).get("url")
            if url:
                return str(url)
        return None

    ordered = sorted(
        (i for i in images if isinstance(i, dict)),
        key=lambda i: str(i.get("perspective", "")).lower() != "front",
    )
    for image in ordered:
        url = first_url(image)
        if url:
            return url
    return None


def extract_serving_grams(product: dict[str, Any]) -> float | None:
    """A serving's weight in grams, or ``None`` if not stated in a weight unit."""
    serving = (_nutrition_block(product) or {}).get("servingSize") or {}
    quantity = _to_float(serving.get("quantity"))
    if quantity is None:
        return None
    uom = serving.get("unitOfMeasure") or {}
    for key in ("name", "abbreviation", "code"):
        token = str(uom.get(key) or "").strip().lower()
        if token in _GRAMS_PER_UNIT:
            return quantity * _GRAMS_PER_UNIT[token]
    return None


def protein_per_100g(product: dict[str, Any]) -> float | None:
    """Protein DENSITY -- the denomination-independent form.

    This is the number that makes ``soldBy`` irrelevant to the cost
    calculation: a density multiplied by whatever weight the price refers to
    gives the protein that price buys, whether that weight is a package or a
    pound. See the module docstring's trap section.
    """
    grams = extract_protein_per_serving(product)
    serving_grams = extract_serving_grams(product)
    if not grams or not serving_grams:
        return None
    return grams / serving_grams * 100.0


def _size_is_weight(size_text: str | None) -> bool:
    """Whether ``savings.parse_size`` reads this size as a weight."""
    if not size_text:
        return False
    from .. import savings

    size = savings.parse_size(size_text)
    return size is not None and size.base_unit == savings.WEIGHT and size.base_quantity > 0


def display_item_name(description: str, size_text: str | None) -> str:
    """Fold the PRICED size into the name, so ``savings.parse_size`` reads it.

    Only a weight-readable size is folded in. This is the same discipline
    ``wholefoods.py`` applies for the same reason (GFP-73): a name is what the
    size parser reads, so putting anything approximate in there launders a
    guess into what looks like a printed fact.

    Note what ``size`` means per denomination, because it is the crux of this
    module: for ``soldBy=UNIT`` it is the package; for ``soldBy=WEIGHT`` it is
    the *pricing* unit ("1 lb"). Either way it is the quantity the price buys,
    which is exactly what the cost calculation needs.
    """
    if size_text and _size_is_weight(size_text):
        return f"{description}, {size_text}"
    return description


def product_to_row(
    product: dict[str, Any], category: str, zip_code: str, now: datetime
) -> tuple[dict[str, Any], _FoodFact | None]:
    """Map one Kroger product to a ``deals`` row plus (when computable) a food fact.

    Pure -- no DB, no network -- so the awkward cases are directly testable.
    """
    product_id = str(product.get("productId") or "").strip()
    description = str(product.get("description") or "").strip()
    item = first_item(product)
    size_text = str(item.get("size") or "").strip() or None
    sold_by = str(item.get("soldBy") or "").strip().upper() or None

    price, regular = extract_price(item)
    per_unit, per_unit_uom = extract_price_per_unit(item, sold_by)
    density = protein_per_100g(product)
    item_name = display_item_name(description, size_text)
    has_price = price is not None
    identifier, identifier_ns = base.product_identifier(product_id, PRODUCT_IDENTIFIER_NS)

    parts = []
    if has_price:
        parts.append(f"${price:.2f}" + ("/lb" if sold_by == "WEIGHT" else ""))
    if per_unit is not None and per_unit_uom:
        parts.append(f"(${per_unit:.2f}/{per_unit_uom})")
    deal_description = " ".join(parts) if parts else "Harris Teeter shelf listing"

    notes = [
        "source=kroger_api",
        f"product_id={product_id}",
        f"category={category}",
        f"postal_code={zip_code}",
    ]
    if size_text:
        notes.append(f"size={size_text}")
    if sold_by:
        notes.append(f"sold_by={sold_by}")
    serving_grams = extract_serving_grams(product)
    protein_serving = extract_protein_per_serving(product)
    if protein_serving is not None:
        notes.append(f"protein_per_serving_g={protein_serving:g}")
    if serving_grams is not None:
        notes.append(f"serving_grams={serving_grams:g}")
    if density is not None:
        notes.append(f"protein_per_100g={density:.2f}")
    if not has_price:
        notes.append("price_missing=true")

    row = {
        "item_name": item_name,
        "sub_category": category,
        "deal_type": DEAL_TYPE if has_price else f"{DEAL_TYPE} (price not listed)",
        "deal_description": deal_description,
        "regular_price": regular,
        "sale_price": price,
        "dollar_price": price,
        "discount_amount": None,
        "discount_percent": None,
        "valid_from": now.date().isoformat(),
        # A shelf price carries no announced expiry the way a dated weekly ad
        # does, and `deals` is replaced wholesale on every re-scrape. Missing
        # valid_to reads as "unknown", never "expired" (GFP-16).
        "valid_to": None,
        "loyalty_required": "N",
        "notes": "; ".join(notes),
        # GFP-99: both come from the payload, both stay None when it does not
        # carry them -- the where-to-buy column degrades to plain text rather
        # than rendering a dead control.
        "source_url": product_page_url(product),
        "image_url": product_image_url(product),
        "flipp_flyer_id": None,
        "flipp_item_id": None,
        "flipp_coupon_id": None,
        "sold_by": sold_by,
        # GFP-152. Computed HERE because this is the only place the evidence
        # exists: `categories` is not persisted, and by the time a row reaches
        # the display layer there is nothing left to classify from.
        "weight_basis": weight_basis.classify(
            sold_by, product.get("categories"), description
        ),
        "price_per_unit": per_unit,
        "price_per_unit_uom": per_unit_uom,
        # GFP-111: Kroger's own productId, written at scrape time from the
        # field already in hand a few lines above -- the same value `notes`
        # records for provenance, but in a real column so nothing downstream
        # has to string-parse that blob to find it. `notes` is unchanged.
        # NULL/NULL when the API omitted productId: a shelf listing with no id
        # cannot be ordered, and a substitute would be a fabricated SKU.
        "product_identifier": identifier,
        "product_identifier_ns": identifier_ns,
    }

    fact = None
    if product_id and density and density > 0:
        fact = _FoodFact(
            product_id=product_id,
            name=description or item_name,
            category=category,
            protein_per_100g=density,
            item_name=item_name,
        )
    return row, fact


# --------------------------------------------------------------------------- #
# Persistence -- same shape as wholefoods.py, for the same reasons
# --------------------------------------------------------------------------- #
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _upsert_food_fact(conn: sqlite3.Connection, fact: _FoodFact) -> None:
    now = _now_iso()
    conn.execute(
        "INSERT INTO foods(name, category, source, source_ref, slug, updated_at) "
        "VALUES (?, ?, 'kroger', ?, ?, ?) "
        "ON CONFLICT(source, source_ref) DO UPDATE SET "
        "name=excluded.name, category=excluded.category, slug=excluded.slug, "
        "updated_at=excluded.updated_at",
        (fact.name, fact.category, fact.product_id, f"kroger-{fact.product_id}", now),
    )
    food_id = conn.execute(
        "SELECT id FROM foods WHERE source='kroger' AND source_ref=?", (fact.product_id,)
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO food_nutrients(food_id, nutrient, amount_per_100g, unit) "
        "VALUES (?, 'protein', ?, 'g') "
        "ON CONFLICT(food_id, nutrient) DO UPDATE SET "
        "amount_per_100g=excluded.amount_per_100g, unit=excluded.unit",
        (food_id, fact.protein_per_100g),
    )
    # match_source=MANUAL is deliberate, exactly as in wholefoods.py: the
    # figure came off the retailer's own label for this exact product, so the
    # keyword auto-matcher must never be allowed to downgrade it. `method`
    # keeps the real provenance auditable.
    conn.execute(
        "INSERT INTO deal_food_match"
        "(store, item_name, food_id, confidence, method, match_source, updated_at) "
        f"VALUES (?, ?, ?, 1.0, 'kroger_api_direct', '{matching.MANUAL}', ?) "
        "ON CONFLICT(store, item_name) DO UPDATE SET "
        "food_id=excluded.food_id, confidence=1.0, method='kroger_api_direct', "
        f"match_source='{matching.MANUAL}', updated_at=excluded.updated_at",
        (STORE_KEY, fact.item_name, food_id, now),
    )


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def serves(postal_code: str) -> bool | None:
    """Does a grocery store of this chain exist near ``postal_code``? (GFP-257)

    The real capability, and the reason GFP-257's design is "ask the source":
    Kroger already answers this exactly, and :func:`pick_store` already reads
    the ``chain`` value BACK rather than trusting a name -- GFP-77's lesson,
    which cost real time to learn.

    ``None`` when the question cannot be put -- no credentials, an API error, a
    rate limit. Unknown is not the same as absent, and availability.py treats it
    permissively: a store we could not ask about is still scraped.
    """
    ready, _ = readiness()
    if not ready:
        return None
    try:
        with KrogerClient(load_auth()) as client:
            return pick_store(client.locations_near(postal_code)) is not None
    except (KrogerError, OSError, ValueError):
        return None


def scrape(
    postal_code: str | None = None,
    queries: Iterable[tuple[str, str]] | None = None,
    conn: sqlite3.Connection | None = None,
    config_file: Path | None = None,
    client: KrogerClient | None = None,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Scrape Harris Teeter shelf prices and return ``(rows, meta, stats)``.

    Matches the contract ``service/ingest.run_scrape`` expects of every
    scraper. Like ``wholefoods.py`` -- and unlike the Flipp stores -- it also
    upserts ``foods``/``food_nutrients``/``deal_food_match`` for every product
    whose protein density is computable, so the nutrition arrives with the
    price instead of needing a USDA matching pass.
    """
    zip_code = postal_code or DEFAULT_POSTAL_CODE
    moment = now or datetime.now(timezone.utc)
    terms = tuple(queries) if queries is not None else DEFAULT_QUERIES

    owned_client = client is None
    active = client or KrogerClient(load_auth(config_file))
    own = conn or db.connect()

    try:
        store = pick_store(active.locations_near(zip_code))
        if store is None:
            raise NoStoreFoundError(
                f"No Harris Teeter grocery store within 25 miles of {zip_code}. "
                "Note that Harris Teeter grocery locations report chain="
                f"{GROCERY_CHAIN_CODES[0]!r}; the 'HARRIS TEETER FUEL' chains are "
                "petrol stations and are deliberately not matched."
            )
        location_id = str(store.get("locationId"))

        rows: list[dict[str, Any]] = []
        facts: dict[str, _FoodFact] = {}
        seen: set[str] = set()
        for term, category in terms:
            for product in active.products(term, location_id):
                product_id = str(product.get("productId") or "")
                # The same product legitimately answers several terms; keep the
                # first, so counts mean products rather than search hits.
                if product_id and product_id in seen:
                    continue
                if product_id:
                    seen.add(product_id)
                row, fact = product_to_row(product, category, zip_code, moment)
                rows.append(row)
                if fact is not None:
                    facts[fact.product_id] = fact

        for fact in facts.values():
            _upsert_food_fact(own, fact)
        if conn is None:
            own.commit()

        priced = sum(1 for r in rows if r["dollar_price"] is not None)
        by_weight = sum(1 for r in rows if r["sold_by"] == "WEIGHT")
        meta = {
            "name": f"{MERCHANT} shelf prices ({zip_code})",
            "id": location_id,
            "location_id": location_id,
            "store_name": store.get("name"),
            "chain": store.get("chain"),
        }
        stats = {
            # Field names mirror scrapers/base.py::scrape_store's stats shape,
            # the same way wholefoods.py does, so cli.py's scrape output needs
            # no per-store branching. "weekly_ad"/"digital_coupons"/"bogo" are
            # Flipp-vintage names this store has no literal equivalent of --
            # repurposed rather than forking the CLI's formatting per store.
            "weekly_ad": len(rows),
            "digital_coupons": 0,
            "no_price": len(rows) - priced,
            "bogo": 0,
            "expired_items": 0,
            "total": len(rows),
            "flyer_id": location_id,
            "flyer_name": meta["name"],
            "flyer_status": "active",
            "valid_from": moment.date().isoformat(),
            "valid_to": moment.date().isoformat(),
            # Kroger-specific, for anyone reading the job record: how much of
            # this scrape can actually be priced per gram of protein, and how
            # much of it is the per-pound denomination that needs the UI tag.
            "priced": priced,
            "with_protein": len(facts),
            "sold_by_weight": by_weight,
            "products_seen": len(rows),
            "queries": [term for term, _cat in terms],
        }
        return rows, meta, stats
    finally:
        if owned_client:
            active.close()
