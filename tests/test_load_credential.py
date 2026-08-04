"""Settings ▸ Load credential… (GFP-148).

A user is emailed a credential file. Without this they have to put it in a
per-OS data directory whose path differs on every platform and is inside a
hidden folder on macOS -- which is not a setup step, it is a support call.

The identification logic is the interesting part and is pure, so most of this
tests :mod:`grocery_planner.credentials` directly rather than driving Qt.

**Nothing here uses a real credential.** The values are obvious fakes, and one
test exists to prove a value cannot reach the screen even if it were real.
"""
from __future__ import annotations

import json

import pytest

from grocery_planner import credentials

KROGER_INI = "[kroger]\nclient_id = fake-id\nclient_secret = fake-secret\n"
KROGER_BARE = "client_id = fake-id\nclient_secret = fake-secret\n"
WHOLEFOODS_JSON = json.dumps({"wfm_store_d8": "fake-cookie"})
LICENCE_JSON = json.dumps({"licence_key": "fake-licence"})


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """A data dir of our own, patched where credentials.py looked it up."""
    monkeypatch.setattr(credentials, "data_dir", lambda: tmp_path)
    for spec in credentials.SPECS.values():
        monkeypatch.delenv(spec.env_var, raising=False)
    credentials.set_provider(None)
    yield tmp_path
    credentials.set_provider(None)


# --------------------------------------------------------------------------- #
# Identification -- why the menu item does not have to say "Kroger"
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("document,expected", [
    (KROGER_INI, "kroger"),
    (KROGER_BARE, "kroger"),          # INI with no section header
    (WHOLEFOODS_JSON, "wholefoods"),
    (LICENCE_JSON, "licence"),
])
def test_a_credential_is_identified_by_its_contents(document, expected):
    assert credentials.identify(document).name == expected


def test_identification_is_driven_by_the_registry_not_a_hardcoded_list():
    """THE PROPERTY THAT KEEPS THE UI GENERIC. Every spec's declared `keys`
    must be what identifies it, so adding a credential to SPECS teaches the
    dialog about it without touching the dialog."""
    for spec in credentials.SPECS.values():
        document = json.dumps({key: "fake" for key in spec.keys})
        assert credentials.identify(document).name == spec.name


@pytest.mark.parametrize("document", [
    "", "   ", "hello world", "not a credential at all",
    json.dumps({"unrelated": "value"}),
    json.dumps(["a", "list"]),
    "{ broken json",
])
def test_something_that_is_not_a_credential_is_not_guessed_at(document):
    """A wrong guess would write a junk file to a credential path and report
    success. 'I do not recognise this' is the only safe answer."""
    assert credentials.identify(document) is None


def test_the_most_specific_match_wins():
    """A document carrying several credentials' keys is the one needing most
    of them -- otherwise a stray 'licence_key' would make a Kroger file look
    like a licence."""
    document = json.dumps({
        "client_id": "fake", "client_secret": "fake", "licence_key": "fake",
    })
    assert credentials.identify(document).name == "kroger"


def test_a_case_mismatch_still_identifies():
    """Files arrive hand-edited. CLIENT_ID is the same key as client_id."""
    assert credentials.identify(
        "[kroger]\nCLIENT_ID = fake\nCLIENT_SECRET = fake\n"
    ).name == "kroger"


# --------------------------------------------------------------------------- #
# Installing
# --------------------------------------------------------------------------- #
def test_install_puts_the_document_where_the_app_reads_it(isolated):
    """The end-to-end point of the feature: after loading, fetch() works."""
    spec = credentials.install(KROGER_INI)
    assert spec.name == "kroger"
    assert credentials.fetch("kroger").strip() == KROGER_INI.strip()


def test_install_strips_a_bom(isolated):
    """The file came through a mail client and possibly Notepad. A BOM breaks
    an INI section header -- a real bug in this project (GFP-93)."""
    credentials.install("﻿" + KROGER_INI)
    written = (isolated / credentials.KROGER.filename).read_text(encoding="utf-8")
    assert not written.startswith("﻿")
    assert written.startswith("[kroger]")


def test_install_normalises_line_endings(isolated):
    credentials.install(KROGER_INI.replace("\n", "\r\n"))
    raw = (isolated / credentials.KROGER.filename).read_bytes()
    assert b"\r\n" not in raw


def test_install_refuses_a_document_it_cannot_identify(isolated):
    with pytest.raises(credentials.UnrecognisedCredentialError):
        credentials.install("this is not a credential")


def test_the_refusal_names_what_would_be_accepted(isolated):
    """The user is holding a file and has been told 'no'. Not saying what a
    valid one looks like leaves them with nowhere to go."""
    with pytest.raises(credentials.UnrecognisedCredentialError) as caught:
        credentials.install("nope")
    assert "client_id" in str(caught.value)


def test_install_replaces_an_existing_credential(isolated):
    """Unlike the installer, which must never touch a credential it did not
    place. This runs only because somebody chose a file and confirmed, and
    refusing would leave no way to replace an expired credential at all."""
    credentials.install(KROGER_INI)
    credentials.install("[kroger]\nclient_id = second\nclient_secret = second\n")
    assert "second" in credentials.fetch("kroger")


# --------------------------------------------------------------------------- #
# Provenance -- what makes the v2 removal safe (GFP-149)
# --------------------------------------------------------------------------- #
def test_installing_records_that_this_app_placed_the_file(isolated):
    credentials.install(KROGER_INI)
    assert credentials.installed_by_app("kroger") is True


def test_a_credential_the_user_placed_themselves_is_not_recorded(isolated):
    """THE WHOLE REASON PROVENANCE EXISTS. GFP-149 removes the shipped
    credential in v2, and 'delete kroger-env.config' would also delete an
    operator's own key -- silently, during an upgrade nobody thought was
    risky."""
    (isolated / credentials.KROGER.filename).write_text(KROGER_INI, encoding="utf-8")
    assert credentials.fetch("kroger")            # it works
    assert credentials.installed_by_app("kroger") is False


def test_a_corrupt_provenance_record_reports_not_installed(isolated):
    """The safe direction: it stops a later cleanup deleting something it
    should not, rather than causing one."""
    (isolated / credentials.PROVENANCE_FILENAME).write_text("{ broken", encoding="utf-8")
    credentials.install(KROGER_INI)
    (isolated / credentials.PROVENANCE_FILENAME).write_text("{ broken", encoding="utf-8")
    assert credentials.installed_by_app("kroger") is False


def test_provenance_records_no_secret(isolated):
    """It is written beside the credentials and is not itself protected."""
    credentials.install(KROGER_INI)
    record = (isolated / credentials.PROVENANCE_FILENAME).read_text(encoding="utf-8")
    assert "fake-secret" not in record
    assert "fake-id" not in record


# --------------------------------------------------------------------------- #
# No secret reaches the screen
# --------------------------------------------------------------------------- #
def test_the_dialog_module_never_renders_a_document(isolated):
    """A secret on screen is a secret in a screenshot, and this dialog exists
    to be used by someone who will be talked through it on a call.

    Checked structurally: no message box in the module may be handed the
    document or a credential value.
    """
    import pathlib

    source = pathlib.Path(
        "grocery_planner/gui/loadcredential.py"
    ).read_text(encoding="utf-8")
    # The variable holding the file contents must never appear inside a
    # QMessageBox call.
    for line in source.splitlines():
        if "QMessageBox" in line or ".setText" in line:
            assert "document" not in line, f"a document reaches a dialog: {line}"


def test_identification_reads_key_names_not_values(isolated):
    """Which is why identifying a file never requires holding its secret --
    two files with identical keys and different values identify the same."""
    a = credentials.identify(json.dumps({"wfm_store_d8": "aaaa"}))
    b = credentials.identify(json.dumps({"wfm_store_d8": "bbbb"}))
    assert a is b is credentials.WHOLEFOODS
