"""User-defined formula storage and safe evaluation."""
import pytest

from grocery_planner import formulas


def test_set_and_eval_with_profile(conn):
    conn.execute("INSERT INTO profile(key, value) VALUES ('weight', '82')")
    conn.commit()
    formulas.set_formula(conn, "target_protein", "weight * 1.6")
    assert formulas.evaluate(conn, "target_protein") == pytest.approx(131.2)


def test_eval_override_var(conn):
    formulas.set_formula(conn, "p", "weight * 2")
    assert formulas.evaluate(conn, "p", {"weight": 100}) == 200


def test_conditional_expression(conn):
    formulas.set_formula(conn, "t", "weight * 1.6 if weight < 100 else weight * 1.8")
    assert formulas.evaluate(conn, "t", {"weight": 120}) == pytest.approx(216.0)


def test_unknown_formula_raises(conn):
    with pytest.raises(KeyError):
        formulas.evaluate(conn, "nope")
