"""GFP-97: the credential provider seam.

The point of these tests is not that reading a file works — it did before.
It is that credential resolution now has ONE definition, that a different
provider can be substituted without touching a caller, and that nothing in
this path reveals a secret.
"""
from __future__ import annotations

import pytest

from grocery_planner import credentials
from grocery_planner.scrapers import kroger, wholefoods


@pytest.fixture(autouse=True)
def clean_provider():
    """No test may leak a provider into the next one."""
    credentials.set_provider(None)
    yield
    credentials.set_provider(None)


@pytest.fixture
def local_dir(tmp_path, monkeypatch):
    """Point every credential at an isolated directory."""
    monkeypatch.setenv(credentials.KROGER.env_var, str(tmp_path / "kroger-env.config"))
    monkeypatch.setenv(credentials.WHOLEFOODS.env_var, str(tmp_path / "wf.json"))
    return tmp_path


# --------------------------------------------------------------------------- #
# The seam itself
# --------------------------------------------------------------------------- #
def test_every_known_credential_has_a_spec():
    # 'licence' joined the registry with GFP-101: it is the broker's own auth,
    # and registering it here rather than inside broker.py means listing the
    # credentials does not depend on the broker having been imported.
    assert set(credentials.SPECS) == {"kroger", "wholefoods", "licence"}
    for spec in credentials.SPECS.values():
        assert spec.filename and spec.env_var and spec.obtain_hint
        assert spec.keys


def test_an_unknown_credential_name_is_a_clear_error():
    with pytest.raises(KeyError, match="unknown credential"):
        credentials.spec("nope")


def test_missing_credential_names_where_it_looked_and_how_to_fix(local_dir):
    with pytest.raises(credentials.CredentialsMissingError) as exc:
        credentials.fetch("kroger")
    message = str(exc.value)
    assert str(local_dir / "kroger-env.config") in message
    assert "developer.kroger.com" in message


def test_fetch_returns_the_document_verbatim(local_dir):
    (local_dir / "kroger-env.config").write_text(
        "[kroger]\nclient_id = abc\nclient_secret = xyz\n", encoding="utf-8"
    )
    assert "client_id = abc" in credentials.fetch("kroger")


def test_a_utf8_bom_does_not_break_the_document(local_dir):
    """PowerShell 5.1 writes a BOM; GFP-93 was exactly this bug."""
    (local_dir / "kroger-env.config").write_text(
        "[kroger]\nclient_id = abc\nclient_secret = xyz\n", encoding="utf-8-sig"
    )
    assert credentials.fetch("kroger").lstrip().startswith("[kroger]")


# --------------------------------------------------------------------------- #
# The substitution the whole ticket exists for
# --------------------------------------------------------------------------- #
class _FakeBroker:
    """Stands in for the token broker GFP-97 deliberately does not build."""

    origin = "test broker"

    def __init__(self, documents):
        self._documents = documents

    def fetch(self, spec):
        return self._documents.get(spec.name)

    def describe(self, spec):
        return f"broker://{spec.name}"


def test_a_different_provider_satisfies_the_protocol():
    assert isinstance(_FakeBroker({}), credentials.CredentialProvider)


def test_a_broker_can_supply_credentials_without_touching_callers(local_dir):
    """No file on disk, yet kroger.load_credentials() succeeds unchanged."""
    credentials.set_provider(_FakeBroker(
        {"kroger": "[kroger]\nclient_id = from-broker\nclient_secret = s3cret\n"}
    ))
    assert not (local_dir / "kroger-env.config").exists()

    creds = kroger.load_credentials()
    assert creds.client_id == "from-broker"
    assert creds.client_secret == "s3cret"


def test_a_broker_that_has_nothing_still_produces_the_helpful_error():
    credentials.set_provider(_FakeBroker({}))
    with pytest.raises(kroger.CredentialsMissingError) as exc:
        kroger.load_credentials()
    assert "broker://kroger" in str(exc.value)


def test_an_unimplemented_provider_refuses_rather_than_falling_back(monkeypatch):
    """Asking for a provider this build does not have must not silently read
    secrets off this disk.

    ``broker`` was the example here until GFP-101 implemented it, so the test
    now names something genuinely unimplemented. The property under test never
    changed: an unrecognised value is refused rather than quietly downgraded to
    local files, because someone who set it has said they do not want that.
    """
    monkeypatch.setenv(credentials.PROVIDER_ENV_VAR, "vault")
    with pytest.raises(credentials.UnknownProviderError, match="implements only"):
        credentials.provider()


def test_the_broker_provider_is_selectable(monkeypatch):
    """The other half of the test above: 'broker' must now resolve, or the
    seam GFP-97 built has a config value that still goes nowhere."""
    monkeypatch.setenv(credentials.PROVIDER_ENV_VAR, credentials.BROKER_PROVIDER)
    from grocery_planner.broker import BrokerCredentialProvider

    assert isinstance(credentials.provider(), BrokerCredentialProvider)


def test_the_default_provider_is_the_local_file_one(monkeypatch):
    monkeypatch.delenv(credentials.PROVIDER_ENV_VAR, raising=False)
    assert isinstance(credentials.provider(), credentials.LocalFileProvider)


# --------------------------------------------------------------------------- #
# Status reveals presence, never values
# --------------------------------------------------------------------------- #
def test_status_reports_presence_and_location(local_dir):
    (local_dir / "wf.json").write_text('{"wfm_store_d8": "cookie"}', encoding="utf-8")
    by_name = {s.name: s for s in credentials.status()}

    assert by_name["wholefoods"].configured is True
    assert by_name["kroger"].configured is False
    assert by_name["wholefoods"].location == str(local_dir / "wf.json")
    assert by_name["kroger"].overridden is True     # env var points elsewhere


def test_status_never_carries_a_secret_value(local_dir):
    secret = "super-secret-value-do-not-leak"
    (local_dir / "kroger-env.config").write_text(
        f"[kroger]\nclient_id = abc\nclient_secret = {secret}\n", encoding="utf-8"
    )
    blob = repr(credentials.status())
    assert secret not in blob
    assert "abc" not in blob


# --------------------------------------------------------------------------- #
# The scrapers still behave exactly as before
# --------------------------------------------------------------------------- #
def test_scraper_constants_come_from_the_registry_so_they_cannot_drift():
    assert kroger.CONFIG_FILENAME == credentials.KROGER.filename
    assert kroger.CONFIG_ENV_VAR == credentials.KROGER.env_var
    assert wholefoods.SESSION_FILENAME == credentials.WHOLEFOODS.filename


def test_scraper_paths_resolve_through_the_seam(local_dir):
    assert kroger.config_path() == local_dir / "kroger-env.config"
    assert wholefoods.session_path() == local_dir / "wf.json"


def test_an_explicit_path_still_bypasses_the_provider(local_dir, tmp_path):
    """`load_credentials(path)` remains the 'check this exact file' escape hatch."""
    elsewhere = tmp_path / "other.config"
    elsewhere.write_text("client_id = direct\nclient_secret = direct-secret\n",
                         encoding="utf-8")
    credentials.set_provider(_FakeBroker({"kroger": "[kroger]\nclient_id = broker\n"
                                                    "client_secret = b\n"}))
    assert kroger.load_credentials(elsewhere).client_id == "direct"


def test_readiness_still_reflects_the_local_file(local_dir):
    ready, why = kroger.readiness()
    assert ready is False and "kroger-env.config" in why

    (local_dir / "kroger-env.config").write_text(
        "client_id = a\nclient_secret = b\n", encoding="utf-8")
    assert kroger.readiness()[0] is True
