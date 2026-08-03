"""The installers, checked from Python (GFP-91).

Two of these scripts are PowerShell and shell, so pytest cannot run them on a
Windows dev machine and CI runs them for real on their own runners (GFP-116).
What CAN be checked here is the class of mistake that a successful CI run would
not catch, and which is the most expensive one this ticket has:

**THE PINNED NAMES DRIFTING APART.** GFP-102's rule 1 is that the Scheduled
Task path, the LaunchAgent label and the install directory cannot change
between releases, because a user whose uninstall failed can only remove by hand
what the documentation correctly names. Those names now exist in four places in
three languages -- install.ps1, install.sh, install_paths.py, and the
documentation -- and nothing about editing one of them makes you edit the
others. An install that works perfectly and a checklist naming a directory that
no longer exists is exactly the failure GFP-102 was written to prevent, and it
passes every functional test.

So: install_paths.py is authoritative, and these tests fail if a script or a
doc disagrees with it.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from grocery_planner import install_paths as ip

ROOT = pathlib.Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"
WINDOWS_INSTALLER = PACKAGING / "install.ps1"
MACOS_INSTALLER = PACKAGING / "install.sh"
INSTALL_DOC = PACKAGING / "INSTALL.md"


def _text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# The scripts exist and are the shape they claim to be
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "path", [WINDOWS_INSTALLER, MACOS_INSTALLER, INSTALL_DOC], ids=lambda p: p.name
)
def test_the_installer_ships(path):
    assert path.exists(), f"{path.name} is missing"
    assert path.stat().st_size > 0


def test_the_macos_installer_has_a_shebang_and_fails_fast():
    """`set -e` is what stops a half-copied install from reporting success."""
    body = _text(MACOS_INSTALLER)
    assert body.startswith("#!/bin/bash"), "no shebang"
    assert re.search(r"^set -euo pipefail$", body, re.M), "missing `set -euo pipefail`"


# --------------------------------------------------------------------------- #
# The pinned names -- the reason this file exists
# --------------------------------------------------------------------------- #
def test_the_windows_installer_uses_the_pinned_names():
    body = _text(WINDOWS_INSTALLER)
    assert ip.APP_DISPLAY_NAME in body
    assert ip.WINDOWS_INSTALL_DIRNAME in body
    assert ip.WINDOWS_START_MENU_FOLDER in body
    assert ip.MANIFEST_FILENAME in body
    # The registry key is written as a PowerShell literal, so compare the tail
    # rather than the HKCU: prefix form.
    assert ip.WINDOWS_REGISTRY_KEY.split("\\")[-1] in body
    assert "CurrentVersion\\Uninstall" in body


def test_the_macos_installer_uses_the_pinned_names():
    body = _text(MACOS_INSTALLER)
    assert ip.APP_DISPLAY_NAME in body
    assert ip.MACOS_APP_BUNDLE_NAME in body
    assert ip.MACOS_SUPPORT_DIRNAME in body
    assert ip.MACOS_CLI_DIRNAME in body
    assert ip.MANIFEST_FILENAME in body


def test_both_installers_write_the_same_manifest_schema():
    """The uninstaller refuses a schema it does not understand, so a mismatch
    would make one platform's install un-uninstallable."""
    for path in (WINDOWS_INSTALLER, MACOS_INSTALLER):
        assert str(ip.MANIFEST_SCHEMA) in _text(path), path.name


def test_the_install_doc_names_the_real_locations():
    """GFP-102: a user removing this by hand can only remove what the docs name
    correctly."""
    body = _text(INSTALL_DOC)
    assert ip.WINDOWS_INSTALL_DIRNAME in body
    assert ip.MACOS_SUPPORT_DIRNAME in body
    assert ip.MACOS_APP_BUNDLE_NAME in body
    assert "CurrentVersion" in body, "the Add/Remove Programs key is not documented"


# --------------------------------------------------------------------------- #
# The properties the ticket actually asks for
# --------------------------------------------------------------------------- #
def test_both_installers_offer_a_dry_run():
    """An operation that changes a machine should be able to answer 'what would
    this do?' without doing it -- same stance as `gplan scrape --dry-run`."""
    assert "DryRun" in _text(WINDOWS_INSTALLER)
    assert "--dry-run" in _text(MACOS_INSTALLER)


def test_neither_installer_needs_administrator_rights():
    """A nutritionist on a managed work laptop may not have an administrator
    password, and an installer they cannot run is not an installer."""
    windows = _text(WINDOWS_INSTALLER)
    assert "HKLM" not in windows, "writing to HKLM would require elevation"
    assert "LOCALAPPDATA" in windows

    macos = _text(MACOS_INSTALLER)
    assert "sudo" not in macos, "an installer must not ask for sudo"
    # /Applications is machine-wide and needs authentication; ~/Applications
    # does not. The bare string appears inside "$HOME/Applications", so check
    # for the rooted form specifically.
    assert not re.search(r'(?<![\w$}~/"])/Applications', macos), \
        "use ~/Applications, not the machine-wide /Applications"


def test_the_macos_installer_does_not_silently_defeat_gatekeeper():
    """Stripping com.apple.quarantine without being asked teaches users that
    installers do that, which is the habit every malicious package relies on.

    So `xattr -d` must appear only behind the explicit opt-in flag.
    """
    body = _text(MACOS_INSTALLER)
    for match in re.finditer(r"xattr\s+-d\w*\s", body):
        line_start = body.rfind("\n", 0, match.start()) + 1
        # The one deletion is inside the `if [ "$CLEAR_QUARANTINE" -eq 1 ]`
        # branch; walk back far enough to see the guard.
        context = body[max(0, line_start - 400):match.end()]
        assert "CLEAR_QUARANTINE" in context, (
            "a quarantine attribute is removed outside the --clear-quarantine "
            "opt-in"
        )


def test_both_installers_verify_the_binary_actually_runs():
    """A copy that succeeded is not an install that worked: a binary for the
    wrong architecture copies perfectly and fails on first launch, at which
    point the user blames the app rather than the install."""
    assert "version" in _text(WINDOWS_INSTALLER)
    assert re.search(r'gplan" version', _text(MACOS_INSTALLER))


def test_the_windows_installer_refuses_to_overwrite_a_running_binary():
    """Windows locks a running executable, so the copy fails partway and leaves
    a half-installed directory."""
    body = _text(WINDOWS_INSTALLER)
    assert "Get-Process" in body and "gplan-gui" in body


# --------------------------------------------------------------------------- #
# The manifest reader
# --------------------------------------------------------------------------- #
def test_a_missing_manifest_reads_as_none_rather_than_raising(tmp_path):
    """This is called by an uninstaller. A traceback in front of someone
    already trying to remove the software is the worst possible moment."""
    assert ip.read_manifest(tmp_path) is None


def test_a_corrupt_manifest_reads_as_none(tmp_path):
    (tmp_path / ip.MANIFEST_FILENAME).write_text("{not json", encoding="utf-8")
    assert ip.read_manifest(tmp_path) is None


def test_a_manifest_from_an_unknown_schema_is_refused(tmp_path):
    """Better to fall back to the documented default locations than to delete
    by guesswork against a shape this version does not understand."""
    (tmp_path / ip.MANIFEST_FILENAME).write_text(
        '{"schema": 99, "files": ["/etc"]}', encoding="utf-8"
    )
    assert ip.read_manifest(tmp_path) is None


def test_a_good_manifest_reads_back(tmp_path):
    (tmp_path / ip.MANIFEST_FILENAME).write_text(
        '{"schema": 1, "app": "Grocery Planner", "files": ["a", "b"]}',
        encoding="utf-8",
    )
    manifest = ip.read_manifest(tmp_path)
    assert manifest is not None
    assert manifest["files"] == ["a", "b"]


def test_a_manifest_with_a_bom_reads_back(tmp_path):
    """Notepad adds one, and this file can end up hand-inspected. GFP-93 hit
    exactly this with the Whole Foods cookie, so it is a known failure here
    rather than a hypothetical."""
    (tmp_path / ip.MANIFEST_FILENAME).write_text(
        '{"schema": 1, "files": []}', encoding="utf-8-sig"
    )
    assert ip.read_manifest(tmp_path) is not None


def test_the_default_install_root_is_under_the_user_profile():
    """Per-user, on whichever platform the tests happen to run."""
    root = ip.default_install_root()
    assert str(root).startswith(str(pathlib.Path.home())) or "AppData" in str(root)


# --------------------------------------------------------------------------- #
# Shell scripts, checked for the mistakes only their own OS would reveal
# --------------------------------------------------------------------------- #
SHELL_SCRIPTS = sorted(PACKAGING.glob("*.sh")) + sorted((ROOT / "scripts").glob("*.sh"))


@pytest.mark.parametrize("path", SHELL_SCRIPTS, ids=lambda p: p.name)
def test_every_shell_helper_is_defined(path):
    """Catches calling a helper that exists only in the OTHER script.

    Found the hard way: install.sh gained a line calling `did`, which is
    uninstall.sh's helper -- install.sh calls it `green`. `bash -n` passes,
    because an undefined function is a runtime error rather than a syntax one.
    It surfaced only on the macOS runner, minutes into a 10x-billed job, after
    the install had already copied 54 MB.
    """
    body = path.read_text(encoding="utf-8")
    defined = set(re.findall(r"^(\w+)\(\)", body, re.M))
    helpers = ("green", "gray", "skip", "warn", "did", "bad", "note", "run",
               "is_timer", "remove_path")
    used = set(re.findall(r"^\s*(" + "|".join(helpers) + r")\b", body, re.M))
    missing = used - defined
    assert not missing, f"{path.name} calls undefined helper(s): {sorted(missing)}"


@pytest.mark.parametrize("path", SHELL_SCRIPTS, ids=lambda p: p.name)
def test_no_shell_script_has_windows_line_endings(path):
    """A .sh with CRLF gives `bad interpreter: /bin/bash^M` on macOS, which
    reads as a missing bash rather than a line-ending problem and sends whoever
    hits it a long way in the wrong direction.

    .gitattributes pins these to LF; this is the check that the pin works. It
    fails on a Windows checkout made BEFORE that pin existed, which is correct
    -- such a tree would ship a broken installer.
    """
    assert b"\r\n" not in path.read_bytes(), (
        f"{path.name} has CRLF line endings and would not run on macOS"
    )
