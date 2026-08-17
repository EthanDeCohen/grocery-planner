# ######### decohen-partners ##########
# Protein Ledger
"""The week, and the budget it is measured against (GFP-127, 128, 131).

Three tickets, one idea: take the plan the optimiser already produced, show it
over the horizon people actually shop in, and say how it sits against what this
client can spend.

**THE OPTIMISER IS NOT TOUCHED.** Nothing in this module re-ranks, re-filters or
re-solves anything. It calls :mod:`grocery_planner.bill`, which minimises cost
per gram of protein for the target, and then reports. The user was explicit and
repeated it three times -- "I don't want to upset the optimizer" -- and the
reason is stronger than preference: a budget-constrained solver can quietly
under-deliver protein to make a number fit, and on a tool that computes what
somebody eats that is the worst failure available. Keeping the budget out of the
solve makes it impossible rather than merely forbidden.

WHY SEVEN DAYS AND NOT THIRTY
-----------------------------
:mod:`grocery_planner.bill` amortises weekly ad prices across a single day's
protein. Seven of those days is close to exact **within one ad period**, because
that is the period the price came from. Thirty is not: it asserts this week's
promotions run all month, which is false. So the week ships in v1 and the month
is GFP-126, built from price history and labelled an estimate.

WHEN THE PLAN COSTS MORE THAN THE BUDGET
----------------------------------------
Exactly two options, per GFP-131, and what they have in common matters more than
what separates them:

1. **Keep the cost low** -- relax a preference. :func:`relaxations` prices each
   one so the choice is a number rather than a suggestion.
2. **Go above budget** -- spend more.

**Neither reduces the protein target.** Cost gives way, or a preference gives
way; the nutrition never does. That invariant is the whole design, and anything
later that adds a third option trimming the target is reintroducing what was
rejected.

The app reports and prices the options. The nutritionist decides -- they are the
professional in the room, and "allow pork" is not a neutral thing for software
to recommend on its own authority.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable

from . import bill as bill_module
from . import db, nutrition, preferences, protein_kind
from .customers import Customer, CustomerRepository

#: Seven, and it is a definition rather than a setting -- see GFP-89. A "week"
#: is not a threshold somebody should be able to tune.
DAYS_PER_WEEK = 7

#: Below this many dollars, "over budget" is rounding rather than a real
#: overspend. Floating-point cost sums land a fraction of a cent either side of
#: exact, and reporting a client as over budget by $0.004 would be technically
#: true and practically noise.
CENT = 0.005


@dataclass(frozen=True)
class WeeklyPlan:
    """The optimiser's plan over seven days, against this client's budget."""

    daily: bill_module.Bill
    #: The client's weekly budget, or ``None`` if none is set. Null is not
    #: zero: a client with no budget is unmeasured, not permanently over.
    budget: float | None = None
    #: The real seven-day allocation, when one was solved (GFP-155).
    #:
    #: WHY THIS EXISTS RATHER THAN daily * 7. Once Mix It Up became the
    #: default (GFP-142), multiplying one day stopped describing the plan the
    #: client is actually shown: the flat week is $22.17 where the varied one
    #: is $50.42. A budget verdict computed from the multiplication could
    #: therefore be wrong BY MORE THAN 2x -- telling a nutritionist their
    #: client is comfortably under when the plan on screen is far over.
    #:
    #: ``None`` keeps the old behaviour for callers that have not been given a
    #: week, so nothing silently changes meaning underneath them.
    week: "bill_module.WeekPlan | None" = None

    @property
    def weekly_cost(self) -> float:
        """What seven days actually cost.

        The real week when there is one; otherwise a day multiplied, which is
        exact only while every day is identical.
        """
        if self.week is not None:
            return self.week.total_cost
        return self.daily.total_cost * DAYS_PER_WEEK

    @property
    def weekly_target_grams(self) -> float:
        if self.week is not None:
            return self.week.target_grams
        return self.daily.target_grams * DAYS_PER_WEEK

    @property
    def has_budget(self) -> bool:
        return self.budget is not None

    @property
    def over_by(self) -> float | None:
        """Dollars over the budget, or ``None`` with no budget set.

        Negative means under. Callers that want "is this a problem" should ask
        :attr:`is_over`, which applies the rounding tolerance.
        """
        if self.budget is None:
            return None
        return self.weekly_cost - self.budget

    @property
    def is_over(self) -> bool:
        """Meaningfully over budget -- not over by a fraction of a cent."""
        return self.over_by is not None and self.over_by > CENT

    @property
    def headroom(self) -> float | None:
        """Dollars left under the budget, floored at zero."""
        if self.over_by is None:
            return None
        return max(0.0, -self.over_by)


@dataclass(frozen=True)
class Relaxation:
    """What allowing one more protein category back in would do.

    The unit of GFP-131's first option. "Relax a preference" is useless as a
    sentence and valuable as a number: *allowing pork brings the week to $41,
    $12 under budget* is something a nutritionist can act on.
    """

    category: str
    weekly_cost: float
    #: Dollars saved against the current plan. Can be 0.0 -- a category that
    #: contains nothing cheaper changes nothing, and saying so is the honest
    #: answer rather than hiding the row.
    saves: float
    #: Does this ONE change bring the week within budget?
    brings_under_budget: bool
    #: The plan itself, so a caller can show what it would actually contain.
    plan: WeeklyPlan


def weekly_plan(
    customer: Customer,
    categories: Iterable[str] | None = None,
    conn: sqlite3.Connection | None = None,
    selection: "bill_module.Selection | None" = None,
    ranked: "tuple[list[dict], int] | None" = None,
    cache: dict | None = None,
) -> WeeklyPlan | None:
    """This client's plan over seven days. ``None`` with no protein target.

    ``categories`` overrides the client's stored preferences without saving
    them, mirroring :func:`bill.compare_bills_for` -- a checkbox is a filter,
    and pricing a hypothetical should not require a write.

    ``ranked``/``cache``: the candidate pool and the resolved protein chain,
    when the caller is solving the SAME week many ways and has already paid for
    them once (GFP-335). Both default to None, which reproduces the old
    behaviour exactly -- each is a reuse of work, never a change of answer.
    """
    own = conn or db.connect()
    daily = bill_module.daily_bill_for(
        customer, categories=categories, conn=own, selection=selection,
        cache=cache,
    )
    if daily is None:
        return None
    week = None
    if customer.id is not None:
        # The real seven days, so the budget is measured against the plan the
        # client is shown rather than against a multiplication of day one.
        week = bill_module.week_plan(
            customer.id, categories=categories, selection=selection, conn=own,
            ranked=ranked,
        )
    return WeeklyPlan(daily=daily, budget=customer.weekly_budget, week=week)


def weekly_plan_for_id(
    customer_id: int,
    categories: Iterable[str] | None = None,
    conn: sqlite3.Connection | None = None,
) -> WeeklyPlan | None:
    own = conn or db.connect()
    customer = CustomerRepository.get(customer_id, conn=own)
    if customer is None:
        return None
    return weekly_plan(customer, categories=categories, conn=own)


def _categories_with_candidates(conn: sqlite3.Connection) -> set[str]:
    """Categories that have at least one priced, unexpired, matched deal.

    Folded to lowercase because `foods.category` and the preference strings are
    not reliably cased the same way, and a case mismatch here would silently
    drop a real relaxation rather than a useless one.

    One query, not one per category: the point of this is to replace hundreds
    of week-plan solves, so it must not itself become a loop over categories.
    """
    rows = conn.execute(
        "SELECT DISTINCT LOWER(TRIM(f.category)) "
        "FROM deals d "
        "JOIN deal_food_match m ON m.store = d.store AND m.item_name = d.item_name "
        "JOIN foods f ON f.id = m.food_id "
        "WHERE COALESCE(d.dollar_price, d.sale_price, d.regular_price) > 0 "
        "  AND NOT (d.valid_to IS NOT NULL AND d.valid_to <> '' "
        "           AND d.valid_to < DATE('now')) "
        "  AND f.category IS NOT NULL AND TRIM(f.category) <> ''"
    ).fetchall()
    return {row[0] for row in rows if row[0]}


def relaxations(
    customer: Customer,
    categories: Iterable[str] | None = None,
    conn: sqlite3.Connection | None = None,
    selection: "bill_module.Selection | None" = None,
) -> list[Relaxation]:
    """Price each preference this client could relax, cheapest week first.

    One entry per category the client is NOT currently allowing. Each is the
    existing optimiser run with that one category added back -- never a new
    kind of solve, and never the budget entering the calculation.

    Empty when the client has no preferences set at all: an unconstrained plan
    has nothing to relax, and its cost is simply what protein costs.
    """
    own = conn or db.connect()
    allowed = list(
        categories if categories is not None
        else preferences.list_preferences(customer.id, conn=own)
    )
    if not allowed:
        # No preferences means unconstrained, which is already the cheapest
        # the optimiser can do. There is nothing to give back.
        return []

    # GFP-156: only categories the nutritionist can actually TICK -- advising
    # "allow X" must name a control that exists, or the saving it quotes cannot
    # be acted on.
    #
    # GFP-335: THAT IS THE TEN PROTEIN KINDS, not the retailer taxonomy.
    #
    # This read `nutrition.list_categories()`, which is SELECT DISTINCT category
    # FROM foods -- whatever string each scraper happened to write. On a real
    # database that is 245 values including "Bread Flour", "Baby Food Purees"
    # and "Blended and Fruit on the Bottom". Every one of them was a complete
    # week-plan solve before a client page could be drawn, so a beef-only client
    # waited 249 seconds while the optimiser priced her week on baby food.
    #
    # `protein_kind.KINDS` (GFP-106) is the curated vocabulary that already
    # exists for exactly this: ten kinds a person would actually choose between.
    # Nothing needed migrating -- stored preferences were already kinds
    # ('beef', 'chicken', 'Dairy', 'fish') -- and `nutrition.food_ids_in`
    # already matches a preference against `foods.protein_kind` as well as
    # `foods.category`, so this narrows what is OFFERED without changing what
    # any preference MEANS.
    known = sorted({k for k in protein_kind.KINDS if k})
    excluded = [c for c in known if c not in allowed]
    if not excluded:
        return []

    # GFP-335: A CATEGORY WITH NOTHING TO BUY CANNOT CHANGE THE ANSWER.
    #
    # Each entry below is a COMPLETE week-plan solve. `known` comes from
    # `SELECT DISTINCT category FROM foods`, which is the retailer taxonomy
    # verbatim -- 245 of them on a real database, including "Bread Flour" and
    # "Baby Food Purees". A client allowing one category therefore priced 244
    # hypothetical weeks before their page could be drawn: measured at 249
    # SECONDS for a beef-only client, 4.7M cost_per_gram_protein calls and 6M
    # sqlite executes for a single click.
    #
    # Almost all of that was spent solving weeks that could not differ from the
    # current one, because the category has no purchasable protein behind it.
    # Relaxing to a category with no candidate deals returns the same plan at
    # the same cost and a saving of zero, which is not advice.
    #
    # So this is a filter, not a heuristic: it removes only categories that
    # provably cannot appear in a plan, and every relaxation that survives is
    # solved exactly as before. The answers are unchanged; the count is not.
    priced = _categories_with_candidates(own)
    excluded = [c for c in excluded if c.strip().lower() in priced]
    if not excluded:
        return []

    # GFP-156: every plan here is solved under the SAME selection as the one
    # on screen. Advice priced under different constraints from the plan it is
    # advising about would be the GFP-144 defect again -- two numbers that look
    # comparable and are not.
    # PAY FOR THE POOL ONCE (GFP-335).
    #
    # Every plan below is solved against the same deals -- the ranking depends
    # on neither the client nor the selection, which is what
    # `bill.rank_current_deals` was split out to exploit ("so a caller solving
    # the same week several ways pays the ranking once"). That intent existed
    # one level too low: it was honoured inside a single solve and ignored
    # across the hundreds of solves this loop performs.
    #
    # Measured before: 206 categories x (1 daily + 7 daily) rankings of ~11,700
    # deals each -- 4.7M cost_per_gram_protein calls, 6M sqlite executes, and a
    # client page that took 249 seconds to open.
    pool = bill_module.rank_current_deals(own)
    resolved: dict = {}

    current = weekly_plan(customer, categories=allowed, conn=own, selection=selection,
                          ranked=pool, cache=resolved)
    if current is None:
        return []

    found: list[Relaxation] = []
    for category in excluded:
        plan = weekly_plan(
            customer, categories=allowed + [category], conn=own, selection=selection,
            ranked=pool, cache=resolved,
        )
        if plan is None:
            continue
        found.append(
            Relaxation(
                category=category,
                weekly_cost=plan.weekly_cost,
                saves=current.weekly_cost - plan.weekly_cost,
                brings_under_budget=not plan.is_over and plan.has_budget,
                plan=plan,
            )
        )
    # Cheapest week first: the most useful suggestion is the one that helps
    # most, and a caller showing only one row should get that one.
    found.sort(key=lambda r: r.weekly_cost)
    return found


@dataclass(frozen=True)
class BudgetAdvice:
    """What to tell the nutritionist when the plan does not fit the budget.

    Deliberately a small, closed set of outcomes rather than free text, so the
    UI cannot invent a third option (GFP-131) and the wording stays the
    caller's business.
    """

    plan: WeeklyPlan
    #: Cheapest-week-first, only those that actually help.
    options: list[Relaxation]
    #: True when even allowing EVERYTHING is still over budget -- the
    #: preferences are not the problem, the budget is unreachable for this
    #: target at current prices. Real information about the client's
    #: situation, not a failure of the app.
    unreachable: bool = False

    @property
    def is_over(self) -> bool:
        return self.plan.is_over

    @property
    def best(self) -> Relaxation | None:
        """The single relaxation that helps most, if any does."""
        return self.options[0] if self.options else None

    @property
    def any_single_change_is_enough(self) -> bool:
        return any(r.brings_under_budget for r in self.options)


def advise(
    customer: Customer,
    categories: Iterable[str] | None = None,
    conn: sqlite3.Connection | None = None,
    selection: "bill_module.Selection | None" = None,
) -> BudgetAdvice | None:
    """The over-budget situation, priced. ``None`` with no target or no budget.

    Returns advice even when the plan is UNDER budget -- ``is_over`` is False
    and ``options`` is empty -- so a caller has one thing to render rather than
    two code paths.
    """
    own = conn or db.connect()
    plan = weekly_plan(customer, categories=categories, conn=own, selection=selection)
    if plan is None or not plan.has_budget:
        return None
    if not plan.is_over:
        return BudgetAdvice(plan=plan, options=[])

    # Is the budget reachable AT ALL? Priced with every category allowed, which
    # is the cheapest the optimiser can possibly go. If that is still over, no
    # amount of relaxing preferences will help and saying "allow pork" would be
    # actively misleading.
    unconstrained = weekly_plan(
        customer, categories=[], conn=own, selection=selection
    )
    unreachable = unconstrained is not None and unconstrained.is_over

    options = [
        r for r in relaxations(customer, categories, own, selection=selection)
        if r.saves > CENT
    ]
    return BudgetAdvice(plan=plan, options=options, unreachable=unreachable)
