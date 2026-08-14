# ######### decohen-partners ##########
# Protein Ledger
"""Where an install puts things, and what it calls them (GFP-91).

GFP-102 pinned a set of identifiers -- the Windows Scheduled Task path, the
macOS LaunchAgent label, the install directory -- and stated the rule that goes
with them: **you cannot hand-remove what you cannot name**, so these must not
change between releases. A task renamed in v1.1 orphans every v1.0 install
permanently, because the uninstaller shipped with v1.1 will look for the new
name and the v1.0 timer will keep firing forever.

That rule needs somewhere to live. The names are used in four places -- the
Windows installer, the macOS installer, the uninstallers, and the background
timer (GFP-102) -- written in three different languages. This module is the
copy the tests treat as authoritative: ``tests/test_installer_scripts.py``
parses the shell scripts and fails if any literal there disagrees with the
constant here.

WHY THE MANIFEST EXISTS, since it is the non-obvious part. An uninstaller that
assumes default locations silently leaves files behind on any install that
chose a different prefix, and on this app "files left behind" means a Kroger
client_secret and a live Whole Foods session cookie sitting on a machine that
may be resold or repaired. So the installer records what it actually did, and
the uninstaller removes exactly that.

Deliberately no imports beyond the standard library and :mod:`paths`. This is
read by the uninstaller at a moment when the app may be half-removed.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Pinned by GFP-102. Do not change these without a migration story.
#
# RENAMED ONCE, in 1.1.0 (GFP-158), from "Grocery Planner" to "Protein Ledger".
# The migration story is that there was nothing to migrate: v1.0.0 had been
# tagged for under an hour and had never been installed anywhere. That window
# has now closed -- anyone who installs 1.1.0 gets these strings written into
# their Start Menu, registry, Task Scheduler and LaunchAgents, and the
# UNINSTALL checklist names them so a failed uninstall can be finished by hand.
# Changing them again means writing a real migration.
#
# WHAT DELIBERATELY DID NOT CHANGE, and why:
#   * the Python package (grocery_planner) and the CLI (gplan) -- invisible to
#     a user, and renaming them is pure churn across every import and doc;
#   * the DATA directory (see paths.APP_NAME) -- relocating it is the only
#     change here that can lose a nutritionist's clients, and a directory
#     nobody ever looks at carries no branding value to pay for that risk.
# --------------------------------------------------------------------------- #

#: Human-facing product name, used in the Start Menu and Add/Remove Programs.
APP_DISPLAY_NAME = "Protein Ledger"

#: Directory name under %LOCALAPPDATA%\Programs on Windows.
WINDOWS_INSTALL_DIRNAME = "ProteinLedger"

#: HKCU, matching the per-user install -- see install.ps1 for why not HKLM.
WINDOWS_REGISTRY_KEY = (
    r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ProteinLedger"
)

#: Start Menu subfolder. Its own folder so manual cleanup is "delete the folder".
WINDOWS_START_MENU_FOLDER = "Protein Ledger"

#: Task Scheduler path and name for GFP-102's background refresh. In its own
#: folder for the same reason as the Start Menu entry.
WINDOWS_TASK_PATH = "\\ProteinLedger\\"
WINDOWS_TASK_NAME = "Refresh"

#: macOS. The com.proteinledger.* prefix matches the PyInstaller
#: bundle_identifier in packaging/gplan-gui.spec, so everything this app
#: installs greps as one family -- which is what makes `launchctl list | grep
#: grocery` a usable verification step.
MACOS_LAUNCH_AGENT_LABEL = "com.proteinledger.refresh"
MACOS_BUNDLE_IDENTIFIER = "com.proteinledger.gui"
MACOS_APP_BUNDLE_NAME = "Protein Ledger.app"

#: Where the CLI is linked on macOS. ~/.local/bin rather than /usr/local/bin:
#: no sudo, and it is already on PATH for most shells.
MACOS_CLI_DIRNAME = ".local/bin"

#: Bookkeeping root on macOS -- see :func:`default_install_root`.
MACOS_SUPPORT_DIRNAME = ".local/share/protein-ledger"

#: Where the .app is copied. ~/Applications, not /Applications: per-user again,
#: so no administrator password is needed.
MACOS_APPLICATIONS_DIRNAME = "Applications"

MANIFEST_FILENAME = "install-manifest.json"

#: Bumped only if the manifest's shape changes incompatibly. The uninstaller
#: refuses a schema it does not understand rather than deleting by guesswork.
MANIFEST_SCHEMA = 1


def default_install_root() -> Path:
    """Where the installer puts binaries when not told otherwise.

    Per-user on both platforms, so neither installer needs administrator rights
    -- a nutritionist on a managed work laptop may simply not have them, and an
    installer they cannot run is not an installer.
    """
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "Programs" / WINDOWS_INSTALL_DIRNAME
    # macOS splits, because the .app has to be in an Applications folder to be
    # launchable and a CLI has to be on PATH. So this is the BOOKKEEPING root:
    # the manifest, the uninstaller and the CLI binary itself live here, with
    # ~/.local/bin/gplan a symlink to it and the bundle copied to
    # ~/Applications. The manifest lists the real location of every file, so
    # nothing depends on guessing which of the three directories a thing is in.
    return Path.home() / ".local" / "share" / "grocery-planner"


def manifest_path(install_root: Path | str | None = None) -> Path:
    """Where the install manifest lives.

    Inside the install root, not the data directory: the data directory is the
    thing being deleted, and a manifest that describes the deletion should not
    be the first casualty of it.
    """
    root = Path(install_root) if install_root is not None else default_install_root()
    return root / MANIFEST_FILENAME


def read_manifest(install_root: Path | str | None = None) -> dict[str, Any] | None:
    """The manifest, or ``None`` if there isn't a readable one.

    Never raises. This is called by an uninstaller, and an unreadable manifest
    must degrade to "fall back to the documented default locations", not to a
    traceback in front of someone who is already trying to remove the software.
    """
    target = manifest_path(install_root)
    try:
        raw = target.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    if parsed.get("schema") != MANIFEST_SCHEMA:
        return None
    return parsed
