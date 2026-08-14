# ######### decohen-partners ##########
# Protein Ledger
"""What an uninstall must remove, resolved rather than assumed (GFP-92).

The uninstallers themselves are PowerShell and shell, because they have to be
able to delete the binary this code lives inside and to run on a machine where
that binary is already gone. But *deciding what to remove* is the part with
real logic in it, and duplicating that logic in two shell languages is how the
two platforms quietly diverge.

So: this module builds the plan, both uninstallers consume it, and pytest tests
it once.

WHY IT RESOLVES INSTEAD OF ASSUMING. GFP-102's checklist ends with the case
that makes a hard-coded list wrong -- any of ``GROCERY_PLANNER_DB``,
``GROCERY_PLANNER_CONFIG``, ``GROCERY_PLANNER_LOG_DIR``,
``GROCERY_PLANNER_KROGER_CONFIG`` or ``GROCERY_PLANNER_WHOLEFOODS_SESSION``
relocates a file OUT of the user-data directory, at which point "delete the
folder" silently leaves it behind. And what gets left behind is not neutral:
the Kroger config holds an OAuth2 client_secret and the Whole Foods file holds
a live session cookie, on a machine that may then be resold, repaired or handed
on.

TWO RULES FROM THAT CHECKLIST, both load-bearing here:

* **Remove the directory, not a list of filenames.** The data directory
  accumulates things no uninstaller can enumerate in advance -- SQLite's
  ``-wal``/``-shm`` files, and (verified on the dev machine) a hand-made
  ``grocery_planner.pre-gfp60-backup.sqlite3``. An allowlist misses all of it.
* **The timer goes first.** A leftover Scheduled Task or LaunchAgent keeps
  firing against a binary that no longer exists. Removing it last lets a firing
  timer race the removal of what it invokes.

Client records are hand-entered and irreplaceable (GFP-34), so the plan carries
a warning whenever a database is about to go, and the uninstallers refuse to
proceed without an explicit confirmation.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from . import credentials, install_paths
from .config import CONFIG_ENV_VAR, CONFIG_FILENAME
from .logs import LOG_DIR_ENV_VAR, log_dir
from .paths import DB_ENV_VAR, data_dir, db_path

#: An item's kind, which is what tells a shell script HOW to remove it. Kept as
#: plain strings because the consumers are PowerShell and bash.
DIRECTORY = "directory"
FILE = "file"
TASK = "task"          # Windows Scheduled Task
AGENT = "agent"        # macOS LaunchAgent
REGISTRY = "registry"
SHORTCUT = "shortcut"


@dataclass(frozen=True)
class Item:
    """One thing to remove."""

    kind: str
    label: str
    target: str
    #: True if losing this destroys something a user cannot get back.
    irreplaceable: bool = False
    #: True if this holds a secret, so a failure to remove it must be shouted
    #: about rather than logged.
    sensitive: bool = False
    #: Set when an environment variable moved this off its default path. The
    #: uninstaller prints it, because "we deleted somewhere you did not expect"
    #: should never be silent.
    relocated_by: str | None = None


def _relocated(env_var: str) -> str | None:
    return env_var if os.environ.get(env_var) else None


def plan() -> list[Item]:
    """Everything this install put on the machine, in removal order.

    ORDER MATTERS and is not alphabetical: the background timer is first
    (GFP-102), then the things outside the data directory, then the data
    directory itself last -- so that if removal stops partway, what remains is
    an inert directory of files rather than a live timer pointing at a
    half-removed app.
    """
    items: list[Item] = []

    # 1. The timer, first. GFP-102 may not have shipped yet; the uninstaller
    #    still asks, because an uninstaller that only removes what the CURRENT
    #    version installs cannot clean up after any other version.
    if sys.platform == "win32":
        items.append(Item(
            kind=TASK,
            label="Scheduled task (background refresh)",
            target=install_paths.WINDOWS_TASK_PATH + install_paths.WINDOWS_TASK_NAME,
        ))
    elif sys.platform == "darwin":
        items.append(Item(
            kind=AGENT,
            label="LaunchAgent (background refresh)",
            target=install_paths.MACOS_LAUNCH_AGENT_LABEL,
        ))

    # 2. Anything an environment variable moved OUT of the data directory.
    #    Listed individually and only when relocated -- inside the directory
    #    they are covered by rule 2 ("remove the directory"), and listing them
    #    twice would report "could not remove" for a file already gone.
    home = data_dir().resolve()

    def _outside(target: Path) -> bool:
        try:
            return home not in target.resolve().parents and target.resolve() != home
        except OSError:
            return True

    database = db_path()
    if _outside(database):
        items.append(Item(
            kind=FILE, label="Database", target=str(database),
            irreplaceable=True, relocated_by=_relocated(DB_ENV_VAR),
        ))

    config_file = Path(os.environ[CONFIG_ENV_VAR]).expanduser() if os.environ.get(
        CONFIG_ENV_VAR) else home / CONFIG_FILENAME
    if _outside(config_file):
        items.append(Item(
            kind=FILE, label="Settings", target=str(config_file),
            relocated_by=_relocated(CONFIG_ENV_VAR),
        ))

    logs = log_dir()
    if _outside(logs):
        items.append(Item(
            kind=DIRECTORY, label="Logs", target=str(logs),
            relocated_by=_relocated(LOG_DIR_ENV_VAR),
        ))

    for spec in credentials.SPECS.values():
        override = spec.override()
        if override is not None and _outside(override):
            items.append(Item(
                kind=FILE,
                label=f"{spec.name} credential",
                target=str(override),
                sensitive=True,
                relocated_by=spec.env_var,
            ))

    # 3. The data directory itself. LAST, and as a directory: it accumulates
    #    files no uninstaller can name in advance.
    items.append(Item(
        kind=DIRECTORY,
        label="All application data (database, settings, credentials, logs)",
        target=str(home),
        irreplaceable=True,
        sensitive=True,
    ))
    return items


def _credential_present(spec: credentials.CredentialSpec) -> bool:
    """Is there a credential here, without reading the secret if avoidable.

    Two things this is careful about, both because it runs inside an
    uninstaller and an uninstaller that crashes is worse than useless:

    * It NEVER raises. A path that exists but cannot be read -- a directory
      where a file was expected, a permissions problem, a file another process
      has locked -- counts as PRESENT. That is the safe direction: it produces
      a warning about a credential that may not be there, rather than silence
      about one that is.
    * It asks the provider for the location rather than fetching the document,
      so the common case never loads a secret into memory merely to ask whether
      it exists.
    """
    active = credentials.provider()
    locate = getattr(active, "path", None)
    if callable(locate):
        try:
            return locate(spec).exists()
        except OSError:
            return True
    try:
        return active.fetch(spec) is not None
    except OSError:
        return True


def warnings() -> list[str]:
    """Things the user must be told BEFORE they confirm.

    Separate from the plan because these are not lines in a list to skim past;
    they are the reason the confirmation exists at all.
    """
    notes: list[str] = []
    database = db_path()
    if database.exists():
        notes.append(
            f"Your client records are in {database} and CANNOT be recovered "
            "once this runs. Export them first: gplan export --help"
        )
    holding = [
        spec.name for spec in credentials.SPECS.values() if _credential_present(spec)
    ]
    if holding:
        notes.append(
            "Stored credentials will be deleted (" + ", ".join(sorted(holding))
            + "). If any file cannot be removed, delete it by hand -- a "
            "leftover credential on a machine that is later resold or repaired "
            "is the failure this warning exists for."
        )
    return notes


def to_lines(items: list[Item]) -> str:
    """The plan as tab-separated lines: ``kind<TAB>flags<TAB>label<TAB>target``.

    Deliberately not JSON. Both uninstallers have to parse this, and while
    PowerShell has ConvertFrom-Json, macOS is not guaranteed a JSON parser in
    the shell -- and reaching for python3 inside an uninstaller for the app
    whose Python is being removed is a dependency worth not having.
    """
    out = []
    for item in items:
        flags = ",".join(
            f for f, on in (
                ("irreplaceable", item.irreplaceable),
                ("sensitive", item.sensitive),
                (f"relocated:{item.relocated_by}", bool(item.relocated_by)),
            ) if on
        ) or "-"
        out.append(f"{item.kind}\t{flags}\t{item.label}\t{item.target}")
    return "\n".join(out)


def to_json(items: list[Item]) -> str:
    return json.dumps(
        {
            "schema": install_paths.MANIFEST_SCHEMA,
            "warnings": warnings(),
            "items": [
                {
                    "kind": i.kind, "label": i.label, "target": i.target,
                    "irreplaceable": i.irreplaceable, "sensitive": i.sensitive,
                    "relocated_by": i.relocated_by,
                }
                for i in items
            ],
        },
        indent=2,
    )
