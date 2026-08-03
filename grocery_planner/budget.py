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
from . import db, nutrition, preferences
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

    @property
    def weekly_cost(self) -> float:
        return self.daily.total_cost * DAYS_PER_WEEK

    @property
    def weekly_target_grams(self) -> float:
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
) -> WeeklyPlan | None:
    """This client's plan over seven days. ``None`` with no protein target.

    ``categories`` overrides the client's stored preferences without saving
    them, mirroring :func:`bill.compare_bills_for` -- a checkbox is a filter,
    and pricing a hypothetical should not require a write.
    """
    own = conn or db.connect()
    daily = bill_module.daily_bill_for(customer, categories=categories, conn=own)
    if daily is None:
        return None
    return WeeklyPlan(daily=daily, budget=customer.weekly_budget)


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


def relaxations(
    customer: Customer,
    categories: Iterable[str] | None = None,
    conn: sqlite3.Connection | None = None,
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

    known = nutrition.list_categories(own)
    excluded = [c for c in known if c not in allowed]
    if not excluded:
        return []

    current = weekly_plan(customer, categories=allowed, conn=own)
    if current is None:
        return []

    found: list[Relaxation] = []
    for category in excluded:
        plan = weekly_plan(customer, categories=allowed + [category], conn=own)
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
) -> BudgetAdvice | None:
    """The over-budget situation, priced. ``None`` with no target or no budget.

    Returns advice even when the plan is UNDER budget -- ``is_over`` is False
    and ``options`` is empty -- so a caller has one thing to render rather than
    two code paths.
    """
    own = conn or db.connect()
    plan = weekly_plan(customer, categories=categories, conn=own)
    if plan is None or not plan.has_budget:
        return None
    if not plan.is_over:
        return BudgetAdvice(plan=plan, options=[])

    # Is the budget reachable AT ALL? Priced with every category allowed, which
    # is the cheapest the optimiser can possibly go. If that is still over, no
    # amount of relaxing preferences will help and saying "allow pork" would be
    # actively misleading.
    unconstrained = weekly_plan(customer, categories=[], conn=own)
    unreachable = unconstrained is not None and unconstrained.is_over

    options = [r for r in relaxations(customer, categories, own) if r.saves > CENT]
    return BudgetAdvice(plan=plan, options=options, unreachable=unreachable)
