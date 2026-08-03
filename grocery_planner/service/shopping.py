"""A grocery list a client can actually shop from (GFP-112).

The product's criticism of itself, in the user's words: *"what's the point of
seeing this with nothing to do about it."* Everything up to here computes what
protein is cheapest and where. This is the first thing that produces something
a nutritionist can hand to a client.

Why this is not just "print the bill"
--------------------------------------
``bill.Bill`` is deliberately AMORTISED: every figure on it is a daily share of
a weekly-ad price, because "hitting your protein target costs $2.14/day" is the
honest way to compare deals. ``bill.py`` says outright that how many distinct
packages someone must physically buy is out of its scope.

A shopping list is exactly that missing question. Nobody can take "$1.14/day"
to a checkout. So this module converts:

    grams of protein per day  ->  grams over the whole shopping period
                              ->  WHOLE packages (or pounds) to buy
                              ->  what that actually costs at the till

Three consequences worth stating, because each is a place this could quietly
lie:

1. **A period is required, and it defaults to a week.** A one-day grocery list
   is not a thing anyone shops for. The period is on the list itself so a
   printout can never be read as a daily figure by mistake.

2. **Quantities round UP to whole packages.** You cannot buy 0.4 of a packet.
   Rounding down would produce a list that silently misses the client's target,
   which is the failure that matters here -- so the estimate errs toward buying
   slightly too much, and says so.

3. **A WEIGHT item is bought by weight, not by package.** GFP-98's trap in a new
   place: for ``soldBy=WEIGHT`` the price and the ``size`` both describe ONE
   POUND, so the quantity is a weight, and multiplying it by a package count
   would be meaningless. The engine never branches on store; this branches on
   the denomination the source itself stated, which is data.

An item whose package weight was never known (the GFP-69 label-claim path) gets
NO quantity rather than a guessed one, and is carried on the list flagged so a
human can decide. Same rule as everywhere else: absent stays absent.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from datetime import date

from .. import bill as bill_module
from ..customers import Customer
from ..stores import BY_KEY

#: Default shopping period. A week is what a weekly ad covers and what people
#: actually shop for; a daily list would be absurd and a monthly one would
#: outlive the prices it was built from.
DEFAULT_DAYS = 7

#: GFP-98's marker for "this price buys one unit of weight, not a package".
SOLD_BY_WEIGHT = "WEIGHT"

#: Grams per pound, for turning a needed weight into a purchasable one.
GRAMS_PER_POUND = 453.59237


@dataclass(frozen=True)
class GroceryItem:
    """One line of a shopping list: what to buy, where, how much, what it costs."""

    store: str
    store_label: str
    item_name: str
    food_name: str | None
    #: How much to buy, in :attr:`quantity_unit`. ``None`` when the package
    #: weight was never known and a quantity therefore cannot be computed.
    quantity: float | None
    quantity_unit: str            # 'pack' | 'lb'
    #: What that quantity is estimated to cost. ``None`` whenever quantity is.
    estimated_cost: float | None
    shelf_price: float | None     # the observed price for ONE priced quantity
    grams_protein: float          # what this line contributes over the period
    #: The retailer's own product id and which vocabulary it belongs to
    #: (GFP-111) -- a display name identifies nothing to an ordering system.
    product_identifier: str | None
    product_identifier_ns: str | None
    source_url: str | None
    sold_by: str | None
    deal_id: int | None

    @property
    def is_priced(self) -> bool:
        return self.quantity is not None and self.estimated_cost is not None

    @property
    def quantity_label(self) -> str:
        """Human quantity, or a plain statement that it is not known."""
        if self.quantity is None:
            return "quantity unknown"
        if self.quantity_unit == "lb":
            return f"{self.quantity:.2f} lb".replace(".00 ", " ")
        count = int(self.quantity)
        return f"{count} pack" if count == 1 else f"{count} packs"


@dataclass(frozen=True)
class GroceryList:
    """A client's shopping list for one period, grouped-able by store."""

    client_name: str
    days: int
    generated_on: str             # ISO date -- a list with stale prices and no
                                  # date on it is a trap
    target_grams_per_day: float
    items: list[GroceryItem] = field(default_factory=list)
    #: Grams of the period's target the available deals could not cover.
    shortfall_grams: float = 0.0

    @property
    def total_cost(self) -> float:
        return sum(i.estimated_cost or 0.0 for i in self.items)

    @property
    def stores(self) -> list[str]:
        """Store labels present, cheapest-first order preserved."""
        seen: list[str] = []
        for item in self.items:
            if item.store_label not in seen:
                seen.append(item.store_label)
        return seen

    @property
    def is_complete(self) -> bool:
        return self.shortfall_grams <= 0.0

    @property
    def unpriced(self) -> list[GroceryItem]:
        """Items carried without a quantity — a human has to decide on these."""
        return [i for i in self.items if not i.is_priced]

    def by_store(self) -> list[tuple[str, list[GroceryItem]]]:
        """Items grouped by store, in first-appearance (cheapest) order.

        Grouped because a shopping list is walked one shop at a time; a list
        that alternates between two stores line by line is not usable in a shop.
        """
        groups: dict[str, list[GroceryItem]] = {}
        for item in self.items:
            groups.setdefault(item.store_label, []).append(item)
        return [(label, groups[label]) for label in self.stores]


def _store_label(store_key: str) -> str:
    store = BY_KEY.get(store_key)
    return store.display_name if store else store_key


def _quantity_for(line: bill_module.BillLine, days: int) -> tuple[float | None, str]:
    """How much of this line to buy over ``days``, and in what unit.

    Returns ``(None, unit)`` when the package weight was never known -- the
    label-claim path. A guessed quantity is worse than an absent one: it would
    send someone to a shop to buy a number nobody computed.
    """
    grams_needed = line.grams_food * days if line.grams_food is not None else None
    if grams_needed is None or not line.package_grams:
        return None, "pack"

    if line.sold_by == SOLD_BY_WEIGHT:
        # Price and size both describe ONE POUND (GFP-98), so buy a weight.
        # Rounded to a sensible shop quantity rather than 3 decimal places.
        pounds = grams_needed / GRAMS_PER_POUND
        return max(round(pounds * 4) / 4, 0.25), "lb"

    # A package item: whole packs only, rounded UP -- you cannot buy 0.4 of one,
    # and rounding down would silently miss the target.
    return float(max(1, math.ceil(grams_needed / line.package_grams))), "pack"


def _cost_for(line: bill_module.BillLine, quantity: float | None) -> float | None:
    """What ``quantity`` costs at this line's own shelf price."""
    if quantity is None or line.shelf_price is None:
        return None
    return line.shelf_price * quantity


def grocery_list_for(
    customer: Customer,
    days: int = DEFAULT_DAYS,
    categories=None,
    today: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> GroceryList | None:
    """Build ``customer``'s shopping list for the next ``days``.

    ``None`` when there is no daily target to build from -- the same rule as
    ``bill.daily_bill_for``: a list built on a guessed weight is worse than no
    list (see ``targets.py``). Shortfall is carried, not raised: available deals
    frequently cannot cover a full target, and a list that silently looked
    complete would be the lie.
    """
    if days < 1:
        raise ValueError("a grocery list covers at least one day")

    plan = bill_module.daily_bill_for(customer, categories=categories, conn=conn)
    if plan is None:
        return None

    items: list[GroceryItem] = []
    for line in plan.lines:
        quantity, unit = _quantity_for(line, days)
        items.append(GroceryItem(
            store=line.store,
            store_label=_store_label(line.store),
            item_name=line.item_name,
            food_name=line.food_name,
            quantity=quantity,
            quantity_unit=unit,
            estimated_cost=_cost_for(line, quantity),
            shelf_price=line.shelf_price,
            grams_protein=line.grams_protein * days,
            product_identifier=line.product_identifier,
            product_identifier_ns=line.product_identifier_ns,
            source_url=line.source_url,
            sold_by=line.sold_by,
            deal_id=line.deal_id,
        ))

    return GroceryList(
        client_name=customer.name,
        days=days,
        generated_on=today or date.today().isoformat(),
        target_grams_per_day=plan.target_grams,
        items=items,
        shortfall_grams=plan.shortfall_grams * days,
    )
