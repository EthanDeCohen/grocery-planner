"""The registry must not silently shadow a hand-written scraper.

Written after a real, live failure. `scrapers/sprouts.py` was complete,
imported cleanly, and passed all 48 of its own tests -- while being completely
unreachable from the CLI, because `flipp_banners.MODULES` already contained a
`sprouts` banner and `__init__` applies it *after* the hand-written modules::

    SCRAPERS = {SCRAPER_KEY or STORE_KEY: m for m in _MODULES}
    SCRAPERS.update(flipp_banners.MODULES)      # last write wins

`gplan scrape sprouts` therefore ran the weekly ad. Nothing raised, nothing
logged, and the store table showed a plausible row count from the wrong feed.

Unit tests could not catch it: every module was individually correct. Only
running the app did. So the guard belongs here, on the *relationship* between
the two sources of registry keys, rather than on any one module's spelling.
"""
from __future__ import annotations

import pytest

from grocery_planner import scrapers
from grocery_planner.scrapers import flipp_banners


def _hand_written():
    """The modules `__init__` lists explicitly, before banners are merged."""
    return dict(
        (getattr(m, "SCRAPER_KEY", m.STORE_KEY), m) for m in scrapers._MODULES
    )


def test_no_hand_written_module_is_shadowed_by_a_banner():
    """The bug itself. A collision here means a module is dead code at runtime.

    If this fails, the fix is a distinct `SCRAPER_KEY` on the hand-written
    module (see `kroger.py`'s `harristeeter-api` and `sprouts.py`'s
    `sprouts-storefront`) -- NOT removing the banner. The two feeds complement
    each other and `__init__` says neither may evict the other.
    """
    collisions = sorted(set(_hand_written()) & set(flipp_banners.MODULES))
    assert collisions == [], (
        f"these hand-written scrapers are overwritten by Flipp banners and are "
        f"unreachable from the CLI: {collisions}"
    )


def test_every_hand_written_module_survives_into_the_registry():
    """Stated as identity, not membership: the key must resolve to *this*
    module object, not merely to something with the same name."""
    for key, module in _hand_written().items():
        assert scrapers.SCRAPERS[key] is module, (
            f"registry key {key!r} does not resolve to its own module"
        )


def test_two_sources_for_one_store_are_distinguished_by_source():
    """`ingest.run_scrape` scopes its replace to (store, source, postal_code).

    So any two scrapers sharing a `STORE_KEY` MUST differ in `SOURCE`, or each
    run silently deletes the other's rows. Asserted over whatever pairs exist
    rather than over a hard-coded list, so a future third feed is covered the
    day it is added.
    """
    by_store: dict[str, list[tuple[str, str]]] = {}
    for key, module in scrapers.SCRAPERS.items():
        store = scrapers.store_key_for(module, key)
        by_store.setdefault(store, []).append((key, scrapers.source_for(module)))

    for store, feeds in by_store.items():
        if len(feeds) < 2:
            continue
        sources = [source for _key, source in feeds]
        assert len(set(sources)) == len(sources), (
            f"store {store!r} has feeds sharing a source and will clobber "
            f"itself on every scrape: {feeds}"
        )


@pytest.mark.parametrize("expected", ["sprouts-storefront", "harristeeter-api"])
def test_the_known_second_sources_are_registered_under_their_own_keys(expected):
    """Both learned the same lesson; pin both so neither regresses."""
    assert expected in scrapers.SCRAPERS


def test_the_banner_feeds_still_exist_alongside_them():
    """The fix must not have been 'delete the banner'."""
    assert "sprouts" in scrapers.SCRAPERS
    assert "harristeeter" in scrapers.SCRAPERS
    assert scrapers.store_key_for(scrapers.SCRAPERS["sprouts-storefront"]) == "sprouts"
