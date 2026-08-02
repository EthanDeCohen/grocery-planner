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
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx

from .. import db, matching
from ..paths import data_dir

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

CONFIG_FILENAME = "kroger-env.config"
CONFIG_ENV_VAR = "GROCERY_PLANNER_KROGER_CONFIG"

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
_GRAMS_PER_UNIT = {
    "gram": 1.0, "grm": 1.0, "g": 1.0,
    "milligram": 0.001, "mgm": 0.001,
    "kilogram": 1000.0, "kgm": 1000.0,
    "ounce": 28.349523125, "onz": 28.349523125, "oz": 28.349523125,
    "pound": 453.59237, "lbr": 453.59237, "lb": 453.59237,
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
    override = os.environ.get(CONFIG_ENV_VAR)
    if override:
        return Path(override)
    return data_dir() / CONFIG_FILENAME


def load_credentials(path: Path | None = None) -> Credentials:
    """Read client_id/client_secret from the INI file, or raise.

    Accepts the file with or without a ``[section]`` header, because an
    "env config" is as likely to be written as bare ``key = value`` lines, and
    failing on that would be a pointless papercut.
    """
    target = path or config_path()
    if not target.exists():
        raise CredentialsMissingError(
            f"No Kroger credentials at {target}. Register an application at "
            "https://developer.kroger.com and write its client_id/client_secret "
            f"to that file as INI:\n\n    [kroger]\n    client_id = ...\n"
            "    client_secret = ...\n\nNever commit it."
        )

    # utf-8-sig: PowerShell's `Out-File -Encoding utf8` writes a BOM on 5.1, and
    # a BOM ahead of a section header makes configparser fail obscurely. Same
    # trap GFP-93 hit with the Whole Foods session file.
    text = target.read_text(encoding="utf-8-sig").strip()
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


def readiness(config_file: Path | None = None) -> tuple[bool, str]:
    """Whether a scrape could run right now, and why not.

    Same contract as ``wholefoods.readiness`` -- deliberately a cheap existence
    check rather than full validation, so `gplan stores` stays fast and the
    detailed error belongs to the scrape itself.
    """
    target = config_file or config_path()
    if not target.exists():
        return False, (
            f"needs Kroger API credentials at {target} "
            "(register at developer.kroger.com)"
        )
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

    def __init__(self, credentials: Credentials, timeout: float = 45.0):
        self._credentials = credentials
        self._http = httpx.Client(timeout=timeout)
        self._token: str | None = None

    def __enter__(self) -> "KrogerClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def token(self) -> str:
        if self._token:
            return self._token
        resp = self._http.post(
            TOKEN_URL,
            auth=(self._credentials.client_id, self._credentials.client_secret),
            data={"grant_type": "client_credentials", "scope": TOKEN_SCOPE},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
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
        resp = self._http.get(
            f"{API_BASE}{path}",
            params=params,
            headers={"Authorization": f"Bearer {self.token()}", "Accept": "application/json"},
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
        "source_url": None,
        "image_url": None,
        "flipp_flyer_id": None,
        "flipp_item_id": None,
        "flipp_coupon_id": None,
        "sold_by": sold_by,
        "price_per_unit": per_unit,
        "price_per_unit_uom": per_unit_uom,
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
    active = client or KrogerClient(load_credentials(config_file))
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
