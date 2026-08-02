"""Tests for GFP-64: the GUI formula validator must accept every formula a
real consumer can evaluate, not just the deal-scoring ones.

The GUI formula editor (grocery_planner/gui/formulas.py since GFP-35 moved it
out of gui/app.py) validates a formula on Save by running it against a
stand-in "probe" dict of variable names before it's ever stored. GFP-29
made the daily protein target (``targets.FORMULA_NAME`` ==
"protein_target_daily") a user-editable formula whose variables are
``weight_kg`` and ``protein_factor`` (see grocery_planner/targets.py's
``PROTEIN_TARGET_VARS``) -- but the probe only ever listed the variables
``savings.score_deals`` supplies (``savings.DEAL_SCORE_VARS``), so a
nutritionist could not save a protein-target formula through the GUI at
all: it was rejected as invalid before being stored, even though the same
expression works fine from code (see tests/test_targets.py).

Skipped where the ``gui`` extra isn't installed, same as tests/test_gui.py.
"""
from __future__ import annotations

import pytest

from grocery_planner import db
from grocery_planner.savings import DEAL_SCORE_VARS
from grocery_planner.targets import PROTEIN_TARGET_VARS

pytest.importorskip("PySide6", reason="GUI extra not installed")

from simpleeval import simple_eval  # noqa: E402

from grocery_planner.gui.formulas import _formula_probe  # noqa: E402


def test_probe_covers_every_deal_score_var(conn):
    probe = _formula_probe(conn)
    for name in DEAL_SCORE_VARS:
        assert name in probe


def test_probe_covers_every_protein_target_var(conn):
    probe = _formula_probe(conn)
    for name in PROTEIN_TARGET_VARS:
        assert name in probe


def test_a_deal_scoring_formula_still_validates(conn):
    # The pre-existing case (GFP-11): must keep working unchanged.
    simple_eval("saved_percent / unit_price", names=_formula_probe(conn))


def test_the_protein_target_formula_now_validates(conn):
    # This is the bug: GFP-29's default protein-target expression, rejected
    # before this fix even though grocery_planner.targets evaluates it fine.
    simple_eval("weight_kg * protein_factor", names=_formula_probe(conn))


def test_a_formula_mixing_both_kinds_of_vars_validates(conn):
    # Nothing stops a nutritionist writing a formula that references both
    # a deal number and a customer number; the probe must not silently
    # assume a formula is one kind or the other.
    simple_eval("unit_price * protein_factor", names=_formula_probe(conn))


def test_probe_still_rejects_a_genuine_typo(conn):
    # The whole point of validating at all: a name that no real consumer
    # supplies must still be caught before the formula is ever stored.
    with pytest.raises(Exception):
        simple_eval("wieght_kg * protein_factor", names=_formula_probe(conn))


def test_probe_includes_live_profile_context(conn):
    # Every real consumer (score_deals, targets) also merges in
    # formulas._profile_context(conn); the probe should too, so a formula
    # referencing a profile value is validated the same way it will
    # actually be evaluated.
    conn.execute(
        "INSERT INTO profile(key, value) VALUES (?, ?)", ("weekly_budget", "150")
    )
    conn.commit()
    probe = _formula_probe(conn)
    assert "weekly_budget" in probe
    simple_eval("unit_price * weekly_budget", names=probe)
