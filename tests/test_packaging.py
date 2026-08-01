"""Packaging guards (GFP-10).

These do not build a binary — that takes minutes and needs PyInstaller. They
check the things that silently rot between builds: an entry point that stops
importing, or a spec that stops excluding what it must.
"""
from pathlib import Path

import pytest

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"


def test_entry_points_exist():
    assert (PACKAGING / "gplan_entry.py").is_file()
    assert (PACKAGING / "gplan_gui_entry.py").is_file()


def test_cli_entry_point_imports_what_it_claims():
    """The frozen CLI starts here; a rename in cli.py must fail loudly."""
    source = (PACKAGING / "gplan_entry.py").read_text(encoding="utf-8")
    assert "from grocery_planner.cli import app" in source

    from grocery_planner.cli import app  # the import the launcher performs
    assert callable(app)


def test_gui_entry_point_matches_the_gui_module():
    source = (PACKAGING / "gplan_gui_entry.py").read_text(encoding="utf-8")
    assert "from grocery_planner.gui.app import main" in source

    pytest.importorskip("PySide6", reason="GUI extra not installed")
    from grocery_planner.gui.app import main
    assert callable(main)


@pytest.mark.parametrize("spec", ["gplan.spec", "gplan-gui.spec"])
def test_specs_are_syntactically_valid_python(spec):
    """A spec is executed as Python by PyInstaller; a syntax error costs a build."""
    compile((PACKAGING / spec).read_text(encoding="utf-8"), spec, "exec")


def test_cli_spec_collects_apscheduler_and_excludes_qt():
    """The two mistakes that only surface at runtime, guarded at test time."""
    source = (PACKAGING / "gplan.spec").read_text(encoding="utf-8")
    # APScheduler resolves triggers by name, so static analysis misses them and
    # the scheduler would fail only once a schedule fired.
    assert 'collect_submodules("apscheduler")' in source
    # Qt in the CLI binary would quadruple its size for nothing.
    assert '"PySide6"' in source


def test_gui_spec_builds_a_mac_bundle_and_hides_the_console():
    source = (PACKAGING / "gplan-gui.spec").read_text(encoding="utf-8")
    assert "BUNDLE(" in source and 'sys.platform == "darwin"' in source
    assert "console=False" in source
