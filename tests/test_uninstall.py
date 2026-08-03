"""What an uninstall removes (GFP-92).

The uninstallers are PowerShell and shell and are executed for real in CI. What
is tested here is the part with logic in it -- deciding WHAT to remove -- which
lives in Python precisely so it exists once instead of twice.

The stakes are asymmetric and both directions are bad:

* **Too little** leaves a Kroger client_secret and a live Whole Foods session
  cookie on a machine that may then be resold, repaired or handed on.
* **Too much** destroys a nutritionist's client list, which is hand-entered
  over months and exists nowhere else.

So the tests below are mostly about the boundary: what is inside the data
directory (covered by removing the directory) versus what an environment
variable moved outside it (which must be named individually, or "delete the
folder" misses it).
"""
from __future__ import annotations

import json

import pytest

from grocery_planner import install_paths, uninstall
from grocery_planner.config import CONFIG_ENV_VAR
from grocery_planner.logs import LOG_DIR_ENV_VAR
from grocery_planner.paths import DB_ENV_VAR, data_dir


@pytest.fixture
def no_overrides(monkeypatch):
    for var in (DB_ENV_VAR, CONFIG_ENV_VAR, LOG_DIR_ENV_VAR,
                "GROCERY_PLANNER_KROGER_CONFIG",
                "GROCERY_PLANNER_WHOLEFOODS_SESSION"):
        monkeypatch.delenv(var, raising=False)


def _targets(items):
    return [i.target for i in items]


# --------------------------------------------------------------------------- #
# The data directory, and the rule that it goes as a DIRECTORY
# --------------------------------------------------------------------------- #
def test_the_data_directory_is_always_removed(no_overrides):
    items = uninstall.plan()
    assert str(data_dir()) in _targets(items)


def test_the_data_directory_is_removed_as_a_directory_not_a_file_list(no_overrides):
    """GFP-102 rule 2. It accumulates SQLite -wal/-shm files and hand-made
    backups that no allowlist can name in advance."""
    item = [i for i in uninstall.plan() if i.target == str(data_dir())][0]
    assert item.kind == uninstall.DIRECTORY


def test_the_data_directory_is_marked_irreplaceable_and_sensitive(no_overrides):
    item = [i for i in uninstall.plan() if i.target == str(data_dir())][0]
    assert item.irreplaceable and item.sensitive


def test_nothing_inside_the_data_directory_is_listed_separately(no_overrides):
    """Listing it twice would report 'could not remove' for a file the
    directory removal already took, which reads as a failed uninstall."""
    home = data_dir().resolve()
    for item in uninstall.plan():
        if item.target == str(data_dir()) or item.kind in (uninstall.TASK, uninstall.AGENT):
            continue
        from pathlib import Path
        assert home not in Path(item.target).resolve().parents, (
            f"{item.target} is inside the data directory and listed separately"
        )


# --------------------------------------------------------------------------- #
# The relocated case -- the reason this resolves instead of hard-coding
# --------------------------------------------------------------------------- #
def test_a_relocated_database_is_listed_separately(no_overrides, monkeypatch, tmp_path):
    elsewhere = tmp_path / "somewhere-else" / "clients.sqlite3"
    monkeypatch.setenv(DB_ENV_VAR, str(elsewhere))
    items = uninstall.plan()
    match = [i for i in items if "clients.sqlite3" in i.target]
    assert match, "a relocated database was not in the plan"
    assert match[0].irreplaceable
    assert match[0].relocated_by == DB_ENV_VAR


def test_a_relocated_credential_is_listed_and_marked_sensitive(
    no_overrides, monkeypatch, tmp_path
):
    """This is the whole point. A credential moved out of the data directory
    survives 'delete the folder', and what survives is an OAuth2 client_secret
    on a machine that may be resold."""
    elsewhere = tmp_path / "secrets" / "kroger-env.config"
    monkeypatch.setenv("GROCERY_PLANNER_KROGER_CONFIG", str(elsewhere))
    items = uninstall.plan()
    match = [i for i in items if "kroger-env.config" in i.target]
    assert match, "a relocated Kroger credential was not in the plan"
    assert match[0].sensitive
    assert match[0].relocated_by == "GROCERY_PLANNER_KROGER_CONFIG"


def test_a_relocated_log_directory_is_listed(no_overrides, monkeypatch, tmp_path):
    elsewhere = tmp_path / "logs-elsewhere"
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(elsewhere))
    assert str(elsewhere) in _targets(uninstall.plan())


def test_a_relocated_config_is_listed(no_overrides, monkeypatch, tmp_path):
    elsewhere = tmp_path / "elsewhere" / "config.json"
    monkeypatch.setenv(CONFIG_ENV_VAR, str(elsewhere))
    assert str(elsewhere) in _targets(uninstall.plan())


def test_an_override_pointing_back_inside_the_data_dir_is_not_duplicated(
    no_overrides, monkeypatch
):
    """Setting the variable to the place the file already lives must not make
    it appear twice."""
    monkeypatch.setenv(DB_ENV_VAR, str(data_dir() / "grocery_planner.sqlite3"))
    targets = _targets(uninstall.plan())
    assert len(targets) == len(set(targets))


# --------------------------------------------------------------------------- #
# Order -- the timer first
# --------------------------------------------------------------------------- #
def test_the_background_timer_is_first(no_overrides):
    """GFP-102: removing it last lets a firing timer race the removal of the
    binary it invokes, and a leftover timer is the one artifact that keeps
    ACTING after the app is gone."""
    items = uninstall.plan()
    assert items[0].kind in (uninstall.TASK, uninstall.AGENT)


def test_the_data_directory_is_last(no_overrides):
    """So that a run stopping partway leaves an inert directory of files
    rather than a live timer pointing at a half-removed app."""
    assert uninstall.plan()[-1].target == str(data_dir())


def test_the_timer_uses_the_pinned_identifier(no_overrides):
    """You cannot hand-remove what you cannot name, and these names are pinned
    by GFP-102 for exactly that reason."""
    import sys
    first = uninstall.plan()[0]
    if sys.platform == "win32":
        assert first.target == (install_paths.WINDOWS_TASK_PATH
                                + install_paths.WINDOWS_TASK_NAME)
    elif sys.platform == "darwin":
        assert first.target == install_paths.MACOS_LAUNCH_AGENT_LABEL


# --------------------------------------------------------------------------- #
# The wire formats both uninstallers parse
# --------------------------------------------------------------------------- #
def test_the_line_format_is_four_tab_separated_fields(no_overrides):
    for line in uninstall.to_lines(uninstall.plan()).splitlines():
        assert len(line.split("\t")) == 4, line


def test_no_field_contains_a_tab_or_a_newline(no_overrides, monkeypatch, tmp_path):
    """A tab inside a label would silently shift every later field, and a
    shifted field is a path -- which is a very bad thing to get wrong in a
    program that deletes things."""
    monkeypatch.setenv(DB_ENV_VAR, str(tmp_path / "db.sqlite3"))
    for item in uninstall.plan():
        for field in (item.kind, item.label, item.target):
            assert "\t" not in field and "\n" not in field


def test_flags_are_never_empty(no_overrides):
    """An empty middle field would collapse to three fields and be skipped."""
    for line in uninstall.to_lines(uninstall.plan()).splitlines():
        assert line.split("\t")[1] != ""


def test_the_json_form_parses_and_carries_the_warnings(no_overrides):
    parsed = json.loads(uninstall.to_json(uninstall.plan()))
    assert parsed["items"]
    assert "warnings" in parsed


def test_warnings_never_raise_even_with_an_unreadable_credential_path(
    no_overrides, monkeypatch, tmp_path
):
    """warnings() reads credentials, and an unreadable one must not stop
    somebody uninstalling.

    A DIRECTORY where a file is expected is the realistic form of this: the
    provider checks ``exists()`` and then ``read_text()``, and a directory
    passes the first and raises on the second.
    """
    monkeypatch.setenv("GROCERY_PLANNER_KROGER_CONFIG", str(tmp_path))
    try:
        uninstall.warnings()
    except OSError:
        pytest.fail("warnings() raised on an unreadable credential path")


def test_the_plan_is_produced_even_if_a_credential_is_unreadable(
    no_overrides, monkeypatch, tmp_path
):
    """The plan is what the uninstaller acts on. It must survive a machine in
    a strange state -- which is the state a machine is usually in when someone
    is uninstalling."""
    monkeypatch.setenv("GROCERY_PLANNER_WHOLEFOODS_SESSION", str(tmp_path))
    assert uninstall.plan()
