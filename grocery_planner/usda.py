# ######### decohen-partners ##########
# Protein Ledger
"""USDA FoodData Central ingest (GFP-24): supersede curated protein figures
with sourced ones.

GFP-23 seeded ``foods``/``food_nutrients`` with 32 hand-entered, ``source=
'curated'`` protein approximations -- a starting point, not a verified
figure. This module replaces those approximations, food by food, with real
USDA FoodData Central (FDC) data: public domain, free, and citable by FDC id.

Offline-capable is a hard requirement (local-first desktop app, no
guaranteed network) so this does NOT call the USDA API at runtime. Instead,
a small, focused snapshot of FDC search results -- protein figures for the
raw/generic foods GFP-23 curated that USDA FoodData Central actually has a
plain (non-branded) SR Legacy/Foundation entry for, not the whole USDA
database -- is vendored into the repo at
``grocery_planner/data/usda_protein_snapshot.json`` (fetched during
development; see that file's ``"fetched"``/``"source"`` metadata for
provenance, and this ticket's PR description for exactly which curated
items matched and which did not). :func:`sync` loads it from disk and
applies it to the database.

This is a runtime loader (``gplan nutrition sync``), deliberately NOT a
migration: db_script/migration/ is schema history, and populating rows from
a vendored dataset is neither structure (.ddl) nor a fixed seed (.dml) --
it's an operation a user re-runs as the snapshot is refreshed. It also
avoids taking a migration sequence slot while another change is in flight
(see db_script/README.md for why the NNNN sequence must stay contiguous and
uncontested).

Reconciliation (curated -> usda), the honesty rule this ticket exists for:
    A USDA row SUPERSEDES its curated equivalent -- it does not duplicate
    it. Each snapshot entry names the exact ``foods.source_ref`` GFP-23 gave
    its curated counterpart (e.g. ``"chicken-breast-skinless-boneless"``).
    When :func:`sync` finds that curated row, it is turned into the USDA
    row *in place*: same ``foods.id`` (so nothing else that ever comes to
    reference a food_id is orphaned), but ``source`` flips to ``'usda'``,
    ``source_ref`` becomes the FDC id, and its ``food_nutrients`` protein
    figure is overwritten with the sourced value. One row per food, always;
    a caller reading ``foods.source`` afterwards sees ``'usda'`` and knows
    the figure is sourced, not estimated (GFP-50 surfaces this to the
    nutritionist).

    A curated food with no snapshot entry is left exactly as it was,
    ``source='curated'`` -- this module never invents a USDA figure for a
    food USDA does not cover here. :attr:`SyncResult.unmatched_curated`
    lists which curated rows still need one.

Idempotency: re-running :func:`sync` must not duplicate rows. A row already
flipped to ``source='usda', source_ref=<fdc_id>`` is recognized on the next
run (looked up directly, same as any other food) and only its
``food_nutrients`` figure is re-applied (a no-op if unchanged) -- the
``foods`` row itself is not touched again.

Style matches ``grocery_planner/service/deals.py`` and
``grocery_planner/nutrition.py``: module-level functions taking an optional
``conn`` that defaults to ``db.connect()``, never a connection-holding
class (SQLite connections cannot cross threads, and the GUI scrapes on a
background thread).
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from . import db

SOURCE = "usda"
CURATED = "curated"
PROTEIN = "protein"

_SNAPSHOT_PACKAGE = "grocery_planner"
_SNAPSHOT_RELATIVE = "data/usda_protein_snapshot.json"
_SNAPSHOT_NAME = "usda_protein_snapshot.json"


class SnapshotError(RuntimeError):
    """Raised when the vendored USDA snapshot is missing or malformed."""


@dataclass(frozen=True)
class SyncResult:
    """What :func:`sync` did, for the CLI (and tests) to report on.

    ``superseded`` -- curated rows that were turned into sourced USDA rows
    this run (or confirmed already-sourced on a repeat run: see
    ``already_usda``).
    ``already_usda`` -- snapshot entries whose food was already
    ``source='usda'`` when this run started (the idempotent-repeat case).
    ``inserted`` -- snapshot entries with no matching curated row at all, so
    a brand-new ``foods`` row was created directly as ``source='usda'``.
    ``unmatched_curated`` -- curated ``source_ref`` values that exist in the
    database but have no snapshot entry: still estimates, left untouched.
    """

    superseded: int
    already_usda: int
    inserted: int
    unmatched_curated: tuple[str, ...] = field(default_factory=tuple)

    @property
    def applied(self) -> int:
        """Total snapshot entries applied this run (new or repeat)."""
        return self.superseded + self.already_usda + self.inserted


def _snapshot_path() -> Path:
    """Locate the vendored snapshot across dev, installed, and frozen runs.

    Mirrors ``grocery_planner/db.py``'s ``_db_script_root``: importlib.resources
    finds it as installed package data, PyInstaller's ``sys._MEIPASS`` covers a
    frozen build (see packaging/*.spec, which add it via
    ``collect_data_files("grocery_planner")``), and a plain path relative to
    this file is the fallback for a source checkout.
    """
    try:
        with resources.as_file(
            resources.files(_SNAPSHOT_PACKAGE) / _SNAPSHOT_RELATIVE
        ) as p:
            if p.is_file():
                return p
    except (ModuleNotFoundError, FileNotFoundError):
        pass
    return Path(__file__).resolve().parent / "data" / _SNAPSHOT_NAME


def load_snapshot(path: Path | None = None) -> dict:
    """Read and parse the vendored USDA snapshot.

    Raises :class:`SnapshotError` if the file is missing or not valid JSON
    with a ``"foods"`` list -- a loud failure beats silently syncing zero
    rows, which would look like success.
    """
    target = path or _snapshot_path()
    if not target.is_file():
        raise SnapshotError(
            f"No USDA snapshot at {target}. Expected "
            f"grocery_planner/data/{_SNAPSHOT_NAME}, vendored into the repo "
            "(GFP-24) -- this app has no network dependency at runtime."
        )
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SnapshotError(f"{target} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("foods"), list):
        raise SnapshotError(
            f"{target} does not have the expected shape: a JSON object with "
            "a top-level \"foods\" list."
        )
    return data


def _upsert_protein(
    conn: sqlite3.Connection, food_id: int, amount_per_100g: float
) -> None:
    conn.execute(
        "INSERT INTO food_nutrients(food_id, nutrient, amount_per_100g, unit) "
        "VALUES (?, ?, ?, 'g') "
        "ON CONFLICT(food_id, nutrient) DO UPDATE SET "
        "amount_per_100g=excluded.amount_per_100g, unit=excluded.unit",
        (food_id, PROTEIN, amount_per_100g),
    )


def sync(
    path: Path | None = None, conn: sqlite3.Connection | None = None
) -> SyncResult:
    """Load the vendored USDA snapshot and apply it to ``foods``/``food_nutrients``.

    For each snapshot entry (matched to its GFP-23 curated counterpart by
    ``curated_ref``, see the module docstring for the full reconciliation
    rule):

    1. Already ``source='usda', source_ref=<fdc_id>`` -- re-apply the
       protein figure (idempotent no-op if unchanged) and move on.
    2. A matching ``source='curated'`` row exists -- flip it in place to
       ``source='usda'`` and overwrite its protein figure.
    3. Neither exists -- insert a brand-new ``source='usda'`` row.

    Curated rows with no snapshot entry are never touched.
    """
    own = conn or db.connect()
    snapshot = load_snapshot(path)

    superseded = already_usda = inserted = 0
    for entry in snapshot["foods"]:
        fdc_ref = str(entry["fdc_id"])
        protein = float(entry["protein_per_100g"])

        usda_row = own.execute(
            "SELECT id FROM foods WHERE source=? AND source_ref=?",
            (SOURCE, fdc_ref),
        ).fetchone()
        if usda_row:
            food_id = usda_row["id"]
            already_usda += 1
        else:
            curated_row = own.execute(
                "SELECT id FROM foods WHERE source=? AND source_ref=?",
                (CURATED, entry["curated_ref"]),
            ).fetchone()
            if curated_row:
                food_id = curated_row["id"]
                own.execute(
                    "UPDATE foods SET source=?, source_ref=?, name=?, "
                    "category=?, updated_at=datetime('now') WHERE id=?",
                    (SOURCE, fdc_ref, entry["name"], entry["category"], food_id),
                )
                superseded += 1
            else:
                # slug is the stable internal identity and must be set here
                # too (GFP-68): source_ref holds the FDC id, which is
                # provenance, and matching.py keys on slug precisely so a
                # food stays findable when its provenance changes.
                cur = own.execute(
                    "INSERT INTO foods(name, category, source, source_ref, slug) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (entry["name"], entry["category"], SOURCE, fdc_ref,
                     entry["curated_ref"]),
                )
                food_id = cur.lastrowid
                inserted += 1

        _upsert_protein(own, food_id, protein)

    matched_refs = {e["curated_ref"] for e in snapshot["foods"]}
    unmatched = [
        r["source_ref"]
        for r in own.execute(
            "SELECT source_ref FROM foods WHERE source=? ORDER BY source_ref",
            (CURATED,),
        )
        if r["source_ref"] not in matched_refs
    ]

    own.commit()
    return SyncResult(
        superseded=superseded,
        already_usda=already_usda,
        inserted=inserted,
        unmatched_curated=tuple(unmatched),
    )


def source_counts(conn: sqlite3.Connection | None = None) -> dict[str, int]:
    """``{source: count}`` over ``foods`` -- how many rows are sourced
    (``'usda'``) versus still estimated (``'curated'``), for a caller (the
    CLI, GFP-50's GUI surfacing) that needs to say so out loud rather than
    let provenance be invisible."""
    own = conn or db.connect()
    return {
        r["source"]: r["n"]
        for r in own.execute(
            "SELECT source, COUNT(*) AS n FROM foods GROUP BY source"
        )
    }
