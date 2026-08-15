"""The name and the icon (GFP-158).

Renamed from "Grocery Planner" to "Protein Ledger" in 1.1.0, and given a steak
icon. Both are user-visible surfaces with no other test coverage, and the
rename touched identifiers GFP-102 pinned deliberately.

**Why the rename was safe exactly once.** v1.0.0 had been tagged for under an
hour and had never been installed anywhere, so there was nothing to migrate.
That window is now closed: anyone installing 1.1.0 gets these strings written
into their Start Menu, registry, Task Scheduler and LaunchAgents, and the
UNINSTALL checklist names them so a failed uninstall can be finished by hand.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from grocery_planner import install_paths as ip

ROOT = pathlib.Path(__file__).resolve().parent.parent
ICONS = ROOT / "packaging" / "icons"


# --------------------------------------------------------------------------- #
# The name
# --------------------------------------------------------------------------- #
def test_the_display_name_is_the_new_one():
    assert ip.APP_DISPLAY_NAME == "Protein Ledger"


def test_no_pinned_identifier_still_says_the_old_name():
    """A half-done rename is worse than either name: the Start Menu would say
    one thing and the uninstall checklist another."""
    values = {
        name: value for name, value in vars(ip).items()
        if name.isupper() and isinstance(value, str)
    }
    stale = {n: v for n, v in values.items()
             if "GroceryPlanner" in v or "Grocery Planner" in v}
    assert stale == {}, f"pinned identifiers still carry the old name: {stale}"


def test_the_data_directory_was_deliberately_not_renamed():
    """The ONE thing the rename left alone, and on purpose: relocating the data
    directory is the only change here that can lose a nutritionist's clients,
    and a path nobody ever looks at carries no branding value to pay for that.

    This test exists so a later "finish the rename" tidy-up has to argue with
    it rather than silently strand somebody's database.
    """
    from grocery_planner import paths

    assert paths.APP_NAME == "grocery-planner"


def test_the_cli_reports_the_display_name():
    from typer.testing import CliRunner

    from grocery_planner.cli import app

    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert ip.APP_DISPLAY_NAME in result.stdout


# --------------------------------------------------------------------------- #
# The icon
# --------------------------------------------------------------------------- #
def test_the_source_svg_exists_and_is_well_formed_xml():
    """A run of two hyphens is illegal inside an XML comment, and the house
    style uses one as an em-dash everywhere else -- which is exactly how the
    first version of this file came to be unparseable. QSvgRenderer reports
    only "not valid SVG", so the cause is not obvious from the failure.
    """
    import xml.dom.minidom

    source = ROOT / "packaging" / "icon.svg"
    assert source.is_file()
    xml.dom.minidom.parseString(source.read_text(encoding="utf-8"))


def test_every_rendered_icon_is_checked_in():
    """The release runners build with PyInstaller and nothing else. If these
    were generated at build time, a release could fail for want of a
    rasteriser -- a reason entirely unrelated to the code."""
    expected = ["icon.ico", "icon.icns", "icon-256.png"]
    missing = [name for name in expected if not (ICONS / name).is_file()]
    assert missing == [], f"missing rendered icons: {missing}"


def test_the_rendered_icons_are_not_empty():
    for icon in ICONS.glob("icon*"):
        assert icon.stat().st_size > 200, f"{icon.name} looks truncated"


@pytest.mark.parametrize("spec,key", [
    ("gplan-gui.spec", "icon.ico"),
    ("gplan-gui.spec", "icon.icns"),
    ("gplan.spec", "icon.ico"),
    ("gplan-onedir.spec", "icon.ico"),
])
def test_the_build_specs_reference_the_icons(spec, key):
    text = (ROOT / "packaging" / spec).read_text(encoding="utf-8")
    assert key in text


def test_the_specs_resolve_the_icon_against_the_spec_directory():
    """A relative path would resolve against the working directory, which
    differs between a local build (repo root) and the release workflow.
    SPECPATH is the one variable PyInstaller guarantees."""
    for spec in ("gplan.spec", "gplan-gui.spec", "gplan-onedir.spec"):
        text = (ROOT / "packaging" / spec).read_text(encoding="utf-8")
        assert "SPEC_DIR = pathlib.Path(SPECPATH)" in text
        assert 'icon="icons' not in text, "relative icon path in " + spec


def test_the_running_app_can_find_its_icon():
    """app_icon() searches three locations because the file lands somewhere
    different in a dev checkout, a onefile build and a onedir build."""
    pytest.importorskip("PySide6.QtWidgets", reason="GUI extra not installed")
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from grocery_planner.gui.app import app_icon

    assert not app_icon().isNull()


def test_a_missing_icon_is_not_fatal(monkeypatch):
    """Qt falls back to a default window icon. Refusing to open the window
    over a missing decoration would turn a cosmetic loss into an app that does
    not start."""
    pytest.importorskip("PySide6.QtWidgets", reason="GUI extra not installed")
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from grocery_planner.gui import app as app_module

    monkeypatch.setattr(pathlib.Path, "is_file", lambda self: False)
    assert app_module.app_icon().isNull()      # returned, not raised
