"""Packaging guards (GFP-10).

These do not build a binary — that takes minutes and needs PyInstaller. They
check the things that silently rot between builds: an entry point that stops
importing, or a spec that stops excluding what it must.
"""
import re
from pathlib import Path

import pytest

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"


def _schema_datas():
    """Load packaging/_schema_datas.py, which sits beside the specs rather than
    on the import path -- the specs are exec'd by PyInstaller, not imported."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_schema_datas", PACKAGING / "_schema_datas.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


@pytest.mark.parametrize("spec", ["gplan.spec", "gplan-gui.spec"])
def test_specs_bundle_db_script_as_data(spec):
    """GFP-318: the specs must actually COLLECT the schema, not merely mention it.

    This test used to assert the string ``collect_data_files("db_script")``
    appeared in the spec, and it passed for the whole period during which every
    build shipped a binary containing ZERO .ddl files -- the call was present
    and returned nothing, because it resolves by import and the editable install
    does not expose `db_script`. The string was there. The schema was not.

    Worse, a substring assertion cannot tell code from prose: after the call was
    replaced, the gui spec still "passed" on the mention of the old name inside
    an explanatory comment.

    So this asserts the relationship (GFP-179): the collector the spec calls
    returns real migration files for this repo.
    """
    source = (PACKAGING / spec).read_text(encoding="utf-8")
    assert "schema_datas(SPEC_DIR)" in source

    collected = _schema_datas().schema_datas(PACKAGING)
    names = [Path(src).name for src, _ in collected]
    assert any(name.endswith(".ddl") for name in names), names
    # Destinations must mirror what db.py looks for under sys._MEIPASS.
    assert all(dest.startswith("db_script") for _, dest in collected)


@pytest.mark.parametrize("spec", ["gplan.spec", "gplan-gui.spec"])
def test_the_specs_build_one_directory_not_one_file(spec):
    """GFP-320: the packaging MODE is load-bearing, so it is asserted.

    Windows Defender quarantined the one-file CLI as
    Behavior:Win32/Execution.A!ml -- the bootloader unpacking ~700 files into
    %TEMP% and running them from there looks exactly like a dropper. Only
    COLLECT avoids that. A spec that drifted back to one-file would build and
    smoke-test perfectly and then be quarantined on a customer's machine,
    which is the failure this test exists to prevent.
    """
    source = (PACKAGING / spec).read_text(encoding="utf-8")
    assert "COLLECT(" in source
    # EXE must hand its binaries to COLLECT rather than swallow them, which is
    # what exclude_binaries switches between.
    assert "exclude_binaries=True" in source
    # UPX compression sets off the same class of heuristic. Never here.
    assert "upx=False" in source


def test_the_two_apps_use_different_payload_directory_names():
    """GFP-320: both installers flatten into ONE install root, so gplan.exe and
    gplan-gui.exe stay siblings -- PATH, the Start Menu shortcut and
    background.py's windowless-launcher lookup all rely on it.

    Two apps both writing PyInstaller's default `_internal/` would merge, and
    the second would overwrite the first's base_library.zip with one built from
    a different module set. Nothing would fail at build or install time; the
    app would break later, on whichever import lost the coin toss.
    """
    names = {}
    for spec in ("gplan.spec", "gplan-gui.spec"):
        source = (PACKAGING / spec).read_text(encoding="utf-8")
        match = re.search(r'^CONTENTS_DIR = "([^"]+)"', source, re.MULTILINE)
        assert match, f"{spec} does not set CONTENTS_DIR"
        names[spec] = match.group(1)
        # Both EXE and COLLECT have to be told, or they disagree about where
        # the launcher should look.
        assert source.count("contents_directory=CONTENTS_DIR") == 2, spec
    assert len(set(names.values())) == 2, f"payload directories collide: {names}"


def test_the_mac_bundle_wraps_collect_not_the_bare_exe():
    """GFP-320: BUNDLE(exe) with a one-directory build produces an .app whose
    executable has no payload beside it. It builds, and it fails on launch."""
    source = (PACKAGING / "gplan-gui.spec").read_text(encoding="utf-8")
    assert "BUNDLE(\n        coll," in source


def test_schema_collector_aborts_when_it_finds_nothing(tmp_path):
    """The guard that was missing: an empty collection must fail the build.

    A WARNING is what shipped the schema-less binary (GFP-318). Pointed at a
    tree with no scripts, the collector has to stop the build outright rather
    than hand PyInstaller an empty list.
    """
    (tmp_path / "db_script").mkdir()
    with pytest.raises(SystemExit):
        _schema_datas().schema_datas(tmp_path / "anything")


def test_db_script_ships_as_installed_package_data():
    """GFP-59: pip install must not silently drop db_script/*.ddl -- it's
    declared as its own top-level package (with package-data globs) in
    pyproject.toml specifically so setuptools includes it in the wheel.
    """
    pyproject = (PACKAGING.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert '"db_script*"' in pyproject
    assert "[tool.setuptools.package-data]" in pyproject
    assert "db_script = [" in pyproject
