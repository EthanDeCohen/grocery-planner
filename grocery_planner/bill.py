"""Daily protein bill (GFP-48): target grams -> foods -> amortised daily cost.

This is the headline number the client page exists to show: "hitting your
protein target costs about $X/day, built from these foods." Every earlier
ticket in this chain -- GFP-8's size parsing, GFP-25's deal matching, GFP-23's
protein-per-100g catalog, GFP-26's ``savings.cost_per_gram_protein``, GFP-29's
``targets.protein_target_for`` -- computed one link. This module is the first
one that walks all the way from "109 g of protein per day" to "these deals,
these quantities, this many dollars."

Amortisation, and why the money field is not "today's shopping total"
-----------------------------------------------------------------------
A weekly-ad price ("$3.99 for 16 oz, this week") is not a daily figure, and a
protein target ("109 g/day") is not a weekly one. Multiplying a deal's
$/g-of-protein rate (already computed by ``savings.cost_per_gram_protein``,
itself store-agnostic and cadence-agnostic -- it is just price divided by the
grams of protein in the package) by the grams of protein *one day's target*
needs answers a different, honest question: "if this week's cheapest protein
were priced by the gram, what would today's share of it cost?" That is an
AMORTISED cost, not a same-day purchase total -- nobody buys 3.5 grams of
chicken breast, they buy the whole 16 oz package and eat it across several
days, or buy several packages across a week. See :data:`AMORTIZATION_NOTE`,
which a UI should quote (or closely paraphrase) rather than label this figure
with anything that reads as "what will I spend at checkout today." Every
money-bearing field in this module (:attr:`BillLine.cost`,
:attr:`Bill.total_cost`) is named plainly as "cost", never "price" or
"spend", and is documented at the point of definition as amortised -- the
same discipline ``targets.ProteinTarget`` uses to pair a bare number with the
cadence it belongs to (``daily_grams``/``weekly_grams``), applied here to
money instead of grams.

The allocation rule
--------------------
Simplest correct thing, per the ticket: fill the target from the cheapest
$/g-of-protein deal upward (``savings.rank_by_cost_per_gram_protein`` already
does the sorting and already drops anything unpriceable -- this module reuses
it rather than re-deriving the ranking).

Two decisions worth being explicit about, since either way is defensible and
a silent choice would be worse than either:

1. **A single line is capped at that deal's own package (or, for a GFP-69
   label-claim deal with no known package weight, that deal's own serving)
   worth of protein -- never an invented number.** A deal with no natural
   "how much protein is in one of these" figure does not exist in this
   pipeline: ``ProteinCost.protein_grams`` is always populated by the time a
   row survives ``rank_by_cost_per_gram_protein``, whether that number came
   from a package weight x protein-per-100g or from a manufacturer's own
   per-serving claim. Using that figure as the per-line cap means the bill
   naturally spreads across several foods once the cheapest one's own
   package/serving is used up, without this module inventing a threshold
   ("no more than 150 g from one source") that has no basis in the data. A
   client whose cheapest option is a large multi-serving item can still see
   most of their target covered by one line -- that is real information
   about the deal, not a bug to cap around.
2. **A single deal MAY still cover the whole target** if its own
   package/serving protein figure is large enough. Real clients do not eat
   only chicken breast, but nothing in this schema models "how much of one
   food is too much" (no serving-frequency or variety table exists), and
   inventing one here would be exactly the kind of made-up constant rule 1
   above rejects. Diet variety is left to the ``categories`` argument, which
   already exists for a real reason (client protein preferences, GFP-30) --
   a nutritionist who wants the bill to spread across several proteins can
   express that by preference, not by an arbitrary per-line percentage cap
   buried in this engine.

**Shortfall is a normal result, not an error.** Available deals frequently
cannot cover a client's full target -- too few weekly ads matched, or a
preference filter narrows the field to nothing this week. ``daily_bill``
returns a :class:`Bill` either way; :attr:`Bill.is_complete` and
:attr:`Bill.shortfall_grams` say plainly whether -- and by how much -- the
available deals fell short, rather than raising or silently returning a
partial bill that looks whole.

Preferences: zero means unconstrained
--------------------------------------
Per ``preferences.py``'s module docstring, a client with no stored preference
rows has stated no preference at all -- ranking must consider every category,
exactly as if the preference table did not exist for them. ``categories=None``
(the default) looks up the client's stored preferences and passes that list
straight through unchanged: an empty result stays ``[]``, which this module
also treats as "don't filter" (see ``_build_bill`` below), never as "match
nothing." Pass ``categories=[]`` explicitly to force the unconstrained
behaviour regardless of what is on file (useful for GFP-49's baseline side --
see below), or a non-empty list to narrow to specific categories for a
one-off "what if" query without touching the stored preference rows.

What is explicitly out of scope
---------------------------------
GFP-49's baseline-vs-constrained comparison (an unconstrained bill next to a
preference-narrowed one, to show a nutritionist what a preference costs) is a
different ticket and is not built here. Nothing about this module makes it
hard: ``daily_bill_for(customer, categories=[], conn=conn)`` already produces
the unconstrained baseline, and ``daily_bill_for(customer, conn=conn)``
(``categories=None``) already produces the preference-constrained plan, so
GFP-49 is two calls into this module, not a rewrite of it. Also out of scope:
any GUI/CLI wiring (another agent owns that), and anything about how many
DISTINCT packages of one item a client would physically need to buy -- this
module prices grams continuously (see point 1 above and the amortisation
note), matching the "amortised daily figure" framing rather than a literal
shopping list.

Like every other module in this codebase, every function here takes an
optional ``conn`` defaulting to ``db.connect()`` rather than holding a
connection on an object: SQLite connections cannot cross threads, and the GUI
scrapes on a background thread.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Iterable

from . import db, preferences, savings, service, targets
from .customers import Customer, CustomerRepository

# A UI should quote this (or something that says the same thing in fewer
# words) next to any figure this module produces, so "amortised" never gets
# silently dropped on the way to a screen. See the module docstring's
# "Amortisation" section for the full reasoning.
AMORTIZATION_NOTE = (
    "Amortised cost per day: built from weekly ad prices spread across a "
    "single day's worth of protein. This is what today's share of this "
    "week's cheapest protein would cost, not a same-day shopping total."
)


@dataclass(frozen=True)
class BillLine:
    """One food's contribution to a :class:`Bill`.

    ``cost`` is AMORTISED -- see :data:`AMORTIZATION_NOTE` and the module
    docstring. It is ``grams_protein * cost_per_gram_protein``, i.e. what
    this line's share of the daily target would cost priced at this deal's
    own $/g-of-protein rate; it is not the price of a package on a shelf.

    ``grams_food`` is ``None`` when the underlying deal has no known package
    weight -- true of a GFP-69 label-claim deal (a manufacturer's "20G
    Protein" claim gives grams of protein directly, never a package weight).
    Per ``savings.py``'s rule 1, an unknown figure is ``None``, never a
    guess; this mirrors ``ProteinCost.size_grams`` being optional for exactly
    the same reason. ``grams_protein`` is always known (that is what makes a
    row priceable at all), so it alone is never optional here.
    """

    item_name: str
    store: str                    # for the UI's store tag; never branched on
    grams_protein: float          # how much of the daily target this line covers
    grams_food: float | None      # how much food that is; None if package weight is unknown
    cost: float                   # amortised $/day for this line -- see AMORTIZATION_NOTE
    cost_per_gram_protein: float
    # Provenance of the protein figure, e.g. 'usda' | 'curated' | 'label' |
    # 'kroger' -- see savings.ProteinCost. Treat this as an open set: GFP-98
    # added 'kroger' when a structured store API began supplying its own
    # protein data, and a UI that switches on a closed list of three will
    # mis-render the next source the same way.
    protein_source: str
    match_confidence: float       # 0-1, see matching.CONFIDENCE_* / savings.LABEL_CLAIM_CONFIDENCE
    food_id: int | None
    food_name: str | None


@dataclass(frozen=True)
class Bill:
    """One customer's daily protein bill: the target, how it was filled, and
    how much of it the available deals could not fill.

    ``total_cost`` is AMORTISED, same as every ``BillLine.cost`` it sums --
    see :data:`AMORTIZATION_NOTE`.

    ``excluded_deals`` counts deals this bill's pool of current deals could
    not price at all (no readable size, no food match, no protein figure --
    see ``savings.rank_by_cost_per_gram_protein``, which drops them). It is
    computed once over every current deal, independent of ``categories`` --
    a deal that is unpriceable is unpriceable regardless of what a client
    prefers to eat. ``considered_deals`` is narrower: how many PRICED deals
    were actually in the pool this specific bill drew from, i.e. after the
    ``categories`` filter -- a preference naming a category with no priced
    deals in it yields ``considered_deals == 0`` honestly, rather than a
    crash or a silently-unconstrained bill.
    """

    target_grams: float
    lines: list[BillLine] = field(default_factory=list)
    total_cost: float = 0.0
    covered_grams: float = 0.0
    excluded_deals: int = 0
    considered_deals: int = 0
    categories: list[str] = field(default_factory=list)  # [] == unconstrained; see module docstring

    @property
    def is_complete(self) -> bool:
        """Whether the available (and, if filtered, preferred) deals covered
        the whole daily target. ``False`` is a normal, common result -- see
        the module docstring's "Shortfall is a normal result" section."""
        return self.shortfall_grams <= 0.0

    @property
    def shortfall_grams(self) -> float:
        """How many grams of the daily target the bill's lines did not
        reach. Never negative -- floored at 0 so float rounding on a
        just-barely-complete bill can't report a tiny negative shortfall."""
        return max(0.0, self.target_grams - self.covered_grams)


def _category_lookup(
    conn: sqlite3.Connection, food_id: int | None, cache: dict[int, str | None]
) -> str | None:
    """This food's category, or ``None`` if ``food_id`` is unknown/unmatched.

    A label-claim deal (GFP-69) carries no ``food_id`` at all -- there is no
    matched food to ask, so its category is honestly unknown, not "any" or
    "none". Callers filtering by category must therefore treat ``None`` here
    as "cannot confirm this belongs to a preferred category" and exclude it
    from a category-CONSTRAINED pool, the same way an unpriceable deal is
    excluded rather than guessed into the bill.
    """
    if food_id is None:
        return None
    if food_id not in cache:
        row = conn.execute("SELECT category FROM foods WHERE id=?", (food_id,)).fetchone()
        cache[food_id] = row["category"] if row is not None else None
    return cache[food_id]


def _build_bill(
    customer_id: int | None,
    target_grams: float,
    categories: Iterable[str] | None,
    conn: sqlite3.Connection,
) -> Bill:
    """Shared engine behind :func:`daily_bill`/:func:`daily_bill_for`, once a
    valid (non-``None``) daily target is already in hand.

    See the module docstring for the amortisation, allocation and
    zero-preferences-means-unconstrained rules this implements.
    """
    if categories is None:
        # customer_id is None only for an unsaved Customer with no rows to
        # look up -- there is nothing to be unconstrained FROM in that case,
        # so it falls through to the same [] every other unconstrained
        # client gets (see preferences.py's module docstring).
        stated = (
            preferences.list_preferences(customer_id, conn=conn)
            if customer_id is not None
            else []
        )
        applied_categories = list(stated)
    else:
        # Explicit override: sorted + deduped for a deterministic Bill,
        # mirroring preferences.set_preferences' own normalisation.
        applied_categories = sorted({c for c in categories})

    all_deals = service.fetch_deals(hide_expired=True, conn=conn)
    # Cheapest $/g-protein first; anything unpriceable is already dropped --
    # this module never re-derives that chain, only allocates against it.
    ranked = savings.rank_by_cost_per_gram_protein(
        all_deals, conn=conn, limit=0, min_confidence=None
    )
    excluded_deals = len(all_deals) - len(ranked)

    if applied_categories:
        allowed = set(applied_categories)
        cache: dict[int, str | None] = {}
        candidates = [
            item
            for item in ranked
            if _category_lookup(conn, item.get("food_id"), cache) in allowed
        ]
    else:
        candidates = ranked

    lines: list[BillLine] = []
    remaining = target_grams
    for item in candidates:
        if remaining <= 0.0:
            break
        # The per-line cap: at most one package's (or, on the label-claim
        # path, one serving's) worth of protein from a single deal -- a real
        # number carried on the row itself, never an invented threshold. See
        # the module docstring's allocation-rule section.
        package_protein = item["protein_grams"]
        grams_protein = min(remaining, package_protein)
        if grams_protein <= 0.0:
            continue

        size_grams = item.get("size_grams")
        # Grams of FOOD needed for this line's grams of PROTEIN, via the
        # package's own protein-per-gram ratio -- None when that ratio isn't
        # known at all (label-claim path, no package weight; see BillLine's
        # docstring), never guessed.
        grams_food = (
            (grams_protein / package_protein) * size_grams
            if size_grams is not None
            else None
        )

        lines.append(
            BillLine(
                item_name=item["item_name"],
                store=item["store"],
                grams_protein=grams_protein,
                grams_food=grams_food,
                cost=grams_protein * item["cost_per_gram_protein"],
                cost_per_gram_protein=item["cost_per_gram_protein"],
                protein_source=item["protein_source"],
                match_confidence=item["match_confidence"],
                food_id=item.get("food_id"),
                food_name=item.get("food_name"),
            )
        )
        remaining -= grams_protein

    return Bill(
        target_grams=target_grams,
        lines=lines,
        total_cost=sum(line.cost for line in lines),
        covered_grams=sum(line.grams_protein for line in lines),
        excluded_deals=excluded_deals,
        considered_deals=len(candidates),
        categories=applied_categories,
    )


def daily_bill_for(
    customer: Customer,
    categories: Iterable[str] | None = None,
    conn: sqlite3.Connection | None = None,
) -> Bill | None:
    """Daily protein bill for an already-loaded :class:`Customer`.

    ``None`` when the customer has no ``weight_kg`` on file -- same rule as
    ``targets.protein_target_for``: a bill built on a guessed weight is worse
    than no bill at all (see ``targets.py``'s module docstring).

    ``categories=None`` (default) defers to the customer's stored
    preferences (``[]`` if none are recorded, meaning unconstrained -- see
    the module docstring). Pass an explicit iterable (including ``[]``) to
    override the stored preferences for this call only.
    """
    own = conn or db.connect()
    target = targets.protein_target_for(customer, conn=own)
    if target is None:
        return None
    return _build_bill(customer.id, target.daily_grams, categories, own)


def daily_bill(
    customer_id: int,
    categories: Iterable[str] | None = None,
    conn: sqlite3.Connection | None = None,
) -> Bill | None:
    """Daily protein bill for a customer looked up by id.

    ``None`` if no such customer exists, or (see :func:`daily_bill_for`) the
    customer has no ``weight_kg`` on file. Mirrors
    ``targets.protein_target``'s ``include_deleted=True`` default: looking up
    a bill is a read, not a listing a soft-deleted client should be hidden
    from.
    """
    own = conn or db.connect()
    customer = CustomerRepository.get(customer_id, conn=own)
    if customer is None:
        return None
    return daily_bill_for(customer, categories=categories, conn=own)
