"""Minting a Whole Foods session in the app (GFP-80).

The embedded browser cannot be driven in a headless test run, so what is
tested here is everything on either side of it: the validation that decides
whether a captured cookie is allowed to reach the disk, and the decision logic
the dialog applies to each cookie the browser hands it.

**``save_session`` carries the weight.** A session file holding a cookie that
cannot be decoded, or one minted for the wrong ZIP, is worse than no file:
``readiness()`` then reports Whole Foods as ready, the scrape fails, and the
failure surfaces a long way from the moment the mistake was made. Worse still
for a wrong ZIP -- that one does not fail at all. It silently returns another
city's prices, which is the exact failure GFP-53 exists to prevent.
"""
from __future__ import annotations

import base64
import json

import pytest

from grocery_planner.scrapers import wholefoods


def _cookie(**fields) -> str:
    """A wfm_store_d8 value in the encoding the live cookie actually uses.

    base64, per GFP-93 -- a real minted value read out of Chrome began
    ``eyJpZCI6IjEwNDI2Iiwi``.
    """
    payload = {"id": "10426", "deliveryZip": "27401", "name": "Friendly Center"}
    payload.update(fields)
    return base64.b64encode(json.dumps(payload).encode()).decode()


# --------------------------------------------------------------------------- #
# save_session: what is allowed onto the disk
# --------------------------------------------------------------------------- #
def test_a_good_cookie_is_written(tmp_path):
    target = tmp_path / "wholefoods_session.json"
    wholefoods.save_session(_cookie(), postal_code="27401", path=target)

    body = json.loads(target.read_text(encoding="utf-8"))
    assert body["wfm_store_d8"] == _cookie()


def test_what_was_written_is_what_the_scraper_reads_back(tmp_path):
    """The only test that actually matters for round-tripping: the file this
    writes must load through the SAME function the scrape path uses."""
    target = tmp_path / "wholefoods_session.json"
    wholefoods.save_session(_cookie(), postal_code="27401", path=target)

    session = wholefoods.load_session(target)
    assert session.wfm_store_d8 == _cookie()
    assert session.minted_at


def test_the_mint_time_is_recorded(tmp_path):
    target = tmp_path / "s.json"
    wholefoods.save_session(_cookie(), postal_code="27401", path=target)
    body = json.loads(target.read_text(encoding="utf-8"))
    assert body["minted_at"].startswith("20")


def test_the_zip_is_recorded_for_a_human(tmp_path):
    """So somebody looking at the file can tell which ZIP it is for without
    decoding a base64 blob by hand."""
    target = tmp_path / "s.json"
    wholefoods.save_session(_cookie(), postal_code="27401", path=target)
    assert json.loads(target.read_text(encoding="utf-8"))["postal_code"] == "27401"


def test_only_the_one_cookie_is_stored(tmp_path):
    """A browsing session carries a great many cookies. Persisting the jar
    would put more of a customer's browsing on disk than this app has any
    business holding."""
    target = tmp_path / "s.json"
    wholefoods.save_session(_cookie(), postal_code="27401", path=target)
    assert set(json.loads(target.read_text(encoding="utf-8"))) == {
        "wfm_store_d8", "minted_at", "postal_code"
    }


# --------------------------------------------------------------------------- #
# ...and what is refused
# --------------------------------------------------------------------------- #
def test_an_empty_cookie_is_refused(tmp_path):
    target = tmp_path / "s.json"
    with pytest.raises(wholefoods.SessionMissingError):
        wholefoods.save_session("", postal_code="27401", path=target)
    assert not target.exists(), "an empty cookie created a file"


def test_an_undecodable_cookie_is_refused(tmp_path):
    """Saving it would only defer the error to the next scrape, by which time
    nobody remembers minting anything."""
    target = tmp_path / "s.json"
    with pytest.raises(wholefoods.SessionExpiredError):
        wholefoods.save_session("not-a-real-cookie", postal_code="27401", path=target)
    assert not target.exists()


def test_a_cookie_for_the_wrong_zip_is_refused(tmp_path):
    """The dangerous one: this failure does not error later, it silently
    returns another city's prices."""
    target = tmp_path / "s.json"
    with pytest.raises(wholefoods.ZipMismatchError):
        wholefoods.save_session(
            _cookie(deliveryZip="90210"), postal_code="27401", path=target
        )
    assert not target.exists()


def test_a_refusal_does_not_clobber_a_working_session(tmp_path):
    """Re-minting badly must not cost somebody the session they already had."""
    target = tmp_path / "s.json"
    wholefoods.save_session(_cookie(), postal_code="27401", path=target)
    before = target.read_text(encoding="utf-8")

    with pytest.raises(wholefoods.ZipMismatchError):
        wholefoods.save_session(
            _cookie(deliveryZip="90210"), postal_code="27401", path=target
        )
    assert target.read_text(encoding="utf-8") == before


def test_the_validation_is_the_scrape_paths_own(tmp_path):
    """save_session must not grow a second opinion about what is valid.

    If it validated independently, "will this be accepted later" would be
    answered by code that is not the code doing the accepting, and the two
    would drift.
    """
    import inspect
    source = inspect.getsource(wholefoods.save_session)
    assert "_decode_store_cookie" in source
    assert "_check_zip" in source


def test_saving_without_a_zip_skips_the_zip_check(tmp_path):
    """There is one caller that legitimately has no ZIP to check against --
    a user restoring a session by hand. It still has to decode."""
    target = tmp_path / "s.json"
    wholefoods.save_session(_cookie(deliveryZip="90210"), path=target)
    assert wholefoods.load_session(target).wfm_store_d8


# --------------------------------------------------------------------------- #
# The dialog's decision logic, without a browser
# --------------------------------------------------------------------------- #
gui = pytest.importorskip("PySide6", reason="GUI extra not installed")


def test_the_module_imports_no_webengine_at_module_scope():
    """Qt WebEngine is 195 MB of Chromium and most launches never open this
    window, so every import of it is deferred into the dialog."""
    import pathlib

    from grocery_planner.gui import wholefoods as mint_ui

    body = pathlib.Path(mint_ui.__file__).read_text(encoding="utf-8")
    module_level = []
    for line in body.splitlines():
        if line.startswith(("import ", "from ")) and "WebEngine" in line:
            module_level.append(line)
    assert not module_level, f"WebEngine imported at module scope: {module_level}"


def test_it_watches_for_exactly_the_one_cookie():
    from grocery_planner.gui import wholefoods as mint_ui

    assert mint_ui.COOKIE_NAME == b"wfm_store_d8"


def test_the_mint_starts_at_the_store_finder():
    """The home page would leave the user to find the picker themselves,
    which is a step this exists to remove."""
    from grocery_planner.gui import wholefoods as mint_ui

    assert mint_ui.STORE_FINDER_URL.endswith("/stores")
    assert mint_ui.STORE_FINDER_URL.startswith("https://")


def test_a_build_without_webengine_reports_it_cleanly():
    """A CLI-only or size-trimmed build must say so, not raise ImportError in
    front of a nutritionist."""
    from grocery_planner.gui import wholefoods as mint_ui

    assert issubclass(mint_ui.WebEngineUnavailable, RuntimeError)


def test_the_app_imports_webengine_before_the_qapplication():
    """Qt requires it, and deferring it makes the window fail to open at the
    exact moment a user asks for it."""
    import pathlib

    from grocery_planner.gui import app as gui_app

    body = pathlib.Path(gui_app.__file__).read_text(encoding="utf-8")
    where_import = body.index("from PySide6 import QtWebEngineCore")
    where_qapp = body.index("app = QApplication(sys.argv)")
    assert where_import < where_qapp, (
        "QtWebEngineCore is imported after QApplication is constructed"
    )
