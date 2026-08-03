"""The background refresh: the lock, the kill switch, and the timer (GFP-102).

Registering a real Scheduled Task or LaunchAgent is a machine-wide side effect,
so the OS calls are exercised in CI on a throwaway runner and stubbed here.
What is tested here is everything around them, and in particular the two
properties that are easy to break without noticing:

* **The single-refresh lock.** The timer can fire while somebody is scraping by
  hand. Nothing corrupts -- SQLite serialises the writes -- but both would
  scrape the same store, and the Kroger credential is capped at 10,000 calls a
  day, which is already the ceiling on how many customers one credential can
  serve. A wasted duplicate comes straight out of that.
* **The kill switch.** A user who turns background refresh off expects the
  network activity to stop, and expects it to stop whether or not they also
  remember to unregister a scheduled task they never registered.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from grocery_planner import background, install_paths


# --------------------------------------------------------------------------- #
# The lock
# --------------------------------------------------------------------------- #
def test_the_lock_is_taken_and_released(tmp_path):
    with background.refresh_lock(tmp_path) as target:
        assert target.exists()
    assert not target.exists()


def test_a_second_holder_is_refused(tmp_path):
    with background.refresh_lock(tmp_path):
        with pytest.raises(background.AlreadyRunning):
            with background.refresh_lock(tmp_path):
                pytest.fail("two refreshes held the lock at once")


def test_the_lock_records_the_pid(tmp_path):
    """So a human looking at a stuck lock file can find out what holds it."""
    with background.refresh_lock(tmp_path) as target:
        assert target.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_the_lock_is_released_even_when_the_body_raises(tmp_path):
    """A scrape that throws must not wedge the refresh until the staleness
    window expires."""
    with pytest.raises(ZeroDivisionError):
        with background.refresh_lock(tmp_path):
            1 / 0
    assert not (tmp_path / background.LOCK_FILENAME).exists()
    with background.refresh_lock(tmp_path):
        pass


def test_a_stale_lock_is_taken_over(tmp_path):
    """A refresh that can never run again because of a lock file left by a
    power cut is much worse than an occasional double scrape."""
    stale = tmp_path / background.LOCK_FILENAME
    stale.write_text("99999\n", encoding="utf-8")
    old = time.time() - background.STALE_AFTER_SECONDS - 60
    os.utime(stale, (old, old))

    with background.refresh_lock(tmp_path) as target:
        assert target.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_a_fresh_lock_is_not_taken_over(tmp_path):
    fresh = tmp_path / background.LOCK_FILENAME
    fresh.write_text("99999\n", encoding="utf-8")
    with pytest.raises(background.AlreadyRunning):
        with background.refresh_lock(tmp_path):
            pass


def test_the_refusal_names_the_lock_file(tmp_path):
    """Otherwise 'another refresh is already running' is unactionable."""
    (tmp_path / background.LOCK_FILENAME).write_text("1\n", encoding="utf-8")
    with pytest.raises(background.AlreadyRunning) as caught:
        with background.refresh_lock(tmp_path):
            pass
    assert background.LOCK_FILENAME in str(caught.value)


# --------------------------------------------------------------------------- #
# The kill switch
# --------------------------------------------------------------------------- #
def test_refresh_does_nothing_when_the_setting_is_off(monkeypatch, env_db):
    monkeypatch.setenv("GROCERY_PLANNER_BACKGROUND_REFRESH", "false")
    said: list[str] = []

    def explode(*args, **kwargs):
        pytest.fail("a disabled background refresh scraped anyway")

    from grocery_planner import scheduler
    monkeypatch.setattr(scheduler, "run_catch_up", explode)

    assert background.refresh_once(on_event=said.append) == 0
    assert any("turned off" in message for message in said)


def test_being_turned_off_is_not_an_error_exit(monkeypatch, env_db):
    """A non-zero exit shows up in Task Scheduler as a daily red mark, which
    teaches the user to ignore all of them."""
    monkeypatch.setenv("GROCERY_PLANNER_BACKGROUND_REFRESH", "0")
    assert background.refresh_once() == 0


def test_an_already_running_refresh_is_not_an_error_exit(monkeypatch, env_db, tmp_path):
    """The thing the timer was asked to ensure IS happening."""
    monkeypatch.setenv("GROCERY_PLANNER_BACKGROUND_REFRESH", "true")
    from grocery_planner import scheduler
    monkeypatch.setattr(
        scheduler, "list_schedules", lambda *a, **k: [{"store": "foodlion"}]
    )
    monkeypatch.setattr(
        background, "refresh_lock",
        lambda *a, **k: (_ for _ in ()).throw(background.AlreadyRunning("busy")),
    )
    assert background.refresh_once() == 0


def test_no_schedules_is_not_an_error_exit(monkeypatch, env_db):
    """A fresh install has none, and the timer must not report failure daily
    until the user gets round to configuring one."""
    monkeypatch.setenv("GROCERY_PLANNER_BACKGROUND_REFRESH", "true")
    from grocery_planner import scheduler
    monkeypatch.setattr(scheduler, "list_schedules", lambda *a, **k: [])
    assert background.refresh_once() == 0


# --------------------------------------------------------------------------- #
# The timer: names, commands, validation
# --------------------------------------------------------------------------- #
def test_the_identifier_is_the_pinned_one():
    """GFP-102 rule 1: you cannot hand-remove what you cannot name, and a
    rename orphans every earlier install permanently."""
    if sys.platform == "win32":
        assert background.identifier() == (
            install_paths.WINDOWS_TASK_PATH + install_paths.WINDOWS_TASK_NAME
        )
    else:
        assert background.identifier() == install_paths.MACOS_LAUNCH_AGENT_LABEL


@pytest.mark.parametrize("bad", ["", "6", "25:00", "06:99", "noon", "6:00pm"])
def test_a_bad_time_is_refused(bad):
    with pytest.raises(background.TimerError):
        background.install(bad, dry_run=True)


@pytest.mark.parametrize("good", ["00:00", "06:00", "23:59", "6:5"])
def test_a_good_time_is_accepted(good):
    background.install(good, dry_run=True)      # must not raise


def test_the_cadence_is_daily():
    """Settles the ticket's open question. The trends chart plots one point per
    day, so scraping twice a day doubles the load and adds no points."""
    assert background.CADENCE == "daily"
    if sys.platform == "win32":
        assert "DAILY" in background.install("06:00", dry_run=True)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows command shape")
def test_windows_prefers_the_windowless_binary(monkeypatch, tmp_path):
    """The whole reason this is not simply gplan.exe. A console binary run by
    a Scheduled Task blinks a black window onto the screen every morning, and
    the S4U escape hatch is refused on an unelevated account."""
    gui = tmp_path / "gplan-gui.exe"
    gui.write_bytes(b"")
    monkeypatch.setattr(background, "gplan_executable", lambda: tmp_path / "gplan.exe")
    command = background.windows_command()
    assert "gplan-gui.exe" in command
    assert background.REFRESH_FLAG in command


@pytest.mark.skipif(sys.platform != "win32", reason="Windows command shape")
def test_windows_falls_back_to_the_cli_when_there_is_no_gui(monkeypatch, tmp_path):
    """A CLI-only release is a legitimate thing to ship."""
    monkeypatch.setattr(background, "gplan_executable", lambda: tmp_path / "gplan.exe")
    command = background.windows_command()
    assert "schedule run --once" in command
    assert background.REFRESH_FLAG not in command


@pytest.mark.skipif(sys.platform != "win32", reason="Windows command shape")
def test_the_quoting_survives_a_path_with_spaces(monkeypatch, tmp_path):
    """%LOCALAPPDATA% contains the user's name, and plenty of people have a
    space in theirs."""
    spaced = tmp_path / "Program Files" / "Grocery Planner"
    spaced.mkdir(parents=True)
    (spaced / "gplan-gui.exe").write_bytes(b"")
    monkeypatch.setattr(background, "gplan_executable", lambda: spaced / "gplan.exe")
    assert background.windows_command().startswith('"')


def test_a_dry_run_touches_nothing(monkeypatch):
    """Same stance as every other --dry-run in this codebase: an operation
    that changes a machine can answer 'what would this do?' without doing it."""
    def explode(*args, **kwargs):
        pytest.fail("a dry run shelled out to the OS")

    monkeypatch.setattr(background, "_run", explode)
    background.install("06:00", dry_run=True)
    background.remove(dry_run=True)


def test_install_reports_the_os_failure_rather_than_swallowing_it(monkeypatch):
    monkeypatch.setattr(
        background, "_run",
        lambda cmd: subprocess.CompletedProcess(cmd, 1, "", "ERROR: nope"),
    )
    if sys.platform == "darwin":
        pytest.skip("macOS writes a plist first; covered in CI")
    with pytest.raises(background.TimerError) as caught:
        background.install("06:00")
    assert "nope" in str(caught.value)


@pytest.mark.skipif(sys.platform != "win32", reason="schtasks wording")
def test_removing_something_absent_is_success_not_failure(monkeypatch):
    """Idempotence, the same rule as GFP-91/92."""
    monkeypatch.setattr(background, "_delete_task_folder", lambda: None)
    monkeypatch.setattr(
        background, "_run",
        lambda cmd: subprocess.CompletedProcess(
            cmd, 1, "", "ERROR: The system cannot find the file specified."
        ),
    )
    assert background.remove() == "not registered"


@pytest.mark.skipif(sys.platform != "win32", reason="schtasks wording")
def test_a_localised_not_found_is_still_recognised(monkeypatch):
    """The message is localised; the error code is not. Matching only on
    English would make this report a hard failure on a German install."""
    monkeypatch.setattr(background, "_delete_task_folder", lambda: None)
    monkeypatch.setattr(
        background, "_run",
        lambda cmd: subprocess.CompletedProcess(cmd, 1, "", "FEHLER: 0x80070002"),
    )
    assert background.remove() == "not registered"


@pytest.mark.skipif(sys.platform != "darwin", reason="plist shape")
def test_the_plist_names_the_pinned_label_and_the_right_arguments():
    body = background._plist("06:30")
    assert install_paths.MACOS_LAUNCH_AGENT_LABEL in body
    assert "<string>--once</string>" in body
    assert "<integer>6</integer>" in body and "<integer>30</integer>" in body
    # RunAtLoad false: the calendar entry is the schedule, and RunAtLoad would
    # make every reboot an extra scrape.
    assert "<key>RunAtLoad</key>\n  <false/>" in body


def test_the_time_is_zero_padded_for_the_os():
    """`schtasks /ST 6:5` is rejected by Windows, and a time that passes our
    own validation and then fails at the OS puts the error a long way from
    its cause."""
    assert background.normalise_time("6:5") == "06:05"
    assert background.normalise_time(" 23:9 ") == "23:09"
