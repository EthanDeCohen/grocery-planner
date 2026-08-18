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


# --------------------------------------------------------------------------- #
# The RELEASE WORKFLOW is part of the pinned-name surface too (GFP-158)
# --------------------------------------------------------------------------- #
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def test_the_release_ships_the_app_bundle_under_its_pinned_name():
    """THE BUG THIS EXISTS FOR, and it shipped in v1.1.0.

    GFP-158 renamed MACOS_APP_BUNDLE_NAME, and every installer script followed
    because the tests above pin them. release.yml was NOT covered, so it kept
    copying the bundle to "Grocery Planner.app" while install.sh looked for
    "Protein Ledger.app".

    The failure was silent by design: install.sh treats a missing bundle as "a
    CLI-only release is legitimate" and installs the CLI alone. So the macOS
    ZIP shipped a GUI that the installer beside it would never install, and
    nothing failed loudly enough to notice.
    """
    workflow = _text(RELEASE_WORKFLOW)
    assert f'"$NAME/{ip.MACOS_APP_BUNDLE_NAME}"' in workflow, (
        f"release.yml does not ship the bundle as {ip.MACOS_APP_BUNDLE_NAME!r}; "
        "install.sh will not find it and will silently install the CLI only"
    )


#: The legacy Excel workbook, which is a real filename in the repo root and
#: is NOT part of the rename. Anything else matching the old product name is.
LEGACY_FILENAME = "GroceryPlanner.xlsm"


def test_no_workflow_carries_a_stale_product_name():
    """EVERY workflow, not just the release one.

    The GFP-158 rename reached install_paths.py and every script the tests
    above pin, and missed all three workflow files. release.yml shipped a
    bundle the installer could not find; ci.yml and macos-lifecycle.yml
    asserted paths that no longer existed, so the macOS lifecycle test was
    checking for an app bundle at a name nothing produces any more.

    Scanning the whole directory rather than a list of files, so a workflow
    added later is covered without anyone remembering to add it here.
    """
    offenders = {}
    for workflow in sorted((ROOT / ".github").rglob("*")):
        if not workflow.is_file() or workflow.suffix not in (".yml", ".yaml", ".md"):
            continue
        text = _text(workflow).replace(LEGACY_FILENAME, "")
        hits = [s for s in ("Grocery Planner", "GroceryPlanner") if s in text]
        if hits:
            offenders[workflow.relative_to(ROOT).as_posix()] = hits
    assert offenders == {}, f"stale product name in: {offenders}"


def test_the_installer_can_find_what_the_release_packages():
    """The two halves of the contract, asserted against each other rather than
    against a literal: whatever release.yml copies in, install.sh must look
    for."""
    workflow = _text(RELEASE_WORKFLOW)
    installer = _text(MACOS_INSTALLER)
    assert ip.MACOS_APP_BUNDLE_NAME in workflow
    assert ip.MACOS_APP_BUNDLE_NAME in installer


def test_every_launchctl_check_greps_for_the_real_agent_label():
    """THE THIRD INSTANCE OF THE SAME BUG, and the one the earlier guards
    still missed.

    GFP-158 renamed MACOS_LAUNCH_AGENT_LABEL to com.proteinledger.refresh.
    The workflows verify registration with `launchctl list | grep -c grocery`
    -- a bare, lowercase, unbranded substring that neither the rename sweep
    (which matched "Grocery Planner" and "GroceryPlanner") nor the stale-name
    guard (which looks for those same two strings) covered.

    The agent registered perfectly; the CHECK looked for the old name and
    reported "expected 1 loaded agent, found 0". A correct install failing its
    own verification.

    So this asserts the relationship rather than a spelling: whatever a
    workflow greps launchctl for must actually occur in the label.
    """
    label = ip.MACOS_LAUNCH_AGENT_LABEL.lower()
    pattern = re.compile(r"launchctl[^\n|]*\|\s*grep\s+(?:-\w+\s+)*([^\s;|&]+)")
    offenders = []
    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        for number, line in enumerate(_text(workflow).splitlines(), 1):
            for needle in pattern.findall(line):
                if needle.strip("\"'").lower() not in label:
                    offenders.append(
                        f"{workflow.name}:{number} greps {needle!r}, "
                        f"which does not occur in {ip.MACOS_LAUNCH_AGENT_LABEL!r}"
                    )
    assert offenders == [], "launchctl checks look for the wrong agent:\n" + "\n".join(offenders)


# --------------------------------------------------------------------------- #
# Double-click wrappers (GFP-161)
# --------------------------------------------------------------------------- #
WRAPPERS = {
    "Install.cmd": "install.ps1",
    "Uninstall.cmd": "uninstall.ps1",
    "Install.command": "install.sh",
    "Uninstall.command": "uninstall.sh",
}


@pytest.mark.parametrize("wrapper,target", sorted(WRAPPERS.items()))
def test_each_wrapper_exists_and_calls_its_installer(wrapper, target):
    """A user should never have to type a command to install this.

    On Windows a .ps1 is refused outright under the default RemoteSigned
    policy once it carries a mark-of-the-web; on macOS Finder will not run a
    .sh on a double-click at all. Both are solved by an extension the shell
    treats differently, not by asking the user to change a setting.
    """
    text = _text(PACKAGING / wrapper)
    assert target in text, f"{wrapper} does not invoke {target}"


@pytest.mark.parametrize("wrapper", ["Install.cmd", "Uninstall.cmd"])
def test_the_windows_wrappers_bypass_policy_for_one_invocation_only(wrapper):
    """-ExecutionPolicy Bypass on the command line applies to THAT process.

    It must never become Set-ExecutionPolicy, which would weaken the user's
    machine permanently to install one app.
    """
    text = _text(PACKAGING / wrapper)
    assert "-ExecutionPolicy Bypass" in text
    assert "Set-ExecutionPolicy" not in text, (
        f"{wrapper} changes the machine's policy instead of bypassing for one run"
    )


@pytest.mark.parametrize("wrapper", ["Install.cmd", "Uninstall.cmd"])
def test_the_windows_wrappers_resolve_paths_from_their_own_folder(wrapper):
    r"""Double-clicking from Explorer often starts in C:\Windows, so a bare
    relative path would look for the installer in the wrong place."""
    assert "%~dp0" in _text(PACKAGING / wrapper)


@pytest.mark.parametrize("wrapper", ["Install.cmd", "Uninstall.cmd"])
def test_the_windows_wrappers_keep_the_window_open(wrapper):
    """Without `pause` the window closes instantly and the user sees nothing --
    neither success nor the error explaining a failure."""
    assert "pause" in _text(PACKAGING / wrapper)


@pytest.mark.parametrize("wrapper", ["Install.command", "Uninstall.command"])
def test_the_macos_wrappers_are_shell_scripts_with_unix_endings(wrapper):
    """A .command with CRLF fails on the shebang line. This is the third time
    a Windows-authored file has needed this check."""
    raw = (PACKAGING / wrapper).read_bytes()
    assert raw.startswith(b"#!"), f"{wrapper} has no shebang"
    assert b"\r\n" not in raw, f"{wrapper} has Windows line endings"


def test_the_macos_installer_wrapper_clears_quarantine_and_says_so():
    """The double-click path is aimed at somebody who would otherwise meet
    "Apple could not verify..." with no way forward but System Settings.

    Clearing quarantine on the app THIS installer just installed is within
    what the user asked for -- but it must be announced, not silent.
    """
    text = _text(PACKAGING / "Install.command")
    assert "--clear-quarantine" in text
    assert "without a security warning" in text, (
        "Install.command clears quarantine without telling the user"
    )


def test_plain_install_sh_still_refuses_to_clear_quarantine_on_its_own():
    """The conservative default stays for the terminal path. Only the
    double-click wrapper opts in."""
    text = _text(MACOS_INSTALLER)
    assert "CLEAR_QUARANTINE=0" in text or "CLEAR_QUARANTINE:-0" in text


def test_the_quarantine_advice_is_not_the_one_apple_removed():
    """install.sh told users to right-click and choose Open. Apple removed
    that bypass for unsigned apps in macOS 15, so the advice sent people to a
    dead end -- worse than no advice."""
    text = _text(MACOS_INSTALLER)
    assert "Privacy & Security" in text, "no working route offered"
    assert "no longer works" in text, "the stale right-click route is unqualified"


@pytest.mark.parametrize("wrapper", sorted(WRAPPERS))
def test_the_release_ships_every_wrapper(wrapper):
    """A wrapper that exists in packaging/ but not in the ZIP helps nobody."""
    assert wrapper in _text(RELEASE_WORKFLOW), f"release.yml does not ship {wrapper}"


# --------------------------------------------------------------------------- #
# Version parsing (GFP-159)
# --------------------------------------------------------------------------- #
def test_neither_installer_parses_the_version_by_product_name():
    """The banner read "Protein Ledger unknown installed" because both
    installers matched "grocery-planner <version>", which GFP-158 stopped
    producing. On Windows the same value lands in the registry as
    DisplayVersion, so Add/Remove Programs showed "unknown" too.

    Matching a version-shaped token instead survives the next rename.
    """
    for script in (MACOS_INSTALLER, WINDOWS_INSTALLER):
        text = _text(script)
        assert r"grocery-planner\s+" not in text and "^grocery-planner" not in text, (
            f"{script.name} still parses the version by the old product name"
        )


def test_the_version_regex_matches_what_gplan_actually_prints():
    """Asserted against the real output shape rather than a remembered one."""
    from grocery_planner import __version__, install_paths

    printed = f"{install_paths.APP_DISPLAY_NAME} {__version__}"
    match = re.search(r"(\d+\.\d+\.\d+\S*)\s*$", printed)
    assert match and match.group(1) == __version__


# --------------------------------------------------------------------------- #
# Mark-of-the-web (GFP-162)
# --------------------------------------------------------------------------- #
def test_the_windows_wrapper_unblocks_before_running_the_installer():
    """ORDER IS THE WHOLE POINT. install.ps1 cannot unblock itself -- it is the
    file Windows is refusing to run. So the wrapper must strip the tag first,
    then invoke it.

    Verified on a real download: Unblock-File alone made `.\install.ps1` work
    with no policy change at all, which is what identified mark-of-the-web
    rather than the policy as the actual obstacle.
    """
    text = _text(PACKAGING / "Install.cmd")
    assert "Unblock-File" in text
    assert text.index("Unblock-File") < text.index("-File \"%~dp0install.ps1\""), (
        "Install.cmd runs the installer before unblocking it, which cannot work"
    )


def test_the_windows_wrapper_unblocks_only_its_own_folder():
    """Scoped to %~dp0. Unblocking machine-wide would strip a security marker
    from files this installer has nothing to do with."""
    text = _text(PACKAGING / "Install.cmd")
    assert "-LiteralPath '%~dp0'" in text


def test_unblocking_the_installed_files_is_opt_in():
    """Same position as install.sh's --clear-quarantine: silently removing a
    security marker on the user's behalf is not the installer's call. The
    double-click path opts in; a terminal user does not get it by default."""
    text = _text(WINDOWS_INSTALLER)
    assert "[switch]$Unblock" in text
    assert "if ($Unblock)" in text
    assert "mark-of-the-web left in place" in text, (
        "the default path does not say that it left the tag alone"
    )


def test_the_double_click_path_passes_unblock():
    assert "-Unblock" in _text(PACKAGING / "Install.cmd")


def test_the_two_platforms_take_the_same_position():
    """Windows -Unblock and macOS --clear-quarantine are the same decision
    about the same kind of marker. If one ever becomes default-on without the
    other, that is a drift worth arguing about rather than inheriting."""
    windows = _text(WINDOWS_INSTALLER)
    macos = _text(MACOS_INSTALLER)
    assert "CLEAR_QUARANTINE=0" in macos
    assert "[switch]$Unblock" in windows      # a switch defaults to false
    for text, wrapper in ((windows, "Install.cmd"), (macos, "Install.command")):
        assert "not the installer's call" in text, (
            "the opt-in reasoning is no longer stated beside the code"
        )


def test_neither_installer_claims_unblocking_makes_it_signed():
    """A user told "this makes it safe" would be misled. Removing
    mark-of-the-web suppresses warnings about being DOWNLOADED; it does
    nothing about the absence of a signature."""
    assert "does NOT make an unsigned binary signed" in _text(WINDOWS_INSTALLER)


# --------------------------------------------------------------------------- #
# The one-click installer (GFP-340)
#
# ONE INSTALL PATH, NOT TWO. The whole design of the setup stub is that it
# unpacks a ZIP and then runs the install.ps1 out of it, so that the installed
# footprint is whatever install.ps1 lays down and cannot drift from it. Most of
# what follows checks that the stub has not quietly grown an install of its own
# -- because a second one would pass every functional test right up until it
# put something in a different place and orphaned an existing install.
# --------------------------------------------------------------------------- #
SETUP_STUB = PACKAGING / "setup" / "Setup.cs"
SETUP_BUILD = ROOT / "scripts" / "build_setup_exe.ps1"


def test_the_one_click_installer_ships():
    for path in (SETUP_STUB, SETUP_BUILD):
        assert path.exists(), f"{path.name} is missing"
        assert path.stat().st_size > 0


def test_the_stub_runs_the_real_installer():
    """It must invoke install.ps1, and pass the two switches that make a
    double-click enough: -Unblock for the mark-of-the-web, -StopRunning
    because nobody is there to read "close the app and try again"."""
    text = _text(SETUP_STUB)
    assert "install.ps1" in text, "the stub does not run install.ps1"
    assert "-Unblock" in text
    assert "-StopRunning" in text


def test_the_stub_knows_none_of_the_pinned_names():
    """The evidence that it delegates rather than installing anything itself.

    GFP-102 pinned the install root, the Start Menu folder, the registry key
    and the task path, and GFP-340 requires the one-click install to leave a
    footprint identical to the ZIP's. The cheapest way to be sure of that is
    for the stub not to know any of those names -- if it cannot say where the
    app goes, it cannot put it somewhere else.
    """
    # CODE ONLY. The stub's header explains which names GFP-102 pinned, so a
    # check that read the comments would fail on the paragraph saying it must
    # not do this.
    text = chr(10).join(
        line.split("//")[0] for line in _text(SETUP_STUB).splitlines()
    )
    # Deliberately NOT the display name or the install DIRECTORY name: the
    # stub says "Protein Ledger" on screen and names its single-instance mutex
    # after the app, both of which are fine. What it must never contain is
    # anything that decides WHERE something lands.
    forbidden = {
        "registry key": ip.WINDOWS_REGISTRY_KEY,
        "scheduled task path": ip.WINDOWS_TASK_PATH,
        "manifest filename": ip.MANIFEST_FILENAME,
        "the install root's parent": "LOCALAPPDATA",
        "the Start Menu": "Start Menu",
    }
    offenders = [
        f"{what} ({value!r})" for what, value in forbidden.items() if value in text
    ]
    assert offenders == [], (
        "the setup stub decides things only install.ps1 should decide:\n  "
        + "\n  ".join(offenders)
        + "\nA stub that installs anything itself is a second installer, and the "
        "two can drift apart."
    )


def test_the_stub_and_its_build_script_agree_on_the_trailer():
    """The stub finds its payload by a magic string the build script writes.

    Asserted as a RELATIONSHIP rather than as a spelling: the same byte values
    have to appear on both sides. A rename on one side alone produces an
    installer that builds cleanly and then tells the customer it has no
    payload.
    """
    stub_bytes = re.search(
        r"Magic\s*=\s*\{([^}]*)\}", _text(SETUP_STUB)
    )
    assert stub_bytes, "could not find the magic bytes in Setup.cs"
    build_bytes = re.search(
        r"\$magic\s*=\s*\[byte\[\]\]\(([^)]*)\)", _text(SETUP_BUILD)
    )
    assert build_bytes, "could not find the magic bytes in build_setup_exe.ps1"

    def values(text):
        return [int(v, 16) for v in re.findall(r"0x[0-9A-Fa-f]{2}", text)]

    assert values(stub_bytes.group(1)) == values(build_bytes.group(1)), (
        "Setup.cs and build_setup_exe.ps1 disagree about the payload marker"
    )


def test_stopping_a_running_app_is_opt_in():
    """install.ps1 must still REFUSE by default.

    Someone who typed ./install.ps1 in a terminal gets told to close the app;
    only the one-click path, which has no one reading a console, has its
    windows shut for it.
    """
    text = _text(WINDOWS_INSTALLER)
    assert "[switch]$StopRunning" in text, "install.ps1 has no -StopRunning switch"
    assert "if (-not $StopRunning) {" in text, (
        "install.ps1 no longer refuses by default when the app is running"
    )


def test_the_running_check_is_scoped_to_the_install_root():
    """A gplan.exe running from a development checkout locks nothing we are
    about to overwrite, so it is neither a reason to refuse nor something to
    kill."""
    text = _text(WINDOWS_INSTALLER)
    assert "function Get-BlockingProcess" in text
    assert "$installRoot.TrimEnd" in text, (
        "the running-process check no longer compares against the install root"
    )


def test_the_one_click_installer_needs_no_third_party_toolchain():
    """GFP-340 overturns GFP-91's "no installer toolchain" only as far as it
    has to. csc.exe is in the .NET Framework directory of every Windows machine
    and every runner, so there is still nothing to install to build a release.
    """
    text = _text(SETUP_BUILD)
    assert "csc.exe" in text, "the build script does not use the in-box compiler"
    assert "Microsoft.NET" in text, (
        "the compiler is no longer taken from the .NET Framework directory, "
        "which is the thing that makes it in-box"
    )

    # Checked as INSTALLATION, not as mention. install.ps1 names Inno Setup,
    # NSIS and WiX in prose -- it is where the decision not to use them is
    # written down -- so grepping for the names would fail on the very file
    # that records the rule. A toolchain can only reach a runner by being
    # installed, so that is what is looked for.
    offenders = []
    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        for number, line in enumerate(_text(workflow).splitlines(), 1):
            lowered = line.lower()
            if "choco install" in lowered or "winget install" in lowered:
                offenders.append(f"{workflow.name}:{number}: {line.strip()}")
    assert offenders == [], (
        "a build machine now needs something installed on it:\n  "
        + "\n  ".join(offenders)
    )


def test_the_release_builds_and_publishes_the_one_click_installer():
    text = _text(ROOT / ".github" / "workflows" / "release.yml")
    assert "build_setup_exe.ps1" in text, "release.yml does not build the installer"
    assert "assets/*.exe" in text, "release.yml does not publish the installer"
    assert "assets/*.zip" in text, (
        "release.yml stopped publishing the ZIP -- GFP-340 requires it to keep "
        "working so nothing breaks for anyone mid-transition"
    )


def test_ci_actually_runs_the_one_click_installer():
    """Building it proves it compiles. Only running it proves the stub can
    find its own payload and hand over."""
    text = _text(ROOT / ".github" / "workflows" / "ci.yml")
    assert "build_setup_exe.ps1" in text
    assert "ProteinLedger-Setup-ci.exe" in text
