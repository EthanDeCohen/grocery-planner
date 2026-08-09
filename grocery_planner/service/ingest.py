"""Scrape ingestion (GFP-14): pulling fresh deals from a store scraper into SQLite.

Split out of the former ``service.py`` module (GFP-43) as the front-end-agnostic
service layer grows to cover customers, nutrition and ingest. The CLI (``cli``)
and the PySide6 GUI (``gui``) both drive scraping through :func:`run_scrape` so
the scrape + persist logic lives in exactly one place.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .. import config, db, importers, logs, matching, records, scrapers
from ..scrapers import SCRAPERS


class UnknownStoreError(ValueError):
    """Raised when a store key has no registered scraper."""


class ScrapeGuardError(RuntimeError):
    """Base class for run_scrape's replace-guards (GFP-67).

    The whole product's data comes from one undocumented, unauthenticated
    Flipp endpoint (see ``scrapers/base.py``). If that endpoint changes shape
    and a parse silently yields nothing (or next to nothing) *without*
    raising, the old code would still DELETE this store+ZIP's rows and have
    nothing real to put back — total silent data loss. Raising one of these
    instead means the DELETE never happens: existing ``deals`` and
    ``price_history`` rows for this (store, postal_code) are left exactly as
    they were. Pass ``force=True`` to :func:`run_scrape` to replace anyway,
    once you've independently confirmed the low count is real and not
    upstream breakage.
    """


class EmptyScrapeError(ScrapeGuardError):
    """A scrape parsed to zero deal rows.

    Distinct from GFP-16's "no current weekly flyer" ``RuntimeError`` (raised
    earlier, inside the scraper, before any rows exist): this covers a
    well-formed response that *found* a flyer/coupons but the parse produced
    nothing usable from it — the more dangerous case because it looks like a
    successful run.
    """


class ImplausibleCollapseError(ScrapeGuardError):
    """A scrape returned far fewer rows than the last known-good capture.

    E.g. 900 rows yesterday and 3 today. That is a parser/response-shape
    problem, not a quiet ad week — see :data:`_COLLAPSE_RATIO` for the exact
    rule and why it was chosen.
    """


@dataclass(frozen=True)
class ScraperStatus:
    """Whether a REGISTERED scraper is actually usable right now, and why not.

    GFP-4 (Whole Foods) introduced the first store where those two things
    differ: it's registered in ``SCRAPERS`` the moment its module ships, but
    it needs an out-of-band, human-minted session cookie before a scrape can
    do anything (see ``scrapers/wholefoods.py``'s module docstring) --
    unlike the Flipp-sourced scrapers, which need no setup at all. Before
    this, "registered" and "ready to scrape" were the same question; this
    type is what lets code ask them separately.
    """

    key: str
    ready: bool
    reason: str = ""


def scraper_status(store_key: str) -> ScraperStatus:
    """The readiness of one registered scraper.

    A scraper module MAY define ``readiness() -> (bool, str)``; one that
    doesn't (every Flipp-sourced store today) is always ready, so this is a
    pure addition -- no existing scraper module needs to change. Raises
    :class:`UnknownStoreError` for a store key with no registered scraper at
    all (a different, stronger kind of "not usable" than "registered but not
    configured").
    """
    scraper = SCRAPERS.get(store_key)
    if scraper is None:
        raise UnknownStoreError(store_key)
    check = getattr(scraper, "readiness", None)
    if check is None:
        return ScraperStatus(store_key, True, "")
    ready, reason = check()
    return ScraperStatus(store_key, bool(ready), reason or "")


def available_scrapers() -> list[str]:
    """Sorted list of store keys that can *actually* be scraped right now.

    "Registered" and "ready" are different questions since GFP-4 (see
    :class:`ScraperStatus`): this returns only the ready ones, which is what
    lets the GUI's Run scrape dialog (``gui/scrape.py``) and
    ``scheduler.set_schedule`` keep working unchanged -- neither offers nor
    schedules a store that would just fail on every run. Use
    :func:`all_scrapers` for every registered key regardless of readiness
    (e.g. so `gplan stores` can still show an unready one, annotated as
    needing setup, rather than hiding it outright).
    """
    return sorted(key for key in SCRAPERS if scraper_status(key).ready)


def all_scrapers() -> list[str]:
    """Every REGISTERED store key, ready or not (GFP-4). See :func:`available_scrapers`."""
    return sorted(SCRAPERS)


# --------------------------------------------------------------------------- #
# GFP-67 replace-guard: refuse to DELETE this store+ZIP's rows unless the new
# scrape looks like a real replacement for them.
# --------------------------------------------------------------------------- #
# Trip the collapse guard only on a drop of more than 90% (current count under
# 10% of the last capture). Ordinary week-to-week variation — even a much
# smaller ad — rarely wipes out nine in ten rows; a Flipp response-shape
# change routinely wipes out ~100%. 90% is comfortably below "real" noise and
# comfortably above "the parser fell through empty-handed", so it separates
# the two cases without needing any store-specific tuning.
_COLLAPSE_RATIO = 0.1

# Only apply the collapse check when the previous capture was itself
# reasonably sized. A store whose last capture was 5 rows dropping to 1 row
# is not evidence of upstream breakage the way 900 -> 3 is — it's just a
# small store getting smaller — so below this floor the ratio check is
# skipped entirely (only the zero-row guard still applies).
_COLLAPSE_MIN_PREVIOUS = 20


def _previous_capture_count(
    conn: sqlite3.Connection, store_key: str, source: str, zip_code: str
) -> int | None:
    """Row count of this (store, postal_code)'s most recent price_history capture.

    ``price_history`` (GFP-39) records every observation and is keyed one row
    per (store, postal_code, item_name, deal_type) per calendar day, so the
    most recent ``captured_at`` group *is* the last successful scrape's row
    count — a real baseline instead of a hard-coded number. ``None`` means
    there is no prior capture at all (first-ever scrape for this store+ZIP),
    in which case the collapse guard has nothing to compare against.
    """
    # Scoped by SOURCE as well as store (GFP-98). Two feeds can now share one
    # store -- the Flipp weekly ad returns ~940 rows, the shelf-price API a few
    # hundred. Comparing one against the other's last capture would read a
    # perfectly healthy scrape as an implausible collapse and refuse it.
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM price_history WHERE store=? AND source=? AND postal_code=? "
        "AND captured_at=(SELECT MAX(captured_at) FROM price_history "
        "WHERE store=? AND source=? AND postal_code=?)",
        (store_key, source, zip_code, store_key, source, zip_code),
    ).fetchone()
    if row is None or row["n"] == 0:
        return None
    return int(row["n"])


def _guard_replacement(
    conn: sqlite3.Connection, store_key: str, source: str, zip_code: str,
    current: int, force: bool
) -> None:
    """Raise a :class:`ScrapeGuardError` rather than let a bad scrape replace good data.

    Called after the scraper has returned rows but *before* the existing
    ``deals``/``price_history`` rows are touched, so an error here leaves the
    database exactly as it was.
    """
    if force:
        return

    if current == 0:
        raise EmptyScrapeError(
            f"Scrape of {store_key!r} for postal code {zip_code} returned 0 deals. "
            "This almost always means Flipp's response shape changed under the "
            "parser (an unauthenticated, undocumented endpoint), not that the "
            "store genuinely has no ad this week. Refusing to replace existing "
            "data — the deals and price_history rows already stored for this "
            "store and ZIP were left untouched. If you have independently "
            "confirmed (e.g. via flipp.com) that this store truly has nothing "
            "active right now, call run_scrape(..., force=True) to accept the "
            "empty result and replace anyway."
        )

    previous = _previous_capture_count(conn, store_key, source, zip_code)
    if (
        previous is not None
        and previous >= _COLLAPSE_MIN_PREVIOUS
        and current < previous * _COLLAPSE_RATIO
    ):
        raise ImplausibleCollapseError(
            f"Scrape of {store_key!r} for postal code {zip_code} returned only "
            f"{current} deals, down from {previous} at the last capture (a drop "
            f"of over {round((1 - _COLLAPSE_RATIO) * 100)}%). That is far more "
            "likely to be Flipp response-shape breakage than a genuinely quiet "
            "ad week. Refusing to replace existing data — the deals and "
            "price_history rows already stored for this store and ZIP were left "
            "untouched. If this really is a small ad week, call run_scrape(..., "
            "force=True) to accept it and replace anyway."
        )


_HISTORY_UPSERT = (
    "INSERT INTO price_history("
    "store, postal_code, item_name, sub_category, deal_type, regular_price, "
    "sale_price, dollar_price, discount_amount, discount_percent, source, "
    "sold_by, price_per_unit, price_per_unit_uom, weight_basis, "
    "product_identifier, product_identifier_ns, "
    "captured_at, updated_at) "
    "VALUES (:store, :postal_code, :item_name, :sub_category, :deal_type, "
    ":regular_price, :sale_price, :dollar_price, :discount_amount, :discount_percent, "
    ":source, :sold_by, :price_per_unit, :price_per_unit_uom, :weight_basis, "
    ":product_identifier, :product_identifier_ns, "
    ":captured_at, :updated_at) "
    "ON CONFLICT(store, postal_code, item_name, deal_type, captured_at) DO UPDATE SET "
    "regular_price=excluded.regular_price, sale_price=excluded.sale_price, "
    "dollar_price=excluded.dollar_price, discount_amount=excluded.discount_amount, "
    "discount_percent=excluded.discount_percent, source=excluded.source, "
    # GFP-98: carried into history too, so a historical row's denominator is
    # never forgotten. A bare number whose denominator has been lost cannot be
    # compared against anything safely.
    "sold_by=excluded.sold_by, price_per_unit=excluded.price_per_unit, "
    "price_per_unit_uom=excluded.price_per_unit_uom, "
    # GFP-111: the source's own product id travels into history too, and as a
    # PAIR -- `deals` is replaced wholesale on every scrape, so an identifier
    # kept only there survives one week, and history is precisely where "what
    # did this exact product cost in March" has to be answerable from. The
    # namespace is never dropped on the way in: a bare id whose vocabulary has
    # been lost cannot be looked up or compared against anything safely, which
    # is the same reasoning GFP-98 applied to sold_by above.
    "product_identifier=excluded.product_identifier, "
    "product_identifier_ns=excluded.product_identifier_ns, "
    "updated_at=excluded.updated_at"
)


def run_scrape(
    store_key: str,
    postal_code: str | None = None,
    conn: sqlite3.Connection | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Scrape a store's fresh deals and persist them, replacing prior scrape rows.

    Replacement (the ``DELETE`` below) is scoped to this store, this source,
    *and* this postal code (GFP-55) so scraping one ZIP never destroys another
    ZIP's rows for the same store. Every row also carries the ``postal_code``
    it was scraped for (GFP-54).

    Before that DELETE runs, the new rows are checked against two guards
    (GFP-67) so a broken parse can never wipe out good data:

    - :class:`EmptyScrapeError` if the scrape returned zero rows.
    - :class:`ImplausibleCollapseError` if it returned far fewer rows than
      the last successful capture recorded in ``price_history`` (see
      :data:`_COLLAPSE_RATIO`/:data:`_COLLAPSE_MIN_PREVIOUS` for the exact
      rule). This does *not* replace GFP-16's guard, which already raises
      inside the scraper when there is no current weekly flyer at all — that
      covers "nothing to scrape"; this covers "scraped, but the result looks
      broken."

    Both guards are skipped when ``force=True``, the deliberate escape hatch
    for a genuinely tiny (or empty) ad week — without it, a real quiet week
    would make a store permanently unscrapeable.

    Each scraped row is additionally appended to ``price_history`` (GFP-39) so
    price movement over time survives even though ``deals`` itself is a
    current-snapshot table that gets overwritten on every scrape. The append
    is an upsert keyed by calendar day, so re-running a scrape twice in one
    day updates today's history row rather than fabricating a second data
    point.

    Each scrape also folds its rows into ``price_records`` (GFP-75), the
    durable all-time low/high per item. That happens on write rather than
    being queried out of history later, because GFP-42's retention will prune
    the history and a record derived from pruned rows cannot be recomputed.

    Returns ``{"flyer": ..., "stats": ..., "postal_code": ..., "records": ...}``.
    Raises :class:`UnknownStoreError` for an unregistered store. When called
    from a worker thread, pass no ``conn`` so a thread-local connection is
    opened.
    """
    scraper = SCRAPERS.get(store_key)
    if scraper is None:
        raise UnknownStoreError(store_key)

    # GFP-85: the caller's ZIP wins, then the user's config, then the
    # scraper's own constant. 27401 was hard-coded in four modules, so a
    # nutritionist in another city had to edit source; now they edit one JSON
    # file. The per-scraper constant stays as the last resort, which keeps a
    # scraper usable standalone and unchanged for any caller passing a ZIP.
    zip_code = postal_code or config.postal_code() or scraper.DEFAULT_POSTAL_CODE
    # GFP-98: the registry key is no longer necessarily the store. `kroger` is
    # a SECOND source for `harristeeter` (Flipp weekly ad vs Kroger shelf-price
    # API), so the row's store and source come from the module, not from the
    # name the caller typed. Modules that declare neither keep today's exact
    # behaviour.
    store = scrapers.store_key_for(scraper, store_key)
    source = scrapers.source_for(scraper)
    rows, flyer, stats = scraper.scrape(postal_code=postal_code)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    today = now[:10]
    own = conn or db.connect()

    # GFP-87: --dry-run stops HERE -- after the scrape and the stats, before the
    # first destructive statement. That placement is the whole point: run_scrape
    # REPLACES a store's rows (the DELETE below), so "what would this do?" is a
    # question worth being able to ask without finding out. The guard is still
    # evaluated first, so a dry run also tells you whether the replace would
    # have been refused (GFP-67).
    if dry_run:
        _guard_replacement(own, store, source, zip_code, len(rows), force)
        existing = own.execute(
            "SELECT COUNT(*) FROM deals WHERE store=? AND source=? AND postal_code=?",
            (store, source, zip_code),
        ).fetchone()[0]
        logs.get_logger(__name__).info(
            "DRY RUN: would replace %s row(s) with %s for store=%s source=%s zip=%s",
            existing, len(rows), store, source, zip_code,
        )
        return {
            "flyer": flyer,
            "stats": stats,
            "postal_code": zip_code,
            "records": {"created": 0, "updated": 0},
            "dry_run": True,
            "would_replace": existing,
            "would_write": len(rows),
        }

    _guard_replacement(own, store, source, zip_code, len(rows), force)
    cols = importers.DEAL_COLUMNS
    own.execute(
        "DELETE FROM deals WHERE store=? AND source=? AND postal_code=?",
        (store, source, zip_code),
    )
    own.executemany(
        f"INSERT INTO deals(store, postal_code, {', '.join(cols)}, source, imported_at) "
        f"VALUES (:store, :postal_code, {', '.join(':' + c for c in cols)}, :source, :imported_at)",
        [{**{c: None for c in cols}, **r,
          "store": store, "postal_code": zip_code, "source": source, "imported_at": now}
         for r in rows],
    )
    own.executemany(
        _HISTORY_UPSERT,
        [{**{c: None for c in cols}, **r, "store": store, "postal_code": zip_code,
          "source": source, "captured_at": today, "updated_at": now} for r in rows],
    )
    # GFP-75: fold this scrape into the durable records BEFORE committing, so
    # records and the history rows they summarise land in the same
    # transaction. Done after the guards, so a scrape refused as broken never
    # moves a record -- a record low set by a bad parse would be permanent.
    # Records are keyed on the STORE, not the source: a record low is a record
    # low whichever feed observed it, and the Flipp ad and the shelf-price API
    # describe the same shop.
    record_summary = records.update_records(own, store, zip_code, rows, today)
    own.commit()

    # GFP-121: match the deals to foods, for EVERY store, right after they
    # land. Nothing in the app called matching.match_deals() -- Kroger and
    # Whole Foods write deal_food_match inline from their own scrapers, so the
    # two stores with bespoke ingest looked fine while the store-agnostic
    # matcher sat orphaned. Food Lion, which has no bespoke path, therefore
    # never had a single match row and its 297 priced deals were invisible to
    # $/g protein, gplan cheapest, the trends chart and every grocery list.
    #
    # Here, not in a Food Lion branch: GFP-32's rule is that the engine never
    # branches on store identity, and a fix that special-cased one store would
    # leave the next store to be added with the same silent hole. match_deals
    # already runs over every distinct (store, item_name) and already refuses
    # to overwrite a manual correction, so calling it on the one path that
    # writes deals is the whole fix.
    #
    # A failure here must not fail a scrape, for the same reason pruning does
    # not: the prices are the point, and unmatched deals are recoverable on the
    # next run while a lost scrape is not.
    try:
        match_summary = matching.match_deals(conn=own)
    except sqlite3.Error as exc:
        logs.get_logger(__name__).warning(
            "could not match deals to foods after scraping %s: %s", store, exc)
        match_summary = None

    # GFP-42: trim old history AFTER the records are safely committed, and
    # never before. Records are stored rather than recomputed precisely so a
    # record low outlives the observation behind it -- pruning first would
    # discard rows this scrape's record update still had to read.
    #
    # Here rather than on a timer for the same reason logs self-prune: nobody
    # administers this machine, so maintenance has to ride along with the work
    # that creates the data. A failure to prune must never fail a scrape --
    # the prices are the point; the disk saving is housekeeping.
    try:
        pruned = records.prune_history(own)
        if pruned:
            logs.get_logger(__name__).info("pruned %s price_history rows past retention", pruned)
    except sqlite3.Error as exc:
        logs.get_logger(__name__).warning(
            "could not prune price history: %s", exc)
        pruned = 0

    return {
        "flyer": flyer,
        "stats": stats,
        "postal_code": zip_code,
        "records": record_summary,
        "pruned_history_rows": pruned,
        "matches": match_summary,
    }
