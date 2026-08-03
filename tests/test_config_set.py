"""`gplan config set` (GFP-91).

Added because the installer's last act is to hand a brand-new user their first
command, and "open this JSON file in a text editor" is not an instruction an
installer should be giving a nutritionist.

The file this writes is hand-edited and irreplaceable in the same way client
records are, so most of what is checked here is about NOT destroying it.
"""
from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from grocery_planner import config
from grocery_planner.cli import app

runner = CliRunner()


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    target = tmp_path / "config.json"
    monkeypatch.setenv(config.CONFIG_ENV_VAR, str(target))
    for setting in config.SETTINGS:
        monkeypatch.delenv(setting.env_var, raising=False)
    return target


def _body(target):
    return json.loads(target.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# It writes, and it writes the PARSED value
# --------------------------------------------------------------------------- #
def test_it_creates_the_file_if_there_is_none(config_file):
    assert not config_file.exists()
    config.set_value("postal_code", "10001")
    assert _body(config_file) == {"postal_code": "10001"}


def test_it_stores_the_parsed_value_not_the_string_it_was_given(config_file):
    """A shell hands everything over as text. Writing "true" and 7 as strings
    would produce a file that works but reads as though someone hand-edited it
    badly."""
    config.set_value("auto_refresh", "false")
    config.set_value("log_retention_days", "30")
    body = _body(config_file)
    assert body["auto_refresh"] is False
    assert body["log_retention_days"] == 30


def test_the_value_is_what_the_app_then_reads(config_file):
    config.set_value("postal_code", "90210")
    assert config.postal_code() == "90210"


def test_setting_one_key_leaves_the_others_alone(config_file):
    config.set_value("postal_code", "10001")
    config.set_value("log_retention_days", "3")
    body = _body(config_file)
    assert body == {"postal_code": "10001", "log_retention_days": 3}


def test_an_unknown_key_already_in_the_file_survives(config_file):
    """Usually a setting from a newer version. Silently dropping it during an
    unrelated write is a nasty surprise on downgrade-then-upgrade, and load()
    already treats these as a warning rather than an error."""
    config_file.write_text(
        json.dumps({"postal_code": "27401", "from_the_future": 42}), encoding="utf-8"
    )
    config.set_value("postal_code", "10001")
    assert _body(config_file)["from_the_future"] == 42


# --------------------------------------------------------------------------- #
# It refuses rather than corrupts
# --------------------------------------------------------------------------- #
def test_a_bad_value_is_refused_and_nothing_is_written(config_file):
    config.set_value("postal_code", "27401")
    with pytest.raises(config.SettingError):
        config.set_value("postal_code", "banana")
    assert _body(config_file)["postal_code"] == "27401"


def test_an_unknown_key_is_refused(config_file):
    with pytest.raises(KeyError):
        config.set_value("zipcode", "10001")
    assert not config_file.exists()


def test_a_malformed_file_is_left_untouched(config_file):
    """Rewriting it would destroy whatever the user was in the middle of
    typing, and they can still fix it by hand -- which is the entire reason
    the format is JSON."""
    config_file.write_text('{"postal_code": "27401",', encoding="utf-8")
    with pytest.raises(config.SettingError):
        config.set_value("postal_code", "10001")
    assert config_file.read_text(encoding="utf-8") == '{"postal_code": "27401",'


# --------------------------------------------------------------------------- #
# The CLI, which is what the installer actually tells people to run
# --------------------------------------------------------------------------- #
def test_the_command_works(config_file):
    result = runner.invoke(app, ["config", "set", "postal_code", "10001"])
    assert result.exit_code == 0
    assert "postal_code = 10001" in result.stdout
    assert _body(config_file)["postal_code"] == "10001"


def test_gplan_config_alone_still_shows_the_table(config_file):
    """Adding a subcommand must not change what the bare command does."""
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0
    assert "postal_code" in result.stdout
    assert "what it does" in result.stdout


def test_gplan_config_write_still_works(config_file):
    result = runner.invoke(app, ["config", "--write"])
    assert result.exit_code == 0
    assert config_file.exists()


def test_an_unknown_key_lists_the_real_ones(config_file):
    """The person reading this message has never seen the app before."""
    result = runner.invoke(app, ["config", "set", "zip", "10001"])
    assert result.exit_code == 1
    assert "postal_code" in result.stdout
    assert "log_retention_days" in result.stdout


def test_a_bad_value_says_what_was_expected(config_file):
    result = runner.invoke(app, ["config", "set", "postal_code", "banana"])
    assert result.exit_code == 1
    assert "5-digit" in result.stdout


def test_an_environment_override_is_called_out(config_file, monkeypatch):
    """The environment silently wins over the file, so a value that was just
    'saved' may not be the value the app uses. Saying nothing here is how
    someone spends an afternoon on a ZIP code that never took effect."""
    monkeypatch.setenv("GROCERY_PLANNER_POSTAL_CODE", "99999")
    result = runner.invoke(app, ["config", "set", "postal_code", "10001"])
    assert result.exit_code == 0
    assert "GROCERY_PLANNER_POSTAL_CODE" in result.stdout
    assert "takes precedence" in result.stdout
