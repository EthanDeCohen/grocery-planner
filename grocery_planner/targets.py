"""Protein target engine (GFP-29): how many grams of protein a client needs.

GFP-28 gave us the client record; this module turns it into the number the
rest of the product optimises against -- daily (and weekly, since the
shopping bill is weekly) grams of protein for one customer.

**Units, again.** ``grocery_planner/customers.py`` splits a customer's weight
into ``weight_kg`` (canonical, always kilograms) and ``weight_unit``
(display-only, whatever the nutritionist typed) specifically so the 2.2x
dosing bug -- 90 lb mistaken for 90 kg -- can't happen downstream. This
module is that downstream: :func:`protein_target`/:func:`protein_target_for`
read ``Customer.weight_kg`` ONLY. Never a bare ``weight`` in an unspecified
unit, and never ``weight_display`` (which exists purely to echo a value back
in the unit the user thinks in, not to feed a calculation).

**Honesty.** Same rule as ``grocery_planner/savings.py``: "A size we cannot
read is None, never a guess." Here: a client with no ``weight_kg`` on file
gets ``None``, never a target computed from a made-up number.

**protein_factor stays user-editable.** The ticket's 1.6 g/kg default lives
in two places, both editable, neither hard-coded into the arithmetic here:

* ``Customer.protein_factor`` (``grocery_planner/customers.py``) -- a
  per-client override, already settable via ``CustomerRepository.save``.
* The formula named by :data:`FORMULA_NAME` in the ``formulas`` table
  (``grocery_planner/formulas.py``) -- the *expression itself*
  (``weight_kg * protein_factor`` by default) is data a nutritionist can
  replace with ``gplan formula set protein_target_daily "..."`` to express
  something ``weight_kg * protein_factor`` cannot (an age- or
  activity-level-dependent rule, say) without a code change. If no such
  formula has been saved yet, :data:`DEFAULT_FORMULA_EXPRESSION` is used, so
  a fresh database works with no setup step. Either way the multiplication
  itself runs through ``simpleeval`` (never a literal ``*`` on a hard-coded
  1.6 in this module), matching how ``grocery_planner/service/deals.py``'s
  ``score_deals`` evaluates user formulas against a profile + row context.

**Result carries its unit.** :func:`protein_target` never hands back a bare
float -- :class:`ProteinTarget` pairs each of the daily and weekly figures
with its own unit string, so a caller cannot mix up which cadence a number
belongs to (the same spirit as ``grocery_planner/savings.py``'s ``Size``
pairing a quantity with its unit).

Out of scope here (see the ticket): cheapest-protein recommendation logic
(GFP-31/GFP-48), per-client protein *preferences* as opposed to the target
itself (GFP-30), and GUI/CLI wiring (GFP-33).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from simpleeval import simple_eval

from . import db, formulas
from .customers import Customer, CustomerRepository

DAYS_PER_WEEK = 7

GRAMS_PER_DAY = "g/day"
GRAMS_PER_WEEK = "g/week"

# Name of the (optional) formulas-table row a nutritionist can save to
# override how the daily target is computed. See the module docstring.
FORMULA_NAME = "protein_target_daily"

# Used only when no FORMULA_NAME row exists yet -- the ticket's baseline
# g/kg formula, expressed as data rather than a literal Python multiply so
# the "no hard-coded multiplier" rule holds even on a formula-free DB.
DEFAULT_FORMULA_EXPRESSION = "weight_kg * protein_factor"


@dataclass(frozen=True)
class ProteinTarget:
    """A customer's protein target, at both a daily and a weekly cadence.

    Each figure carries its own unit string (rather than the pair sharing
    one implied unit) so ``target.daily_grams`` and ``target.weekly_grams``
    can never be transposed or read as a bare, unit-less number by a caller.
    """

    daily_grams: float
    weekly_grams: float
    daily_unit: str = GRAMS_PER_DAY
    weekly_unit: str = GRAMS_PER_WEEK


def _formula_context(conn: sqlite3.Connection, customer: Customer) -> dict[str, Any]:
    """Profile values + this customer's weight/factor, for formula evaluation.

    Customer values are merged in *after* the profile so a client-specific
    ``protein_factor`` always wins over any practice-wide profile default of
    the same name -- the more specific setting should never be silently
    shadowed by the more general one.
    """
    return {
        **formulas._profile_context(conn),
        "weight_kg": customer.weight_kg,
        "protein_factor": customer.protein_factor,
    }


def _daily_grams(conn: sqlite3.Connection, customer: Customer) -> float:
    """Evaluate the daily protein target for one customer via the formulas engine.

    Prefers a saved :data:`FORMULA_NAME` formula (so a nutritionist's
    customization applies); falls back to :data:`DEFAULT_FORMULA_EXPRESSION`
    only when no such formula has been saved. Either branch runs the
    expression through ``simpleeval`` -- the multiplier is never inlined as
    literal Python arithmetic in this module.
    """
    extra = {"weight_kg": customer.weight_kg, "protein_factor": customer.protein_factor}
    try:
        return float(formulas.evaluate(conn, FORMULA_NAME, extra))
    except KeyError:
        names = _formula_context(conn, customer)
        return float(simple_eval(DEFAULT_FORMULA_EXPRESSION, names=names))


def protein_target_for(
    customer: Customer, conn: sqlite3.Connection | None = None
) -> ProteinTarget | None:
    """Daily/weekly protein target for an already-loaded :class:`Customer`.

    ``None`` if ``customer.weight_kg`` is unknown -- never a guess (see the
    module docstring). Reads ``weight_kg`` only; a caller cannot pass a raw
    pounds-or-kg-unspecified weight in because ``Customer`` doesn't expose
    one for math (see ``grocery_planner/customers.py``).
    """
    if customer.weight_kg is None:
        return None
    own = conn or db.connect()
    daily = _daily_grams(own, customer)
    return ProteinTarget(daily_grams=daily, weekly_grams=daily * DAYS_PER_WEEK)


def protein_target(
    customer_id: int, conn: sqlite3.Connection | None = None
) -> ProteinTarget | None:
    """Daily/weekly protein target for a customer looked up by id.

    ``None`` if no customer with this id exists, or the customer has no
    ``weight_kg`` on file. A soft-deleted customer still resolves (mirrors
    ``CustomerRepository.get``'s ``include_deleted=True`` default) since
    looking up a target is a read, not a listing a deleted client should be
    hidden from.
    """
    own = conn or db.connect()
    customer = CustomerRepository.get(customer_id, conn=own)
    if customer is None:
        return None
    return protein_target_for(customer, conn=own)
