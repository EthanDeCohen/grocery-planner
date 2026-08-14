"""Publix storefront tenant (GFP-293) -- the candidate filter and the key collisions.

The platform behaviour is tested in test_sprouts.py / test_instacart_storefront.py
and is not re-tested here. What is Publix-specific is that this shop now has
THREE registered feeds, and that its catalogue is large enough that handing it
to the platform unfiltered is the difference between a ten-minute scrape and an
intractable one.
"""
from __future__ import annotations

import pytest

from grocery_planner import service
from grocery_planner.scrapers import SCRAPERS, publix_storefront as ps


# --------------------------------------------------------------------------- #
# Three feeds, one shop -- the collision that already cost a debugging session
# --------------------------------------------------------------------------- #
def test_both_publix_feeds_are_reachable():
    """`publix` is the Flipp weekly ad, `publix-storefront` the Instacart one.

    There were three until GFP-304 deleted the Parse.bot `publix-catalog`, which
    this feed superseded -- same shop, free, better data.

    The registry is last-write-wins and the Flipp banners register LAST, so a
    module whose key collides with a banner is silently shadowed and unreachable
    from the CLI. That is what happened to sprouts, and it is why the second
    feed carries its own SCRAPER_KEY.
    """
    assert {"publix", "publix-storefront"} <= set(SCRAPERS)
    assert "publix-catalog" not in SCRAPERS, "the Parse.bot feed is gone (GFP-304)"
    assert SCRAPERS["publix-storefront"] is ps
    # The banner must NOT have been displaced by the second source.
    assert SCRAPERS["publix"] is not ps


def test_the_feeds_write_to_one_store_under_different_sources():
    """Same shop, different `source`, so a scrape of one cannot delete the other.

    run_scrape scopes its replace to (store, source, postal_code). Equal
    store_key is the point -- the deals belong to the same Publix -- and unequal
    source is what keeps them from overwriting each other on every run.
    """
    banner = SCRAPERS["publix"]
    assert ps.STORE_KEY == banner.STORE_KEY == "publix"
    assert ps.SOURCE != getattr(banner, "SOURCE", "scrape")
    assert ps.SCRAPER_KEY != getattr(banner, "SCRAPER_KEY", "publix")


def test_storefront_needs_no_credential():
    ready, why = ps.readiness()
    assert ready is True
    assert "no credentials" in why


# --------------------------------------------------------------------------- #
# name_from_slug
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("slug,want", [
    ("54638-lundberg-family-farms-organic-california-brown-jasmine-rice-32-oz",
     "lundberg family farms organic california brown jasmine rice 32 oz"),
    ("18581912-idahoan-smokey-cheese-bacon-mashed-potatoes-4-oz",
     "idahoan smokey cheese bacon mashed potatoes 4 oz"),
    # An id-less slug must not lose its first word to the prefix rule.
    ("just-bare-chicken-tenders", "just bare chicken tenders"),
])
def test_name_from_slug_strips_only_the_id_prefix(slug, want):
    assert ps.name_from_slug(slug) == want


# --------------------------------------------------------------------------- #
# The candidate filter -- the reason this module is not three constants
# --------------------------------------------------------------------------- #
def test_candidates_drop_what_cannot_be_a_protein_buy():
    """Measured on the live catalogue: 108,978 unique slugs -> 7,725 (7.1%).

    `limit` does not save you here -- it bounds the PRICING pass, while the
    nutrition pass walks every slug it is given, one GraphQL call each.
    """
    slugs = [
        "1-just-bare-natural-fresh-chicken-tenders-14-0-oz",
        "2-festive-ground-turkey-1-0-lb",
        "3-lundberg-family-farms-organic-brown-jasmine-rice-32-oz",
        "4-charmin-ultra-soft-toilet-paper-12-rolls",
        "5-wholesome-sugar-turbinado-24-oz",
    ]
    kept = ps.protein_candidate_slugs(slugs)

    assert slugs[0] in kept and slugs[1] in kept
    for junk in slugs[2:]:
        assert junk not in kept, f"{junk} would cost a GraphQL call for nothing"


def test_candidates_no_longer_deduplicate_here():
    """De-duplication moved to the platform (GFP-294), where every tenant gets it.

    Asserted rather than just deleted: if a future edit re-adds a `seen` set
    here, that is a workaround creeping back into one tenant for a problem the
    platform already solves.
    """
    slug = "1-just-bare-natural-fresh-chicken-tenders-14-0-oz"
    assert ps.protein_candidate_slugs([slug, slug]) == [slug, slug]


def test_candidates_preserve_input_order():
    """Reproducibility: a bounded run must walk the catalogue the same way twice.

    Sorting, or a set, would make successive `--limit` runs price a different
    arbitrary slice each time -- the ingestion-side reading of GFP-224's
    same-inputs-same-plan invariant.
    """
    slugs = [
        "3-festive-ground-turkey-1-0-lb",
        "1-boar-s-head-chicken-sausage-chorizo-12-oz",
        "2-perdue-fresh-ground-chicken-breast-1-lb",
    ]
    assert ps.protein_candidate_slugs(slugs) == slugs
    assert ps.protein_candidate_slugs(slugs) == ps.protein_candidate_slugs(slugs)


def test_non_meat_protein_survives_the_filter():
    """protein_kind names SPECIES, so it answers 'unknown' for every one of these.

    Filtering on it alone made Publix a meat-only source while the GUI offers an
    "Overall protein" tab -- a coverage cap the user cannot see, because the tab
    would just look sparse at Publix and nobody would know why. Measured: the
    non-meat vocabulary recovers 8,475 candidates, 7,725 -> 15,669.
    """
    slugs = [
        "1-fage-total-greek-yogurt-0-percent-32-oz",
        "2-eggland-s-best-large-white-eggs-12-ct",
        "3-optimum-nutrition-gold-standard-whey-protein-powder-vanilla",
        "4-kraft-sharp-cheddar-cheese-block-8-oz",
        "5-skippy-peanut-butter-creamy-16-oz",
        "6-tofu-extra-firm-14-oz",
    ]
    kept = ps.protein_candidate_slugs(slugs)
    assert kept == slugs, "a real protein source was dropped as 'unknown'"


def test_the_non_meat_match_is_word_bounded():
    """'egg' must not match 'eggplant'.

    A substring test puts a vegetable into a protein ranking, and 'bean' would
    likewise catch 'beanie'. This is the cheapest possible guard against the
    laziest possible implementation of the term list.
    """
    assert ps.protein_candidate_slugs(["1-fresh-eggplant-each"]) == []
    assert ps._has_non_meat_protein("eggland s best large white eggs 12 ct")
    assert not ps._has_non_meat_protein("fresh eggplant each")


def test_the_filter_is_permissive_by_design():
    """A supplement survives the name-level filter, and that is deliberate.

    A name cannot distinguish fish oil from fish. Dropping a real protein to
    avoid a supplement is the worse error, and the pipeline already rejects an
    implausible density downstream. If this ever needs tightening it belongs in
    protein_kind, where every source benefits -- so this test exists to make a
    per-tenant blocklist here an obvious deviation rather than a quiet one.
    """
    assert ps.protein_candidate_slugs(["1-nature-made-fish-oil-1200-mg-100-ct"])


# --------------------------------------------------------------------------- #
# scrape() wiring
# --------------------------------------------------------------------------- #
def test_scrape_filters_the_catalogue_before_the_nutrition_pass(monkeypatch):
    """The whole point: the platform must never see the raw catalogue."""
    seen = {}

    def fake_scrape(tenant, **kwargs):
        seen["slugs"] = list(kwargs["slugs"])
        return [], {}, {}

    class FakeClient:
        def discover(self): pass
        def close(self): pass
        def product_slugs(self):
            return [
                "1-perdue-fresh-ground-chicken-breast-1-lb",
                "2-charmin-ultra-soft-toilet-paper-12-rolls",
                "1-perdue-fresh-ground-chicken-breast-1-lb",   # a repeat
            ]

    monkeypatch.setattr(ps._platform, "scrape", fake_scrape)
    ps.scrape(client=FakeClient())

    # Filtering happens here; de-duplication is the platform's job (GFP-294),
    # so the repeat survives this call and is dropped downstream.
    assert seen["slugs"] == [
        "1-perdue-fresh-ground-chicken-breast-1-lb",
        "1-perdue-fresh-ground-chicken-breast-1-lb",
    ], "the raw catalogue reached the platform unfiltered"


def test_explicit_slugs_bypass_the_filter(monkeypatch):
    """Reproducing one product must not have it filtered out from under you."""
    seen = {}

    def fake_scrape(tenant, **kwargs):
        seen["slugs"] = list(kwargs["slugs"])
        return [], {}, {}

    class FakeClient:
        def discover(self): pass
        def close(self): pass
        def product_slugs(self):  # pragma: no cover - must not be consulted
            raise AssertionError("product_slugs() called despite explicit slugs")

    monkeypatch.setattr(ps._platform, "scrape", fake_scrape)
    ps.scrape(client=FakeClient(), slugs=["9-charmin-ultra-soft-toilet-paper"])

    assert seen["slugs"] == ["9-charmin-ultra-soft-toilet-paper"]


# --------------------------------------------------------------------------- #
# Tenant identity -- read back from the live storefront, never invented
# --------------------------------------------------------------------------- #
def test_tenant_ids_match_what_the_live_storefront_reported():
    """Confirmed 2026-08-14 from the storefront's Apollo cache:
    {"retailerId":"57","retailerSlug":"publix","zoneId":"430"} and
    {"postalCode":"27401","shopId":"3548"}.
    """
    assert ps.TENANT.retailer_id == "57"
    assert ps.TENANT.retailer_slug == "publix"
    assert ps.TENANT.default_zone_id == "430"
    assert ps.TENANT.default_shop_id == "3548"
    assert ps.TENANT.base_url == "https://delivery.publix.com"


def test_no_canary_is_declared_rather_than_invented():
    """A fabricated canary turns "no product to test" into a false pass."""
    assert ps.CANARY_PRODUCT_ID is None
    assert ps.TENANT.canary_product_id is None


def test_sitemap_host_key_follows_the_delivery_subdomain():
    """delivery.publix.com, not www -- the tenant lives on its own host."""
    assert ps.TENANT.sitemap_host_key == "delivery_publix_com"
    assert "delivery_publix_com" in ps.TENANT.sitemap_index


def test_scrape_builds_its_own_client_bound_to_this_tenant(monkeypatch):
    """The default-client path, which no other test exercises.

    Every other test injects a client, so `client or StorefrontClient(...)` was
    never run. GFP-305 removed the PublixStorefrontClient subclass that used to
    supply TENANT implicitly, and the bare platform client requires it
    positionally -- so an omission here is a TypeError on the one path a real
    scrape actually takes.
    """
    built = {}

    class FakeClient:
        def __init__(self, tenant, **kw):
            built["tenant"] = tenant
        def discover(self): pass
        def close(self): pass
        def product_slugs(self): return []

    monkeypatch.setattr(ps._platform, "StorefrontClient", FakeClient)
    monkeypatch.setattr(ps._platform, "scrape", lambda tenant, **kw: ([], {}, {}))
    ps.scrape()

    assert built["tenant"] is ps.TENANT, (
        "scrape() built a client without binding it to the Publix tenant"
    )
