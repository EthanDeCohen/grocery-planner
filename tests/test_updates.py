"""The update check (GFP-96).

The app is a binary downloaded from a GitHub page, so nothing else will tell a
user their copy is stale. GFP-93 is the shape of the failure: Whole Foods
changed its cookie encoding under a shipped decoder, and the only symptom was
an error telling the user to do something that could not work. Someone on an
old binary had no way to learn a fix existed.

Most of what is tested here is RESTRAINT, because that is what the ticket is
mostly about:

* it never raises, whatever the network or the filesystem does
* it never claims an update it cannot justify
* it never downloads or installs anything
* it does nothing at all when switched off
"""
from __future__ import annotations

import json
from datetime import date

import pytest
from typer.testing import CliRunner

from grocery_planner import __version__, config, updates
from grocery_planner.cli import app

runner = CliRunner()


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """A throwaway data directory and a clean config."""
    monkeypatch.setenv("GROCERY_PLANNER_DB", str(tmp_path / "db.sqlite3"))
    monkeypatch.setenv(config.CONFIG_ENV_VAR, str(tmp_path / "config.json"))
    for setting in config.SETTINGS:
        monkeypatch.delenv(setting.env_var, raising=False)
    monkeypatch.setattr(updates, "state_path", lambda: tmp_path / "update-check.json")
    return tmp_path


def _tag(monkeypatch, value):
    monkeypatch.setattr(updates, "_fetch_latest_tag", lambda: value)


# --------------------------------------------------------------------------- #
# Version comparison -- where a wrong answer is worst
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text,expected",
    [
        ("v1.2.3", (1, 2, 3)), ("1.2.3", (1, 2, 3)), ("V0.1.0", (0, 1, 0)),
        ("2.0", (2, 0)), ("1.2.3-rc1", (1, 2, 3)), ("1.2.3+build7", (1, 2, 3)),
        ("  v1.0.0  ", (1, 0, 0)),
    ],
)
def test_versions_parse(text, expected):
    assert updates.parse_version(text) == expected


@pytest.mark.parametrize("text", ["", "nightly", "2026-08-03-hotfix", "v", "latest",
                                  "1.2.x", None])
def test_a_tag_that_is_not_a_version_parses_to_none(text):
    """Guessing a number out of `2026-08-03-hotfix` would send users to a
    download page for nothing."""
    assert updates.parse_version(text) is None


@pytest.mark.parametrize(
    "latest,current,newer",
    [
        ("v0.2.0", "0.1.0", True),
        ("v0.1.1", "0.1.0", True),
        ("v1.0.0", "0.9.9", True),
        ("v0.1.0", "0.1.0", False),
        ("v0.1.0", "0.2.0", False),      # a downgrade is not an update
        ("v0.10.0", "0.9.0", True),      # numeric, not lexicographic
        ("v1.2", "1.2.0", False),        # 1.2 == 1.2.0, not less
        ("v1.2.1", "1.2", True),
    ],
)
def test_is_newer(latest, current, newer):
    assert updates.is_newer(latest, current) is newer


def test_an_unparseable_version_is_never_newer():
    """An unknown version is not an excuse to nag somebody."""
    assert updates.is_newer("nightly", "0.1.0") is False
    assert updates.is_newer("v9.9.9", "not-a-version") is False


# --------------------------------------------------------------------------- #
# The check
# --------------------------------------------------------------------------- #
def test_a_newer_release_is_reported(isolated, monkeypatch):
    _tag(monkeypatch, "v99.0.0")
    found = updates.check()
    assert found is not None
    assert found.latest == "99.0.0"
    assert found.current == __version__
    assert found.url == updates.RELEASES_PAGE


def test_the_same_version_is_not_reported(isolated, monkeypatch):
    _tag(monkeypatch, f"v{__version__}")
    assert updates.check() is None


def test_an_older_release_is_not_reported(isolated, monkeypatch):
    _tag(monkeypatch, "v0.0.1")
    assert updates.check() is None


def test_no_releases_yet_is_not_reported(isolated, monkeypatch):
    """GitHub returns 404 until the first release is published, which is the
    normal state today."""
    _tag(monkeypatch, None)
    assert updates.check() is None


def test_the_message_names_both_versions(isolated, monkeypatch):
    _tag(monkeypatch, "v99.0.0")
    message = updates.check().message
    assert "99.0.0" in message and __version__ in message
    assert "!" not in message, "no urgency: this is a passive notice"


# --------------------------------------------------------------------------- #
# Once a day
# --------------------------------------------------------------------------- #
def test_it_checks_once_a_day(isolated, monkeypatch):
    calls = []
    monkeypatch.setattr(updates, "_fetch_latest_tag",
                        lambda: (calls.append(1), "v99.0.0")[1])
    today = date(2026, 8, 3)

    assert updates.check(today=today) is not None
    assert updates.check(today=today) is None       # gated
    assert len(calls) == 1


def test_a_new_day_checks_again(isolated, monkeypatch):
    _tag(monkeypatch, "v99.0.0")
    assert updates.check(today=date(2026, 8, 3)) is not None
    assert updates.check(today=date(2026, 8, 4)) is not None


def test_force_ignores_the_daily_gate(isolated, monkeypatch):
    """`gplan update` is somebody asking on purpose."""
    _tag(monkeypatch, "v99.0.0")
    updates.check(today=date(2026, 8, 3))
    assert updates.check(force=True, today=date(2026, 8, 3)) is not None


def test_a_failed_check_still_counts_as_today(isolated, monkeypatch):
    """Otherwise an offline machine retries on every single launch."""
    _tag(monkeypatch, None)
    updates.check(today=date(2026, 8, 3))
    assert updates.checked_today(date(2026, 8, 3))


def test_a_corrupt_state_file_is_survivable(isolated, monkeypatch):
    updates.state_path().write_text("{not json", encoding="utf-8")
    _tag(monkeypatch, "v99.0.0")
    assert updates.check(today=date(2026, 8, 3)) is not None


def test_a_state_file_with_a_nonsense_date_is_survivable(isolated, monkeypatch):
    updates.state_path().write_text(
        json.dumps({"last_checked": "yesterday"}), encoding="utf-8"
    )
    _tag(monkeypatch, "v99.0.0")
    assert updates.check(today=date(2026, 8, 3)) is not None


# --------------------------------------------------------------------------- #
# Opt-out, and silence
# --------------------------------------------------------------------------- #
def test_it_does_nothing_when_turned_off(isolated, monkeypatch):
    monkeypatch.setenv("GROCERY_PLANNER_UPDATE_CHECK", "false")

    def explode():
        pytest.fail("a disabled update check made a network call")

    monkeypatch.setattr(updates, "_fetch_latest_tag", explode)
    assert updates.check() is None
    assert updates.check(force=True) is None, "force must not override the opt-out"


def test_a_network_failure_is_silent(isolated, monkeypatch):
    """An update check that errors because the wifi is down is worse than no
    update check."""
    def boom():
        raise OSError("no route to host")

    monkeypatch.setattr(updates, "_fetch_latest_tag", boom)
    with pytest.raises(OSError):
        updates.check()                     # check() itself does not catch this
    assert updates.check_quietly() is None  # the startup path does


def test_check_quietly_survives_anything(isolated, monkeypatch):
    """This call sits between a user and their app opening. A bug in this
    module must not be why the app fails to start."""
    monkeypatch.setattr(
        updates, "check",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("something odd")),
    )
    assert updates.check_quietly() is None


def test_it_never_downloads_anything():
    """The whole restraint of this ticket, asserted rather than trusted.

    A silent self-update is a remote-code-execution path by design, and this
    module has no business acquiring one by accident.
    """
    import pathlib
    body = pathlib.Path(updates.__file__).read_text(encoding="utf-8")
    code = "\n".join(
        line for line in body.splitlines() if not line.strip().startswith("#")
    )
    for forbidden in ("urlretrieve", "shutil.move", "os.replace", "zipfile",
                      "subprocess", "os.execv", "chmod"):
        assert forbidden not in code, f"updates.py references {forbidden}"


def test_the_url_is_the_human_page_not_a_binary():
    """It hands off to a browser and a person, never to a downloader."""
    assert updates.RELEASES_PAGE.startswith("https://github.com/")
    assert not updates.RELEASES_PAGE.endswith((".zip", ".exe", ".dmg"))


# --------------------------------------------------------------------------- #
# The CLI
# --------------------------------------------------------------------------- #
def test_the_command_reports_an_update(isolated, monkeypatch):
    _tag(monkeypatch, "v99.0.0")
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 0
    assert "99.0.0" in result.stdout
    assert updates.RELEASES_PAGE in result.stdout
    assert "Nothing has been downloaded" in result.stdout


def test_the_command_is_quiet_when_up_to_date(isolated, monkeypatch):
    _tag(monkeypatch, f"v{__version__}")
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 0
    assert "No newer version" in result.stdout


def test_the_command_says_so_when_checks_are_off(isolated, monkeypatch):
    monkeypatch.setenv("GROCERY_PLANNER_UPDATE_CHECK", "false")
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 0
    assert "turned off" in result.stdout


def test_the_command_does_not_report_a_network_error(isolated, monkeypatch):
    """Someone asked a question about versions. They should not get a
    traceback about DNS."""
    monkeypatch.setattr(updates, "_fetch_latest_tag", lambda: None)
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 0
    assert "No newer version" in result.stdout


# --------------------------------------------------------------------------- #
# The thing the ticket asked for by name
# --------------------------------------------------------------------------- #
def test_the_declared_version_matches_pyproject():
    """The update check compares a release TAG against __version__. The tag is
    checked against __version__ by .github/workflows/release.yml; this checks
    the other pairing, which nothing else does.

    A drift here does not fail loudly -- it tells every existing install either
    that no update exists when one does, or that one exists forever after they
    have taken it.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    declared = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M)
    assert declared, "pyproject.toml has no version"
    assert declared.group(1) == __version__, (
        f"pyproject.toml says {declared.group(1)}, "
        f"grocery_planner.__version__ says {__version__}"
    )


def test_the_release_workflow_guards_the_tag_against_the_version():
    """Belt and braces on the same failure, from the other side."""
    import pathlib

    workflow = (pathlib.Path(__file__).resolve().parent.parent
                / ".github" / "workflows" / "release.yml")
    assert workflow.exists(), "GFP-96 presumes tagged releases; nothing publishes them"
    body = workflow.read_text(encoding="utf-8")
    assert "__version__" in body
    assert "MISMATCH" in body, "the release does not verify tag == __version__"
    assert "draft: false" in body, (
        "a draft release is skipped by /releases/latest, so installs would "
        "never be told about it"
    )
