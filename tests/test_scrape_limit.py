"""``--limit``: bounding a crawl that a source is rate-policing (GFP-263).

Why this exists at all. ALDI's product-page path returns 403 after roughly
three requests, the AIMD pacer correctly backs off to its 30 s ceiling, and a
full 1,200-page run then takes about ten hours. Nothing is written to the
database until a scrape RETURNS -- ``run_scrape`` holds every row in memory and
does one atomic replace, because the GFP-67 guards can only refuse a bad scrape
if the full row count is known before the DELETE. So an unbounded run against a
hostile source is a ten-hour bet that writes nothing if it is interrupted.

A caller therefore has to be able to ask for a smaller slice. The interesting
part is not the happy path; it is what happens when the limit CANNOT be
honoured, because silently dropping it would let an operator ask for 200
products, wait, and get the full crawl they were explicitly trying to avoid.
"""
from __future__ import annotations

import pytest

from grocery_planner import service
from grocery_planner.service import ingest
from grocery_planner.scrapers import aldi, foodlion, lidl_catalogue, sprouts, traderjoes


def _row(name: str) -> dict:
    row = {c: None for c in __import__(
        "grocery_planner.importers", fromlist=["x"]).DEAL_COLUMNS}
    row.update(item_name=name, sale_price=1.99, dollar_price=1.99,
               deal_type="Shelf Price", sub_category="Meat")
    return row


class _Bounded:
    """A scraper module that takes a limit and honours it."""

    DEFAULT_POSTAL_CODE = "27401"
    MERCHANT = "Bounded Store"

    def __init__(self, available: int = 10):
        self.available = available
        self.seen: list[int | None] = []

    def scrape(self, postal_code=None, limit=None):
        self.seen.append(limit)
        rows = [_row(f"Chicken {i}") for i in range(self.available)][:limit]
        return rows, {"id": "b"}, {"total": len(rows), "price_limit": limit}


class _Unbounded:
    """A scraper module with no limit parameter -- every Flipp banner."""

    DEFAULT_POSTAL_CODE = "27401"
    MERCHANT = "Unbounded Store"

    def scrape(self, postal_code=None, include_coupons=True):
        return [_row("Chicken 0")], {"id": "u"}, {"total": 1}


@pytest.fixture
def bounded(monkeypatch):
    fake = _Bounded()
    monkeypatch.setitem(ingest.SCRAPERS, "foodlion", fake)
    return fake


@pytest.fixture
def unbounded(monkeypatch):
    fake = _Unbounded()
    monkeypatch.setitem(ingest.SCRAPERS, "foodlion", fake)
    return fake


# --------------------------------------------------------------------------- #
# The bound reaches the scraper
# --------------------------------------------------------------------------- #
def test_the_limit_reaches_the_scraper(conn, bounded):
    service.run_scrape("foodlion", postal_code="27401", conn=conn, limit=3)
    assert bounded.seen == [3]


def test_only_the_bounded_slice_is_stored(conn, bounded):
    service.run_scrape("foodlion", postal_code="27401", conn=conn, limit=3)
    stored = conn.execute(
        "SELECT COUNT(*) n FROM deals WHERE store='foodlion'").fetchone()["n"]
    assert stored == 3


def test_no_limit_means_the_argument_is_not_passed_at_all(conn, monkeypatch):
    """Omitted, not ``limit=None``. The distinction is load-bearing.

    ALDI's ``scrape`` defaults to :data:`aldi.DEFAULT_PRICE_LIMIT` rather than
    to ``None``, deliberately: unbounded there means walking into the wall.
    Forwarding an explicit ``limit=None`` would overwrite that considered
    default with "no bound" -- the exact opposite of what the module decided --
    and no assertion on the received VALUE can tell the two apart, so this
    asserts on the keyword's presence.
    """
    # A sentinel default, not **kwargs: `supports_limit` reads the signature,
    # and a `**kwargs` scraper is deliberately treated as NOT supporting a
    # limit -- it would swallow the argument silently, which is the failure
    # this whole feature exists to prevent.
    UNSET = object()

    class _RecordsWhetherPassed:
        DEFAULT_POSTAL_CODE = "27401"
        MERCHANT = "Recorder"

        def __init__(self):
            self.calls: list[object] = []

        def scrape(self, postal_code=None, limit=UNSET):
            self.calls.append(limit)
            return [_row("Chicken")], {"id": "r"}, {"total": 1}

    fake = _RecordsWhetherPassed()
    monkeypatch.setitem(ingest.SCRAPERS, "foodlion", fake)

    service.run_scrape("foodlion", postal_code="27401", conn=conn)
    assert fake.calls == [UNSET], "run_scrape passed a limit nobody asked for"

    service.run_scrape("foodlion", postal_code="27401", conn=conn, limit=1)
    assert fake.calls[-1] == 1


# --------------------------------------------------------------------------- #
# A limit that cannot be honoured is refused, never ignored
# --------------------------------------------------------------------------- #
def test_a_limit_on_a_scraper_without_one_is_an_error(conn, unbounded):
    """THE ONE THAT MATTERS. Silently ignoring it is the bad outcome: the
    operator waits out the very crawl they asked to avoid."""
    with pytest.raises(service.UnsupportedLimitError):
        service.run_scrape("foodlion", postal_code="27401", conn=conn, limit=5)


def test_the_refusal_names_the_scrapers_that_do_take_one(conn, unbounded):
    with pytest.raises(service.UnsupportedLimitError) as exc:
        service.run_scrape("foodlion", postal_code="27401", conn=conn, limit=5)
    message = str(exc.value)
    assert "Unbounded Store" in message
    for key in ("aldi-storefront", "lidl-catalogue", "traderjoes"):
        assert key in message


def test_a_refused_limit_writes_nothing(conn, unbounded):
    """The refusal must happen before the DELETE, not after it."""
    service.run_scrape("foodlion", postal_code="27401", conn=conn)   # seed one row
    before = conn.execute(
        "SELECT COUNT(*) n FROM deals WHERE store='foodlion'").fetchone()["n"]
    with pytest.raises(service.UnsupportedLimitError):
        service.run_scrape("foodlion", postal_code="27401", conn=conn, limit=5)
    after = conn.execute(
        "SELECT COUNT(*) n FROM deals WHERE store='foodlion'").fetchone()["n"]
    assert after == before == 1


# --------------------------------------------------------------------------- #
# The capability is read off the signature, not maintained as a list
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("module", [aldi, lidl_catalogue, sprouts, traderjoes])
def test_the_crawling_scrapers_all_accept_a_bound(module):
    """Each of these walks product pages one at a time against a source that
    can throttle. A crawler with no bound is the ten-hour bet."""
    assert service.supports_limit(module)


def test_a_flipp_banner_does_not_pretend_to_accept_one(unbounded):
    assert not service.supports_limit(foodlion)


def test_the_supported_list_is_derived_not_hand_maintained():
    """It is computed from the registry, so a scraper that gains or loses a
    bound cannot drift out of agreement with the error message or the help."""
    supported = service.scrapers_supporting_limit()
    assert supported == sorted(supported)
    assert "aldi-storefront" in supported
    assert "foodlion" not in supported
