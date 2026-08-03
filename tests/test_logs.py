"""GFP-86: logging, self-pruning, and never writing a secret to disk.

There was no logging in this codebase at all. That is tolerable for a tool
driven by hand and indefensible for one that scrapes an undocumented endpoint
on a schedule on a machine nobody administers.

The redaction tests matter most. A credential in a plaintext file on a
customer's disk is not a bug you fix in the next release -- it is a rotation and
a disclosure -- so it is enforced by a filter, not by remembering.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
from datetime import datetime, timedelta, timezone

import pytest

from grocery_planner import logs


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    monkeypatch.setenv(logs.LOG_DIR_ENV_VAR, str(tmp_path / "logs"))
    monkeypatch.delenv("GROCERY_PLANNER_LOG_RETENTION_DAYS", raising=False)
    logs.reset_for_tests()
    yield tmp_path / "logs"
    logs.reset_for_tests()


def _write_and_read(log_dir, message: str, level=logging.INFO) -> str:
    logs.setup(console=False)
    logs.get_logger("test").log(level, message)
    for handler in logging.getLogger("grocery_planner").handlers:
        handler.flush()
    return logs.log_path().read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Nothing credential-shaped ever reaches disk
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("secret_line", [
    "session cookie wfm_store_d8=eyJzdG9yZUlkIjoxMDQyNn0",
    "client_secret=abc123def456",
    "client_id=grocery-planner-prod",
    "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig",
    "access_token=ya29.a0AfH6SM",
    "licence_key=GP-1234-5678",
    "api_key=sk-live-9999",
    "password=hunter2",
])
def test_a_credential_never_reaches_the_log_file(log_dir, secret_line):
    """The failure this prevents is a rotation and a disclosure, not a bugfix."""
    written = _write_and_read(log_dir, secret_line)

    assert logs.REDACTED in written
    # The secret's own VALUE must be absent, not merely the keyword.
    for fragment in ("eyJzdG9yZUlkIjoxMDQyNn0", "abc123def456", "ya29.a0AfH6SM",
                     "GP-1234-5678", "sk-live-9999", "hunter2",
                     "eyJhbGciOiJIUzI1NiJ9.payload.sig"):
        if fragment in secret_line:
            assert fragment not in written, f"{fragment} was written to disk"


def test_redaction_survives_lazy_formatting(log_dir):
    """Loggers are usually called with %s args, not a pre-built string."""
    logs.setup(console=False)
    logs.get_logger("test").info("token is %s", "Bearer supersecretvalue")
    for handler in logging.getLogger("grocery_planner").handlers:
        handler.flush()
    written = logs.log_path().read_text(encoding="utf-8")

    assert "supersecretvalue" not in written
    assert logs.REDACTED in written


def test_an_ordinary_line_is_not_redacted(log_dir):
    """Over-redacting everything would make the log useless."""
    written = _write_and_read(log_dir, "scrape ok: store=harristeeter stored=989")
    assert "store=harristeeter" in written
    assert logs.REDACTED not in written


def test_the_console_handler_redacts_too(log_dir, capsys):
    """A secret echoed to a terminal is a secret in someone's scrollback."""
    logs.setup(console=True, level=logging.INFO)
    logs.get_logger("test").info("client_secret=leakme")
    captured = capsys.readouterr()
    assert "leakme" not in (captured.out + captured.err)


# --------------------------------------------------------------------------- #
# Self-pruning: nobody administers this machine
# --------------------------------------------------------------------------- #
def test_the_default_retention_is_seven_days(log_dir):
    """The figure moved 30 -> 2 -> 7 in discussion; 7 is what was filed."""
    assert logs.retention_days() == 7


def test_retention_is_configurable(log_dir, monkeypatch):
    monkeypatch.setenv("GROCERY_PLANNER_LOG_RETENTION_DAYS", "2")
    assert logs.retention_days() == 2


def test_rotated_logs_older_than_the_window_are_deleted(log_dir):
    logs.setup(console=False)
    old = log_dir / f"{logs.LOG_FILENAME}.3"
    old.write_text("ancient", encoding="utf-8")
    stale = (datetime.now(timezone.utc) - timedelta(days=30)).timestamp()
    os.utime(old, (stale, stale))

    removed = logs.prune()
    assert old in removed and not old.exists()


def test_a_recent_rotated_log_is_kept(log_dir):
    logs.setup(console=False)
    recent = log_dir / f"{logs.LOG_FILENAME}.1"
    recent.write_text("yesterday", encoding="utf-8")

    logs.prune()
    assert recent.exists()


def test_the_live_log_is_never_deleted(log_dir):
    """Removing the file a handler holds open loses logging entirely on Windows."""
    logs.setup(console=False)
    live = logs.log_path()
    live.write_text("current", encoding="utf-8")
    stale = (datetime.now(timezone.utc) - timedelta(days=90)).timestamp()
    os.utime(live, (stale, stale))

    logs.prune()
    assert live.exists()


def test_pruning_ignores_files_that_are_not_ours(log_dir):
    """Pointing LOG_DIR at a shared directory must not make this a deleter."""
    logs.setup(console=False)
    innocent = log_dir / "important-notes.txt"
    innocent.write_text("do not delete", encoding="utf-8")
    stale = (datetime.now(timezone.utc) - timedelta(days=90)).timestamp()
    os.utime(innocent, (stale, stale))

    logs.prune()
    assert innocent.exists()


def test_size_is_capped_as_well_as_age(log_dir):
    """A retry loop can fill a disk well inside a 7-day window."""
    assert logs.MAX_BYTES <= 5 * 1024 * 1024
    assert logs.BACKUP_COUNT >= 1
    ceiling_mb = logs.MAX_BYTES * (logs.BACKUP_COUNT + 1) / 1024 / 1024
    assert ceiling_mb <= 25, f"log directory could reach {ceiling_mb:.0f} MB"


# --------------------------------------------------------------------------- #
# Never a reason the app fails to run
# --------------------------------------------------------------------------- #
def test_setup_is_idempotent(log_dir):
    """The CLI, the GUI and a test may all call it; twice must not double lines.

    Counts only the handlers WE install -- pytest attaches its own capture
    handlers to the same logger, so a total count would measure pytest.
    """
    def ours() -> int:
        return sum(
            1 for h in logging.getLogger("grocery_planner").handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        )

    logs.setup(console=False)
    after_first = ours()
    logs.setup(console=False)

    assert after_first == 1, "setup should install exactly one file handler"
    assert ours() == 1, "a second setup() duplicated the file handler"


def test_setup_degrades_to_console_when_the_directory_cannot_be_made(tmp_path, monkeypatch):
    """Diagnostics must not be a reason the app cannot start."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("I am a file", encoding="utf-8")
    monkeypatch.setenv(logs.LOG_DIR_ENV_VAR, str(blocker / "logs"))
    logs.reset_for_tests()

    logs.setup(console=True)          # must not raise
    logs.get_logger("test").warning("still works")
    logs.reset_for_tests()


def test_get_logger_namespaces_everything_under_the_app(log_dir):
    assert logs.get_logger("scrapers.kroger").name == "grocery_planner.scrapers.kroger"
    assert logs.get_logger("grocery_planner.db").name == "grocery_planner.db"
