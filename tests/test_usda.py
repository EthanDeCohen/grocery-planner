"""Tests for grocery_planner.usda (GFP-24): the USDA FoodData Central ingest.

Kept out of test_nutrition.py / test_db.py per GFP-24's instructions (other
agents work in those files in parallel). Uses small, synthetic snapshot
files written to tmp_path for the reconciliation-logic tests, so they don't
depend on exactly which curated items the real vendored snapshot happens to
match -- plus a handful of smoke tests against the real vendored file at
grocery_planner/data/usda_protein_snapshot.json.
"""
from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from grocery_planner import usda
from grocery_planner.cli import app

CHICKEN_BREAST_REF = "chicken-breast-skinless-boneless"
runner = CliRunner()


def _write_snapshot(tmp_path, entries):
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps({"foods": entries}), encoding="utf-8")
    return path


def _snapshot_entry(curated_ref, fdc_id=999001, protein=25.0, name=None, category="chicken"):
    return {
        "curated_ref": curated_ref,
        "fdc_id": fdc_id,
        "name": name or f"USDA food for {curated_ref}",
        "category": category,
        "protein_per_100g": protein,
        "description": "test fixture entry",
        "data_type": "SR Legacy",
    }


# --------------------------------------------------------------------------- #
# Reconciliation: curated rows are superseded, not duplicated.
# --------------------------------------------------------------------------- #
def test_sync_supersedes_curated_row_in_place(conn, tmp_path):
    before = conn.execute(
        "SELECT id, source, source_ref FROM foods WHERE source_ref = ?",
        (CHICKEN_BREAST_REF,),
    ).fetchone()
    assert before is not None and before["source"] == "curated"
    food_id = before["id"]

    path = _write_snapshot(
        tmp_path, [_snapshot_entry(CHICKEN_BREAST_REF, fdc_id=171077, protein=22.5,
                                    name="Chicken, breast, boneless, skinless, raw")]
    )
    result = usda.sync(path=path, conn=conn)

    assert result.superseded == 1
    assert result.already_usda == 0
    assert result.inserted == 0

    after = conn.execute(
        "SELECT id, source, source_ref FROM foods WHERE id = ?", (food_id,)
    ).fetchone()
    # Same row (same id) -- superseded, not duplicated alongside the curated one.
    assert after["id"] == food_id
    assert after["source"] == "usda"
    assert after["source_ref"] == "171077"

    # No leftover curated row for this food, and no extra foods row either.
    dupes = conn.execute(
        "SELECT COUNT(*) AS n FROM foods WHERE source_ref = ?", (CHICKEN_BREAST_REF,)
    ).fetchone()["n"]
    assert dupes == 0

    protein = conn.execute(
        "SELECT amount_per_100g FROM food_nutrients WHERE food_id = ? AND nutrient = 'protein'",
        (food_id,),
    ).fetchone()["amount_per_100g"]
    assert protein == 22.5


def test_sync_is_idempotent_no_duplicate_rows(conn, tmp_path):
    path = _write_snapshot(
        tmp_path, [_snapshot_entry(CHICKEN_BREAST_REF, fdc_id=171077, protein=22.5)]
    )
    total_before = conn.execute("SELECT COUNT(*) AS n FROM foods").fetchone()["n"]

    first = usda.sync(path=path, conn=conn)
    assert first.superseded == 1

    second = usda.sync(path=path, conn=conn)
    assert second.superseded == 0
    assert second.already_usda == 1
    assert second.inserted == 0

    total_after = conn.execute("SELECT COUNT(*) AS n FROM foods").fetchone()["n"]
    # Superseding in place never changes the row count.
    assert total_after == total_before

    nutrient_rows = conn.execute(
        "SELECT COUNT(*) AS n FROM food_nutrients WHERE nutrient = 'protein'"
    ).fetchone()["n"]
    # And food_nutrients never grows a duplicate (food_id, nutrient) row either.
    dedup = conn.execute(
        "SELECT COUNT(*) AS n FROM ("
        "SELECT food_id FROM food_nutrients WHERE nutrient='protein' "
        "GROUP BY food_id, nutrient)"
    ).fetchone()["n"]
    assert nutrient_rows == dedup


def test_sync_inserts_when_no_curated_counterpart_exists(conn, tmp_path):
    path = _write_snapshot(
        tmp_path,
        [_snapshot_entry("no-such-curated-ref", fdc_id=888001, protein=30.0,
                          name="Some new USDA-only food", category="fish")],
    )
    result = usda.sync(path=path, conn=conn)
    assert result.inserted == 1
    assert result.superseded == 0

    row = conn.execute(
        "SELECT source, category FROM foods WHERE source_ref = '888001'"
    ).fetchone()
    assert row["source"] == "usda"
    assert row["category"] == "fish"


def test_sync_leaves_unmatched_curated_foods_alone(conn, tmp_path):
    other_curated = conn.execute(
        "SELECT source_ref, id FROM foods WHERE source = 'curated' AND source_ref != ?",
        (CHICKEN_BREAST_REF,),
    ).fetchall()
    assert other_curated  # sanity: there are 31 other curated foods

    path = _write_snapshot(
        tmp_path, [_snapshot_entry(CHICKEN_BREAST_REF, fdc_id=171077, protein=22.5)]
    )
    result = usda.sync(path=path, conn=conn)

    still_curated = {
        r["source_ref"]
        for r in conn.execute("SELECT source_ref FROM foods WHERE source = 'curated'")
    }
    assert CHICKEN_BREAST_REF not in still_curated
    for row in other_curated:
        assert row["source_ref"] in still_curated

    assert CHICKEN_BREAST_REF not in result.unmatched_curated
    for row in other_curated:
        assert row["source_ref"] in result.unmatched_curated


def test_sync_reports_unchanged_protein_value_for_matched_curated(conn, tmp_path):
    """A supersede overwrites the amount even when a food already had a
    curated protein row -- the sourced figure always wins, never merges."""
    path = _write_snapshot(
        tmp_path, [_snapshot_entry(CHICKEN_BREAST_REF, fdc_id=171077, protein=99.9)]
    )
    usda.sync(path=path, conn=conn)
    row = conn.execute(
        "SELECT n.amount_per_100g FROM foods f "
        "JOIN food_nutrients n ON n.food_id = f.id AND n.nutrient='protein' "
        "WHERE f.source_ref = '171077'"
    ).fetchone()
    assert row["amount_per_100g"] == 99.9


# --------------------------------------------------------------------------- #
# source_counts
# --------------------------------------------------------------------------- #
def test_source_counts_reflects_curated_and_usda_split(conn, tmp_path):
    counts_before = usda.source_counts(conn=conn)
    assert counts_before.get("curated", 0) >= 32
    assert "usda" not in counts_before or counts_before["usda"] == 0

    path = _write_snapshot(
        tmp_path, [_snapshot_entry(CHICKEN_BREAST_REF, fdc_id=171077, protein=22.5)]
    )
    usda.sync(path=path, conn=conn)

    counts_after = usda.source_counts(conn=conn)
    assert counts_after["usda"] == 1
    assert counts_after["curated"] == counts_before["curated"] - 1


# --------------------------------------------------------------------------- #
# Snapshot loading / error handling
# --------------------------------------------------------------------------- #
def test_load_snapshot_missing_file_raises(tmp_path):
    with pytest.raises(usda.SnapshotError):
        usda.load_snapshot(tmp_path / "does-not-exist.json")


def test_load_snapshot_invalid_json_raises(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(usda.SnapshotError):
        usda.load_snapshot(path)


def test_load_snapshot_missing_foods_key_raises(tmp_path):
    path = tmp_path / "shape.json"
    path.write_text(json.dumps({"oops": []}), encoding="utf-8")
    with pytest.raises(usda.SnapshotError):
        usda.load_snapshot(path)


def test_sync_missing_snapshot_raises(conn, tmp_path):
    with pytest.raises(usda.SnapshotError):
        usda.sync(path=tmp_path / "nope.json", conn=conn)


# --------------------------------------------------------------------------- #
# The real vendored snapshot (GFP-24's actual deliverable).
# --------------------------------------------------------------------------- #
def test_real_vendored_snapshot_loads_and_is_focused():
    data = usda.load_snapshot()
    foods = data["foods"]
    assert len(foods) > 0
    # A focused snapshot of the curated catalog, not "the entire USDA database".
    assert len(foods) < 100
    for entry in foods:
        assert isinstance(entry["fdc_id"], int)
        assert isinstance(entry["curated_ref"], str) and entry["curated_ref"]
        assert isinstance(entry["protein_per_100g"], (int, float))
        assert entry["protein_per_100g"] > 0
        assert entry["name"]
        assert entry["category"]
    # No duplicate fdc_id / curated_ref entries -- each maps to exactly one food.
    fdc_ids = [e["fdc_id"] for e in foods]
    refs = [e["curated_ref"] for e in foods]
    assert len(fdc_ids) == len(set(fdc_ids))
    assert len(refs) == len(set(refs))


def test_real_vendored_snapshot_only_targets_known_curated_refs(conn):
    """GFP-24 must not silently invent foods USDA doesn't cover here -- every
    curated_ref in the vendored snapshot should name a food GFP-23 actually
    curated, not a typo or an unrelated addition."""
    known_refs = {
        r["source_ref"]
        for r in conn.execute("SELECT source_ref FROM foods WHERE source = 'curated'")
    }
    data = usda.load_snapshot()
    for entry in data["foods"]:
        assert entry["curated_ref"] in known_refs, (
            f"{entry['curated_ref']!r} in the vendored snapshot does not match "
            "any curated foods.source_ref"
        )


def test_real_vendored_snapshot_applied_supersedes_most_curated_foods(conn):
    total_curated = conn.execute(
        "SELECT COUNT(*) AS n FROM foods WHERE source = 'curated'"
    ).fetchone()["n"]

    result = usda.sync(conn=conn)

    assert result.superseded > 0
    counts = usda.source_counts(conn=conn)
    assert counts.get("usda", 0) == result.superseded + result.inserted
    assert counts.get("usda", 0) + counts.get("curated", 0) >= total_curated

    # Idempotent against the real file too.
    again = usda.sync(conn=conn)
    assert again.superseded == 0
    assert again.inserted == 0
    assert again.already_usda == result.applied


# --------------------------------------------------------------------------- #
# CLI: gplan nutrition sync
# --------------------------------------------------------------------------- #
def test_cli_nutrition_sync_reports_sourced_and_estimated_counts(env_db):
    result = runner.invoke(app, ["nutrition", "sync"])
    assert result.exit_code == 0, result.stdout
    assert "USDA sync" in result.stdout
    assert "sourced (usda)" in result.stdout
    assert "estimated (curated)" in result.stdout


def test_cli_nutrition_sync_is_idempotent(env_db):
    first = runner.invoke(app, ["nutrition", "sync"])
    assert first.exit_code == 0, first.stdout
    second = runner.invoke(app, ["nutrition", "sync"])
    assert second.exit_code == 0, second.stdout
    assert "0 curated food(s) superseded" in second.stdout
