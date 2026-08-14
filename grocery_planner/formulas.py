# ######### decohen-partners ##########
# Protein Ledger
"""User-defined formulas evaluated safely with simpleeval.

Formulas are stored as text in the DB and evaluated against a context dict
(profile values + caller-supplied vars), e.g.:
    target_protein = weight * 1.6 if weight < 100 else weight * 1.8
Never uses raw eval().
"""
from __future__ import annotations

import sqlite3
from typing import Any

from simpleeval import simple_eval


def set_formula(conn: sqlite3.Connection, name: str, expression: str, description: str = "") -> None:
    conn.execute(
        "INSERT INTO formulas(name, expression, description) VALUES (?, ?, ?) "
        "ON CONFLICT(name) DO UPDATE SET expression=excluded.expression, "
        "description=excluded.description",
        (name, expression, description),
    )
    conn.commit()


def list_formulas(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT name, expression, description FROM formulas ORDER BY name").fetchall()


def _profile_context(conn: sqlite3.Connection) -> dict[str, Any]:
    ctx: dict[str, Any] = {}
    for row in conn.execute("SELECT key, value FROM profile"):
        raw = row["value"]
        try:
            ctx[row["key"]] = float(raw)
        except (TypeError, ValueError):
            ctx[row["key"]] = raw
    return ctx


def evaluate(conn: sqlite3.Connection, name: str, extra: dict[str, Any] | None = None) -> Any:
    row = conn.execute("SELECT expression FROM formulas WHERE name=?", (name,)).fetchone()
    if row is None:
        raise KeyError(f"No formula named {name!r}")
    names = _profile_context(conn)
    if extra:
        names.update(extra)
    return simple_eval(row["expression"], names=names)
