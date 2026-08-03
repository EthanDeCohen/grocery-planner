"""Application logging (GFP-86): a record of what happened while nobody watched.

There was no logging anywhere in this codebase. That is tolerable for a tool
someone drives by hand and indefensible for one that scrapes an undocumented
endpoint on a schedule (GFP-102) on a machine nobody administers. When a
scheduled refresh fails at 3am there has to be something to read afterwards.

Three rules, each from the ticket and each with teeth here:

**Logs delete themselves.** Nobody is administering this machine, so retention
cannot be a manual chore. Default 7 days, configurable via GFP-85's
``log_retention_days``. This mirrors the stance already taken on scraped data —
disposable, self-pruning — with the one exception the codebase already
observes: GFP-75's record lows outlive retention because they cannot be
recomputed.

**Age alone is not enough.** A scraper stuck in a retry loop can produce a great
deal of log in a short time, and a 7-day policy would happily let it fill a
disk first. So size is capped too, by rotation, and the two limits are
independent: whichever bites first wins.

**Nothing credential-shaped is ever written.** Not the Whole Foods session
cookie, not a Kroger client_secret, not a bearer token. This is enforced by a
filter on the handler rather than by reviewer discipline, because the failure
mode — a secret sitting in a plaintext file on a customer's disk — is not one
to leave to good intentions. ``gplan credentials`` already holds this line by
printing presence and location only; this extends it to everything that logs.

Deliberately NOT structured/JSON logging. A human reads these files, usually
while confused, often over someone's shoulder. Plain lines win.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config
from .paths import data_dir

LOG_DIRNAME = "logs"
LOG_FILENAME = "grocery-planner.log"

#: Override where logs are written. Mirrors every other path override in this
#: codebase (GROCERY_PLANNER_DB, GROCERY_PLANNER_CONFIG) so tests and an
#: operator running several configurations behave predictably.
LOG_DIR_ENV_VAR = "GROCERY_PLANNER_LOG_DIR"

#: One file may reach this before rotating. Small on purpose: these are read by
#: a human in a text editor, and a 50 MB log is not read, it is given up on.
MAX_BYTES = 2 * 1024 * 1024

#: Rotated files kept alongside the live one. With MAX_BYTES above this caps
#: the directory at roughly 12 MB even if a retry loop never stops -- the
#: size half of the ticket's "cap size as well as age".
BACKUP_COUNT = 5

LINE_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

#: Substrings that mark a log record as carrying a secret. Matched against the
#: FORMATTED message, so it catches an f-string that interpolated a value as
#: well as a bare mention.
#:
#: Deliberately broad and deliberately dumb: the cost of redacting a harmless
#: line is a slightly less useful log, and the cost of missing one is a
#: credential in plaintext on a customer's disk. Those are not comparable, so
#: this errs heavily toward redacting.
SECRET_PATTERNS: tuple[str, ...] = (
    r"wfm_store_d8",
    r"client_secret",
    r"client_id",
    r"access_token",
    r"bearer\s+\S+",
    r"authorization",
    r"\basin=\S+",           # not secret, but identifies a customer's basket
    r"licence[_-]?key",
    r"license[_-]?key",
    r"api[_-]?key",
    r"password",
)

_SECRET_RE = re.compile("|".join(SECRET_PATTERNS), re.IGNORECASE)

REDACTED = "[redacted: this line matched a credential pattern]"

#: Set once by :func:`setup` so repeated calls are harmless -- the GUI, the CLI
#: and a test may all reach for logging, and configuring handlers twice would
#: duplicate every line.
_configured = False


class RedactingFilter(logging.Filter):
    """Drops the content of any record that looks like it carries a secret.

    Replaces the message wholesale rather than trying to mask the secret part.
    Partial masking needs to know where the secret ends, and getting that wrong
    silently writes most of a token to disk -- which is the failure this exists
    to prevent.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:                       # a broken format string
            return True
        if _SECRET_RE.search(message):
            record.msg = f"{REDACTED} (from {record.name})"
            record.args = ()
        return True


def log_dir() -> Path:
    """Where log files live, honouring :data:`LOG_DIR_ENV_VAR`."""
    override = os.environ.get(LOG_DIR_ENV_VAR)
    if override:
        return Path(override).expanduser()
    return data_dir() / LOG_DIRNAME


def log_path() -> Path:
    return log_dir() / LOG_FILENAME


def retention_days() -> int:
    """How long to keep logs. GFP-85's setting, default 7."""
    return int(config.get("log_retention_days"))


def prune(now: datetime | None = None) -> list[Path]:
    """Delete rotated logs older than the retention window. Returns what went.

    Only touches files in the log directory whose name starts with the live
    log's name, so pointing LOG_DIR at a directory holding other things cannot
    turn this into a general-purpose deleter.

    The LIVE file is never deleted -- rotation caps its size, and removing the
    file a handler holds open is a good way to lose logging entirely on
    Windows.
    """
    directory = log_dir()
    if not directory.exists():
        return []

    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=retention_days())
    removed: list[Path] = []
    live = log_path()
    for candidate in directory.iterdir():
        if not candidate.is_file() or candidate == live:
            continue
        if not candidate.name.startswith(LOG_FILENAME):
            continue
        try:
            modified = datetime.fromtimestamp(
                candidate.stat().st_mtime, tz=timezone.utc
            )
        except OSError:
            continue
        if modified < cutoff:
            try:
                candidate.unlink()
                removed.append(candidate)
            except OSError:
                # A file we cannot delete is not worth crashing the app over;
                # it will be retried on the next start.
                continue
    return removed


def setup(level: int | None = None, console: bool = True) -> Path:
    """Configure file + console logging. Returns the log file's path.

    Idempotent: the CLI, the GUI and a test may all call it, and configuring
    handlers twice would duplicate every line.

    Never raises. A machine where the log directory cannot be created still has
    to run the app -- logging is diagnostics, not a feature to fail on -- so a
    filesystem problem degrades to console-only.
    """
    global _configured
    root = logging.getLogger("grocery_planner")
    if _configured:
        return log_path()

    root.setLevel(logging.DEBUG)
    root.propagate = False
    formatter = logging.Formatter(LINE_FORMAT, datefmt=DATE_FORMAT)
    redactor = RedactingFilter()

    target = log_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            target, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(redactor)
        root.addHandler(file_handler)
    except OSError:
        pass        # console-only; see the docstring

    if console:
        stream = logging.StreamHandler()
        # The console is for a person watching a command run; the FILE is the
        # forensic record. So the console stays quiet and the file keeps
        # everything.
        stream.setLevel(level if level is not None else logging.WARNING)
        stream.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        stream.addFilter(redactor)
        root.addHandler(stream)

    _configured = True
    prune()
    return target


def get_logger(name: str) -> logging.Logger:
    """A logger under the app's namespace. Use ``__name__`` at the call site."""
    if not name.startswith("grocery_planner"):
        name = f"grocery_planner.{name}"
    return logging.getLogger(name)


def reset_for_tests() -> None:
    """Tear down handlers so a test can configure logging again."""
    global _configured
    root = logging.getLogger("grocery_planner")
    for handler in list(root.handlers):
        handler.close()
        root.removeHandler(handler)
    _configured = False
