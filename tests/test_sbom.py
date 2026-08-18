"""The release SBOM (GFP-344).

An SBOM's whole job is to be trustworthy at the moment somebody is asking
"is this build affected?" about a dependency that turned out to be
compromised. Two failure modes matter, and both pass a naive check:

* **an SBOM that is not published** -- the release goes out clean and the
  document exists only on a runner that has been destroyed;
* **an SBOM that is empty** -- schema-valid, publishes fine, and answers
  "what is in this build?" with "nothing".

The second is the worse one, because it looks like an answer. Most of what is
tested here is the refusal to produce either.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "build_sbom.py"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def _text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def build_sbom():
    """Load the script by path -- scripts/ is not an importable package."""
    spec = importlib.util.spec_from_file_location("build_sbom", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_sbom"] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# The generator
# --------------------------------------------------------------------------- #
def test_the_generator_ships():
    assert SCRIPT.exists() and SCRIPT.stat().st_size > 0


def test_it_reads_the_declared_dependencies(build_sbom):
    """Parsed from pyproject, so adding a dependency automatically widens what
    the SBOM is required to account for."""
    names = build_sbom.direct_dependencies()
    assert {"httpx", "typer", "platformdirs"} <= names
    # Environment markers and version specifiers must not survive into the name.
    assert all(
        not any(c in name for c in "<>=!~;[ ") for name in names
    ), f"a version specifier leaked into a package name: {sorted(names)}"


def test_an_empty_sbom_is_refused(build_sbom, tmp_path):
    """The failure this file exists for. A document with no components is
    worse than no document, because it publishes cleanly."""
    target = tmp_path / "sbom.cdx.json"
    target.write_text(
        json.dumps({"specVersion": "1.6", "components": [],
                    "metadata": {"component": {"name": "grocery-planner"}}}),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="no components"):
        build_sbom.verify(target)


def test_an_sbom_missing_a_declared_dependency_is_refused(build_sbom, tmp_path):
    """It means the SBOM describes some other environment than the one being
    frozen, which is a document that is confidently wrong."""
    target = tmp_path / "sbom.cdx.json"
    target.write_text(
        json.dumps({
            "specVersion": "1.6",
            "metadata": {"component": {"name": "grocery-planner"}},
            "components": [{"name": "httpx", "version": "0.27.0"}],
        }),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="missing direct dependencies"):
        build_sbom.verify(target)


def test_a_missing_file_is_refused(build_sbom, tmp_path):
    with pytest.raises(SystemExit, match="was not written"):
        build_sbom.verify(tmp_path / "nothing.json")


def test_a_corrupt_sbom_is_refused(build_sbom, tmp_path):
    target = tmp_path / "sbom.cdx.json"
    target.write_text("{ not json", encoding="utf-8")
    with pytest.raises(SystemExit, match="not valid JSON"):
        build_sbom.verify(target)


def test_a_complete_sbom_is_accepted(build_sbom, tmp_path):
    """The other half: the guard must not be so strict that nothing passes."""
    target = tmp_path / "sbom.cdx.json"
    target.write_text(
        json.dumps({
            "specVersion": "1.6",
            "metadata": {"component": {"name": "grocery-planner", "version": "1.1.5"}},
            "components": [
                {"name": name, "version": "1.0"}
                for name in build_sbom.direct_dependencies()
            ],
        }),
        encoding="utf-8",
    )
    assert build_sbom.verify(target)["specVersion"] == "1.6"


def test_it_is_written_reproducibly(build_sbom):
    """Without this every SBOM differs from every other by a timestamp and a
    serial number, and "what changed between two releases" stops being
    answerable by looking at them."""
    assert "--output-reproducible" in _text(SCRIPT)


# --------------------------------------------------------------------------- #
# It has to actually reach a customer
# --------------------------------------------------------------------------- #
def test_the_release_generates_the_sbom():
    assert "build_sbom.py" in _text(RELEASE_WORKFLOW), (
        "the release no longer generates an SBOM"
    )


def test_the_release_publishes_the_sbom_as_its_own_asset():
    """So it can be read without downloading 238 MB to look inside."""
    assert "assets/*.sbom.cdx.json" in _text(RELEASE_WORKFLOW)


def test_both_platforms_ship_the_sbom_inside_the_zip():
    """It should travel with what the customer actually holds -- and that is
    also what puts it inside the one-click installer's payload without the
    installer needing to know it exists."""
    text = _text(RELEASE_WORKFLOW)
    assert 'Copy-Item sbom.cdx.json $name/' in text, "the Windows ZIP has no SBOM"
    assert 'cp sbom.cdx.json "$NAME/"' in text, "the macOS ZIP has no SBOM"


def test_the_generator_is_a_build_dependency():
    """In the build extra, not dev: the SBOM has to be generated from the
    environment that is about to be frozen."""
    pyproject = _text(ROOT / "pyproject.toml")
    build_line = next(
        line for line in pyproject.splitlines() if line.startswith("build = [")
    )
    assert "cyclonedx-bom" in build_line, (
        "cyclonedx-bom is not a build dependency, so the release cannot "
        "generate an SBOM"
    )


def test_the_sbom_is_generated_before_the_binaries_are_packaged():
    """Ordering, asserted as a RELATIONSHIP rather than by eyeballing the file.

    If the SBOM step drifted below the packaging steps, both ZIPs would ship
    whatever sbom.cdx.json happened to be lying around -- or fail on a missing
    file, which is at least loud. This is the quiet half.
    """
    text = _text(RELEASE_WORKFLOW)
    generated = text.index("scripts/build_sbom.py")
    packaged_windows = text.index("Copy-Item sbom.cdx.json $name/")
    packaged_macos = text.index('cp sbom.cdx.json "$NAME/"')
    assert generated < packaged_windows and generated < packaged_macos, (
        "the SBOM is packaged before it is generated"
    )
