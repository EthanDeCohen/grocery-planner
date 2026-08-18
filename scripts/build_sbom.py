#!/usr/bin/env python3
# ######### decohen-partners ##########
# Protein Ledger
"""Build the release SBOM (GFP-344).

A release ZIP is a quarter of a gigabyte of frozen third-party code. PyInstaller
bundles the whole dependency tree into the binary, so what a customer runs
includes about thirty-five packages that nothing currently enumerates. When one
of those turns out to be compromised upstream, the question is "are we
affected?" -- and today the only way to answer it is to rebuild and read a pip
freeze. That question always arrives under time pressure.

This does not stop a bad dependency reaching the build. It makes the blast
radius knowable, which is the part that is missing. The control that actually
stops a swapped artifact is hash-pinned installs, and that is deliberately a
separate piece of work rather than something smuggled in behind this.

THE ENVIRONMENT, NOT THE MANIFEST. cyclonedx-py is pointed at the interpreter
that is about to be frozen, so the SBOM lists what was really installed --
including transitive packages and the versions pip actually resolved. An SBOM
built from pyproject.toml would describe what should have been installed, which
is the thing you cannot trust in the situation this exists for.

WRITTEN REPRODUCIBLY, so two releases can be diffed. Without that, every SBOM
differs from every other by its timestamp and serial number, and "what changed
between 1.2.0 and 1.2.1" stops being answerable by looking.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = REPO_ROOT / "sbom.cdx.json"

#: CycloneDX rather than SPDX: it is what the scanners this would feed take as
#: input, and cyclonedx-py is a first-party tool for Python environments.
SPEC_VERSION = "1.6"


def normalise(name: str) -> str:
    """PEP 503 name normalisation, so PySide6 and pyside6 are one package."""
    return re.sub(r"[-_.]+", "-", name).lower()


def direct_dependencies() -> set[str]:
    """The runtime dependencies pyproject declares, normalised.

    Only the required ones. The extras are legitimately absent from a
    CLI-only build, and failing on a missing PySide6 would refuse to produce
    an SBOM for a release that is perfectly valid.
    """
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    names = set()
    for spec in data["project"]["dependencies"]:
        # "tzdata>=2024.1; sys_platform == 'win32'" -> "tzdata"
        name = re.split(r"[<>=!~;\[\s]", spec.strip(), maxsplit=1)[0]
        if name:
            names.add(normalise(name))
    return names


def generate(output: Path, environment: str | None) -> None:
    command = [
        sys.executable, "-m", "cyclonedx_py", "environment",
        *( [environment] if environment else [] ),
        "--pyproject", str(REPO_ROOT / "pyproject.toml"),
        "--of", "JSON",
        "--sv", SPEC_VERSION,
        "--output-file", str(output),
        # Strips the timestamp and pins the serial number, so the same inputs
        # produce the same bytes.
        "--output-reproducible",
        # Schema validation, on purpose. A malformed SBOM is refused here
        # rather than by whatever tool someone later feeds it to.
        "--validate",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except FileNotFoundError:                              # pragma: no cover
        raise SystemExit(f"could not run {sys.executable}")

    if result.returncode != 0:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise SystemExit(
            "cyclonedx-py failed.\n"
            "If it is not installed:  pip install -e \".[build]\""
        )


def verify(output: Path) -> dict:
    """Refuse an SBOM that is technically valid and says nothing.

    An empty component list is WORSE than no SBOM: it passes every automated
    check, publishes cleanly, and answers "what is in this build?" with
    "nothing". The failure it would hide is exactly the one this file exists
    for.
    """
    if not output.exists():
        raise SystemExit(f"{output} was not written")

    try:
        document = json.loads(output.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{output} is not valid JSON: {exc}")

    components = document.get("components") or []
    if not components:
        raise SystemExit(f"{output} lists no components")

    root = (document.get("metadata") or {}).get("component") or {}
    if not root.get("name"):
        raise SystemExit(f"{output} does not name the thing it describes")

    present = {normalise(c.get("name", "")) for c in components}
    missing = sorted(direct_dependencies() - present)
    if missing:
        raise SystemExit(
            "the SBOM is missing direct dependencies: "
            + ", ".join(missing)
            + "\nThe environment it was built from is not the one being shipped."
        )

    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT,
        help=f"where to write the SBOM (default: {DEFAULT_OUTPUT.name})",
    )
    parser.add_argument(
        "--env", dest="environment", default=None,
        help="the Python environment to describe (default: the one running this)",
    )
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    generate(args.output, args.environment)
    document = verify(args.output)

    root = document["metadata"]["component"]
    print(f"  SBOM: {args.output}")
    print(f"  CycloneDX {document.get('specVersion')}")
    print(f"  describes: {root.get('name')} {root.get('version', '')}".rstrip())
    print(f"  components: {len(document['components'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
