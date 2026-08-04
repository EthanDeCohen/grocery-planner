"""Ask for the ZIP before anything is scraped (GFP-122).

**The failure this prevents is the worst shape this app has.** ``postal_code``
defaults to ``27401`` -- the developer's ZIP -- and GFP-105 auto-refreshes on
first run. So without this, the very first act of a new install is to fetch a
different city's prices: confidently, completely, and with nothing on screen
suggesting anything is wrong. A wrong ZIP produces no error. It produces a
plausible answer to a question nobody asked.

The ordering (ask, THEN refresh) is therefore the whole ticket, and is tested
directly rather than left to the reading of ``main()``.
"""
from __future__ import annotations

import pytest

from grocery_planner import config


@pytest.fixture
def fresh_install(tmp_path, monkeypatch):
    """An install that has never been configured."""
    monkeypatch.setenv("GROCERY_PLANNER_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.delenv("GROCERY_PLANNER_POSTAL_CODE", raising=False)
    return tmp_path


# --------------------------------------------------------------------------- #
# What counts as a first run
# --------------------------------------------------------------------------- #
def test_no_config_file_is_a_first_run(fresh_install):
    assert config.is_first_run() is True


def test_writing_the_config_ends_the_first_run(fresh_install):
    config.write_defaults()
    assert config.is_first_run() is False


def test_setting_the_zip_ends_the_first_run(fresh_install):
    config.set_value("postal_code", "90210")
    assert config.is_first_run() is False


def test_first_run_means_no_config_not_no_database(fresh_install):
    """Someone who clears their data to start over has already answered the
    setup questions. Asking again would be the app forgetting what it was
    told."""
    config.set_value("postal_code", "90210")
    # Nothing about the database is consulted -- that is the assertion.
    assert config.is_first_run() is False
    assert config.postal_code() == "90210"


def test_a_config_that_declined_to_change_anything_still_counts_as_configured(
    fresh_install,
):
    """Being nagged every launch is worse than a default somebody chose to
    keep."""
    config.write_defaults()
    assert config.is_first_run() is False
    assert config.postal_code() == "27401"


# --------------------------------------------------------------------------- #
# The default is the thing being defended against
# --------------------------------------------------------------------------- #
def test_the_default_zip_is_not_the_users(fresh_install):
    """Pins the premise. If this default ever became something neutral the
    urgency of the whole ticket changes, and this test should be the thing
    that makes someone think about it."""
    assert config.postal_code() == "27401"


def test_the_dialog_does_not_prefill_the_default():
    """A box already containing a plausible ZIP invites Enter, which is
    exactly the silent-wrong-city outcome the dialog exists to prevent."""
    pytest.importorskip("PySide6.QtWidgets")
    from grocery_planner.gui import firstrun

    _app = _qt_app()
    dialog = firstrun.FirstRunDialog()
    assert dialog.postal_code() == ""


def _qt_app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


# --------------------------------------------------------------------------- #
# Validation matches the config layer's own rule
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,valid", [
    ("90210", True), ("27401", True), ("00501", True),   # leading zero survives
    ("", False), ("9", False), ("902", False), ("9021", False),
    ("902100", False), ("abcde", False), ("9021a", False), ("902-1", False),
])
def test_the_dialog_accepts_exactly_what_the_config_layer_accepts(text, valid):
    """If these two rules drift, the user is told 'invalid' by a component
    they cannot see, after the one they can see accepted it.

    Both are given ALREADY-STRIPPED input, because that is how both are
    reached: the dialog strips in ``_validate`` and again in ``postal_code``,
    and ``config._postal_code`` strips before matching. Feeding one padded
    input would test a path neither takes.
    """
    from grocery_planner.config import SettingError, _postal_code
    from grocery_planner.gui.firstrun import ZIP_PATTERN

    assert bool(ZIP_PATTERN.match(text)) is valid

    try:
        _postal_code("postal_code", text)
        config_accepts = True
    except SettingError:
        config_accepts = False
    assert config_accepts is valid, (
        f"the dialog and config disagree about {text!r}"
    )


# --------------------------------------------------------------------------- #
# The ZIP stays on screen
# --------------------------------------------------------------------------- #
def test_the_zip_control_survives_construction(fresh_install):
    """REGRESSION. The corner widget was first created unparented, and
    setCornerWidget does not take ownership -- so Python collected it as soon
    as the method returned, taking the button's C++ object with it. The
    control simply never appeared, and touching it raised "already deleted".

    Caught by a screenshot, not by a test, which is why this one exists.
    """
    pytest.importorskip("PySide6.QtWidgets")
    import gc

    from PySide6.QtCore import Qt

    from grocery_planner.gui.app import MainWindow

    _qt_app()
    window = MainWindow()
    try:
        # THE FORCED COLLECTION IS THE TEST. Without it the widget is still
        # alive at this point and the bug does not reproduce -- which is
        # exactly why the first version of this test passed against the broken
        # code and had to be thrown away.
        gc.collect()

        # Reading .text() is what raised RuntimeError when the object was gone.
        assert window.zip_button.text() == config.postal_code()

        # ...and the symptom the user would actually see: the control has to
        # occupy space. A surviving widget that lays out to nothing is still an
        # invisible ZIP, which is the failure this whole ticket is about.
        corner = window.menuBar().cornerWidget(Qt.TopRightCorner)
        assert corner is not None
        assert corner.sizeHint().width() > 0, "the ZIP control renders as nothing"
    finally:
        window.close()


def test_the_zip_control_shows_the_configured_zip(fresh_install):
    """A wrong ZIP has to be noticeable at a glance -- that is the entire
    reason it is on screen rather than buried in a settings dialog."""
    pytest.importorskip("PySide6.QtWidgets")
    import gc

    config.set_value("postal_code", "90210")

    from grocery_planner.gui.app import MainWindow

    _qt_app()
    window = MainWindow()
    try:
        gc.collect()        # for the same reason as above
        assert window.zip_button.text() == "90210"
    finally:
        window.close()


# --------------------------------------------------------------------------- #
# The ordering, which is the ticket
# --------------------------------------------------------------------------- #
def test_the_zip_is_asked_before_the_first_scrape(fresh_install, monkeypatch):
    """THE ACCEPTANCE CRITERION. Reversing these two lines in main() would
    reintroduce the exact bug: a confident scrape of the wrong city."""
    import inspect

    from grocery_planner.gui import app as app_module

    source = inspect.getsource(app_module.main)
    assert "is_first_run" in source, "main() no longer asks for the ZIP"
    assert source.index("is_first_run") < source.index("maybe_auto_refresh"), (
        "the ZIP must be asked BEFORE the first auto-refresh, or the first "
        "thing a new install does is scrape the developer's ZIP"
    )
