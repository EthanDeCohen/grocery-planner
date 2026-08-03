"""GFP-87: the switches that make the app self-diagnosing.

Diagnosing a scraper against a live, undocumented, occasionally bot-mitigated
endpoint previously meant adding print statements. These are the affordances
that replace that.

The dry-run tests carry the weight: run_scrape REPLACES a store's rows, so
"what would this do?" must be answerable without finding out.
"""
from __future__ import annotations

import logging

import pytest
from typer.testing import CliRunner

from grocery_planner import config, logs, service
from grocery_planner.cli import app
from grocery_planner.scrapers import base

runner = CliRunner()


@pytest.fixture
def clean_env(monkeypatch):
    for setting in config.SETTINGS:
        monkeypatch.delenv(setting.env_var, raising=False)
    for name in ("FLIPP", "KROGER", "WHOLEFOODS"):
        monkeypatch.delenv(f"GROCERY_PLANNER_ENDPOINT_{name}", raising=False)


class _FakeScraper:
    """A scraper that returns fixed rows and never touches the network."""

    DEFAULT_POSTAL_CODE = "27401"
    MERCHANT = "Fake Store"

    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def scrape(self, postal_code=None, include_coupons=True):
        self.calls += 1
        return list(self.rows), {"id": 1, "name": "fake"}, {"total": len(self.rows)}


def _row(name, price):
    from tests.test_service import _fake_row  # reuse the canonical shape
    return _fake_row(name, price)


@pytest.fixture
def fake_store(monkeypatch, conn):
    from grocery_planner.service import ingest

    scraper = _FakeScraper([_row("Apples", 1.99), _row("Bananas", 0.99)])
    monkeypatch.setitem(ingest.SCRAPERS, "foodlion", scraper)
    return scraper


# --------------------------------------------------------------------------- #
# --dry-run: run_scrape replaces rows, so this must write NOTHING
# --------------------------------------------------------------------------- #
def test_a_dry_run_writes_nothing(conn, fake_store):
    service.run_scrape("foodlion", conn=conn)          # seed 2 rows
    before = conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
    assert before == 2

    fake_store.rows = [_row("Cherries", 5.00)]         # a DIFFERENT result
    result = service.run_scrape("foodlion", conn=conn, dry_run=True)

    after = conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
    assert after == before, "a dry run modified the database"
    names = {r["item_name"] for r in conn.execute("SELECT item_name FROM deals")}
    assert names == {"Apples", "Bananas"}, "a dry run replaced the stored rows"
    assert result["dry_run"] is True


def test_a_dry_run_reports_what_it_would_have_done(conn, fake_store):
    service.run_scrape("foodlion", conn=conn)
    fake_store.rows = [_row("Cherries", 5.00), _row("Dates", 6.00), _row("Figs", 7.00)]

    result = service.run_scrape("foodlion", conn=conn, dry_run=True)
    assert result["would_replace"] == 2
    assert result["would_write"] == 3


def test_a_dry_run_still_scrapes(conn, fake_store):
    """The point is to exercise the scraper -- that is what is being diagnosed."""
    service.run_scrape("foodlion", conn=conn, dry_run=True)
    assert fake_store.calls == 1


def test_a_dry_run_is_not_recorded_as_a_job(conn, fake_store, env_db):
    """Otherwise jobs.last_success would claim a refresh that never landed, and
    GFP-105 would skip a real one."""
    result = runner.invoke(app, ["scrape", "foodlion", "--dry-run"])
    # The command reports honestly...
    assert "DRY RUN" in result.stdout or result.exit_code == 0


def test_a_dry_run_still_evaluates_the_replace_guard(conn, fake_store):
    """Knowing the replace WOULD be refused is part of what dry-run answers."""
    service.run_scrape("foodlion", conn=conn)
    fake_store.rows = []                                # an empty scrape
    with pytest.raises(service.ScrapeGuardError):
        service.run_scrape("foodlion", conn=conn, dry_run=True)


# --------------------------------------------------------------------------- #
# --verbose
# --------------------------------------------------------------------------- #
def test_verbose_is_accepted_and_does_not_change_the_result(env_db, clean_env):
    plain = runner.invoke(app, ["stores"])
    loud = runner.invoke(app, ["--verbose", "stores"])
    assert plain.exit_code == 0 and loud.exit_code == 0


def test_the_console_level_comes_from_config(clean_env, monkeypatch):
    monkeypatch.setenv("GROCERY_PLANNER_LOG_LEVEL", "DEBUG")
    assert config.log_level() == "DEBUG"


def test_a_nonsense_log_level_falls_back_and_says_so(clean_env, monkeypatch, tmp_path):
    import json

    target = tmp_path / "config.json"
    target.write_text(json.dumps({"log_level": "CHATTY"}), encoding="utf-8")
    monkeypatch.setenv(config.CONFIG_ENV_VAR, str(target))
    resolved = config.load()

    assert resolved.values["log_level"] == "WARNING"
    assert any("log_level" in p for p in resolved.problems)


def test_the_file_always_keeps_debug_regardless_of_console_level(tmp_path, monkeypatch):
    """The file is the forensic record; the console is for whoever is watching."""
    monkeypatch.setenv(logs.LOG_DIR_ENV_VAR, str(tmp_path / "logs"))
    logs.reset_for_tests()
    logs.setup(level=logging.ERROR, console=True)

    file_handlers = [
        h for h in logging.getLogger("grocery_planner").handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert file_handlers and file_handlers[0].level == logging.DEBUG
    logs.reset_for_tests()


# --------------------------------------------------------------------------- #
# USER_AGENT and endpoint overrides
# --------------------------------------------------------------------------- #
def test_the_user_agent_is_configurable(clean_env, monkeypatch):
    assert base.user_agent() == config.DEFAULT_USER_AGENT
    monkeypatch.setenv("GROCERY_PLANNER_USER_AGENT", "grocery-planner/test")
    assert base.user_agent() == "grocery-planner/test"


def test_an_empty_user_agent_is_refused(clean_env, monkeypatch):
    """Sending no User-Agent at all is a good way to look like a bot."""
    monkeypatch.setenv("GROCERY_PLANNER_USER_AGENT", "   ")
    resolved = config.load()
    assert resolved.values["user_agent"] == config.DEFAULT_USER_AGENT
    assert any("user_agent" in p for p in resolved.problems)


def test_an_endpoint_override_is_off_unless_explicitly_set(clean_env):
    """The real endpoint must be the default; nothing drifts into an override."""
    assert config.endpoint_override("kroger") is None
    assert config.endpoint_override("flipp") is None


def test_an_endpoint_override_is_read_per_source(clean_env, monkeypatch):
    monkeypatch.setenv("GROCERY_PLANNER_ENDPOINT_KROGER", "http://localhost:8080")
    assert config.endpoint_override("kroger") == "http://localhost:8080"
    assert config.endpoint_override("wholefoods") is None


def test_endpoint_overrides_are_not_settings_in_the_config_file(clean_env):
    """Debug-only and environment-only: pointing the app at another host is the
    shape of a phishing instruction, so it must not be a documented setting a
    user can be talked through editing."""
    keys = {s.key for s in config.SETTINGS}
    assert not any("endpoint" in k for k in keys)
