# ######### decohen-partners ##########
# Protein Ledger
"""What should this client buy RIGHT NOW, and where (GFP-107).

The trends chart answers "is protein getting cheaper over time". This answers a
different and more immediately useful question: **of what is on offer today, what
is the cheapest animal protein at each store?** It needs no history at all — one
scrape is enough — which is why it is the one panel with something to show on the
day a nutritionist installs the app, while the chart is still saying "come back
tomorrow".

Reads ``deals`` (the current snapshot), not ``price_history``. A shopping
decision is about what is on the shelf this week; a historical low nobody can buy
today is the wrong number to put in front of someone about to go shopping.
:mod:`grocery_planner.records` already answers "is this a good price, ever".

**Expired offers are excluded, not greyed.** ``fetch_deals`` offers callers a
choice because a deal table is for browsing; a "buy this" recommendation has no
such latitude. Sending someone to a shop for an offer that ended is worse than
sending them nowhere.

Meat is decided by GFP-106's ``protein_kind`` through the same
``price_trend(meat_only=)`` definition the chart's Animal-protein tab and
``gplan trends --meat`` use, so the strip, the chart and the CLI cannot disagree
about what counts — the rule GFP-40 was built on.

Store-agnostic (GFP-32): a store key is a grouping key here and nothing else.
Adding store #3 adds a row and changes no line of this module.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .. import db, savings
from ..stores import BY_KEY
from .deals import today_iso


@dataclass(frozen=True)
class CheapestProtein:
    """One store's best protein buy on offer today."""

    store: str                      # stable key
    label: str                      # store display name, presentation-ready
    item_name: str
    kind: str | None                # 'chicken', 'beef', ... or None when unknown
    cost_per_gram_protein: float
    price: float
    protein_grams: float
    #: GFP-98's denomination, carried so the UI can tag a per-pound price. A
    #: WEIGHT item's price buys ONE POUND, not the package, and rendering the
    #: two identically is a wrong buying decision made from correct data.
    sold_by: str | None = None
    price_per_unit: float | None = None
    price_per_unit_uom: str | None = None
    #: GFP-270's retail-format state, carried for the SAME reason `sold_by` is:
    #: the display layer must be able to tell a per-pound RATE (‡ -- not a price
    #: yet) from a random-weight package (** -- a real price that may shift).
    #: Without it every by-weight row here falls to UNKNOWN (†), which
    #: understates how provisional a Publix rate figure actually is.
    weight_basis: str | None = None
    # GFP-118: this panel answers "where is protein cheapest right now", so it
    # is the first thing a nutritionist reads -- and it was the one place that
    # could not be clicked through, while the grocery list could. The deal row
    # this came from already carries both, so carrying them here costs nothing.
    source_url: str | None = None
    product_identifier: str | None = None


def _store_label(store_key: str) -> str:
    store = BY_KEY.get(store_key)
    return store.display_name if store else store_key


def cheapest_protein_by_store(
    *,
    meat_only: bool = True,
    postal_code: str | None = None,
    today: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[CheapestProtein]:
    """The cheapest resolvable protein currently on offer at each store.

    Cheapest first, so the store to shop at reads top. A store whose offers
    contain nothing resolvable is simply absent rather than present with a
    blank or a zero — the same rule the rest of this layer follows.

    ``meat_only`` defaults to **True** here, unlike ``price_trend``. This
    function exists to answer the meat question; the chart keeps both views
    because it is a comparison, whereas a "buy this" line naming a pancake mix
    is just wrong.
    """
    from .. import protein_kind as pk

    own = conn or db.connect()
    anchor = today or today_iso()
    # Classified BEFORE the query, not after: a food added by this morning's
    # scrape would otherwise read as NULL and be dropped from a meat filter,
    # understating the cheapest meat rather than merely omitting a row. For a
    # number someone shops from, understating is the dangerous direction.
    pk.ensure_classified(own)

    where = [
        "COALESCE(d.dollar_price, d.sale_price, d.regular_price) > 0",
        # Expired offers are excluded outright -- see the module docstring.
        "(d.valid_to IS NULL OR d.valid_to = '' OR d.valid_to >= ?)",
    ]
    params: list[object] = [anchor]
    if postal_code:
        where.append("d.postal_code = ?")
        params.append(postal_code)
    if meat_only:
        kinds = sorted(pk.MEAT_KINDS)
        where.append(f"f.protein_kind IN ({','.join('?' * len(kinds))})")
        params.extend(kinds)

    # LEFT JOIN so an unmatched item still reaches the protein chain, which can
    # still resolve it via the GFP-69 label-claim path -- that path has no food
    # and so is excluded by the meat filter above, but must not be excluded from
    # the unfiltered call.
    rows = own.execute(
        "SELECT d.store, d.item_name, d.sold_by, d.weight_basis, d.price_per_unit, "
        "d.price_per_unit_uom, "
        "d.source_url, d.product_identifier, "
        "COALESCE(d.dollar_price, d.sale_price, d.regular_price) AS price, "
        "f.protein_kind AS kind "
        "FROM deals d "
        "LEFT JOIN deal_food_match m ON m.store = d.store AND m.item_name = d.item_name "
        "LEFT JOIN foods f ON f.id = m.food_id "
        f"WHERE {' AND '.join(where)}",
        params,
    ).fetchall()

    # Cached per (store, item_name): the protein chain is the expensive half and
    # the same item recurs across deal types. Same reasoning as service/trends.
    resolved: dict[tuple[str, str], object] = {}
    best: dict[str, CheapestProtein] = {}

    for row in rows:
        # GFP-274: THE DEAL'S OWN NAME OVERRIDES ITS MATCHED FOOD'S KIND.
        #
        # `f.protein_kind` above is the kind of the food this deal MATCHED to,
        # not of the deal itself, and matching is fuzzy by design. Measured live
        # on 2026-08-12:
        #
        #   "Hanover Brown Sugar & Bacon Baked Beans, 16 oz"
        #       -> food 'Bacon, raw' (pork), confidence 0.9, method cut_keyword
        #   "beef cooking stock 3G Protein, 32 oz"
        #       -> food 'Beef sirloin steak, raw' (beef), confidence 0.3
        #
        # Both matched foods really are pork and beef, so the SQL filter above
        # is working correctly on the data it has. The tin of beans still
        # arrived as GIANT's cheapest pork.
        #
        # Note the beans matched at confidence 0.9, so no confidence floor --
        # not GFP-271's and not any plausible one -- would ever have caught it.
        # The only evidence that the row is not meat is its own name, which
        # `protein_kind` already reads correctly; nothing was asking it.
        #
        # Skipped before `cost_per_gram_protein` rather than after, because that
        # call is the expensive half of this function.
        if meat_only and pk.is_disqualified(row["item_name"]):
            continue

        key = (row["store"], row["item_name"])
        if key not in resolved:
            resolved[key] = savings.cost_per_gram_protein(
                1.0, row["item_name"], row["store"], conn=own
            )
        cost = resolved[key]
        if cost is None or cost.protein_grams <= 0:
            continue      # no protein figure -> no row, never a zero

        value = row["price"] / cost.protein_grams
        current = best.get(row["store"])
        if current is None or value < current.cost_per_gram_protein:
            best[row["store"]] = CheapestProtein(
                store=row["store"],
                label=_store_label(row["store"]),
                item_name=row["item_name"],
                kind=row["kind"],
                cost_per_gram_protein=value,
                price=row["price"],
                protein_grams=cost.protein_grams,
                sold_by=row["sold_by"],
                weight_basis=row["weight_basis"],
                price_per_unit=row["price_per_unit"],
                price_per_unit_uom=row["price_per_unit_uom"],
                source_url=row["source_url"],
                product_identifier=row["product_identifier"],
            )

    return sorted(best.values(), key=lambda item: item.cost_per_gram_protein)
