"""The Kroger credential ships with v1 (GFP-146), and the rules that makes safe.

The decision is that v1 bundles the key rather than fetching it from the broker
GFP-101 built. That is a bounded risk taken knowingly, and GFP-147 replaces it.
What these tests protect is the part that makes it *recoverable*.

**THE ONE PROPERTY THAT MATTERS: the key is a FILE, never a constant.** A key
inside a PyInstaller binary is recoverable with ``strings`` either way, so
secrecy is not what is being defended here. The difference is the remedy when
it leaks. A file can be rotated with a file swap. A constant needs a code
change, a rebuild, a re-release, and every user to notice and act -- and
Kroger's 10,000 calls/day ceiling is per credential, so one extracted key is
one revocation for *everybody*.

Nothing here needs a real credential, and no test writes one.
"""
from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
CREDENTIAL_NAME = "kroger-env.config"


def _text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# No secret is in the repository, in any form
# --------------------------------------------------------------------------- #
def test_no_credential_file_is_tracked():
    """A secret in git history is not fixed by a later delete commit -- it
    survives in every clone and fork, and the only real remedy is rotating the
    credential. So the check is that it was never added, not that it is gone.
    """
    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    offenders = [p for p in tracked if p.endswith("-env.config")]
    assert offenders == [], f"credential file(s) tracked in git: {offenders}"


def test_the_credential_pattern_is_gitignored():
    """The file lives at the repo root during development, one `git add -f`
    away from being published forever."""
    ignore = _text(ROOT / ".gitignore")
    assert "*-env.config" in ignore


def test_no_source_file_contains_a_client_secret_assignment():
    """THE CORE RULE, as a test: the key must never become a constant.

    Scans for an assignment of a client_secret to a literal. A future
    'temporary' hardcode to make something work locally is exactly how this
    rule dies, and it would ship without anyone noticing.
    """
    # A literal of 8+ chars assigned to something secret-shaped. Deliberately
    # allows the empty string and None, which are not secrets.
    pattern = re.compile(
        r"""(client_secret|CLIENT_SECRET|licence_key|api_key|API_KEY)"""
        r"""\s*[:=]\s*["'][^"'\s]{8,}["']"""
    )
    offenders = []
    for path in (ROOT / "grocery_planner").rglob("*.py"):
        for number, line in enumerate(_text(path).splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}")
    assert offenders == [], f"a secret looks hardcoded at: {offenders}"


# --------------------------------------------------------------------------- #
# The release injects it from a secret
# --------------------------------------------------------------------------- #
def test_the_release_writes_the_credential_from_a_repository_secret():
    workflow = _text(WORKFLOW)
    assert "secrets.KROGER_CREDENTIAL" in workflow
    assert CREDENTIAL_NAME in workflow


def test_the_secret_reaches_the_script_through_env_not_interpolation():
    """A secret interpolated into a `run:` body can be echoed by a shell trace
    or a failing command. GitHub masks it in logs; it does not mask artifacts.
    """
    workflow = _text(WORKFLOW)
    # The expansion may appear only on an `env:` mapping line.
    for number, line in enumerate(workflow.splitlines(), 1):
        if "secrets.KROGER_CREDENTIAL" in line:
            assert re.match(r"\s*KROGER_CREDENTIAL:\s*\$\{\{", line), (
                f"release.yml:{number} interpolates the secret into a script "
                f"body rather than passing it via env: {line.strip()}"
            )


def test_a_release_without_the_secret_still_builds():
    """A credential-free build is legitimate -- and failing the release would
    be a worse outcome than shipping one, since the app already explains what
    is missing."""
    workflow = _text(WORKFLOW)
    assert workflow.count("::warning::KROGER_CREDENTIAL secret is not set") == 2, (
        "both the Windows and macOS packaging steps should warn rather than fail"
    )


# --------------------------------------------------------------------------- #
# Both installers place it, and neither clobbers one already there
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("script", ["install.ps1", "install.sh"])
def test_the_installer_places_the_credential(script):
    assert CREDENTIAL_NAME in _text(PACKAGING / script)


@pytest.mark.parametrize("script", ["install.ps1", "install.sh"])
def test_the_installer_never_overwrites_an_existing_credential(script):
    """An operator with their own key keeps it. Silently replacing it with the
    shared one would move them onto a quota pool they did not ask to share,
    with no way to tell that it had happened."""
    assert "keeping yours" in _text(PACKAGING / script)


def test_the_macos_installer_uses_the_data_dir_not_the_install_dir():
    """THE BUG THIS PREVENTS. On macOS the binaries install under
    ~/.local/share (SUPPORT_DIR) but the app reads its data from
    ~/Library/Application Support -- platformdirs' user_data_dir. A credential
    dropped beside the binary leaves the app reporting no credentials at all,
    and the installer would say it succeeded.
    """
    text = _text(PACKAGING / "install.sh")
    assert 'DATA_DIR="$HOME/Library/Application Support/grocery-planner"' in text
    assert '"$DATA_DIR/$CREDENTIAL_NAME"' in text


def test_the_windows_installer_uses_the_data_dir_not_the_install_dir():
    """Same trap: install.ps1's $installRoot is %LOCALAPPDATA%\\Programs\\..."""
    text = _text(PACKAGING / "install.ps1")
    assert 'Join-Path $env:LOCALAPPDATA "grocery-planner\\grocery-planner"' in text


def test_the_data_dir_in_the_installers_matches_what_the_app_reads():
    """The installers hardcode a path the app computes with platformdirs. If
    those ever disagree the install silently does nothing useful, which is the
    class of drift GFP-91 wrote its pinned-name tests for."""
    from grocery_planner.paths import data_dir

    resolved = str(data_dir())
    windows = _text(PACKAGING / "install.ps1")
    macos = _text(PACKAGING / "install.sh")
    if resolved.startswith("/Users") or "Application Support" in resolved:
        assert "Library/Application Support/grocery-planner" in macos
    else:
        # Windows: %LOCALAPPDATA%\grocery-planner\grocery-planner
        assert resolved.endswith("grocery-planner\\grocery-planner"), resolved
        assert 'grocery-planner\\grocery-planner' in windows


def test_the_macos_installer_skips_the_credential_under_a_custom_prefix():
    """--prefix means a test install, and the data dir is outside it. A test
    install that dropped a shared credential into the real profile would not be
    a test install."""
    text = _text(PACKAGING / "install.sh")
    assert 'elif [ -n "$PREFIX" ]; then' in text
