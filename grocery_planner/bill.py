# ######### decohen-partners ##########
# Protein Ledger
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

Baseline vs preference-constrained (GFP-49)
--------------------------------------------
:func:`compare_bills` solves the bill twice -- once unconstrained, once inside
the client's stated preferences -- so a nutritionist can see what a preference
actually costs per day. The baseline is a genuine unconstrained optimum (the
same allocation run over every priced deal), not the cheapest single item.

**The delta's sign is not assumed**, per the ticket. It is also not the whole
story, which is the trap this comparison has to avoid: because a constrained
pool can fail to *cover* the target, a preference-narrowed bill can come out
CHEAPER simply by buying less protein. Comparing two totals that stand for
different numbers of grams is not a price comparison at all. So
:attr:`BillComparison.is_comparable` reports whether both sides actually
reached the target, and :attr:`BillComparison.caveat` gives a UI the words for
the case where they did not -- the figures are still shown, but never as a
clean "your preference costs $0.88 more" when the truth is "your preference
could not feed them."

What is explicitly out of scope
---------------------------------
Any GUI/CLI wiring (GFP-52 owns the panel), and anything about how many
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
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from enum import Enum
from typing import Iterable

from . import db, nutrition, preferences, protein_kind, savings, service, targets
from .customers import Customer, CustomerRepository

#: The lowest match confidence a row may have and still be RECOMMENDED (GFP-271).
#:
#: `savings.cost_per_gram_protein` grades every row: 1.0 is a density measured
#: from a published nutrition panel, 0.3 is a number scraped out of a product
#: name with no servings-per-container behind it. Ranking is cheapest-first, so
#: without a floor a 0.3 guess competes head-to-head with a 1.0 measurement and
#: wins whenever the guess is low -- and a guess is wrong in the CHEAP direction
#: far more often than the dear one, because the usual failure is missing a
#: multiplier. An understatement therefore never sits harmlessly mid-list; it
#: goes straight to the top, which is the only part of the list anyone reads.
#:
#: 0.9 rather than something softer, because the measurement that matters is
#: what a floor COSTS: it removes 539 rows, and of those, sources publishing
#: real panels barely notice (Sprouts keeps 111 of 112, Trader Joe's 865 of 870)
#: while Lidl loses 168 of 269. That asymmetry is the point -- the floor is
#: paid for almost entirely by rows that were guesses.
MIN_MATCH_CONFIDENCE = 0.9

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
    # GFP-15's corroborating link and ad clipping, carried through so GFP-38's
    # where-to-buy column can offer them without a second lookup. Both are
    # frequently None -- only the Flipp path populates them, and the Kroger and
    # Whole Foods scrapers write None -- so a consumer MUST degrade to plain
    # text rather than render a dead control (see scrapers/base.py).
    source_url: str | None = None
    image_url: str | None = None
    # GFP-50: which deal row this line came from. Without it a bill line is a
    # dead end -- you can read the recommendation but cannot get back to the
    # record that produced it, which blocks tracing a wrong number and blocks
    # a grocery list (GFP-112) from referencing the exact offer it priced.
    # None for a line built from a row that carried no id (a hand-made dict in
    # a test, or any future non-deal source).
    deal_id: int | None = None
    # GFP-98's denomination, carried to every renderer of this line. A
    # soldBy=WEIGHT price buys ONE POUND while a UNIT price buys the package;
    # shown identically they invite a wrong buying decision from correct data.
    # NULL on every Flipp and CSV row -- those sources do not state it, and a
    # guess would be worse than an absent value (savings.py rule 1).
    sold_by: str | None = None
    #: GFP-152: 'deli', 'prepackaged', 'unknown', or None when no marker
    #: applies. None means the question does not arise (fixed-price package,
    #: or a source that never stated a denomination); 'unknown' means it does
    #: arise and could not be answered. The display layer must keep those
    #: apart -- collapsing them puts a caveat on items that need none and
    #: hides it on items that do.
    weight_basis: str | None = None
    price_per_unit_uom: str | None = None
    # GFP-112: what a SHOPPING list needs and an amortised bill does not.
    #
    # `cost` above is an amortised daily share and is deliberately never called
    # a price. To actually buy this, you need the shelf price of one package and
    # how much food that package holds -- so a list can say "2 packs, $7.98"
    # instead of "$1.14/day", which nobody can hand to a shop assistant.
    #
    # `shelf_price` is the observed price for the PRICED quantity: the package
    # for a UNIT item, one pound for a WEIGHT item (GFP-98). `package_grams` is
    # None when the package weight was never known -- the GFP-69 label-claim
    # path -- in which case a quantity cannot be computed and must not be
    # guessed.
    shelf_price: float | None = None
    package_grams: float | None = None
    product_identifier: str | None = None
    product_identifier_ns: str | None = None


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
    #: GFP-136: the constraints and objective this bill was built under, so a
    #: caller can say WHY the plan looks the way it does rather than leaving a
    #: user to wonder why the cheapest item is not in it.
    selection: "Selection" = field(default_factory=lambda: Selection())

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


@dataclass(frozen=True)
class BillComparison:
    """An unconstrained baseline beside a preference-constrained plan (GFP-49).

    Both halves are full :class:`Bill` objects, so a caller can show the
    itemised lines of either. ``constrained`` is the plan built inside the
    client's stated preferences; when they have stated none it is the same
    unconstrained solve as ``baseline`` (see :attr:`is_constrained`).
    """

    baseline: Bill
    constrained: Bill

    @property
    def delta_cost(self) -> float:
        """``constrained - baseline``: what the preference costs per day.

        Deliberately signed and deliberately not clamped. A preference is not
        always more expensive -- it can land on exactly the same deals (0.00)
        or, when it narrows the field to a pool that cannot cover the target,
        come out negative. A negative delta is a red flag to explain, not a
        saving to celebrate: see :attr:`is_comparable`.
        """
        return self.constrained.total_cost - self.baseline.total_cost

    @property
    def is_constrained(self) -> bool:
        """Whether a preference filter actually applied at all.

        ``False`` for a client with no stated preferences -- the two bills are
        then the same solve, and a UI should show one figure rather than a
        meaningless "+$0.00 for your preferences".
        """
        return bool(self.constrained.categories)

    @property
    def is_comparable(self) -> bool:
        """Whether the two totals stand for the same amount of protein.

        Both bills must have reached the target. If either fell short, the
        totals price different numbers of grams and their difference is not a
        cost-of-preference figure -- see :attr:`caveat`.
        """
        return self.baseline.is_complete and self.constrained.is_complete

    @property
    def caveat(self) -> str:
        """Why the delta cannot be read at face value, or ``""`` when it can.

        The specific failure this exists to prevent: a preference that starves
        the bill produces a *lower* total, which reads as "this preference is
        cheaper" when it actually means "this preference cannot feed them
        from what is on offer this week."
        """
        if self.is_comparable:
            return ""
        if not self.constrained.is_complete and self.baseline.is_complete:
            return (
                f"These are not comparable: the preferred categories cover only "
                f"{self.constrained.covered_grams:.0f} g of the "
                f"{self.constrained.target_grams:.0f} g target from what is on "
                f"offer, so the lower figure buys less protein rather than the "
                f"same protein for less."
            )
        if not self.baseline.is_complete:
            return (
                f"These are not comparable: even unconstrained, the current deals "
                f"cover only {self.baseline.covered_grams:.0f} g of the "
                f"{self.baseline.target_grams:.0f} g target. Run a scrape "
                f"(Data > Run scrape…) for fresher prices."
            )
        return "These are not comparable: at least one plan fell short of the target."


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


# --------------------------------------------------------------------------- #
# Selection (GFP-136): HOW to choose, as distinct from WHAT is eligible
# --------------------------------------------------------------------------- #
class Objective(str, Enum):
    """What the allocation optimises for. Exactly one applies."""

    #: Today's behaviour, and the default: the cheapest way to hit the target.
    LOWEST_COST = "lowest_cost"
    #: Get as close to the target as a budget allows, and REPORT the
    #: shortfall. Never silently reduces the target -- GFP-131's invariant.
    MOST_PROTEIN_WITHIN_BUDGET = "most_protein_within_budget"


@dataclass(frozen=True)
class Selection:
    """How to choose, as distinct from what is eligible.

    THE DISTINCTION THAT MADE THIS COHERENT. "Include all" and "lowest price"
    were first described as rival modes, but they are not the same kind of
    thing: lowest price is an OBJECTIVE (what to optimise) and include-all is
    a CONSTRAINT (what the answer must contain). They compose, and "include
    all, at the lowest price" is almost certainly what was meant by the
    former.

    So constraints are independent flags and the objective is one choice.
    """

    #: Best effort to include EVERY ticked category, rather than filling the
    #: whole target from whichever is cheapest.
    #:
    #: The complaint that produced this: with beef and chicken both ticked,
    #: chicken was cheaper, so chicken filled the entire target and beef never
    #: appeared. Correct for "minimise cost", wrong for what ticking two boxes
    #: means -- "I want both", not "consider both and pick one".
    cover_all_categories: bool = False

    #: Buy everything from ONE store. The optimiser will otherwise send
    #: somebody to three shops to save two dollars, which is a bad trade for a
    #: real person and the commonest practical constraint a client has.
    single_store: bool = False

    objective: Objective = Objective.LOWEST_COST

    #: Daily cap for MOST_PROTEIN_WITHIN_BUDGET. Ignored by the lowest-cost
    #: objective, which is what makes the pair honest: UNDER BUDGET THE TWO
    #: OBJECTIVES PRODUCE AN IDENTICAL PLAN, and the UI should say so rather
    #: than offer a control that usually changes nothing.
    daily_budget: float | None = None

    #: Vary the week instead of recommending the same item seven days running
    #: (GFP-142). A CONSTRAINT, not an objective: "lowest cost" still decides
    #: what to reach for, and this constrains what counts as an acceptable
    #: week -- exactly as cover-all and single-store constrain a day. Keeping
    #: one model (objective + composable constraints) is why this is a flag
    #: here rather than a parallel mechanism.
    #:
    #: DEFAULT TRUE. A plan that recommends the same item seven days running
    #: is not one a nutritionist would hand a client, so the professional
    #: default is the varied week and the flat one is the opt-out.
    #:
    #: Shipped False first, on the reasoning that changing a default silently
    #: rewrites every existing client's plan. Two things retired that: there
    #: are no production installs to disrupt, and the cost penalty was
    #: measured wrong -- 127% for a client with ONE category ticked, but 20%
    #: unconstrained. The expensive case is a narrow preference list, not
    #: variety itself.
    vary_week: bool = True


#: How many days back "do not repeat" looks when :attr:`Selection.vary_week`
#: is on.
#:
#: Not the whole week on purpose. Forbidding an item for all seven days would
#: demand seven genuinely distinct proteins, which the catalog frequently
#: cannot supply -- and the fallback below would then fire most days, making
#: the setting look broken. Three days is enough that nobody eats the same
#: thing twice running, while leaving a rotation the data can actually
#: sustain.
VARIETY_LOOKBACK_DAYS = 3

#: Half a cent. Comparing covered grams against a target needs a tolerance or
#: floating-point noise reads as a shortfall; budget.py uses the same figure.
CENT = 0.005

DAYS_IN_WEEK = 7


def _line_for(item: dict, grams_protein: float) -> BillLine:
    """One bill line for ``grams_protein`` taken from ``item``."""
    package_protein = item["protein_grams"]
    size_grams = item.get("size_grams")
    # Grams of FOOD for this line's grams of PROTEIN, via the package's own
    # ratio -- None when that ratio is not known at all (label-claim path, no
    # package weight), never guessed.
    grams_food = (
        (grams_protein / package_protein) * size_grams
        if size_grams is not None
        else None
    )
    return BillLine(
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
        source_url=item.get("source_url"),
        image_url=item.get("image_url"),
        deal_id=item.get("deal_id"),
        sold_by=item.get("sold_by"),
        weight_basis=item.get("weight_basis"),
        price_per_unit_uom=item.get("price_per_unit_uom"),
        shelf_price=item.get("price"),
        package_grams=size_grams,
        product_identifier=item.get("product_identifier"),
        product_identifier_ns=item.get("product_identifier_ns"),
    )


def _allocate(
    candidates: list[dict],
    target: float,
    taken: set,
    budget: float | None = None,
) -> tuple[list[BillLine], float, float]:
    """Fill ``target`` grams from ``candidates``, cheapest first.

    Returns ``(lines, grams_covered, cost)``. ``taken`` carries deal markers
    across calls so a deal used by one pass is not used again by the next --
    which is what lets cover-all-categories run several passes over
    overlapping pools without buying the same package twice.

    ``budget`` stops when the money runs out rather than when the target is
    met. It never trims the TARGET; the caller reports the shortfall.
    """
    lines: list[BillLine] = []
    covered = 0.0
    spent = 0.0

    for index, item in enumerate(candidates):
        if covered >= target:
            break
        key = item.get("deal_id")
        marker = key if key is not None else (item["store"], item["item_name"])
        if marker in taken:
            continue

        # At most one package's worth of protein from a single deal -- a real
        # number carried on the row, never an invented threshold.
        grams = min(target - covered, item["protein_grams"])
        if grams <= 0.0:
            continue

        cost = grams * item["cost_per_gram_protein"]
        if budget is not None and spent + cost > budget:
            affordable = budget - spent
            if affordable <= 0 or item["cost_per_gram_protein"] <= 0:
                break
            grams = affordable / item["cost_per_gram_protein"]
            if grams <= 0.0:
                break
            cost = affordable

        taken.add(marker)
        lines.append(_line_for(item, grams))
        covered += grams
        spent += cost

    return lines, covered, spent


def _select(
    candidates: list[dict],
    target_grams: float,
    applied_categories: list[str],
    selection: Selection,
    conn: sqlite3.Connection,
) -> list[BillLine]:
    """Apply the constraints and the objective to a ranked candidate pool."""
    if selection.single_store:
        return _single_store(
            candidates, target_grams, applied_categories, selection, conn
        )

    budget = (
        selection.daily_budget
        if selection.objective is Objective.MOST_PROTEIN_WITHIN_BUDGET
        else None
    )

    taken: set = set()
    lines: list[BillLine] = []
    covered = 0.0
    spent = 0.0

    if selection.cover_all_categories and len(applied_categories) > 1:
        # Reserve an equal share of the target for each ticked category and
        # fill each from its OWN cheapest. Equal shares rather than anything
        # cleverer because what was asked for is "best effort to include all",
        # not a particular balance -- and a share nobody can fill simply rolls
        # into the greedy pass below.
        share = target_grams / len(applied_categories)
        for category in applied_categories:
            allowed = nutrition.food_ids_in([category], conn=conn)
            pool = [c for c in candidates if c.get("food_id") in allowed]
            remaining_budget = None if budget is None else budget - spent
            got, grams, cost = _allocate(pool, share, taken, remaining_budget)
            lines.extend(got)
            covered += grams
            spent += cost

    # Fill whatever is left greedily from everything eligible. This IS the
    # whole allocation in lowest-cost mode, and the top-up in cover-all mode
    # for categories that could not fill their share.
    remaining_budget = None if budget is None else budget - spent
    got, _grams, _cost = _allocate(
        candidates, target_grams - covered, taken, remaining_budget
    )
    lines.extend(got)
    return lines


def _single_store(
    candidates: list[dict],
    target_grams: float,
    applied_categories: list[str],
    selection: Selection,
    conn: sqlite3.Connection,
) -> list[BillLine]:
    """The best plan buyable from ONE store.

    Solved by running the whole selection once per store and keeping the best
    result. That is exact for one store and cheap -- there are three stores,
    not three hundred. Generalising to "at most N stores" is a genuinely
    harder combinatorial problem and is not what was asked for.

    BEST MEANS: covers the most protein, and among equals costs least.
    Coverage leads because a cheaper plan that misses the target is not a
    better answer to "what should this client eat".
    """
    single = replace(selection, single_store=False)
    stores = sorted({c["store"] for c in candidates})

    best: list[BillLine] = []
    best_key: tuple[float, float] | None = None
    for store in stores:
        pool = [c for c in candidates if c["store"] == store]
        lines = _select(pool, target_grams, applied_categories, single, conn)
        covered = sum(line.grams_protein for line in lines)
        cost = sum(line.cost for line in lines)
        key = (-covered, cost)          # most protein, then least money
        if best_key is None or key < best_key:
            best, best_key = lines, key
    return best


def _eligible(
    ranked: list[dict],
    applied_categories: list[str],
    conn: sqlite3.Connection,
) -> list[dict]:
    """Stage 1: which ranked deals this client may be given at all.

    Zero preferences means UNCONSTRAINED, never "match nothing".

    GFP-134: resolved through nutrition.food_ids_in, which understands that
    foods.category holds two taxonomies at once -- broad buckets ("Meat")
    beside specific kinds ("chicken") -- and consults protein_kind as well.

    Comparing the category STRING here is what made a client who ticked
    "chicken" miss the cheapest chicken in the database: Harris Teeter
    Drumsticks are filed under "Meat", so they failed a string equality test
    and the bill was built from breast at 2.5x the price.

    Extracted so the client chart (GFP-144) applies the SAME eligibility rule
    as the bill. The two disagreeing is precisely the bug that ticket exists
    to fix, so they must not be able to.
    """
    # GFP-271: this gate runs BEFORE the preference check and applies to every
    # client, including one who has expressed no preferences at all.
    #
    # The old code returned `ranked` untouched when `applied_categories` was
    # empty, reading "no preferences" as "no filtering". But stock is not a
    # preference question -- `protein_kind` has disqualified broth and gravy all
    # along, which is exactly why the cheapest-meat strip never showed them. The
    # bill simply never asked, so a preference-less client got the one answer
    # every other surface already knew to reject.
    #
    # Deliberately `is_not_a_protein_buy` and not `is_disqualified`: the latter
    # also rejects plant-based analogues, and using it here would delete a vegan
    # client's entire diet on the way to removing a stock cube.
    buyable = [
        item for item in ranked
        if not protein_kind.is_not_a_protein_buy(item.get("item_name"))
    ]
    if not applied_categories:
        return buyable
    allowed_ids = nutrition.food_ids_in(applied_categories, conn=conn)
    return [item for item in buyable if item.get("food_id") in allowed_ids]


def effective_cost_per_gram(
    ranked: list[dict],
    target_grams: float,
    applied_categories: list[str],
    selection: "Selection",
    conn: sqlite3.Connection,
) -> float | None:
    """What one day's plan actually costs per gram of protein (GFP-144).

    Total cost divided by grams covered, for the plan the CURRENT selection
    produces -- not for the cheapest thing the client is willing to eat.

    The distinction is the whole ticket. The client chart used to filter by
    category alone, so with "include every protein I ticked" switched on it
    reported that a client's preferences cost nothing extra while the bill
    beside it showed +$2.70/day. Both were on screen at once.

    ``None`` when nothing could be allocated: no deals, or none eligible. A
    day with no answer draws no point rather than a zero, which would read as
    "free" -- the same rule savings.py holds to.

    Takes an ALREADY-RANKED pool because ranking a day of history is the
    expensive part (~40 ms) and does not depend on the selection, while this
    is effectively free. A caller redrawing on every checkbox click ranks once
    and calls this many times.
    """
    candidates = _eligible(ranked, applied_categories, conn)
    lines = _select(candidates, target_grams, applied_categories, selection, conn)
    covered = sum(line.grams_protein for line in lines)
    if covered <= 0:
        return None
    return sum(line.cost for line in lines) / covered


def _build_bill(
    customer_id: int | None,
    target_grams: float,
    categories: Iterable[str] | None,
    conn: sqlite3.Connection,
    selection: "Selection | None" = None,
) -> Bill:
    """Shared engine behind :func:`daily_bill`/:func:`daily_bill_for`, once a
    valid (non-``None``) daily target is already in hand.

    See the module docstring for the amortisation, allocation and
    zero-preferences-means-unconstrained rules this implements.
    """
    selection = selection or Selection()
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
        all_deals, conn=conn, limit=0, min_confidence=MIN_MATCH_CONFIDENCE
    )
    excluded_deals = len(all_deals) - len(ranked)

    candidates = _eligible(ranked, applied_categories, conn)

    lines = _select(candidates, target_grams, applied_categories, selection, conn)

    return Bill(
        target_grams=target_grams,
        lines=lines,
        total_cost=sum(line.cost for line in lines),
        covered_grams=sum(line.grams_protein for line in lines),
        excluded_deals=excluded_deals,
        considered_deals=len(candidates),
        categories=applied_categories,
        selection=selection,
    )


def daily_bill_for(
    customer: Customer,
    categories: Iterable[str] | None = None,
    conn: sqlite3.Connection | None = None,
    selection: Selection | None = None,
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
    return _build_bill(customer.id, target.daily_grams, categories, own, selection)


def compare_bills_for(
    customer: Customer,
    categories: Iterable[str] | None = None,
    conn: sqlite3.Connection | None = None,
    selection: Selection | None = None,
) -> BillComparison | None:
    """Unconstrained baseline beside the preference-constrained plan (GFP-49).

    ``None`` on the same terms as :func:`daily_bill_for` -- no ``weight_kg``,
    no target, no bill, and therefore nothing to compare.

    The baseline is a genuine unconstrained optimum: the same allocation run
    over every priced deal (``categories=[]``), not the cheapest single item.
    ``categories`` overrides the constrained side only, so a caller can price
    a hypothetical preference set without writing it to the client's record --
    which is exactly what GFP-52's checkboxes need, since a checkbox is a
    filter and should not require a save step.
    """
    own = conn or db.connect()
    target = targets.protein_target_for(customer, conn=own)
    if target is None:
        return None
    # GFP-136: the BASELINE stays unconstrained in every sense -- no
    # categories AND no selection constraints. It is the "what could this
    # cost at best" figure, and applying the user's constraints to it would
    # make the comparison meaningless.
    return BillComparison(
        baseline=_build_bill(customer.id, target.daily_grams, [], own),
        constrained=_build_bill(
            customer.id, target.daily_grams, categories, own, selection
        ),
    )


def compare_bills(
    customer_id: int,
    categories: Iterable[str] | None = None,
    conn: sqlite3.Connection | None = None,
    selection: Selection | None = None,
) -> BillComparison | None:
    """:func:`compare_bills_for` for a customer looked up by id."""
    own = conn or db.connect()
    customer = CustomerRepository.get(customer_id, conn=own)
    if customer is None:
        return None
    return compare_bills_for(
        customer, categories=categories, conn=own, selection=selection
    )


def daily_bill(
    customer_id: int,
    categories: Iterable[str] | None = None,
    conn: sqlite3.Connection | None = None,
    selection: Selection | None = None,
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
    return daily_bill_for(customer, categories=categories, conn=own, selection=selection)


def rank_history_by_day(
    days: int,
    conn: sqlite3.Connection,
    today: "date | None" = None,
    postal_code: str | None = None,
) -> dict[str, list[dict]]:
    """One ranked candidate pool per day, from ``price_history`` (GFP-144).

    Reads price_history rather than ``deals`` because a scrape REPLACES deals
    and only appends to history -- the same reason service/trends.py reads it.

    Each day's rows are put back into the shape ``rank_by_cost_per_gram_protein``
    already consumes, so the $/g chain, the size parsing and the food matching
    are the ones the bill uses rather than a second implementation that could
    disagree with it.

    THIS IS THE EXPENSIVE HALF, about 40 ms per day of history against ~1,900
    rows, and it is independent of the selection. Callers that redraw when a
    checkbox moves should hold on to the result and re-run only
    :func:`effective_cost_per_gram`, which is free by comparison.
    """
    anchor = today or date.today()
    since = (anchor - timedelta(days=days)).isoformat()

    # ONE ZIP IS ONE MARKET. Now that the ZIP can be changed from the main
    # window (GFP-122), an install can hold history captured under two of them,
    # and averaging Greensboro prices with Beverly Hills prices into a single
    # line would be a quiet lie of exactly the kind service/trends.py refuses.
    # None means "every ZIP", which is what the CLI and the tests want.
    where = [
        "captured_at >= ?",
        "COALESCE(dollar_price, sale_price, regular_price) > 0",
    ]
    params: list[object] = [since]
    if postal_code:
        where.append("postal_code = ?")
        params.append(postal_code)

    rows = conn.execute(
        "SELECT substr(captured_at, 1, 10) AS day, store, item_name, "
        "       COALESCE(dollar_price, sale_price, regular_price) AS dollar_price "
        "FROM price_history "
        f"WHERE {' AND '.join(where)} "
        # Deterministic, for the same reason the deal query is: SQLite gives no
        # row order without one, and rank_by_cost_per_gram_protein's sort is
        # stable, so ties would otherwise swap between runs.
        "ORDER BY day, store, item_name",
        params,
    ).fetchall()

    by_day: dict[str, list[dict]] = {}
    for row in rows:
        by_day.setdefault(row["day"], []).append({
            "store": row["store"],
            "item_name": row["item_name"],
            "dollar_price": row["dollar_price"],
        })

    return {
        day: savings.rank_by_cost_per_gram_protein(
            deals, conn=conn, limit=0, min_confidence=MIN_MATCH_CONFIDENCE
        )
        for day, deals in by_day.items()
    }


@dataclass(frozen=True)
class WeekPlan:
    """Seven days as SEVEN ALLOCATIONS, not one multiplied by seven (GFP-142).

    Until this existed the weekly view was literally ``daily * 7``, so if
    drumsticks were cheapest today they were cheapest all week and the plan was
    drumsticks seven days running. There was no object to vary -- which is why
    GFP-142 recorded that the week had to become first-class before variety
    could be expressed at all.
    """

    days: list[Bill]
    selection: Selection

    @property
    def total_cost(self) -> float:
        return sum(day.total_cost for day in self.days)

    @property
    def covered_grams(self) -> float:
        return sum(day.covered_grams for day in self.days)

    @property
    def target_grams(self) -> float:
        return sum(day.target_grams for day in self.days)

    @property
    def is_complete(self) -> bool:
        """Every day hit its target. THE INVARIANT, asked of the week."""
        return all(day.is_complete for day in self.days)

    @property
    def categories(self) -> list[str]:
        """The preference list every day was built under.

        Exposed on the week rather than left for callers to dig out of
        ``days[0]``: a caller reaching into one day to learn something about
        the whole week is a caller that will eventually read the wrong day.
        """
        return list(self.days[0].categories) if self.days else []

    @property
    def distinct_items(self) -> int:
        return len({line.item_name for day in self.days for line in day.lines})

    @property
    def repeated_days(self) -> int:
        """Days whose item set is identical to the day before.

        What "Mix It Up" is judged on, and the number the panel can show
        instead of asserting that variety happened.
        """
        sets = [frozenset(line.item_name for line in day.lines) for day in self.days]
        return sum(1 for a, b in zip(sets, sets[1:]) if a and a == b)


def rank_current_deals(conn: sqlite3.Connection) -> tuple[list[dict], int]:
    """The ranked candidate pool for THIS week's deals, and how many were dropped.

    Split out so a caller solving the same week several ways -- the GFP-153
    comparison grid runs four -- pays the ranking once. It is the expensive
    part (~40 ms over ~1,900 deals) and it does not depend on the selection or
    the client.
    """
    all_deals = service.fetch_deals(hide_expired=True, conn=conn)
    ranked = savings.rank_by_cost_per_gram_protein(
        all_deals, conn=conn, limit=0, min_confidence=MIN_MATCH_CONFIDENCE
    )
    return ranked, len(all_deals) - len(ranked)


def week_plan(
    customer_id: int,
    categories: Iterable[str] | None = None,
    selection: Selection | None = None,
    conn: sqlite3.Connection | None = None,
    ranked: tuple[list[dict], int] | None = None,
    days: int = DAYS_IN_WEEK,
) -> WeekPlan | None:
    """Seven daily plans, varied or not per ``selection``. ``None`` with no target."""
    own = conn or db.connect()
    customer = CustomerRepository.get(customer_id, conn=own)
    if customer is None:
        return None
    return week_plan_for(
        customer, categories, selection, own, ranked, days
    )


def week_plan_for(
    customer: Customer,
    categories: Iterable[str] | None = None,
    selection: Selection | None = None,
    conn: sqlite3.Connection | None = None,
    ranked: tuple[list[dict], int] | None = None,
    days: int = DAYS_IN_WEEK,
) -> WeekPlan | None:
    """The same plan for an ALREADY-LOADED customer, as :func:`daily_bill_for` is.

    Exists because GFP-169's shopping list holds a :class:`Customer`, not an
    id, and re-fetching it to build the plan would be the second lookup that
    lets the list and the on-screen plan drift apart -- which is the whole
    defect that ticket exists to close.

    ``None`` on the same terms as :func:`daily_bill_for`: no weight on file
    means no target, and a plan built on a guessed weight is worse than none.
    """
    own = conn or db.connect()
    target = targets.protein_target_for(customer, conn=own)
    if target is None or not target.daily_grams:
        return None
    return _week_from(
        target.daily_grams, customer.id, categories, selection, own, ranked, days
    )


def _week_from(
    daily_target: float,
    customer_id: int | None,
    categories: Iterable[str] | None,
    selection: Selection | None,
    conn: sqlite3.Connection,
    ranked: tuple[list[dict], int] | None = None,
    days: int = DAYS_IN_WEEK,
) -> WeekPlan:
    """Build the week one day at a time.

    **The invariant, and where it is enforced.** Variety is a preference about
    presentation; the protein target is not negotiable. So each day is first
    solved with recently-used items withheld, and if that cannot cover the
    target the day is re-solved with the full pool. Variety gives way. The
    nutrition never does (GFP-131/GFP-136).

    ``days`` defaults to a week and is a parameter only because GFP-169 needs
    a shopping list to cover the period the caller asked for. The variety
    lookback stays fixed at :data:`VARIETY_LOOKBACK_DAYS` regardless: it is a
    statement about not eating the same thing twice running, which does not
    change because the period got longer.
    """
    if days < 1:
        raise ValueError("a plan covers at least one day")
    selection = selection or Selection()

    if customer_id is not None and categories is None:
        applied = list(preferences.list_preferences(customer_id, conn=conn))
    else:
        applied = sorted({c for c in (categories or ())})

    pool_ranked, excluded = ranked if ranked is not None else rank_current_deals(conn)
    eligible = _eligible(pool_ranked, applied, conn)

    built: list[Bill] = []
    recent: list[set[str]] = []            # item names used, most recent last

    for _day in range(days):
        pool = eligible
        if selection.vary_week:
            avoid = {
                name
                for names in recent[-VARIETY_LOOKBACK_DAYS:]
                for name in names
            }
            varied = [c for c in eligible if c.get("item_name") not in avoid]
            lines = _select(varied, daily_target, applied, selection, conn)
            if sum(l.grams_protein for l in lines) + CENT < daily_target:
                # Withholding those items cannot feed the client today. Fall
                # back to the full pool rather than deliver less protein.
                lines = _select(pool, daily_target, applied, selection, conn)
        else:
            lines = _select(pool, daily_target, applied, selection, conn)

        built.append(Bill(
            target_grams=daily_target,
            lines=lines,
            total_cost=sum(l.cost for l in lines),
            covered_grams=sum(l.grams_protein for l in lines),
            excluded_deals=excluded,
            considered_deals=len(eligible),
            categories=applied,
            selection=selection,
        ))
        recent.append({l.item_name for l in lines})

    return WeekPlan(days=built, selection=selection)
