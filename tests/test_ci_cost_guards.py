"""The CI triggers, guarded (GFP-116).

This is a cost test, and it exists because the failure already happened. On
2026-08-01 an unthrottled workflow matrix burned the entire 2,000-minute
monthly GitHub Actions allowance in a SINGLE DAY and hard-blocked CI for the
project (GFP-94). GitHub bills Windows at 2x and macOS at 10x, so a trigger
that looks harmless in wall-clock terms is not harmless in allowance terms.

The specific mistakes that caused it were both one-line edits:

* ``push: branches: ["**"]`` alongside ``pull_request``, so every commit on a
  PR branch paid for two full runs.
* no ``concurrency`` block, so pushing again while a run was in flight paid for
  both.

Neither shows up as a failure. CI stays green right up until the allowance is
gone, and then everything stops at once. So the guardrails are asserted here,
where breaking one is a red test rather than an invoice.
"""
from __future__ import annotations

import pathlib

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOWS = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows"
CI = WORKFLOWS / "ci.yml"
LIFECYCLE = WORKFLOWS / "macos-lifecycle.yml"


def _load(path: pathlib.Path) -> dict:
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    # PyYAML reads the bare key `on:` as the boolean True (YAML 1.1), which is
    # a genuine trap: a test that looked up "on" would silently find nothing
    # and pass no matter what the triggers were.
    parsed["on"] = parsed.get("on", parsed.get(True))
    return parsed


@pytest.mark.parametrize("path", [CI, LIFECYCLE], ids=lambda p: p.name)
def test_the_workflow_parses(path):
    assert path.exists(), f"{path.name} is missing"
    assert _load(path)["on"], f"{path.name} declares no triggers"


# --------------------------------------------------------------------------- #
# ci.yml
# --------------------------------------------------------------------------- #
def test_ci_does_not_run_on_every_branch_push():
    """`push: branches: ["**"]` plus `pull_request` is what doubled the bill.
    A PR must run once, not twice."""
    triggers = _load(CI)["on"]
    push = triggers.get("push")
    assert push, "ci.yml no longer runs on push at all"
    branches = push.get("branches")
    assert branches == ["main"], (
        f"ci.yml pushes on {branches!r}; only main is affordable alongside "
        "pull_request"
    )


def test_ci_cancels_superseded_runs():
    """Without this, pushing again while a run is in flight pays for both."""
    concurrency = _load(CI).get("concurrency")
    assert concurrency, "ci.yml has no concurrency block"
    assert concurrency.get("cancel-in-progress") is True


def test_the_expensive_job_does_not_run_on_pull_requests():
    """The binary job is the 10x macOS cost. A packaging break is caught at
    merge or on dispatch; it need not gate every PR commit."""
    condition = _load(CI)["jobs"]["binary"].get("if", "")
    assert "pull_request" in condition, (
        "the binary job no longer excludes pull requests"
    )


# --------------------------------------------------------------------------- #
# macos-lifecycle.yml -- the slowest thing this project runs
# --------------------------------------------------------------------------- #
def test_the_lifecycle_never_runs_on_a_push_to_a_branch():
    """A full install/uninstall lifecycle is slower than a test run, on the
    10x runner. Tags only."""
    triggers = _load(LIFECYCLE)["on"]
    assert "pull_request" not in triggers
    push = triggers.get("push", {})
    assert "branches" not in push, (
        "the macOS lifecycle would run on every push to a branch"
    )
    assert push.get("tags"), "the lifecycle should still run for release tags"


def test_the_lifecycle_is_not_nightly():
    """Weekly catches runner drift -- OS updates, Xcode, Python -- for about
    four runs a month instead of thirty."""
    schedule = _load(LIFECYCLE)["on"].get("schedule")
    assert schedule, "no scheduled run, so runner drift would go unnoticed"
    cron = schedule[0]["cron"].split()
    assert cron[4] != "*", f"cron {schedule[0]['cron']!r} runs every day"


def test_the_lifecycle_has_a_timeout():
    """A hung macOS job bills at 10x for as long as it hangs."""
    assert _load(LIFECYCLE)["jobs"]["lifecycle"].get("timeout-minutes")


def test_the_lifecycle_cancels_superseded_runs():
    assert _load(LIFECYCLE)["concurrency"]["cancel-in-progress"] is True


def test_the_cost_is_written_down_in_both_workflows():
    """So the next person changing a trigger can see what they are spending
    before they spend it, rather than after."""
    for path in (CI, LIFECYCLE):
        body = path.read_text(encoding="utf-8").lower()
        assert "10x" in body, f"{path.name} does not state the macOS multiplier"
        assert "allowance" in body, f"{path.name} does not mention the allowance"


# --------------------------------------------------------------------------- #
# The evidence the lifecycle exists to produce
# --------------------------------------------------------------------------- #
def test_the_evidence_is_uploaded_even_when_the_run_fails():
    """A red CI run whose logs are gone is worth nothing, and the failing run
    is precisely the one whose evidence is needed."""
    steps = _load(LIFECYCLE)["jobs"]["lifecycle"]["steps"]
    uploads = [s for s in steps if "upload-artifact" in str(s.get("uses", ""))]
    assert uploads, "the lifecycle uploads no evidence"
    assert any(str(s.get("if", "")).strip() == "always()" for s in uploads), (
        "the evidence is only uploaded on success"
    )


def test_the_lifecycle_snapshots_before_and_after_every_phase():
    """The snapshots ARE the test. An assertion that says 'the app is
    installed' passes just as happily when it is installed in the wrong
    place."""
    names = [str(s.get("name", "")) for s in _load(LIFECYCLE)["jobs"]["lifecycle"]["steps"]]
    snapshots = [n for n in names if n.startswith("Snapshot")]
    assert len(snapshots) >= 4, f"only {len(snapshots)} snapshots: {snapshots}"


def test_the_snapshot_script_ships_and_is_a_shell_script():
    script = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "mac_snapshot.sh"
    assert script.exists()
    body = script.read_text(encoding="utf-8")
    assert body.startswith("#!/bin/bash")
    # The one artifact that keeps ACTING after the app is gone.
    assert "launchctl" in body
