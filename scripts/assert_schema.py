# ######### decohen-partners ##########
# Protein Ledger
"""Fail if a freshly built binary created a database with no schema (GFP-318).

CI ran four `gplan` commands against a binary that bundled ZERO .ddl files and
every one of them exited 0. An exit code proves the command ran; it does not
prove the schema shipped. The binary went on to create a database containing
one table -- `schema_version`, empty -- and nothing noticed.

So this asserts the relationship rather than an exit code: after the smoke
commands, the database the binary made must actually hold the application's
tables.

Usage:  python scripts/assert_schema.py <path-to-db>
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# A few core tables rather than the whole list: this is a "did the schema ship"
# check, not a schema snapshot. GFP-61's snapshot_schema is where the full
# structure is pinned.
REQUIRED = {"deals", "foods", "customers", "schema_version"}


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    db = Path(sys.argv[1])
    if not db.exists():
        print(f"FAIL: {db} was never created")
        return 1

    with sqlite3.connect(db) as conn:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    missing = REQUIRED - names
    if missing:
        print(
            f"FAIL: the binary created a database without {sorted(missing)}.\n"
            f"      Found {len(names)} table(s): {sorted(names)}\n"
            "      The bundle is almost certainly missing db_script -- see "
            "GFP-318."
        )
        return 1

    print(f"OK: {len(names)} tables present, including {sorted(REQUIRED)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
