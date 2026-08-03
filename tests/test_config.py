"""GFP-85: one JSON config file, and it may never crash the app.

The concrete problem this started with: 27401 was hard-coded in FOUR modules
(the ticket said three; GFP-98 added the fourth), so a nutritionist in another
city had to edit source code.

The tests that matter most are the ones proving a hand-edited file cannot break
the product. A trailing comma in JSON is not a hypothetical.
"""
from __future__ import annotations

import json

import pytest

from grocery_planner import config


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """Point the config module at a throwaway file."""
    target = tmp_path / "config.json"
    monkeypatch.setenv(config.CONFIG_ENV_VAR, str(target))
    for setting in config.SETTINGS:
        monkeypatch.delenv(setting.env_var, raising=False)
    return target


def _write(target, body: str) -> None:
    target.write_text(body, encoding="utf-8")


# --------------------------------------------------------------------------- #
# Defaults and precedence
# --------------------------------------------------------------------------- #
def test_a_missing_file_is_normal_not_an_error(cfg):
    """A fresh install has no config file, and that is the correct state."""
    resolved = config.load()
    assert resolved.ok, resolved.problems
    assert resolved.values["postal_code"] == "27401"


def test_the_file_overrides_the_default(cfg):
    _write(cfg, json.dumps({"postal_code": "10001"}))
    assert config.load().values["postal_code"] == "10001"


def test_the_environment_overrides_the_file(cfg, monkeypatch):
    """Keeps the GROCERY_PLANNER_DB pattern, and rescues a broken file."""
    _write(cfg, json.dumps({"postal_code": "10001"}))
    monkeypatch.setenv("GROCERY_PLANNER_POSTAL_CODE", "60601")
    assert config.load().values["postal_code"] == "60601"


def test_a_partial_file_keeps_defaults_for_everything_else(cfg):
    _write(cfg, json.dumps({"postal_code": "10001"}))
    values = config.load().values
    assert values["postal_code"] == "10001"
    assert values["auto_refresh"] is True          # untouched default


# --------------------------------------------------------------------------- #
# Never crashing -- the ticket's hard requirement
# --------------------------------------------------------------------------- #
def test_a_trailing_comma_does_not_crash_the_app(cfg):
    """A hand-edited JSON file WILL eventually have one of these."""
    _write(cfg, '{\n  "postal_code": "10001",\n}\n')
    resolved = config.load()

    assert resolved.values == config.defaults()     # every default applied
    assert resolved.problems, "a broken file must be reported, not swallowed"
    assert "not valid JSON" in resolved.problems[0]
    assert str(cfg) in resolved.problems[0]         # names the file


def test_one_bad_value_costs_only_its_own_key(cfg):
    """One bad ZIP must not also cost you your logging settings."""
    _write(cfg, json.dumps({"postal_code": "nope", "log_retention_days": 30}))
    resolved = config.load()

    assert resolved.values["postal_code"] == "27401"        # fell back
    assert resolved.values["log_retention_days"] == 30      # survived
    assert any("postal_code" in p for p in resolved.problems)


def test_a_bad_value_names_the_key_and_what_was_expected(cfg):
    _write(cfg, json.dumps({"postal_code": "ABCDE"}))
    problem = config.load().problems[0]
    assert "postal_code" in problem
    assert "5-digit" in problem
    assert "27401" in problem            # says what it used instead


def test_a_json_array_is_refused_without_crashing(cfg):
    _write(cfg, json.dumps(["not", "an", "object"]))
    resolved = config.load()
    assert resolved.values == config.defaults()
    assert "JSON object" in resolved.problems[0]


def test_an_empty_file_is_treated_as_no_settings(cfg):
    _write(cfg, "   \n")
    assert config.load().ok


def test_a_byte_order_mark_does_not_break_parsing(cfg):
    """Notepad on Windows writes one; GFP-93 was this exact bug elsewhere."""
    cfg.write_text(json.dumps({"postal_code": "10001"}), encoding="utf-8-sig")
    assert config.load().values["postal_code"] == "10001"


def test_an_unknown_key_is_a_warning_not_a_failure(cfg):
    """Usually a typo or a setting from a newer version; neither should stop us."""
    _write(cfg, json.dumps({"postal_code": "10001", "future_setting": 1}))
    resolved = config.load()
    assert resolved.values["postal_code"] == "10001"
    assert any("future_setting" in p for p in resolved.problems)


# --------------------------------------------------------------------------- #
# Values
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw,expected", [
    (True, True), (False, False), ("true", True), ("no", False), ("1", True), ("off", False),
])
def test_booleans_accept_what_a_human_would_type(cfg, raw, expected):
    _write(cfg, json.dumps({"auto_refresh": raw}))
    assert config.load().values["auto_refresh"] is expected


def test_a_nonsense_boolean_falls_back_and_says_so(cfg):
    _write(cfg, json.dumps({"auto_refresh": "maybe"}))
    resolved = config.load()
    assert resolved.values["auto_refresh"] is True
    assert any("auto_refresh" in p for p in resolved.problems)


@pytest.mark.parametrize("bad", ["0", "-5", "abc"])
def test_log_retention_must_be_a_positive_whole_number(cfg, bad):
    _write(cfg, json.dumps({"log_retention_days": bad}))
    resolved = config.load()
    assert resolved.values["log_retention_days"] == 7
    assert any("log_retention_days" in p for p in resolved.problems)


# --------------------------------------------------------------------------- #
# Writing, and the file the user actually edits
# --------------------------------------------------------------------------- #
def test_write_defaults_creates_a_readable_file(cfg):
    written = config.write_defaults()
    assert written.exists()
    assert json.loads(written.read_text(encoding="utf-8")) == config.defaults()


def test_write_defaults_refuses_to_clobber_a_hand_edited_file(cfg):
    """That file is hand-edited and irreplaceable; overwriting needs asking."""
    _write(cfg, json.dumps({"postal_code": "10001"}))
    config.write_defaults()
    assert json.loads(cfg.read_text(encoding="utf-8"))["postal_code"] == "10001"

    config.write_defaults(overwrite=True)
    assert json.loads(cfg.read_text(encoding="utf-8"))["postal_code"] == "27401"


def test_describe_says_where_each_value_came_from(cfg, monkeypatch):
    """"Why is it using that ZIP" is answered by origin, not by value."""
    _write(cfg, json.dumps({"postal_code": "10001"}))
    monkeypatch.setenv("GROCERY_PLANNER_AUTO_REFRESH", "false")
    origins = {key: origin for key, _v, origin, _d in config.describe()}

    assert origins["postal_code"] == "config file"
    assert origins["auto_refresh"].startswith("env ")
    assert origins["log_retention_days"] == "default"


def test_an_unknown_setting_is_a_programming_error_not_a_silent_none():
    with pytest.raises(KeyError):
        config.get("nope")
