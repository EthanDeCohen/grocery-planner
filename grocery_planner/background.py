# ######### decohen-partners ##########
# Protein Ledger
"""Refresh in the background without the app open (GFP-102).

Today the refresh only runs while a human is driving the app: ``gplan schedule
run`` blocks a terminal and the GUI never starts a scheduler. So on a
nutritionist's machine nothing refreshes unless they remember to open the app
and leave it open.

That is not a convenience gap. **Price history only accumulates going forward
and cannot be backfilled**, so every day the machine does not scrape is a
permanent hole in the trends chart (GFP-40/41) and in the record lows (GFP-75).
The optimiser degrades the less the app is used, which is backwards.

AN OS TIMER, NOT A DAEMON
-------------------------
Windows gets a per-user Scheduled Task, macOS a per-user LaunchAgent, and both
invoke ``gplan schedule run --once`` -- GFP-7's catch-up pass, which already
reaps interrupted jobs, scrapes what is overdue, and exits.

A resident daemon would need its own crash recovery, its own supervision and
its own "is it still alive?" story. The OS already provides all three, survives
reboot for free, and owns the retry semantics. There is also nothing to leave
running by accident.

THE CONSOLE WINDOW, which is the one genuinely fiddly part
----------------------------------------------------------
``gplan.exe`` is a console binary, and a Scheduled Task set to run only when
the user is logged on gives it an interactive session -- which means a black
window blinking onto a customer's screen every morning. The ticket rules that
out in terms.

The fix is ``schtasks /RU <user> /NP``: run whether the user is logged on or
not, using S4U (service-for-user) rather than a stored password. That runs the
task non-interactively, so there is no window to see, and it needs no
administrator rights and no password prompt.

S4U can be refused on locked-down machines (it depends on a logon right some
domain policies remove). So registration FALLS BACK to an interactive task and
says plainly that a window will appear -- a visible window is worse than no
window, but both are much better than no refresh at all.

OVERLAPPING RUNS
----------------
The timer can fire while somebody is running ``gplan scrape`` by hand. SQLite
serialises the writes so nothing corrupts, but both would scrape the same store
and the second result would simply replace the first. That matters more than it
looks: the Kroger credential is capped at 10,000 calls a day and a full store
scrape is roughly 20 of them, so wasted duplicate runs come out of a budget
that is already the ceiling on how many customers one credential can serve.

Hence :func:`refresh_lock` -- a lock file, not a mutex, because it has to work
across two processes that may be a GUI and a scheduled task on either platform.
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import install_paths
from .paths import data_dir

#: Where the single-instance lock lives. In the data directory, so an
#: uninstall removes it with everything else.
LOCK_FILENAME = "refresh.lock"

#: A lock older than this is assumed abandoned. A scrape of every store takes
#: a couple of minutes; an hour is far beyond any real run, and the cost of
#: guessing wrong is one duplicated scrape rather than a permanently wedged
#: refresh. A refresh that can never run again because of a stale lock file is
#: much worse than an occasional double scrape.
STALE_AFTER_SECONDS = 3600

#: When the timer fires, local time. Early enough that the day's prices are
#: there before anyone looks, late enough that a laptop is plausibly awake.
DEFAULT_TIME = "06:00"

#: Daily, and deliberately not more often: the trends chart plots one point per
#: day, so scraping twice a day doubles the load and adds no points. This
#: settles the ticket's open question.
CADENCE = "daily"


class AlreadyRunning(RuntimeError):
    """Another refresh holds the lock."""


class TimerError(RuntimeError):
    """The OS refused to register or remove the timer. Carries its output."""


@contextmanager
def refresh_lock(directory: Path | None = None):
    """Hold the single-refresh lock, or raise :class:`AlreadyRunning`.

    Uses ``O_CREAT | O_EXCL``, which is atomic on both platforms, rather than
    "check then create" -- the gap in the latter is exactly the race this
    exists to close.

    Staleness is judged by MTIME, not by whether the recorded PID is alive:
    ``os.kill(pid, 0)`` does not mean on Windows what it means elsewhere, and a
    PID check that is wrong on one platform is worse than a clock check that is
    merely approximate on both.
    """
    target = (directory or data_dir()) / LOCK_FILENAME
    try:
        handle = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        age = None
        try:
            age = time.time() - target.stat().st_mtime
        except OSError:
            pass
        if age is None or age < STALE_AFTER_SECONDS:
            raise AlreadyRunning(
                f"another refresh is already running (lock: {target})"
            ) from None
        # Stale. Take it over rather than leaving the machine unable to
        # refresh ever again.
        try:
            target.unlink()
        except OSError:
            pass
        handle = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY)

    try:
        os.write(handle, f"{os.getpid()}\n".encode())
        os.close(handle)
        yield target
    finally:
        try:
            target.unlink()
        except OSError:
            # A lock we cannot delete becomes stale in an hour and is taken
            # over then; refusing to exit over it would be worse.
            pass


def refresh_once(on_event=None) -> int:
    """One catch-up pass, with the kill switch and the lock. Returns an exit code.

    The single implementation of "the timer fired". Both callers reach it: the
    CLI's ``gplan schedule run --once`` and the windowless
    ``gplan-gui.exe --refresh-once``. Two copies of this would eventually
    disagree about whether the setting is honoured, which is the sort of
    divergence nobody notices until a customer's machine is scraping after
    they turned it off.

    EVERYTHING IS LOGGED, because an unattended run has no other witness --
    GFP-102 requires that a background failure is not silent, and this is the
    only moment at which logging stops being optional.

    EXIT CODES ARE DELIBERATE. "Already running" and "turned off" are 0: the
    thing the timer was asked to ensure is either happening or has been
    declined on purpose, and a non-zero exit shows up in Task Scheduler as a
    daily red mark that teaches the user to ignore all of them.
    """
    from . import config, db, logs, scheduler

    logs.setup(console=False)
    log = logs.get_logger(__name__)
    say = on_event or (lambda _message: None)

    def note(message: str) -> None:
        """Every scheduler event goes to BOTH the log and the caller."""
        log.info(message)
        say(message)

    if not config.get("background_refresh"):
        log.info("background refresh is disabled by config; doing nothing")
        say("Background refresh is turned off (config `background_refresh`).")
        return 0

    connection = db.connect()
    if not scheduler.list_schedules(connection, enabled_only=True):
        log.info("no schedules configured; nothing to refresh")
        say("No schedules configured.")
        return 0

    try:
        with refresh_lock():
            log.info("background refresh starting")
            summary = scheduler.run_catch_up(connection, on_event=note)
    except AlreadyRunning as exc:
        log.info("skipped: %s", exc)
        say(str(exc))
        return 0

    log.info(
        "background refresh finished: %d scraped, %d failed, %d reaped",
        len(summary["ran"]), len(summary["failed"]), summary["reaped"],
    )
    for store, problem in summary["failed"].items():
        log.error("background refresh failed for %s: %s", store, problem)
    say(
        f"Catch-up: {len(summary['ran'])} scraped, "
        f"{len(summary['failed'])} failed, "
        f"{summary['reaped']} interrupted job(s) reaped."
    )
    return 1 if summary["failed"] else 0


# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TimerStatus:
    """What the OS says about the timer, plus how to name it by hand."""

    supported: bool
    registered: bool
    identifier: str
    #: Raw output from the OS query, for `gplan timer status` to show when
    #: something is wrong. Never a secret.
    detail: str = ""


def identifier() -> str:
    """The pinned name of the timer on this platform (GFP-102 rule 1)."""
    if sys.platform == "win32":
        return install_paths.WINDOWS_TASK_PATH + install_paths.WINDOWS_TASK_NAME
    return install_paths.MACOS_LAUNCH_AGENT_LABEL


def agent_plist_path() -> Path:
    return (Path.home() / "Library" / "LaunchAgents"
            / f"{install_paths.MACOS_LAUNCH_AGENT_LABEL}.plist")


def _run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        command, capture_output=True, text=True,
        # A hung schtasks/launchctl must not hang an installer.
        timeout=60,
        # CREATE_NO_WINDOW: registering the timer must not itself flash the
        # window this whole module exists to avoid.
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def gplan_executable() -> Path:
    """The command the timer should invoke.

    ``sys.executable`` is the PyInstaller binary in a real install and the
    Python interpreter in a development checkout, so a dev machine registering
    a timer would otherwise point it at ``python.exe`` with no arguments.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    installed = install_paths.default_install_root() / (
        "gplan.exe" if sys.platform == "win32" else "gplan"
    )
    if installed.exists():
        return installed
    # Development: whatever launched us. argv[0] arrives without the .exe on
    # Windows, and a Scheduled Task pointed at an extensionless path fails at
    # 6am with "the system cannot find the file specified" -- which nobody is
    # awake to see.
    launched = Path(sys.argv[0]).resolve()
    if sys.platform == "win32" and not launched.exists():
        with_exe = launched.with_suffix(".exe")
        if with_exe.exists():
            return with_exe
    return launched


def status() -> TimerStatus:
    """Whether the timer is registered right now, asked of the OS."""
    name = identifier()
    if sys.platform == "win32":
        result = _run(["schtasks", "/Query", "/TN", name])
        return TimerStatus(
            supported=True,
            registered=result.returncode == 0,
            identifier=name,
            detail=(result.stdout or result.stderr).strip(),
        )
    if sys.platform == "darwin":
        result = _run(["launchctl", "print", f"gui/{os.getuid()}/{name}"])
        loaded = result.returncode == 0
        return TimerStatus(
            supported=True,
            # A plist on disk that is not loaded still means "installed but
            # not running", which is a different problem from "not installed"
            # and has to be distinguishable.
            registered=loaded or agent_plist_path().exists(),
            identifier=name,
            detail=("loaded" if loaded else
                    "plist present but not loaded" if agent_plist_path().exists()
                    else "not registered"),
        )
    return TimerStatus(
        supported=False, registered=False, identifier=name,
        detail=f"background refresh is not implemented for {platform.system()}",
    )


# --------------------------------------------------------------------------- #
# Windows
# --------------------------------------------------------------------------- #
#: The flag the windowless binary answers to. See :func:`windows_command`.
REFRESH_FLAG = "--refresh-once"


def windows_command() -> str:
    """What the Scheduled Task should invoke on Windows.

    THE CONSOLE WINDOW PROBLEM, and why this is not simply ``gplan.exe``.

    A console binary run by a Scheduled Task in the user's own session gets an
    interactive console, so a black window blinks onto the screen every
    morning. The documented escape is ``schtasks /RU <user> /NP`` (S4U: run
    whether logged on or not, no stored password), which runs the task
    non-interactively.

    MEASURED, not assumed: on a normal unelevated Windows 11 account that is
    refused. ``schtasks`` prompts for a password and then reports "Access is
    denied" -- creating a task that runs while logged off needs a privilege an
    ordinary user does not have. So S4U cannot be the answer for an installer
    that refuses to ask for administrator rights (GFP-91).

    What works is a binary that has no console to show. ``gplan-gui.exe`` is
    already built with ``console=False`` -- it is a GUI-subsystem executable,
    so Windows never gives it a console at all -- and its entry point answers
    to ``--refresh-once`` by running the same catch-up pass headlessly,
    without importing Qt. No third binary, no scripting-host wrapper, and the
    windowless behaviour is a property of the executable format rather than of
    a task setting some policy can withdraw.

    If only the CLI is installed, this falls back to ``gplan.exe`` and
    :func:`install` says out loud that a window will appear.
    """
    cli = gplan_executable()
    windowless = cli.with_name("gplan-gui.exe")
    if windowless.exists():
        return f'"{windowless}" {REFRESH_FLAG}'
    return f'"{cli}" schedule run --once'


def scheduled_command() -> str:
    """What the timer invokes on this platform, for `gplan timer status`.

    macOS has no console-window problem -- a LaunchAgent runs with no terminal
    attached whatever the binary is -- so it invokes the CLI directly.
    """
    if sys.platform == "win32":
        return windows_command()
    return f"{gplan_executable()} schedule run --once"


def _install_windows(at: str, dry_run: bool) -> str:
    command = windows_command()
    create = [
        "schtasks", "/Create",
        "/TN", identifier(),
        "/TR", command,
        "/SC", "DAILY",
        "/ST", at,
        # /F makes this idempotent: a second run replaces the task rather than
        # failing with "already exists" or creating a duplicate.
        "/F",
    ]
    if dry_run:
        return " ".join(create)

    result = _run(create)
    if result.returncode != 0:
        raise TimerError((result.stdout + result.stderr).strip()
                         or "schtasks failed without output")
    if REFRESH_FLAG in command:
        return "registered"
    return (
        "registered -- but only the command-line tool is installed, so a "
        "console window will appear briefly each time it runs. Installing the "
        "desktop app removes the window"
    )


def _delete_task_folder() -> None:
    """Remove the now-empty \\GroceryPlanner folder from Task Scheduler.

    ``schtasks`` cannot delete folders at all, so without this the folder
    survives every removal -- and GFP-102 is explicit that an empty folder left
    in Task Scheduler reads as a failed uninstall. The COM interface can, and
    its absence is fine: the folder only exists once something has been
    registered in it.

    Never raises. Failing to tidy a folder must not turn a successful removal
    into a reported failure.
    """
    folder = install_paths.WINDOWS_TASK_PATH.strip("\\")
    script = (
        "$ErrorActionPreference='Stop'; "
        "$s = New-Object -ComObject Schedule.Service; $s.Connect(); "
        f"$s.GetFolder('\\').DeleteFolder('{folder}', 0)"
    )
    # PowerShell rather than a COM binding: it is always present on Windows,
    # and deleting an empty folder is not worth taking a dependency on
    # pywin32 or comtypes for. Failures are ignored -- the folder is either
    # already gone or not empty, and neither should turn a successful task
    # removal into a reported failure.
    try:
        _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])
    except (OSError, subprocess.SubprocessError):
        pass


def _remove_windows(dry_run: bool) -> str:
    if dry_run:
        return f'schtasks /Delete /TN "{identifier()}" /F, then delete the empty folder'
    result = _run(["schtasks", "/Delete", "/TN", identifier(), "/F"])
    if result.returncode == 0:
        _delete_task_folder()
        return "removed"
    combined = (result.stdout + result.stderr).lower()
    # Idempotent: removing something that is not there is success, not an
    # error. Matched on the ERROR CODE rather than the message, which is
    # localised -- 0x80070002 is ERROR_FILE_NOT_FOUND.
    if "cannot find" in combined or "does not exist" in combined or "80070002" in combined:
        # Still tidy the folder: a previous removal may have taken the task
        # and left the folder, and that empty folder is what a user later
        # reads as "the uninstall failed".
        _delete_task_folder()
        return "not registered"
    raise TimerError((result.stdout + result.stderr).strip())


# --------------------------------------------------------------------------- #
# macOS
# --------------------------------------------------------------------------- #
def _plist(at: str) -> str:
    hour, _, minute = at.partition(":")
    log_target = data_dir() / "logs" / "refresh.out"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{install_paths.MACOS_LAUNCH_AGENT_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{gplan_executable()}</string>
    <string>schedule</string>
    <string>run</string>
    <string>--once</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>{int(hour)}</integer>
    <key>Minute</key><integer>{int(minute or 0)}</integer>
  </dict>
  <!-- false: a login should not trigger a scrape. The calendar entry is the
       schedule, and RunAtLoad would make every reboot an extra one. -->
  <key>RunAtLoad</key>
  <false/>
  <key>StandardOutPath</key>
  <string>{log_target}</string>
  <key>StandardErrorPath</key>
  <string>{log_target}</string>
</dict>
</plist>
"""


def _install_macos(at: str, dry_run: bool) -> str:
    target = agent_plist_path()
    if dry_run:
        return f"write {target} and launchctl bootstrap gui/{os.getuid()}"

    target.parent.mkdir(parents=True, exist_ok=True)
    (data_dir() / "logs").mkdir(parents=True, exist_ok=True)
    target.write_text(_plist(at), encoding="utf-8")

    # Boot it OUT first. bootstrap on an already-loaded label fails, so this is
    # what makes a second run a no-op instead of an error -- and it is also
    # what makes the NEW plist take effect rather than the one already loaded.
    _run(["launchctl", "bootout", f"gui/{os.getuid()}/{identifier()}"])
    result = _run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(target)])
    if result.returncode != 0:
        raise TimerError((result.stdout + result.stderr).strip()
                         or f"launchctl bootstrap failed ({result.returncode})")
    return "registered"


def _remove_macos(dry_run: bool) -> str:
    target = agent_plist_path()
    if dry_run:
        return f"launchctl bootout, then delete {target}"

    # Unload BEFORE deleting the file: deleting a loaded agent leaves it
    # running until logout, which is the one artifact that keeps ACTING after
    # the app is gone.
    booted = _run(["launchctl", "bootout", f"gui/{os.getuid()}/{identifier()}"])
    existed = target.exists()
    if existed:
        try:
            target.unlink()
        except OSError as exc:
            raise TimerError(f"could not delete {target}: {exc}") from exc
    if booted.returncode != 0 and not existed:
        return "not registered"
    return "removed"


# --------------------------------------------------------------------------- #
# The public two
# --------------------------------------------------------------------------- #
def install(at: str = DEFAULT_TIME, dry_run: bool = False) -> str:
    """Register the daily refresh. Idempotent. Returns a line to print."""
    at = normalise_time(at)
    if sys.platform == "win32":
        return _install_windows(at, dry_run)
    if sys.platform == "darwin":
        return _install_macos(at, dry_run)
    raise TimerError(f"background refresh is not implemented for {platform.system()}")


def remove(dry_run: bool = False) -> str:
    """Unregister the daily refresh. Idempotent. Returns a line to print."""
    if sys.platform == "win32":
        return _remove_windows(dry_run)
    if sys.platform == "darwin":
        return _remove_macos(dry_run)
    raise TimerError(f"background refresh is not implemented for {platform.system()}")


def normalise_time(at: str) -> str:
    """``"6:5"`` -> ``"06:05"``, or raise :class:`TimerError`.

    Zero-padding is not cosmetic: ``schtasks /ST 6:5`` is rejected by Windows,
    and a time that passes our own validation and then fails at the OS produces
    a confusing error a long way from its cause.

    A bare ``"6"`` is refused rather than read as six o'clock -- it is just as
    likely to be a mistyped ``18``, and this is a setting nobody looks at again
    once it is wrong.
    """
    hour, sep, minute = at.strip().partition(":")
    if not sep:
        raise TimerError(f"{at!r} is not a time -- expected HH:MM, e.g. 06:00")
    try:
        h, m = int(hour), int(minute)
    except ValueError:
        raise TimerError(f"{at!r} is not a time -- expected HH:MM, e.g. 06:00") from None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise TimerError(f"{at!r} is not a time -- expected HH:MM, e.g. 06:00")
    return f"{h:02d}:{m:02d}"
