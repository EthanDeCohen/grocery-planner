"""The credential broker client (GFP-101).

Every test here runs against an injected transport. None of them touch a
network, and one of them exists specifically to prove that a *product* code
path does not either.

Two properties are load-bearing and are asserted structurally rather than
described in a comment, because both are the kind of thing a later refactor
breaks without noticing:

* **The broker never sees client data.** It hands out credentials; it is not in
  the data path. The request body is asserted key-by-key.
* **Unreachable is an error, never a quiet fallback.** Reading a local secret
  because the broker was down would defeat the whole reason for choosing the
  broker, and would do it invisibly.
"""
from __future__ import annotations

import json

import pytest

from grocery_planner import broker, credentials
from grocery_planner.scrapers import kroger

LICENCE = "licence-key-under-test"
TOKEN_DOC = json.dumps({"access_token": "brokered-token", "expires_in": 1800})


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """A data dir and an environment of our own.

    ``data_dir`` is patched where each module looked it up, not just at its
    definition -- both import it by name, so patching only ``paths.data_dir``
    would leave the real user profile in play and this suite would write a
    licence file into the developer's actual install.
    """
    monkeypatch.setattr(broker, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(credentials, "data_dir", lambda: tmp_path)
    for var in (
        broker.BROKER_URL_ENV_VAR,
        credentials.PROVIDER_ENV_VAR,
        credentials.LICENCE.env_var,
        credentials.KROGER.env_var,
    ):
        monkeypatch.delenv(var, raising=False)
    credentials.set_provider(None)
    yield tmp_path
    credentials.set_provider(None)


@pytest.fixture
def configured(monkeypatch, isolated):
    monkeypatch.setenv(broker.BROKER_URL_ENV_VAR, "https://broker.example/creds")
    broker.set_licence_key(LICENCE)
    return isolated


class FakeTransport:
    """Records what it was asked, answers what it was told to."""

    def __init__(self, status=200, body=TOKEN_DOC, raises=None):
        self.status, self.body, self.raises = status, body, raises
        self.calls: list[tuple[str, dict, float]] = []

    def __call__(self, url, payload, timeout):
        self.calls.append((url, payload, timeout))
        if self.raises is not None:
            raise self.raises
        return self.status, self.body


# --------------------------------------------------------------------------- #
# The two constraints from the module docstring
# --------------------------------------------------------------------------- #
def test_the_request_carries_only_the_licence_and_the_credential_name(configured):
    """THE PRIVACY CONSTRAINT. The broker is not in the data path, and must
    never learn anything about a nutritionist's clients."""
    transport = FakeTransport()
    broker.BrokerCredentialProvider(transport=transport).fetch(credentials.KROGER)

    (_url, payload, _timeout) = transport.calls[0]
    assert set(payload) == {"licence_key", "credential"}
    assert payload["credential"] == "kroger"


def test_an_unreachable_broker_raises_rather_than_reading_a_local_secret(configured, tmp_path):
    """THE NO-FALLBACK CONSTRAINT.

    A local Kroger file is deliberately present. Choosing the broker is a
    statement that secrets are not read off this disk, so a network failure
    must surface -- silently using the local file would be the wrong way to
    succeed.
    """
    (tmp_path / credentials.KROGER.filename).write_text(
        "[kroger]\nclient_id = local\nclient_secret = local\n", encoding="utf-8"
    )
    transport = FakeTransport(raises=OSError("connection refused"))

    with pytest.raises(broker.BrokerError):
        broker.BrokerCredentialProvider(transport=transport).fetch(credentials.KROGER)


# --------------------------------------------------------------------------- #
# What is brokered, and what deliberately is not
# --------------------------------------------------------------------------- #
def test_wholefoods_is_never_fetched_from_the_broker(configured, tmp_path):
    """The Kroger-only decision, made explicit.

    A Whole Foods cookie is minted in a browser and is specific to the ZIP the
    user shops in, so there is nothing central to hand out -- a brokered one
    would be for the wrong store.
    """
    (tmp_path / credentials.WHOLEFOODS.filename).write_text(
        '{"wfm_store_d8": "cookie"}', encoding="utf-8"
    )
    transport = FakeTransport()
    document = broker.BrokerCredentialProvider(transport=transport).fetch(
        credentials.WHOLEFOODS
    )
    assert "cookie" in document
    assert transport.calls == []


def test_the_licence_is_never_fetched_from_the_broker(configured):
    """It is what this install presents TO the broker. Asking the broker for
    it would recurse: fetch -> _fetch_remote -> licence_key -> fetch."""
    transport = FakeTransport()
    document = broker.BrokerCredentialProvider(transport=transport).fetch(
        credentials.LICENCE
    )
    assert LICENCE in document
    assert transport.calls == []


def test_listing_credentials_makes_no_network_call(configured, monkeypatch):
    """`gplan credentials` is the command somebody runs BECAUSE something is
    broken, frequently the network. It must not need one."""
    transport = FakeTransport()
    monkeypatch.setenv(credentials.PROVIDER_ENV_VAR, credentials.BROKER_PROVIDER)
    credentials.set_provider(broker.BrokerCredentialProvider(transport=transport))

    entries = credentials.status()
    assert transport.calls == []
    assert {e.name for e in entries} == set(credentials.SPECS)


def test_listing_survives_an_unconfigured_broker(isolated, monkeypatch):
    """No URL, no licence -- and the diagnostic command still prints instead of
    raising in the user's face."""
    monkeypatch.setenv(credentials.PROVIDER_ENV_VAR, credentials.BROKER_PROVIDER)
    entries = credentials.status()
    kroger_row = next(e for e in entries if e.name == "kroger")
    assert not kroger_row.configured
    assert "not configured" in kroger_row.location


# --------------------------------------------------------------------------- #
# Errors say which remedy applies
# --------------------------------------------------------------------------- #
def test_no_licence_is_a_distinct_error_from_a_network_failure(monkeypatch, isolated):
    """The remedies are completely different -- enter something, versus wait --
    so conflating them would send a user to the wrong one."""
    monkeypatch.setenv(broker.BROKER_URL_ENV_VAR, "https://broker.example/creds")
    transport = FakeTransport()

    with pytest.raises(broker.LicenceMissing):
        broker.BrokerCredentialProvider(transport=transport).fetch(credentials.KROGER)
    assert transport.calls == []


def test_no_broker_configured_is_its_own_error(isolated):
    with pytest.raises(broker.BrokerNotConfigured):
        broker.broker_url()


def test_a_rejected_licence_says_so(configured):
    transport = FakeTransport(status=403, body="nope")
    with pytest.raises(broker.BrokerError, match="licence"):
        broker.BrokerCredentialProvider(transport=transport).fetch(credentials.KROGER)


def test_a_credential_the_broker_does_not_hold_is_none_not_an_error(configured):
    """404 means "answered, and does not have this one" -- a different
    situation from "could not be reached", with a different remedy."""
    transport = FakeTransport(status=404, body="")
    assert broker.BrokerCredentialProvider(transport=transport).fetch(
        credentials.KROGER
    ) is None


def test_a_broker_error_is_a_credential_error(configured):
    """So callers that already handle "credentials did not work" keep handling
    it when the source changes -- the promise the GFP-97 seam made."""
    assert issubclass(broker.BrokerError, credentials.CredentialError)


# --------------------------------------------------------------------------- #
# The cache
# --------------------------------------------------------------------------- #
def test_a_second_fetch_uses_the_cache(configured):
    provider = broker.BrokerCredentialProvider(transport=FakeTransport())
    provider.fetch(credentials.KROGER)
    transport = FakeTransport()
    provider.transport = transport
    assert provider.fetch(credentials.KROGER) is not None
    assert transport.calls == []


def test_a_token_is_not_cached_past_its_own_expiry():
    """A 30-minute token cached for 12 hours is a 401 in the middle of a
    scrape, eleven and a half hours from now, with no explanation."""
    assert broker.ttl_for(TOKEN_DOC) == pytest.approx(1800 - broker.EXPIRY_MARGIN_SECONDS)


def test_a_document_with_no_expiry_gets_the_default():
    """An INI credential file does not expire on its own."""
    assert broker.ttl_for("[kroger]\nclient_id = x") == broker.CACHE_SECONDS


def test_a_nonsense_expiry_refetches_rather_than_never_expiring(configured):
    assert broker.ttl_for(json.dumps({"access_token": "x", "expires_in": -99})) == 0.0


def test_an_expired_entry_is_a_miss(configured):
    broker.remember("kroger", TOKEN_DOC, now=0.0)
    assert broker.cached("kroger", now=0.0) is not None
    assert broker.cached("kroger", now=1_000_000.0) is None


def test_a_corrupt_cache_is_a_miss_not_a_crash(configured, tmp_path):
    (tmp_path / broker.CACHE_FILENAME).write_text("{not json", encoding="utf-8")
    assert broker.cached("kroger") is None


def test_setting_a_new_licence_drops_the_cache(configured):
    """A new key may map to a different upstream credential, so anything held
    under the old one is no longer trustworthy."""
    broker.remember("kroger", TOKEN_DOC)
    broker.set_licence_key("a-different-key")
    assert broker.cached("kroger") is None
    assert broker.licence_key() == "a-different-key"


def test_refresh_drops_the_cache(configured):
    """What makes an upstream rotation take effect now rather than in 12
    hours -- the reason `gplan credentials --refresh` exists."""
    broker.remember("kroger", TOKEN_DOC)
    broker.forget()
    assert broker.cached("kroger") is None


def test_no_secret_is_written_to_the_licence_file_in_the_clear_by_accident(configured, tmp_path):
    """Not encryption -- just that the cache and the licence stay separate
    files, so `--set-licence` cannot overwrite cached credentials."""
    broker.remember("kroger", TOKEN_DOC)
    assert (tmp_path / broker.CACHE_FILENAME).exists()
    assert (tmp_path / credentials.LICENCE.filename).exists()


# --------------------------------------------------------------------------- #
# The licence key itself
# --------------------------------------------------------------------------- #
def test_the_environment_variable_holds_the_key_itself_not_a_path(monkeypatch, isolated):
    """Unlike every other credential override. A licence key is one short
    opaque string and demanding a file for it would be friction with no gain.
    """
    monkeypatch.setenv(credentials.LICENCE.env_var, "key-from-env")
    assert broker.licence_key() == "key-from-env"
    # And the spec must not then try to treat that key as a filename.
    assert credentials.LICENCE.override() is None


def test_an_empty_licence_key_is_refused(isolated):
    with pytest.raises(broker.LicenceMissing):
        broker.set_licence_key("   ")


# --------------------------------------------------------------------------- #
# The Kroger client end of it
# --------------------------------------------------------------------------- #
def test_a_brokered_token_skips_the_oauth_exchange(configured, monkeypatch):
    """THE POINT OF BROKERING A TOKEN RATHER THAN A SECRET.

    The client_secret stays on the broker, so there is nothing on this machine
    to exchange -- and the client must not try. If it did, the failure would be
    a confusing 401 from Kroger rather than anything nameable.
    """
    import httpx

    # The exchange is an httpx POST to TOKEN_URL, so THAT is what has to be
    # proved not to happen. Patching a higher-level method would pass whether
    # or not the code under test was correct.
    monkeypatch.setattr(
        httpx.Client, "post",
        lambda *a, **k: pytest.fail("a brokered client must not exchange credentials"),
    )
    client = kroger.KrogerClient(kroger.BearerToken(access_token="brokered-token"))
    try:
        assert client.token() == "brokered-token"
    finally:
        client.close()


def test_a_local_client_still_does_exchange_credentials(isolated, monkeypatch):
    """The mirror of the test above -- without it, deleting the whole OAuth2
    path would leave the suite green."""
    import httpx

    posts: list[str] = []

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"access_token": "exchanged"}

    monkeypatch.setattr(
        httpx.Client, "post", lambda self, url, **k: (posts.append(url), Response())[1]
    )
    client = kroger.KrogerClient(kroger.Credentials("id", "secret"))
    try:
        assert client.token() == "exchanged"
    finally:
        client.close()
    assert posts == [kroger.TOKEN_URL]


def test_load_auth_reads_a_token_document_as_a_token(configured, monkeypatch):
    monkeypatch.setenv(credentials.PROVIDER_ENV_VAR, credentials.BROKER_PROVIDER)
    credentials.set_provider(
        broker.BrokerCredentialProvider(transport=FakeTransport(body=TOKEN_DOC))
    )
    auth = kroger.load_auth()
    assert isinstance(auth, kroger.BearerToken)
    assert auth.access_token == "brokered-token"


def test_load_auth_still_reads_an_ini_document_as_credentials(isolated, tmp_path):
    """The local path is unchanged. Nothing about this ticket may alter what
    an existing install does."""
    config = tmp_path / "kroger-env.config"
    config.write_text(
        "[kroger]\nclient_id = abc\nclient_secret = def\n", encoding="utf-8"
    )
    auth = kroger.load_auth(config)
    assert isinstance(auth, kroger.Credentials)
    assert auth.client_id == "abc"


def test_an_ini_file_is_not_mistaken_for_a_token():
    assert kroger._as_token("[kroger]\nclient_id = x") is None
    assert kroger._as_token('{"something_else": 1}') is None


def test_an_unreachable_broker_does_not_read_as_missing_credentials(configured, monkeypatch):
    """"Nothing is configured" sends a user off to set something up. The right
    advice for a broker outage is to try again, so these must not collapse
    into one error."""
    monkeypatch.setenv(credentials.PROVIDER_ENV_VAR, credentials.BROKER_PROVIDER)
    credentials.set_provider(
        broker.BrokerCredentialProvider(
            transport=FakeTransport(raises=OSError("connection refused"))
        )
    )
    with pytest.raises(kroger.KrogerError) as caught:
        kroger.load_auth()
    assert not isinstance(caught.value, kroger.CredentialsMissingError)


def test_load_credentials_refuses_a_token_with_a_useful_message(configured, monkeypatch):
    """The old function still exists and other callers use it. Handed a token
    it must say what to do, not fall over parsing JSON as INI."""
    monkeypatch.setenv(credentials.PROVIDER_ENV_VAR, credentials.BROKER_PROVIDER)
    credentials.set_provider(
        broker.BrokerCredentialProvider(transport=FakeTransport(body=TOKEN_DOC))
    )
    with pytest.raises(kroger.CredentialsMissingError, match="load_auth"):
        kroger.load_credentials()


# --------------------------------------------------------------------------- #
# Provider selection
# --------------------------------------------------------------------------- #
def test_the_default_provider_is_still_local(isolated):
    """Nothing switches to the broker on its own. A build with no broker
    deployed must not start failing because a default moved."""
    assert isinstance(credentials.provider(), credentials.LocalFileProvider)


def test_an_unknown_provider_still_raises(monkeypatch, isolated):
    monkeypatch.setenv(credentials.PROVIDER_ENV_VAR, "vault")
    with pytest.raises(credentials.UnknownProviderError):
        credentials.provider()
