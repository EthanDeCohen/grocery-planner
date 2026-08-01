"""GFP-61: guard the db_script/ freeze rule (GFP-60) and the schema it
produces in CI, so a broken freeze or an undocumented schema change fails
the build instead of being noticed later.

Three guards, each mirroring a rule documented in db_script/README.md:

1. db_script/init/0001_GFP-9.ddl is pinned by checksum -- if someone folds a
   change back into that FROZEN baseline, its hash changes and this fails
   loudly, pointing at the README section that explains why it must not.
2. A committed, human-readable snapshot of "tables and columns a brand-new
   database ends up with" must match what db_script/ actually produces --
   see scripts/update_schema_snapshot.py for how it's built and regenerated.
3. Script hygiene: every file under db_script/ matches the NNNN_GFP-KEY
   naming convention, the NNNN sequence is contiguous with no gaps or
   duplicates, and init/ contains exactly one file.
"""
from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DB_SCRIPT_ROOT = ROOT / "db_script"
INIT_DIR = DB_SCRIPT_ROOT / "init"
MIGRATION_DIR = DB_SCRIPT_ROOT / "migration"
INIT_BASELINE = INIT_DIR / "0001_GFP-9.ddl"
# Deliberately NOT under init/: GFP-61 also asserts init/ contains exactly
# one file (the frozen baseline itself), so the checksum sidecar lives at
# the db_script/ root instead, next to README.md and schema_snapshot.txt.
INIT_BASELINE_CHECKSUM_FILE = DB_SCRIPT_ROOT / "init_baseline.sha256"
SCHEMA_SNAPSHOT_PATH = DB_SCRIPT_ROOT / "schema_snapshot.txt"

SCRIPT_NAME_RE = re.compile(r"^(\d{4})_GFP-\d+\.(?:ddl|dml)$")


def _load_snapshot_module():
    """Import scripts/update_schema_snapshot.py by path (scripts/ is not a
    package). Both this test and `python scripts/update_schema_snapshot.py`
    build the snapshot through the exact same build_snapshot() function, so
    there is exactly one definition of "what the snapshot should contain"."""
    path = ROOT / "scripts" / "update_schema_snapshot.py"
    spec = importlib.util.spec_from_file_location("update_schema_snapshot", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _expected_checksum() -> str:
    """Parse db_script/init/0001_GFP-9.ddl.sha256: the first non-comment,
    non-blank line's first whitespace-separated token is the pinned hash."""
    for line in INIT_BASELINE_CHECKSUM_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        return line.split()[0]
    raise AssertionError(
        f"{INIT_BASELINE_CHECKSUM_FILE} has no hash line (only comments/blank "
        "lines)"
    )


# --------------------------------------------------------------------------- #
# 1. init/ baseline is pinned by checksum
# --------------------------------------------------------------------------- #
def test_init_baseline_checksum_is_pinned():
    """db_script/init/0001_GFP-9.ddl is a FROZEN baseline (GFP-60): every
    schema change since lives only in db_script/migration/. If this hash no
    longer matches the file on disk, someone edited the frozen baseline --
    fail loudly and point at the rule, rather than let it quietly reintroduce
    the GFP-59 double-description bug the freeze exists to prevent."""
    actual = hashlib.sha256(INIT_BASELINE.read_bytes()).hexdigest()
    expected = _expected_checksum()
    assert actual == expected, (
        f"{INIT_BASELINE} no longer matches its pinned checksum "
        f"({INIT_BASELINE_CHECKSUM_FILE}): expected {expected}, got {actual}.\n\n"
        "db_script/init/0001_GFP-9.ddl is a FROZEN baseline -- see the "
        "'init/ is FROZEN' section of db_script/README.md. Every schema "
        "change since GFP-60 belongs in a new db_script/migration/ script, "
        "not folded back into init/. If you have a legitimate reason to "
        "re-baseline init/ itself (this should be exceedingly rare), update "
        f"{INIT_BASELINE_CHECKSUM_FILE.name} deliberately -- see the comment "
        "at the top of that file for the regenerate command."
    )


# --------------------------------------------------------------------------- #
# 2. schema snapshot matches what db_script/ actually produces
# --------------------------------------------------------------------------- #
def test_schema_snapshot_matches_a_freshly_built_database():
    """db_script/schema_snapshot.txt must match the tables/columns a
    brand-new database gets from replaying init/ + every migration/ script.
    Keeping this in sync is what forces a schema change to show up as a
    reviewable text diff instead of hiding inside a .ddl/.dml file."""
    snap = _load_snapshot_module()
    actual = snap.build_snapshot()

    assert SCHEMA_SNAPSHOT_PATH.exists(), (
        f"{SCHEMA_SNAPSHOT_PATH} does not exist. Generate it with: "
        ".venv/Scripts/python.exe scripts/update_schema_snapshot.py"
    )
    committed = SCHEMA_SNAPSHOT_PATH.read_text(encoding="utf-8")
    assert actual == committed, (
        f"{SCHEMA_SNAPSHOT_PATH} is out of date with the schema db_script/ "
        "produces. If this schema change is intentional, regenerate the "
        "snapshot in this same PR with:\n"
        "  .venv/Scripts/python.exe scripts/update_schema_snapshot.py\n"
        "and commit the result."
    )


# --------------------------------------------------------------------------- #
# 3. script hygiene
# --------------------------------------------------------------------------- #
def _all_script_paths() -> list[Path]:
    return sorted((INIT_DIR).iterdir()) + sorted((MIGRATION_DIR).iterdir())


def test_every_script_matches_the_naming_convention():
    for path in _all_script_paths():
        assert SCRIPT_NAME_RE.match(path.name), (
            f"{path} does not match the required NNNN_GFP-KEY.ddl|dml "
            "naming convention (see db_script/README.md)"
        )


def test_script_sequence_is_contiguous_with_no_gaps_or_duplicates():
    prefixes = [
        int(SCRIPT_NAME_RE.match(p.name).group(1)) for p in _all_script_paths()
    ]
    prefixes.sort()
    expected = list(range(1, len(prefixes) + 1))
    assert prefixes == expected, (
        f"db_script/ NNNN sequence prefixes are {prefixes}, expected a "
        f"contiguous {expected} with no gaps or duplicates across init/ and "
        "migration/ (see db_script/README.md)."
    )


def test_init_dir_contains_exactly_one_file():
    files = list(INIT_DIR.iterdir())
    assert len(files) == 1, (
        f"db_script/init/ must contain exactly one file (the frozen "
        f"baseline), found {[f.name for f in files]}. Every change since "
        "GFP-60 belongs in db_script/migration/ instead -- see "
        "db_script/README.md."
    )
    assert files[0] == INIT_BASELINE


# --------------------------------------------------------------------------- #
# regenerator script sanity
# --------------------------------------------------------------------------- #
def test_update_schema_snapshot_check_mode_agrees_with_committed_file():
    """--check is the CI-usable form of the same guard as
    test_schema_snapshot_matches_a_freshly_built_database above; prove it
    reports success against the committed file so it stays trustworthy as
    the documented regenerate/verify command."""
    snap = _load_snapshot_module()
    assert snap.main(["--check"]) == 0
