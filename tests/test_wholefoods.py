"""Tests for the Whole Foods storefront scraper (GFP-4).

No network — the HTTP layer (``WholeFoodsClient``) is monkeypatched out the
same way ``tests/test_scraper.py`` fakes ``FlippClient``, and the pure
extraction functions (``product_to_row`` and friends) are exercised directly
against product dicts shaped like the real payload documented in
``docs/spikes/GFP-70-whole-foods.md``.
"""
from __future__ import annotations

import base64
import json
import urllib.parse
from datetime import datetime, timezone

import pytest

from grocery_planner import db, matching
from grocery_planner.scrapers import wholefoods as wf

NOW = datetime(2026, 8, 1, tzinfo=timezone.utc)

# --------------------------------------------------------------------------- #
# Representative product payloads, shaped after the GFP-70 spike's real
# examples (docs/spikes/GFP-70-whole-foods.md).
# --------------------------------------------------------------------------- #
CHICKEN_BREAST = {
    "name": "Bell & Evans Boneless Skinless Chicken Breast",
    "asin": "B0787WTY4C",
    "variationsList": [
        {
            "displayString": "Size",
            "symbol": "size_name",
            "variationNodeList": [
                {"dimensionValue": "1.5 Pound (Pack of 1)", "asin": "B0787WTY4C"},
                {"dimensionValue": "3.8 Pound (Value Pack)", "asin": "B0787Y4X59"},
            ],
        }
    ],
    "offerDetails": {
        "price": {"currencyCode": "USD", "priceAmount": 6.99},
        "unitPrice": {"baseUnit": "each", "currencyCode": "USD", "priceAmount": 10.21},
    },
    "nutritionFacts": {
        "servingSize": "4.0 oz",
        "servingsPerContainer": "4.0 servings per container",
        "macronutrients": [
            {"name": "Protein", "amount": "27g", "percent": "", "level": "TOP"}
        ],
    },
}

# A butcher-counter item: no fixed package (no variationsList), priced and
# sold per pound, per the module docstring's "Package weight and GFP-73"
# section.
GROUND_CHICKEN_BY_WEIGHT = {
    "name": "Organic Ground Chicken",
    "asin": "B0TESTGC01",
    "offerDetails": {
        "price": {"currencyCode": "USD", "priceAmount": 5.49},
        "unitPrice": {"baseUnit": "pound", "currencyCode": "USD", "priceAmount": 5.49},
    },
    "nutritionFacts": {
        "servingSize": "4 oz",
        "servingsPerContainer": "3 servings per container",
        "macronutrients": [{"name": "Protein", "amount": "21g"}],
    },
}

NO_NUTRITION_PRODUCT = {
    "name": "Simple Truth Sparkling Water",
    "asin": "B0TESTSW01",
    "offerDetails": {"price": {"currencyCode": "USD", "priceAmount": 3.99}},
}

NO_PRICE_PRODUCT = {
    "name": "Mystery Item",
    "asin": "B0TESTNP01",
    "offerDetails": {},
}


def _cookie_value(id_: int = 10426, delivery_zip: str = "27401", **extra) -> str:
    """A wfm_store_d8-shaped value: URL-encoded JSON, per the GFP-70 spike."""
    blob = {"id": id_, "name": "Lamar", "state": "TX", "deliveryZip": delivery_zip, **extra}
    return urllib.parse.quote(json.dumps(blob))


def _write_session(tmp_path, cookie_value=None, minted_at=None):
    path = tmp_path / "wholefoods_session.json"
    payload = {"wfm_store_d8": cookie_value or _cookie_value()}
    if minted_at:
        payload["minted_at"] = minted_at
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Session config file
# --------------------------------------------------------------------------- #
def test_load_session_missing_file_raises(tmp_path):
    with pytest.raises(wf.SessionMissingError, match="No Whole Foods session file"):
        wf.load_session(tmp_path / "nope.json")


def test_load_session_bad_json_raises(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(wf.SessionMissingError, match="not valid JSON"):
        wf.load_session(path)


def test_load_session_missing_cookie_key_raises(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"minted_at": "2026-01-01"}), encoding="utf-8")
    with pytest.raises(wf.SessionMissingError, match="wfm_store_d8"):
        wf.load_session(path)


def test_load_session_reads_cookie_and_optional_minted_at(tmp_path):
    path = _write_session(tmp_path, minted_at="2026-07-01")
    session = wf.load_session(path)
    assert session.wfm_store_d8 == _cookie_value()
    assert session.minted_at == "2026-07-01"


def test_session_path_lives_in_the_user_data_dir(monkeypatch, tmp_path):
    """Unchanged intent; GFP-97 moved the mechanism behind the credential seam.

    This used to patch ``wf.data_dir``. wholefoods.py no longer resolves the
    path itself -- it asks ``credentials.LocalFileProvider`` -- so the patch
    now goes where the resolution actually happens. If that indirection ever
    breaks, this test fails, which is the point of keeping it.
    """
    from grocery_planner import credentials

    monkeypatch.setattr(credentials, "data_dir", lambda: tmp_path)
    assert wf.session_path() == tmp_path / "wholefoods_session.json"


def test_session_path_honours_its_environment_override(monkeypatch, tmp_path):
    """GFP-97 gave Whole Foods the same override Kroger already had."""
    from grocery_planner import credentials

    target = tmp_path / "elsewhere.json"
    monkeypatch.setenv(credentials.WHOLEFOODS.env_var, str(target))
    assert wf.session_path() == target


# --------------------------------------------------------------------------- #
# readiness() -- "registered" and "ready to scrape" are different questions
# (the bug this whole section exists to prevent: CI/a fresh install has no
# session cookie yet, so registering wholefoods must not make it look
# scrapable until it actually is).
# --------------------------------------------------------------------------- #
def test_readiness_false_without_a_session_file(tmp_path):
    ready, reason = wf.readiness(tmp_path / "nope.json")
    assert ready is False
    assert "session cookie" in reason


def test_readiness_true_once_a_session_file_exists(tmp_path):
    # A cheap existence check, deliberately not full validation -- even a
    # trivial/malformed file counts as "ready" here; load_session() (run at
    # actual scrape time) is what does full validation and raises the
    # detailed error. See the readiness() docstring for why.
    path = tmp_path / "wholefoods_session.json"
    path.write_text("not even valid json", encoding="utf-8")
    ready, reason = wf.readiness(path)
    assert ready is True
    assert reason == ""


def test_readiness_uses_the_default_session_path_when_unspecified(monkeypatch, tmp_path):
    monkeypatch.setattr(wf, "session_path", lambda: tmp_path / "wholefoods_session.json")
    assert wf.readiness() == (False, wf.readiness()[1])
    assert "session cookie" in wf.readiness()[1]


# --------------------------------------------------------------------------- #
# Cookie decoding + ZIP check (GFP-53)
# --------------------------------------------------------------------------- #
def test_decode_store_cookie_url_encoded():
    data = wf._decode_store_cookie(_cookie_value(id_=999, delivery_zip="90210"))
    assert data == {"id": 999, "name": "Lamar", "state": "TX", "deliveryZip": "90210"}


def test_decode_store_cookie_already_decoded():
    raw = json.dumps({"id": 1, "deliveryZip": "27401"})
    assert wf._decode_store_cookie(raw)["deliveryZip"] == "27401"


def test_decode_store_cookie_base64():
    # GFP-93: the live cookie is base64, not URL-encoded. This is the exact
    # prefix of a real value read out of Chrome on 2026-08-02 -- the shape
    # that used to be rejected as "re-mint required" on a perfectly good
    # session, which is why this asserts on a verbatim capture rather than a
    # round-tripped fixture.
    real = (
        "eyJpZCI6IjEwNDI2IiwibmFtZSI6IkxhbWFyIiwidGxjIjoiTE1SIiwicGF0aCI6Imxh"
        "bWFyIiwic3RhdGUiOiJUWCIsInN0b3JlX25pZCI6IiIsImRlbGl2ZXJ5WmlwIjoiMjc0"
        "MDEifQ=="
    )
    data = wf._decode_store_cookie(real)
    assert data["deliveryZip"] == "27401"
    assert data["id"] == "10426"


def test_decode_store_cookie_base64_without_padding():
    # Whole Foods pads inconsistently; a value that lost its trailing "="
    # must still decode rather than send the user off to re-mint.
    padded = base64.b64encode(
        json.dumps({"id": "1", "deliveryZip": "27401"}).encode()
    ).decode()
    assert wf._decode_store_cookie(padded.rstrip("="))["deliveryZip"] == "27401"


def test_decode_store_cookie_garbage_raises():
    with pytest.raises(wf.SessionExpiredError, match="Could not parse"):
        wf._decode_store_cookie("%%%not-json-at-all%%%")


def test_check_zip_matching_is_a_noop():
    wf._check_zip({"deliveryZip": "27401"}, "27401")  # does not raise


def test_check_zip_mismatch_raises():
    with pytest.raises(wf.ZipMismatchError, match="does not match"):
        wf._check_zip({"deliveryZip": "90210"}, "27401")


def test_check_zip_tolerates_a_cookie_with_no_zip_field():
    wf._check_zip({}, "27401")  # nothing to disagree with -> no raise


def test_check_zip_ignores_the_stale_name_and_state_fields():
    # A real minted cookie was confirmed (during this ticket's review) to
    # decode with a CORRECT deliveryZip alongside a STALE name="Lamar"/
    # state="TX" (Austin, TX placeholder coordinates) -- GFP-74/the module
    # docstring both call this out. The check must key off deliveryZip only.
    stale_cookie = {"id": 10426, "name": "Lamar", "state": "TX", "deliveryZip": "27401"}
    wf._check_zip(stale_cookie, "27401")  # matches on deliveryZip -> no raise
    with pytest.raises(wf.ZipMismatchError):
        wf._check_zip(stale_cookie, "90210")  # still compares deliveryZip, not name/state


# --------------------------------------------------------------------------- #
# buildId / __NEXT_DATA__ extraction
# --------------------------------------------------------------------------- #
def test_extract_build_id():
    html = '<html><script>{"buildId":"abc123XYZ","other":1}</script></html>'
    assert wf._extract_build_id(html) == "abc123XYZ"


def test_extract_build_id_missing_raises():
    with pytest.raises(wf.PageStructureError, match="buildId"):
        wf._extract_build_id("<html>no build id here</html>")


def test_extract_next_data():
    payload = {"props": {"pageProps": {"pageType": "search"}}, "buildId": "xyz"}
    html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'
    assert wf._extract_next_data(html) == payload


def test_extract_next_data_missing_raises():
    with pytest.raises(wf.PageStructureError, match="__NEXT_DATA__"):
        wf._extract_next_data("<html>no next data script</html>")


def test_is_loading_shell_detects_the_dead_cookie_signature():
    assert wf._is_loading_shell(
        {"nonce": "abc", "pageType": "loading", "productsInfo": None}
    )


def test_is_loading_shell_false_for_real_results():
    assert not wf._is_loading_shell(
        {"pageType": "search", "productsInfo": {"B01": {}}}
    )


def test_is_loading_shell_false_for_genuine_empty_results():
    # A real "nothing matched" response is a DIFFERENT valid shape (empty,
    # not null) -- must never be confused with a dead cookie.
    assert not wf._is_loading_shell({"pageType": "search", "productsInfo": {}})


def test_is_loading_shell_tolerates_missing_page_props():
    assert not wf._is_loading_shell(None)
    assert not wf._is_loading_shell({})


# --------------------------------------------------------------------------- #
# product_to_row — the core extraction (name/asin/price/size/nutrition)
# --------------------------------------------------------------------------- #
def test_product_to_row_full_nutrition_and_size():
    row, fact = wf.product_to_row(CHICKEN_BREAST, "chicken", "27401", NOW)

    assert row["item_name"] == (
        "Bell & Evans Boneless Skinless Chicken Breast, 1.5 Pound (Pack of 1)"
    )
    assert row["sale_price"] == 6.99
    assert row["dollar_price"] == 6.99
    assert row["deal_type"] == "Storefront Price"
    assert row["sub_category"] == "Meat & Seafood"
    assert row["loyalty_required"] == "N"
    assert row["valid_from"] == "2026-08-01"
    assert row["valid_to"] is None
    assert "asin=B0787WTY4C" in row["notes"]
    assert "package_weight_source=variationsList" in row["notes"]
    assert "unit_price=10.21/each" in row["notes"]
    assert "servings_per_container=4" in row["notes"]

    assert fact is not None
    assert fact.asin == "B0787WTY4C"
    assert fact.category == "chicken"
    assert fact.item_name == row["item_name"]
    # protein_per_100g = 27g / (4.0 oz in grams) * 100
    serving_grams = wf._weight_grams("4.0 oz")
    assert fact.protein_per_100g == pytest.approx(27.0 / serving_grams * 100, rel=1e-6)
    assert fact.protein_per_100g == pytest.approx(23.8095, rel=1e-3)


def test_product_to_row_butcher_counter_uses_serving_estimate_not_item_name():
    row, fact = wf.product_to_row(GROUND_CHICKEN_BY_WEIGHT, "chicken", "27401", NOW)

    # No printed size exists for this product -- the name must NOT gain a
    # fabricated size token (module docstring's GFP-73 honesty rule).
    assert row["item_name"] == "Organic Ground Chicken"
    assert "package_weight_source=serving_estimate" in row["notes"]
    assert row["sale_price"] == 5.49

    assert fact is not None
    # protein_per_100g is still a plain density figure (21g / 4oz-in-grams *
    # 100) -- servingsPerContainer affects package-weight estimation, not
    # this density value.
    serving_grams = wf._weight_grams("4 oz")
    assert fact.protein_per_100g == pytest.approx(21.0 / serving_grams * 100, rel=1e-3)


def test_product_to_row_no_nutrition_still_returns_a_row():
    row, fact = wf.product_to_row(NO_NUTRITION_PRODUCT, "beef", "27401", NOW)
    assert row["item_name"] == "Simple Truth Sparkling Water"
    assert row["sale_price"] == 3.99
    assert fact is None


def test_product_to_row_no_price_is_flagged_not_dropped():
    row, fact = wf.product_to_row(NO_PRICE_PRODUCT, "beef", "27401", NOW)
    assert row["sale_price"] is None
    assert row["dollar_price"] is None
    assert row["deal_type"] == "Storefront Price (price not listed)"
    assert "price_missing=true" in row["notes"]
    assert fact is None


def test_find_own_size_text_matches_the_products_own_asin():
    assert wf._find_own_size_text(CHICKEN_BREAST) == "1.5 Pound (Pack of 1)"


def test_find_own_size_text_none_when_no_variations_list():
    assert wf._find_own_size_text(GROUND_CHICKEN_BY_WEIGHT) is None


def test_iter_products_handles_dict_and_list_shapes():
    as_dict = {"B01": {"asin": "B01"}, "B02": {"asin": "B02"}}
    as_list = [{"asin": "B01"}, {"asin": "B02"}]
    assert {p["asin"] for p in wf._iter_products(as_dict)} == {"B01", "B02"}
    assert {p["asin"] for p in wf._iter_products(as_list)} == {"B01", "B02"}
    assert wf._iter_products(None) == []


# --------------------------------------------------------------------------- #
# Full scrape() orchestration — fake WholeFoodsClient, no network
# --------------------------------------------------------------------------- #
class _FakeWholeFoodsClient:
    """Stands in for the HTTP client so scrape() can be exercised offline,
    the same pattern tests/test_scraper.py uses for FlippClient."""

    build_id = "build123"
    pages: dict[str, dict] = {}

    def __init__(self, cookie_value):
        self.cookie_value = cookie_value

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def bootstrap(self, query):
        return self.build_id, self.pages[query]

    def search(self, build_id, query):
        assert build_id == self.build_id
        return self.pages[query]


def _fake_client(monkeypatch, pages):
    _FakeWholeFoodsClient.pages = pages
    monkeypatch.setattr(wf, "WholeFoodsClient", _FakeWholeFoodsClient)


def test_scrape_happy_path_lands_deals_and_nutrition(monkeypatch, tmp_path, conn):
    session_file = _write_session(tmp_path)
    _fake_client(
        monkeypatch,
        {
            "chicken breast": {
                "pageType": "search",
                "productsInfo": {"B0787WTY4C": CHICKEN_BREAST},
            },
            "salmon": {
                "pageType": "search",
                "productsInfo": {"B0TESTSW01": NO_NUTRITION_PRODUCT},
            },
        },
    )

    rows, meta, stats = wf.scrape(
        postal_code="27401",
        queries=[("chicken breast", "chicken"), ("salmon", "fish")],
        conn=conn,
        session_file=session_file,
        now=NOW,
    )

    assert len(rows) == 2
    assert stats["total"] == 2
    assert stats["with_full_nutrition"] == 1
    assert stats["no_price"] == 0
    assert meta["id"] == 10426

    food = conn.execute(
        "SELECT * FROM foods WHERE source='wholefoods' AND source_ref=?",
        ("B0787WTY4C",),
    ).fetchone()
    assert food is not None
    assert food["slug"] == "wholefoods-B0787WTY4C"
    assert food["category"] == "chicken"

    nutrient = conn.execute(
        "SELECT amount_per_100g FROM food_nutrients WHERE food_id=? AND nutrient='protein'",
        (food["id"],),
    ).fetchone()
    assert nutrient["amount_per_100g"] == pytest.approx(23.8095, rel=1e-3)

    match = matching.get_match(
        "wholefoods",
        "Bell & Evans Boneless Skinless Chicken Breast, 1.5 Pound (Pack of 1)",
        conn=conn,
    )
    assert match is not None
    assert match["food_id"] == food["id"]
    assert match["confidence"] == 1.0
    assert match["method"] == "wholefoods_direct"
    assert match["match_source"] == "manual"  # protected from the keyword auto-matcher

    # No food/nutrient row for the no-nutrition product.
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM foods WHERE source='wholefoods' AND source_ref=?",
        ("B0TESTSW01",),
    ).fetchone()["n"] == 0


def test_scrape_dedupes_the_same_asin_across_queries(monkeypatch, tmp_path, conn):
    session_file = _write_session(tmp_path)
    _fake_client(
        monkeypatch,
        {
            "chicken breast": {
                "pageType": "search",
                "productsInfo": {"B0787WTY4C": CHICKEN_BREAST},
            },
            "chicken thigh": {
                "pageType": "search",
                "productsInfo": {"B0787WTY4C": CHICKEN_BREAST},
            },
        },
    )
    rows, _meta, stats = wf.scrape(
        postal_code="27401",
        queries=[("chicken breast", "chicken"), ("chicken thigh", "chicken")],
        conn=conn,
        session_file=session_file,
        now=NOW,
    )
    assert len(rows) == 1
    assert stats["products_seen"] == 2  # seen twice, stored once


def test_scrape_raises_session_expired_on_loading_shell(monkeypatch, tmp_path, conn):
    session_file = _write_session(tmp_path)
    _fake_client(
        monkeypatch,
        {
            "chicken breast": {
                "pageType": "loading",
                "productsInfo": None,
                "searchResults": None,
            },
        },
    )
    with pytest.raises(wf.SessionExpiredError, match="re-minted"):
        wf.scrape(
            postal_code="27401",
            queries=[("chicken breast", "chicken")],
            conn=conn,
            session_file=session_file,
            now=NOW,
        )


def test_scrape_raises_zip_mismatch_before_any_http_call(monkeypatch, tmp_path, conn):
    session_file = _write_session(tmp_path, cookie_value=_cookie_value(delivery_zip="90210"))

    class _ExplodingClient:
        def __init__(self, *_a, **_kw):
            raise AssertionError("scrape() must refuse before touching the network")

    monkeypatch.setattr(wf, "WholeFoodsClient", _ExplodingClient)

    with pytest.raises(wf.ZipMismatchError):
        wf.scrape(postal_code="27401", conn=conn, session_file=session_file, now=NOW)


def test_scrape_raises_session_missing_without_a_config_file(tmp_path, conn):
    with pytest.raises(wf.SessionMissingError):
        wf.scrape(session_file=tmp_path / "nope.json", conn=conn, now=NOW)


def test_scrape_requires_at_least_one_query(tmp_path, conn):
    session_file = _write_session(tmp_path)
    with pytest.raises(ValueError, match="at least one"):
        wf.scrape(postal_code="27401", queries=[], conn=conn, session_file=session_file, now=NOW)


# --------------------------------------------------------------------------- #
# Registry surface (mirrors tests/test_scraper.py::test_store_registry)
# --------------------------------------------------------------------------- #
def test_registered_in_scrapers():
    from grocery_planner.scrapers import SCRAPERS

    assert SCRAPERS["wholefoods"] is wf
    assert wf.STORE_KEY == "wholefoods"
    assert wf.MERCHANT == "Whole Foods Market"
    assert wf.DEFAULT_POSTAL_CODE
    assert callable(wf.scrape)
