# ######### decohen-partners ##########
# Protein Ledger
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
from .customers import (
    MAX_PROTEIN_FACTOR,
    MIN_PROTEIN_FACTOR,
    PRESCRIBED_MAX_FACTOR,
    PRESCRIBED_MIN_FACTOR,
    Customer,
    CustomerRepository,
    kg_to_lb,
)

DAYS_PER_WEEK = 7

GRAMS_PER_DAY = "g/day"
GRAMS_PER_WEEK = "g/week"

# Name of the (optional) formulas-table row a nutritionist can save to
# override how the daily target is computed. See the module docstring.
FORMULA_NAME = "protein_target_daily"

# Used only when no FORMULA_NAME row exists yet -- the ticket's baseline
# g/kg formula, expressed as data rather than a literal Python multiply so
# the "no hard-coded multiplier" rule holds even on a formula-free DB.
#
# GFP-132 CORRECTED THIS. It was ``weight_kg * protein_factor`` -- grams per
# KILOGRAM of CURRENT weight -- and it was wrong twice over. The nutritionist's
# formula is:
#
#     protein (g/day) = 0.8 to 1.0 grams per POUND of DESIRED body weight
#
# so a client who wants to weigh 150 lb eats 120-150 g/day.
#
# The expression is written in POUNDS, deliberately, even though the database
# stores kilograms. Storing the factor in g/kg would mean 1.7637 -- a number
# that is a unit conversion hiding inside a user-editable field, and the first
# person to read it cannot tell whether it is a clinical judgement or an
# artefact of the metric system. In pounds the nutritionist says "point eight"
# and types 0.8. Nobody converts anything by hand, which is where unit bugs
# come from.
#
# GFP-89's rule holds and is what makes this safe: KG_PER_LB is a DEFINITION
# and lives in code as a constant; the FACTOR is a threshold and stays data.
DEFAULT_FORMULA_EXPRESSION = "desired_weight_lb * protein_factor"

# The customer-specific variable names this module supplies to a
# FORMULA_NAME formula, over and above the live profile context (see
# formulas._profile_context). GFP-64: named and importable for the same
# reason as grocery_planner/savings.py's DEAL_SCORE_VARS -- so a validator
# elsewhere (the GUI's formula editor) can derive its probe from what this
# module actually supplies rather than hand-maintaining a second list that
# can drift.
# ``desired_weight_lb`` is DERIVED, never stored -- the database keeps one
# canonical unit (kilograms) and this is computed at read time. ``weight_kg``
# stays available so a nutritionist's own saved formula that used it keeps
# working.
PROTEIN_TARGET_VARS = ("weight_kg", "desired_weight_lb", "protein_factor")


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

    # ------------------------------------------------------------------ #
    # GFP-132: the prescription is a RANGE, not a point.
    #
    # "0.8 or 1 g per pound of desired body weight" -- a client aiming for
    # 150 lb eats 120-150 g/day. The app used to flatten that to a single
    # number it had effectively invented. Both ends are carried now, and
    # ``daily_grams`` remains whatever the client's own factor produces --
    # the nutritionist's chosen point INSIDE the band, not a recomputation
    # of it.
    #
    # Nullable because a saved custom formula (GFP-64) may express something
    # that has no band at all -- an age- or activity-based rule, say. In that
    # case there is one number and no range, and saying so beats inventing
    # ends for it.
    # ------------------------------------------------------------------ #
    daily_low_grams: float | None = None
    daily_high_grams: float | None = None
    #: "desired" | "current" | "none" -- WHICH weight this was computed from,
    #: so a caller can say so rather than implying it was the desired one.
    weight_basis: str = "none"

    @property
    def has_range(self) -> bool:
        return self.daily_low_grams is not None and self.daily_high_grams is not None

    @property
    def in_range(self) -> bool | None:
        """Is the chosen daily figure inside the prescribed band?

        ``None`` when there is no band. A client whose factor was set before
        GFP-132's bounds existed can legitimately sit outside it; that is
        worth showing, not hiding.
        """
        if not self.has_range:
            return None
        return self.daily_low_grams <= self.daily_grams <= self.daily_high_grams


def target_weight_kg(customer: Customer) -> tuple[float | None, str]:
    """The weight the target is computed from, and which one it is.

    Returns ``(kilograms, "desired"|"current")``, or ``(None, "none")``.

    THE FALLBACK IS THE POINT. The nutritionist's formula is stated on DESIRED
    weight, but a client whose goal weight has not been discussed yet is an
    ordinary state rather than a broken record. Falling back to current weight
    keeps the app useful for them -- and returning WHICH was used means the
    caller can say so, instead of presenting a number computed from one thing
    as though it came from the other.

    For a client maintaining their weight the two coincide and the distinction
    never surfaces. For a client cutting or gaining it is the whole difference.
    """
    if customer.desired_weight_kg is not None:
        return customer.desired_weight_kg, "desired"
    if customer.weight_kg is not None:
        return customer.weight_kg, "current"
    return None, "none"


def _customer_vars(customer: Customer) -> dict[str, Any]:
    """This customer's values for each name in :data:`PROTEIN_TARGET_VARS`.

    ``desired_weight_lb`` is derived here rather than stored, so kilograms
    stay the single canonical unit in the database.
    """
    kilograms, _which = target_weight_kg(customer)
    return {
        "weight_kg": customer.weight_kg,
        "desired_weight_lb": None if kilograms is None else kg_to_lb(kilograms),
        "protein_factor": customer.protein_factor,
    }


def _formula_context(conn: sqlite3.Connection, customer: Customer) -> dict[str, Any]:
    """Profile values + this customer's weight/factor, for formula evaluation.

    Customer values are merged in *after* the profile so a client-specific
    ``protein_factor`` always wins over any practice-wide profile default of
    the same name -- the more specific setting should never be silently
    shadowed by the more general one.
    """
    return {**formulas._profile_context(conn), **_customer_vars(customer)}


def _has_custom_formula(conn: sqlite3.Connection) -> bool:
    """Has a nutritionist saved their own protein_target_daily formula?

    Decides whether the prescribed 0.8-1.0 band is meaningful. It is a band on
    a FACTOR, so it only describes the default formula; a custom rule (GFP-64
    allows an age- or activity-based one) may have no band at all, and drawing
    ends around it would be inventing a prescription nobody gave.

    Asks the formulas table rather than trying to evaluate and catching -- an
    evaluation failure could mean a broken expression rather than an absent
    one, and those must not be conflated.
    """
    return any(row["name"] == FORMULA_NAME for row in formulas.list_formulas(conn))


def _daily_grams(conn: sqlite3.Connection, customer: Customer) -> float:
    """Evaluate the daily protein target for one customer via the formulas engine.

    Prefers a saved :data:`FORMULA_NAME` formula (so a nutritionist's
    customization applies); falls back to :data:`DEFAULT_FORMULA_EXPRESSION`
    only when no such formula has been saved. Either branch runs the
    expression through ``simpleeval`` -- the multiplier is never inlined as
    literal Python arithmetic in this module.
    """
    extra = _customer_vars(customer)
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
    kilograms, basis = target_weight_kg(customer)
    if kilograms is None:
        return None
    own = conn or db.connect()
    daily = _daily_grams(own, customer)

    # The band, from the nutritionist's own ends applied to the same weight
    # this client's figure was computed from. Only meaningful while the
    # default formula is in play: a custom formula (GFP-64) may not be a
    # simple factor at all, so inventing ends for it would be a fabrication.
    low = high = None
    if not _has_custom_formula(own):
        pounds = kg_to_lb(kilograms)
        # GFP-282: the PRESCRIBED ends, not the admissible ones. The two were
        # one constant until the band widened to admit the federal figure;
        # using the admissible floor here would report her worked example as
        # 81-150 g/day instead of the 120-150 she actually prescribed.
        low, high = pounds * PRESCRIBED_MIN_FACTOR, pounds * PRESCRIBED_MAX_FACTOR

    return ProteinTarget(
        daily_grams=daily,
        weekly_grams=daily * DAYS_PER_WEEK,
        daily_low_grams=low,
        daily_high_grams=high,
        weight_basis=basis,
    )


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
