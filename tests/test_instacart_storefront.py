"""The Instacart Storefront Pro platform client -- GFP-265.

No network: every test drives the real client through an ``httpx.MockTransport``
that *records the outbound request*. That matters more than convenience here.
The central claim of this refactor is that one client can serve two banners, and
the way that claim fails is not by raising -- it is by quietly sending Sprouts'
shop id to ALDI and returning plausible rows for the wrong store. Only asserting
on what actually went over the wire can catch that.

As in ``test_sprouts.py``, these assert **relationships** rather than spellings,
so a refactor that keeps the behaviour keeps the tests.
"""
from __future__ import annotations

import json
import urllib.parse
from datetime import datetime, timezone

import httpx
import pytest

from grocery_planner import savings
from grocery_planner.scrapers import aldi, instacart_storefront as ist, sprouts

NOW = datetime(2026, 8, 11, tzinfo=timezone.utc)
PIN = sprouts.FALLBACK_HASHES["ProductNutritionalInfo"]


# --------------------------------------------------------------------------- #
# Canned payloads
# --------------------------------------------------------------------------- #
def storefront_html(hashes: dict[str, str]) -> str:
    """A storefront page carrying the doubly-URL-encoded perf blob.

    Built by encoding twice rather than by pasting a captured literal, so the
    fixture cannot drift out of agreement with what the decoder must undo.
    """
    raw = "".join(
        f'operationName={op}&variables=%7B%7D&extensions='
        f'{{"persistedQuery":{{"version":1,"sha256Hash":"{sha}"}}}};'
        for op, sha in hashes.items()
    )
    twice = urllib.parse.quote(urllib.parse.quote(raw))
    return f"<html><body><script>window.__PERF__=\"{twice}\"</script></body></html>"


def product_html(name: str, size: str, price: str | None) -> str:
    offers = {"availability": "https://schema.org/InStock"}
    if price is not None:
        offers["price"] = price
    graph = {
        "@context": "https://schema.org",
        "@graph": [{
            "@type": "Product", "name": name,
            "brand": {"@type": "Brand", "name": "Test"},
            "category": "Meat", "size": size, "offers": offers,
        }],
    }
    return (
        '<html><head><script type="application/ld+json">'
        + json.dumps(graph)
        + "</script></head></html>"
    )


DISCOVERABLE = {
    "SimpleShopCollection": sprouts.FALLBACK_HASHES["SimpleShopCollection"],
    "ShopCollectionScoped": "f" * 64,
}


def shop_payload(shops: list[tuple[str, str]]) -> dict:
    """``[(shop_id, service_type), ...]`` -> a ShopCollection response."""
    return {"data": {"shopCollection": {"shops": [
        {"id": sid, "serviceType": st, "retailerLocationId": "124437"}
        for sid, st in shops
    ]}}}


def panel_payload(protein=13.0, serving="8 oz (227g)", servings="Varied") -> dict:
    return {"data": {"productNutritionalInfo": {"nutritionalInfo": {
        "protein": protein, "calories": 370.0,
        "servingSize": serving, "servingsPerContainer": servings,
    }}}}


class Recorder:
    """A MockTransport handler that logs every request it answers."""

    def __init__(self, shops, panel=None, discoverable=None, product_size="per lb"):
        self.requests: list[httpx.Request] = []
        self._shops = shops
        self._panel = panel if panel is not None else panel_payload()
        self._discoverable = discoverable if discoverable is not None else DISCOVERABLE
        #: The ``size`` the canned product page reports. Settable because the
        #: density route taken depends on it: a rate ("per lb") forces the
        #: serving-grams route, a package weight allows the package route.
        self.product_size = product_size

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/storefront"):
            return httpx.Response(200, text=storefront_html(self._discoverable))
        if path == "/graphql":
            op = request.url.params.get("operationName")
            if op in ("ShopCollectionScoped", "SimpleShopCollection"):
                return httpx.Response(200, json=shop_payload(self._shops))
            if op == "ProductNutritionalInfo":
                return httpx.Response(200, json=self._panel)
            return httpx.Response(200, json={"data": {}})
        if "/products/" in path:
            return httpx.Response(
                200, text=product_html("Ground Beef", self.product_size, "6.49")
            )
        if "sitemap" in path:
            return httpx.Response(200, text="<urlset></urlset>")
        return httpx.Response(404)

    # -- helpers the assertions read ---------------------------------------- #
    def ops(self) -> list[str]:
        return [r.url.params.get("operationName") for r in self.requests
                if r.url.path == "/graphql"]

    def variables_for(self, operation: str) -> dict:
        for r in self.requests:
            if r.url.path == "/graphql" and r.url.params.get("operationName") == operation:
                return json.loads(r.url.params["variables"])
        raise AssertionError(f"{operation} was never requested")

    def hosts(self) -> set[str]:
        return {r.url.host for r in self.requests}

    def all_text(self) -> str:
        return " ".join(str(r.url) for r in self.requests)


def client_for(tenant: ist.Tenant, recorder: Recorder) -> ist.StorefrontClient:
    http = httpx.Client(
        base_url=tenant.base_url, transport=httpx.MockTransport(recorder)
    )
    return ist.StorefrontClient(tenant, client=http)


# --------------------------------------------------------------------------- #
# Two tenants, one client -- the claim this refactor rests on
# --------------------------------------------------------------------------- #
def test_two_tenants_never_leak_ids_into_each_others_requests():
    """The failure mode is silent, so it is asserted on the wire.

    A shared client that mixed tenants up would not raise. It would return
    well-formed rows for the wrong store, which is indistinguishable from
    working until someone compares a price to a real shelf. So: drive both
    tenants, then assert each one's traffic mentions only its own host, slug and
    shop id -- and never the other's.
    """
    sprouts_rec = Recorder([("515202", "instore"), ("5201", "delivery")])
    aldi_rec = Recorder([("6823", "delivery"), ("515201", "instore")])

    s_client = client_for(sprouts.TENANT, sprouts_rec)
    a_client = client_for(aldi.TENANT, aldi_rec)

    # Interleaved on purpose: a per-module global would survive a sequential
    # test and fail here.
    s_client.discover()
    a_client.discover()
    s_ctx = s_client.shop_context("27401")
    a_ctx = a_client.shop_context("27401")
    s_client.nutrition("70516703", s_ctx.shop_id)
    a_client.nutrition("21171551", a_ctx.shop_id)

    assert s_ctx.shop_id == "515202"
    assert a_ctx.shop_id == "515201"
    assert s_ctx.shop_id != a_ctx.shop_id

    # Each tenant talked only to its own host...
    assert sprouts_rec.hosts() == {"shop.sprouts.com"}
    assert aldi_rec.hosts() == {"www.aldi.us"}

    # ...asked for its own retailer slug...
    assert sprouts_rec.variables_for("ShopCollectionScoped")["retailerSlug"] == "sprouts"
    assert aldi_rec.variables_for("ShopCollectionScoped")["retailerSlug"] == "aldi"

    # ...and sent its own shop id, never the other's.
    assert sprouts_rec.variables_for("ProductNutritionalInfo")["shopId"] == s_ctx.shop_id
    assert aldi_rec.variables_for("ProductNutritionalInfo")["shopId"] == a_ctx.shop_id
    assert a_ctx.shop_id not in sprouts_rec.all_text()
    assert s_ctx.shop_id not in aldi_rec.all_text()


def test_rows_from_one_client_are_keyed_to_the_right_store():
    """Same listing, two tenants: the rows must not be interchangeable."""
    listing = ist.Listing(
        product_id="1234", slug="1234-ground-beef", name="Ground Beef",
        brand="Test", category="Meat", size="per lb", price=6.49,
        availability="InStock",
    )
    s_row, _ = ist.listing_to_row(sprouts.TENANT, listing, None, "27401", NOW)
    a_row, _ = ist.listing_to_row(aldi.TENANT, listing, None, "27401", NOW)

    assert s_row["product_identifier_ns"] != a_row["product_identifier_ns"]
    assert sprouts.TENANT.retailer_slug in s_row["source_url"]
    assert aldi.TENANT.retailer_slug in a_row["source_url"]
    assert s_row["source_url"].startswith(sprouts.BASE_URL)
    assert a_row["source_url"].startswith(aldi.BASE_URL)
    # The notes carry provenance, and the two provenances must differ.
    assert f"source={sprouts.TENANT.source_label}" in s_row["notes"]
    assert f"source={aldi.TENANT.source_label}" in a_row["notes"]


# --------------------------------------------------------------------------- #
# THE SERVICE-TYPE TRAP -- 'first shop' was a coincidence, not a rule
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "shops",
    [
        # Sprouts' live order for 27401: instore happens to be first.
        [("515202", "instore"), ("5201", "delivery"), ("5202", "pickup")],
        # ALDI's live order for 27401: instore is LAST. Taking the first shop
        # here prices a delivery basket.
        [("6823", "delivery"), ("22443", "pickup"), ("515201", "instore")],
        # Order is unspecified, so neither position may be relied on.
        [("22443", "pickup"), ("515201", "instore"), ("6823", "delivery")],
    ],
)
def test_shop_resolution_prefers_the_instore_shop_whatever_the_order(shops):
    """Asserted as a relationship: the chosen shop is the one whose service
    type is in-store -- never 'the one at index 0'."""
    rec = Recorder(shops)
    context = client_for(aldi.TENANT, rec).shop_context("27401")

    wanted = next(sid for sid, st in shops if st == ist.PREFERRED_SERVICE_TYPE)
    assert context.shop_id == wanted
    assert context.service_type == ist.PREFERRED_SERVICE_TYPE


def test_a_delivery_only_banner_still_resolves_but_says_what_it_got():
    """No in-store shop is not the same as no shop. The row still gets priced;
    the stats just have to be honest about which basket priced it."""
    rec = Recorder([("6823", "delivery"), ("22443", "pickup")])
    context = client_for(aldi.TENANT, rec).shop_context("27401")
    assert context is not None
    assert context.service_type != ist.PREFERRED_SERVICE_TYPE
    assert context.service_type is not None


def test_the_fallback_reports_an_unknown_service_type_rather_than_guessing():
    """``SimpleShopCollection`` cannot say what a shop is, so the answer is
    ``None`` -- unknown, never a hopeful 'instore'. A missing fact is None."""
    rec = Recorder([("6823", "delivery")], discoverable={})
    client = client_for(aldi.TENANT, rec)
    # No ShopCollectionScoped hash available -> the fallback path.
    context = client.shop_context("27401")

    assert "ShopCollectionScoped" not in rec.ops()
    assert "SimpleShopCollection" in rec.ops()
    assert context.shop_id == "6823"
    assert context.service_type is None


def test_stats_distinguish_a_verified_shop_from_an_assumed_one():
    verified = ist.ShopContext("515201", "430", "27401", service_type="instore")
    assumed = ist.ShopContext("6823", "430", "27401")
    assert verified.service_type is not None
    assert assumed.service_type is None


# --------------------------------------------------------------------------- #
# Tenant configuration
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("tenant", [sprouts.TENANT, aldi.TENANT])
def test_every_pinned_operation_has_a_hash_on_every_tenant(tenant):
    """The pin cannot self-heal, so its absence must be a loud, testable fact --
    and it is per tenant, because a tenant is where the pin lives now."""
    for operation in ist.PINNED_OPERATIONS:
        assert operation in tenant.pinned_hashes
        assert len(tenant.pinned_hashes[operation]) == 64


def test_each_tenant_pins_its_own_hash_rather_than_sharing_the_object():
    """They are currently equal in *value*, which is a measurement, not a law.

    Asserting they are distinct dicts is what keeps that true: if one banner's
    deploy rotates, only that tenant's literal changes, and its own canary is
    what catches it. A shared object would make one edit silently rebind both.
    """
    assert sprouts.TENANT.pinned_hashes is not aldi.TENANT.pinned_hashes


@pytest.mark.parametrize("tenant", [sprouts.TENANT, aldi.TENANT])
def test_a_tenant_either_has_a_canary_or_is_honest_about_why_not(tenant):
    """A canary proves the pin still works. A tenant with no panels cannot have
    one, and must not pretend -- so ``verify_pinned_hashes`` fails closed."""
    if tenant.canary_product_id is None:
        ok, why = ist.verify_pinned_hashes(tenant)   # no network: returns early
        assert ok is False
        assert "canary" in why.lower()
    else:
        assert tenant.canary_product_id.strip()


@pytest.mark.parametrize(
    "base_url,expected",
    [("https://shop.sprouts.com", "shop_sprouts_com"),
     ("https://www.aldi.us", "www_aldi_us")],
)
def test_sitemap_host_key_is_derived_from_the_host(base_url, expected):
    """Verified against both live tenants. ``shop_aldi_com`` -- the shape a
    naive copy of Sprouts' path would produce -- returns 403."""
    assert ist._host_key_from(base_url) == expected


def test_a_tenant_may_override_the_derived_sitemap_key():
    """A derivation that holds on two samples is a convenience, not a law."""
    tenant = ist.Tenant(
        store_key="x", merchant="X", base_url="https://example.com",
        retailer_slug="x", retailer_id="1", pinned_hashes={},
        canary_product_id=None, default_shop_id="1",
        sitemap_host_key="hand_written",
    )
    assert "hand_written" in tenant.sitemap_index


@pytest.mark.parametrize("tenant", [sprouts.TENANT, aldi.TENANT])
def test_tenant_urls_all_carry_that_tenants_slug(tenant):
    assert tenant.retailer_slug in tenant.storefront_path
    assert tenant.retailer_slug in tenant.product_path("1-x")
    assert tenant.product_page_url("1-x").startswith(tenant.base_url)
    assert tenant.product_page_url(None) is None


def test_the_two_tenants_do_not_share_an_identifier_namespace():
    """'21171551' as an ALDI id and as a Sprouts id are unrelated products
    (GFP-111). Without distinct namespaces they are the same string."""
    assert sprouts.TENANT.product_identifier_ns != aldi.TENANT.product_identifier_ns


# --------------------------------------------------------------------------- #
# The traps, exercised through the shared module rather than through Sprouts
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("tenant", [sprouts.TENANT, aldi.TENANT])
def test_a_per_lb_size_survives_a_parse_size_round_trip(tenant):
    """The 'per lb' pricing-unit trap, asserted as a relationship.

    ``savings.parse_size`` does not speak 'per lb'. Folding the normalised unit
    into the item name is what lets the shared parser read the size back, so
    price and size refer to the same quantity downstream. Asserted by
    round-tripping, never by comparing the string.
    """
    listing = ist.Listing(
        product_id="1", slug="1-beef", name="Grass Fed Ground Beef", brand="B",
        category="Meat", size="per lb", price=6.49, availability="InStock",
    )
    row, _fact = ist.listing_to_row(tenant, listing, None, "27401", NOW)

    assert savings.parse_size(listing.size) is None      # the dialect gap
    parsed = savings.parse_size(row["item_name"])        # closed by the fold
    assert parsed is not None
    assert parsed.base_unit == savings.WEIGHT
    assert parsed.base_quantity == pytest.approx(savings.GRAMS_PER_LB / savings.GRAMS_PER_OZ)
    assert row["sold_by"] == "WEIGHT"


def test_the_weight_trap_survives_the_move_to_the_shared_module():
    """A per-pound item must never multiply protein by servings-per-container.

    The same assertion test_sprouts.py makes, repeated here because the code it
    guards now lives in this module and would otherwise only be covered
    transitively.
    """
    facts = ist.Nutrition(21.0, "4 OZ  113 Gram", "30")
    assert ist.protein_per_100g(facts, "per lb") == pytest.approx(21.0 / 113.0 * 100.0)
    assert ist.protein_per_100g(ist.Nutrition(21.0, "PER CONTAINER", "30"), "per lb") is None


def test_pound_and_ounce_come_from_savings_not_from_a_local_literal():
    """One definition of a pound in this codebase. If this module grew its own,
    the two could drift and nobody would notice until a price was wrong."""
    grams = ist.serving_grams("16 oz")
    assert grams == pytest.approx(16 * savings.GRAMS_PER_OZ)
    assert grams == pytest.approx(savings.GRAMS_PER_LB)


def test_discovery_still_needs_both_decode_passes():
    """The blob is doubly URL-encoded and one pass finds nothing, *silently* --
    which degrades to stale pins with no error. Pinned as a behaviour."""
    sha = "a" * 64
    html = storefront_html({"SimpleShopCollection": sha})
    assert ist.discover_persisted_queries(html)["SimpleShopCollection"] == sha
    once = urllib.parse.unquote(html)
    assert ist.discover_persisted_queries(once) != {}
    assert ist.discover_persisted_queries("<html>nothing here</html>") == {}


def test_discovery_never_overwrites_a_pin_it_cannot_see():
    """``ProductNutritionalInfo`` appears in no server-rendered HTML, so a
    successful discovery must leave the pin exactly as it was."""
    rec = Recorder([("515202", "instore")])
    client = client_for(sprouts.TENANT, rec)
    client.discover()
    assert client.hashes["ProductNutritionalInfo"] == PIN
    assert client.discovered is True
    # ...while the discoverable ones did get refreshed.
    assert client.hashes["ShopCollectionScoped"] == DISCOVERABLE["ShopCollectionScoped"]


def test_a_pinned_but_discoverable_hash_is_refreshed_from_the_live_page():
    """The pins are two different things wearing one name.

    ``ProductNutritionalInfo`` is a true pin -- unfindable, so it must survive.
    ``SimpleShopCollection`` is merely a cold-start default for an operation the
    page *does* publish, and it rotates per deploy. Letting pins win
    unconditionally would look like extra safety while freezing the hash most
    likely to go stale, so the live value has to take precedence.
    """
    rotated = "b" * 64
    assert rotated != sprouts.FALLBACK_HASHES["SimpleShopCollection"]
    rec = Recorder([("515202", "instore")],
                   discoverable={"SimpleShopCollection": rotated})
    client = client_for(sprouts.TENANT, rec)

    assert client.hashes["SimpleShopCollection"] != rotated   # the cold default
    client.discover()
    assert client.hashes["SimpleShopCollection"] == rotated   # the live value
    assert client.hashes["ProductNutritionalInfo"] == PIN     # the real pin


def test_a_missing_hash_raises_rather_than_returning_empty():
    """Zero products and no error reads exactly like 'the store has nothing'."""
    client = ist.StorefrontClient(aldi.TENANT, client=object())
    client.hashes = {}
    with pytest.raises(ist.QueryNotAllowedError):
        client._query("ProductNutritionalInfo", {})


def test_a_rejected_hash_names_the_tenant_it_was_rejected_for():
    """Two tenants pin the same value today. When one rotates, the error has to
    say which one, or the fix gets applied to the wrong file."""
    client = ist.StorefrontClient(aldi.TENANT, client=object())
    client.hashes = {}
    with pytest.raises(ist.QueryNotAllowedError) as exc:
        client._query("ProductNutritionalInfo", {})
    assert aldi.TENANT.store_key in str(exc.value)


def test_the_two_pass_scrape_only_fetches_html_for_protein_bearing_products(conn):
    """The guard that keeps a Sprouts run off the product-page wall.

    ``/graphql`` carried 37,500 requests cleanly; product HTML died at ~2,300.
    So the expensive path must be reached only by what the cheap path qualified.
    Asserted as a relationship between the two request counts, not as a fixed
    number of pages.
    """
    catalogue = [f"{i}-item-{i}" for i in range(12)]
    rec = Recorder([("515202", "instore")], panel=panel_payload(protein=0.0))
    rows, _meta, stats = ist.scrape(
        sprouts.TENANT, postal_code="27401", conn=conn,
        client=client_for(sprouts.TENANT, rec), now=NOW, slugs=catalogue,
    )
    html_hits = [r for r in rec.requests if "/products/" in r.url.path]

    assert stats["products_seen"] == len(catalogue)
    assert stats["no_nutrition_panel"] == len(catalogue)   # protein 0 disqualifies
    assert stats["priceable"] == 0
    assert html_hits == []
    assert rows == []


def test_a_protein_bearing_product_does_reach_the_price_pass(conn):
    """The mirror of the guard above -- it must gate, not block."""
    rec = Recorder([("515202", "instore")], panel=panel_payload(protein=13.0))
    rows, _meta, stats = ist.scrape(
        sprouts.TENANT, postal_code="27401", conn=conn,
        client=client_for(sprouts.TENANT, rec), now=NOW, slugs=["1-a", "2-b"],
    )
    assert stats["priceable"] == 2
    assert len(rows) == 2
    assert all(r["dollar_price"] is not None for r in rows)
    # The density came from the panel's serving mass, so a food fact exists.
    assert stats["with_protein"] == 2


# --------------------------------------------------------------------------- #
# THE MISSING-SIZE TRAP -- the bug that only running the app could find
#
# Rows looked perfect (priced, protein present, food_nutrients populated) and
# `gplan cheapest` still said "Nothing to rank yet", because the size never
# reached `item_name` -- the only place `savings.cost_per_gram_protein` looks.
# 32 of 155 real rows were rankable. Asserted below as the relationship that
# would have caught it, over both tenants and both denominations.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("tenant", [sprouts.TENANT, aldi.TENANT])
@pytest.mark.parametrize(
    "size",
    ["7.5 oz", "5 oz", "11 oz", "1 lb", "16 OZ (1 LB)",   # UNIT packages
     "per lb", "per pound", "Per Lb",                      # WEIGHT rates
     "6 ct", "3 fl oz", "2 L"],                            # count and volume
)
def test_a_listing_with_a_size_always_yields_a_parseable_item_name(tenant, size):
    """The invariant, not the spelling.

    Whatever the size says, it has to survive into ``item_name``, because that
    is the only channel the ranking reads. A test on the string format would
    have passed while the app stayed broken.
    """
    listing = ist.Listing(
        product_id="1", slug="1-x", name="Some Clean Product Name", brand="B",
        category="C", size=size, price=4.99, availability="InStock",
    )
    row, _fact = ist.listing_to_row(tenant, listing, None, "27401", NOW)
    assert savings.parse_size(row["item_name"]) is not None


@pytest.mark.parametrize("tenant", [sprouts.TENANT, aldi.TENANT])
@pytest.mark.parametrize("size", ["7.5 oz", "1 lb", "per lb"])
def test_a_weight_size_stays_a_weight_after_the_fold(tenant, size):
    """Surviving is not enough -- ``cost_per_gram_protein`` only acts on WEIGHT
    sizes, so a weight that folds in as a count would still not rank."""
    listing = ist.Listing(
        product_id="1", slug="1-x", name="Clean Name", brand="B", category="C",
        size=size, price=4.99, availability="InStock",
    )
    row, _fact = ist.listing_to_row(tenant, listing, None, "27401", NOW)
    parsed = savings.parse_size(row["item_name"])
    assert parsed is not None and parsed.base_unit == savings.WEIGHT


def test_a_name_that_already_carries_a_weight_is_not_doubled_up():
    """Appending to a self-describing name would leave the parser choosing
    between two answers. It does not have to guess."""
    listing = ist.Listing(
        product_id="1", slug="1-x", name="Peanut Butter 16 oz", brand="B",
        category="C", size="16 oz", price=4.99, availability="InStock",
    )
    row, _fact = ist.listing_to_row(sprouts.TENANT, listing, None, "27401", NOW)
    assert row["item_name"].count("16 oz") == 1


def test_a_listing_with_no_size_is_left_alone_rather_than_decorated():
    """No size is a fact about the product. Inventing one would be worse than
    leaving the row unrankable."""
    listing = ist.Listing(
        product_id="1", slug="1-x", name="Loose Bananas", brand="B",
        category="Produce", size=None, price=0.59, availability="InStock",
    )
    row, _fact = ist.listing_to_row(sprouts.TENANT, listing, None, "27401", NOW)
    assert row["item_name"] == "Loose Bananas"


# --------------------------------------------------------------------------- #
# THE IMPOSSIBLE-DENSITY TRAP -- wrong on exactly the items that dominate
#
# The product ranks by CHEAPEST cost per gram of protein, so an inflated
# density does not sit harmlessly in the tail: it sorts straight to the top of
# the recommendation the customer acts on. Same failure shape as GFP-98.
# --------------------------------------------------------------------------- #
def test_no_row_can_ever_claim_an_impossible_protein_density():
    """The invariant, asserted over a grid rather than by example.

    For every combination of the awkward shapes this platform really prints,
    the density is either ``None`` or in ``(0, 100]``. That single assertion is
    the guard that was missing, and it holds without anyone having to have
    thought of the specific jerky multi-pack that broke it.
    """
    proteins = [None, 0.0, -1.0, 0.5, 6.0, 8.0, 21.0, 60.0, 500.0]
    servings = ["Varied", "", "1", "8", "24", "300"]
    serving_sizes = [None, "PER CONTAINER", "1 BAR  40 Gram", "8 oz (227g)",
                     "3 OZ  85 Gram", "0.1 Gram", "12 FL OZ. (1 PINT)"]
    sizes = [None, "per lb", "1 oz", "0.55 oz", "7.5 oz", "6 ct", "3 fl oz"]

    for protein in proteins:
        for count in servings:
            for serving in serving_sizes:
                for size in sizes:
                    facts = ist.Nutrition(protein, serving, count)
                    density = ist.protein_per_100g(facts, size)
                    assert density is None or 0 < density <= ist.MAX_DENSITY_G_PER_100G, (
                        f"{protein=} {serving=} {count=} {size=} -> {density}"
                    )


@pytest.mark.parametrize(
    "protein,count,size,expected_raw",
    [
        # The two real rejections from the live 155-row Sprouts run, rebuilt
        # from the shape that produced them: `size` is the per-unit weight while
        # `servingsPerContainer` counts the whole multi-pack, so the two are
        # describing different things and their product is nonsense.
        (8.0, "24", "1 oz", 677.3),      # Berski ancestral blend
        (6.0, "8", "0.55 oz", 306.7),    # Country Archer turkey sticks
    ],
)
def test_the_real_impossible_densities_are_rejected_not_clamped(
    protein, count, size, expected_raw
):
    """Rejected, because 100.0 would be an invented number and still wrong --
    the true density is unknown, not maximal (rule 1)."""
    facts = ist.Nutrition(protein, "PER CONTAINER", count)
    raw = ist.raw_protein_per_100g(facts, size)

    assert raw == pytest.approx(expected_raw, rel=0.01)   # the bad arithmetic
    assert ist.protein_per_100g(facts, size) is None      # refused, not clamped
    assert ist.rejected_density(facts, size) == pytest.approx(raw)


def test_a_genuinely_high_density_is_not_thrown_out_with_the_impossible_ones():
    """Pure isolates really do reach ~90. Rejecting a true 92 to catch a fake
    677 would trade a true positive for nothing."""
    facts = ist.Nutrition(27.0, "30 Gram", "1")
    assert ist.protein_per_100g(facts, "1 lb") == pytest.approx(90.0)
    assert ist.rejected_density(facts, "1 lb") is None


def test_a_rejected_density_writes_no_food_fact_but_leaves_an_audit_trail():
    """Dropping it is right; dropping it silently is not."""
    listing = ist.Listing(
        product_id="1", slug="1-jerky", name="Mini Turkey Sticks", brand="B",
        category="Snacks", size="0.55 oz", price=7.99, availability="InStock",
    )
    facts = ist.Nutrition(6.0, "PER CONTAINER", "8")
    row, fact = ist.listing_to_row(sprouts.TENANT, listing, facts, "27401", NOW)

    assert fact is None
    assert "protein_per_100g=" not in row["notes"]
    # The note carries the refused figure itself, so a bad label can be audited
    # without re-scraping. Asserted against the computed value rather than a
    # literal, so the audit trail cannot drift away from the arithmetic.
    refused = ist.rejected_density(facts, listing.size)
    assert f"protein_per_100g_rejected={refused:.2f}" in row["notes"]
    assert refused > ist.MAX_DENSITY_G_PER_100G


def test_rejections_are_counted_in_stats(conn):
    """The no-silent-caps rule. A source that starts disagreeing with itself
    must be visible, not merely quieter than it was last week."""
    # No serving mass and a tiny package size forces the package route, which is
    # the one that produced both real rejections.
    rec = Recorder(
        [("515202", "instore")],
        panel=panel_payload(protein=6.0, serving="PER CONTAINER", servings="8"),
        product_size="0.55 oz",
    )
    _rows, _meta, stats = ist.scrape(
        sprouts.TENANT, postal_code="27401", conn=conn,
        client=client_for(sprouts.TENANT, rec), now=NOW, slugs=["1-a", "2-b"],
    )
    assert stats["density_rejected_implausible"] == 2
    assert stats["with_protein"] == 0


# --------------------------------------------------------------------------- #
# THE FABRICATED-CENTROID TRAP -- serves() said yes to the entire country
# --------------------------------------------------------------------------- #
def test_an_unknown_zip_has_no_coordinates_rather_than_a_plausible_guess():
    """The old code returned the centre of the US for every unknown ZIP.

    That is not a lossy answer, it is a wrong one: the platform selects by
    coordinates, so the guess resolved to a real store in Missouri.
    """
    assert ist.zip_centroid("27401") == ist.ZIP_CENTROIDS["27401"]
    assert ist.zip_centroid("99501", home="19103") == ist.ZIP_CENTROIDS["19103"]
    with pytest.raises(ist.UnknownLocationError):
        ist._require_centroid("99501", home="00000")


@pytest.mark.parametrize("tenant", [sprouts.TENANT, aldi.TENANT])
def test_serves_says_unknown_rather_than_yes_for_an_ungeocodable_zip(tenant):
    """``None`` is the GFP-257 answer for "the question cannot be put".

    Before the fix this returned ``True`` for every ZIP in the country, for both
    banners -- so the Run scrape dialog would offer every storefront in every
    market, and a scrape would file another state's prices under the user's ZIP.
    """
    rec = Recorder([("515201", "instore")])
    assert ist.serves(tenant, "99501") is None
    # And it never even asked, because it could not form the question.
    assert rec.requests == []


def test_the_canonical_fallback_is_switched_off():
    """The storefront sends ``allowCanonicalFallback: True`` because the web UI
    would rather show something than an empty page. This client would rather be
    right: with the fallback on, ALDI answers Anchorage with a Missouri shop."""
    rec = Recorder([("515201", "instore")])
    client_for(aldi.TENANT, rec).shop_context("27401")
    assert rec.variables_for("ShopCollectionScoped")["allowCanonicalFallback"] is False


def test_each_market_sends_its_own_coordinates():
    """Two ZIPs must not resolve through the same point, or the second market
    silently gets the first one's store."""
    rec = Recorder([("515201", "instore")])
    client = client_for(aldi.TENANT, rec)
    client.shop_context("27401")
    first = rec.variables_for("ShopCollectionScoped")["coordinates"]
    rec.requests.clear()
    client.shop_context("19103")
    second = rec.variables_for("ShopCollectionScoped")["coordinates"]
    assert first != second


def test_absent_panel_is_an_absence_not_an_error():
    """ALDI returns this shape for its entire catalogue, and Sprouts for about
    two thirds of its. It is ordinary data, and must never raise."""
    assert ist.nutrition_from_payload(
        {"data": {"productNutritionalInfo": {"nutritionalInfo": None}}}
    ) is None
    assert ist.nutrition_from_payload({"data": {}}) is None
