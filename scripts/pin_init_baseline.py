"""Regenerate db_script/init_baseline.sha256 (GFP-117).

Re-baselining ``db_script/init/0001_GFP-9.ddl`` should be exceedingly rare --
read the "init/ is FROZEN" section of db_script/README.md before you do it.
This script exists so that when it does happen, the hash is produced the ONE
correct way.

Two things it gets right that a hand-rolled command did not:

1. **Line endings are normalised before hashing**, exactly as
   ``grocery_planner.db._checksum`` does. A byte-exact hash is checkout-
   dependent: the same file is CRLF on a Windows working copy that predates
   ``.gitattributes`` and LF everywhere else, so a byte-exact pin passes on one
   machine and fails on every fresh clone, worktree and CI runner. That was the
   GFP-117 bug.

2. **It is a file, not a one-liner in a comment.** The previous instruction was
   an inline ``python -c`` containing backslash escapes; pasting it into the
   sidecar's own header wrote real control characters, split the comment line
   and corrupted the pin file. A script cannot be mangled by being quoted.

Usage:
    .venv/Scripts/python.exe scripts/pin_init_baseline.py          # rewrite
    .venv/Scripts/python.exe scripts/pin_init_baseline.py --check  # verify only
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "db_script" / "init" / "0001_GFP-9.ddl"
PIN_FILE = ROOT / "db_script" / "init_baseline.sha256"


def normalised_sha256(path: Path) -> str:
    """Content hash, independent of how this machine checked the file out."""
    raw = path.read_bytes()
    return hashlib.sha256(
        raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    ).hexdigest()


def pinned() -> str:
    """The hash currently recorded, i.e. the first non-comment token."""
    for line in PIN_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line.split()[0]
    raise SystemExit(f"{PIN_FILE} has no hash line (only comments/blank lines)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="Verify the pin matches without rewriting; exit 1 if it does not.",
    )
    args = parser.parse_args()

    actual = normalised_sha256(BASELINE)
    recorded = pinned()

    if args.check:
        if actual == recorded:
            print(f"OK: {BASELINE.name} matches its pin ({actual}).")
            return 0
        print(f"MISMATCH: pinned {recorded}, file hashes to {actual}.")
        return 1

    if actual == recorded:
        print(f"Already pinned to {actual}; nothing to do.")
        return 0

    text = PIN_FILE.read_text(encoding="utf-8")
    PIN_FILE.write_text(text.replace(recorded, actual), encoding="utf-8")
    print(f"Repinned {PIN_FILE.name}: {recorded} -> {actual}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
