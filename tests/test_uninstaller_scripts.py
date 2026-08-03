"""The uninstaller scripts, checked from Python (GFP-92).

CI runs both for real. What is checked here is the class of mistake a passing
CI run would not catch: a script that removes the right things on a clean
runner but whose *safety properties* have quietly been edited away.

Those properties are not incidental. An uninstaller is the one part of this
product that deletes a nutritionist's hand-entered client records, and the only
part that is supposed to remove a live credential from a machine that may then
be resold. Both directions of failure are severe, and neither shows up as a red
CI run.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from grocery_planner import install_paths as ip

ROOT = pathlib.Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"
WINDOWS = PACKAGING / "uninstall.ps1"
MACOS = PACKAGING / "uninstall.sh"
DOC = PACKAGING / "UNINSTALL.md"


def _text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", [WINDOWS, MACOS, DOC], ids=lambda p: p.name)
def test_the_uninstaller_ships(path):
    assert path.exists() and path.stat().st_size > 0


# --------------------------------------------------------------------------- #
# Confirmation -- the client-records failure
# --------------------------------------------------------------------------- #
def test_both_require_an_explicit_confirmation():
    """Not y/N. Typing REMOVE is deliberate friction in front of a deletion
    that cannot be undone."""
    assert "REMOVE" in _text(WINDOWS) and "Read-Host" in _text(WINDOWS)
    assert "REMOVE" in _text(MACOS) and "read -r" in _text(MACOS)


def test_both_offer_to_keep_the_data():
    """The most common reason to uninstall is reinstalling, and destroying
    months of hand-entered records to do that would be indefensible."""
    assert "KeepData" in _text(WINDOWS)
    assert "--keep-data" in _text(MACOS)


def test_both_point_at_the_export_before_deleting():
    """GFP-92 asks for this in terms: offer an export first, or at minimum
    point at GFP-34's."""
    for path in (WINDOWS, MACOS):
        assert "gplan export" in _text(path), path.name


def test_both_offer_a_dry_run():
    assert "DryRun" in _text(WINDOWS)
    assert "--dry-run" in _text(MACOS)


# --------------------------------------------------------------------------- #
# The credential failure -- what gets left behind
# --------------------------------------------------------------------------- #
def test_both_report_what_they_could_not_remove():
    """'Some files could not be removed' is not good enough when one of them
    is a credential, so failures are collected and printed by full path."""
    windows = _text(WINDOWS)
    assert "COULD NOT REMOVE" in windows
    assert "credential" in windows.lower()
    macos = _text(MACOS)
    assert "COULD NOT REMOVE" in macos
    assert "credential" in macos.lower()


def test_a_failed_removal_is_a_non_zero_exit():
    """So a script or a CI step calling this cannot mistake a partial
    uninstall -- the one that leaves a credential -- for a clean one."""
    assert re.search(r"COULD NOT REMOVE[\s\S]{0,900}?exit 1", _text(WINDOWS))
    assert re.search(r"COULD NOT REMOVE[\s\S]{0,900}?exit 1", _text(MACOS))


def test_the_macos_script_does_not_abort_on_the_first_failure():
    """`set -e` would stop at the first file it could not delete and leave the
    rest in place, unreported. A removal that fails must be REPORTED, not
    abort the run."""
    body = _text(MACOS)
    assert "set -uo pipefail" in body
    assert not re.search(r"^set -e", body, re.M), "set -e would abort on a failed removal"


# --------------------------------------------------------------------------- #
# Order and completeness -- GFP-102's two rules
# --------------------------------------------------------------------------- #
def test_the_timer_is_removed_before_the_program():
    """A leftover timer keeps firing against a binary that no longer exists,
    so removing it last lets a firing timer race the removal of what it
    invokes."""
    windows = _text(WINDOWS)
    assert windows.index("Unregister-ScheduledTask") < windows.index("Program files")
    macos = _text(MACOS)
    assert macos.index("launchctl bootout") < macos.index('"Program files"')


def test_the_macos_agent_is_unloaded_before_its_file_is_deleted():
    """Deleting a loaded agent leaves it running until logout."""
    body = _text(MACOS)
    assert body.index("launchctl bootout") < body.index('remove_path "LaunchAgent plist"')


def test_the_windows_script_removes_the_task_scheduler_folder_too():
    """schtasks cannot delete folders, and an empty GroceryPlanner folder left
    in Task Scheduler reads as a failed uninstall."""
    assert "Schedule.Service" in _text(WINDOWS)
    assert "DeleteFolder" in _text(WINDOWS)


def test_the_data_directory_is_removed_recursively_not_by_filename():
    """GFP-102 rule 2: it accumulates -wal/-shm files and hand-made backups."""
    assert "-Recurse -Force" in _text(WINDOWS)
    assert "rm -rf" in _text(MACOS)


def test_both_use_the_pinned_identifiers():
    windows = _text(WINDOWS)
    assert ip.WINDOWS_TASK_NAME in windows
    assert ip.WINDOWS_INSTALL_DIRNAME in windows
    assert ip.WINDOWS_START_MENU_FOLDER in windows
    assert ip.WINDOWS_REGISTRY_KEY.split("\\")[-1] in windows

    macos = _text(MACOS)
    assert ip.MACOS_LAUNCH_AGENT_LABEL in macos
    assert ip.MACOS_APP_BUNDLE_NAME in macos
    assert ip.MACOS_SUPPORT_DIRNAME in macos
    assert ip.MACOS_BUNDLE_IDENTIFIER in macos, "saved application state not covered"


# --------------------------------------------------------------------------- #
# Resolving rather than assuming
# --------------------------------------------------------------------------- #
def test_both_ask_the_app_where_its_data_is():
    """Environment overrides move files OUT of the data directory, at which
    point 'delete the folder' silently leaves a credential behind."""
    for path in (WINDOWS, MACOS):
        assert "uninstall-plan" in _text(path), path.name


def test_both_fall_back_when_the_binary_will_not_run():
    """Which is a normal state during an uninstall, not an exceptional one --
    the binary may already be half-removed by a previous attempt."""
    for path in (WINDOWS, MACOS):
        assert "default locations" in _text(path), path.name


def test_the_macos_script_does_not_need_a_json_parser():
    """macOS is not guaranteed one in the shell, and depending on python3
    inside the uninstaller for the app whose Python is being removed is a
    dependency worth not having."""
    # Comments stripped: the reason for this rule is written down in one, and
    # matching that would make the test assert the opposite of what it means.
    code = "\n".join(
        line for line in _text(MACOS).splitlines() if not line.lstrip().startswith("#")
    )
    assert "python3" not in code
    assert "jq " not in code


def test_an_unknown_manifest_schema_is_refused_rather_than_guessed_at():
    """The manifest names paths. Misreading one deletes the wrong thing."""
    assert re.search(r"schema\s*-ne\s*1", _text(WINDOWS))


# --------------------------------------------------------------------------- #
# The manual checklist that ships with the product
# --------------------------------------------------------------------------- #
def test_the_doc_names_every_pinned_identifier():
    """GFP-102: a user whose uninstall failed cannot read Jira, and can only
    remove by hand what this document names correctly."""
    body = _text(DOC)
    for name in (
        ip.WINDOWS_INSTALL_DIRNAME,
        ip.WINDOWS_START_MENU_FOLDER,
        ip.WINDOWS_TASK_NAME,
        ip.MACOS_LAUNCH_AGENT_LABEL,
        ip.MACOS_APP_BUNDLE_NAME,
        ip.MACOS_SUPPORT_DIRNAME,
        ip.MACOS_BUNDLE_IDENTIFIER,
    ):
        assert name in body, f"{name} is not in UNINSTALL.md"


def test_the_doc_puts_the_timer_first_on_both_platforms():
    body = _text(DOC)
    assert body.index("Unregister-ScheduledTask") < body.index("%LOCALAPPDATA%\\grocery-planner")
    assert body.index("launchctl bootout") < body.index("Application\\ Support/grocery-planner")


def test_the_doc_warns_about_the_credentials():
    body = _text(DOC)
    assert "kroger-env.config" in body
    assert "wholefoods_session.json" in body


def test_the_doc_lists_every_relocating_environment_variable():
    """Any one of these makes 'delete the folder' insufficient."""
    body = _text(DOC)
    for var in (
        "GROCERY_PLANNER_DB",
        "GROCERY_PLANNER_CONFIG",
        "GROCERY_PLANNER_LOG_DIR",
        "GROCERY_PLANNER_KROGER_CONFIG",
        "GROCERY_PLANNER_WHOLEFOODS_SESSION",
    ):
        assert var in body, f"{var} is not documented in UNINSTALL.md"


def test_the_doc_says_there_is_no_preferences_plist():
    """Verified, and worth stating so nobody wastes time looking."""
    assert "Preferences" in _text(DOC)
