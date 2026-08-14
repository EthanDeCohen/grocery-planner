# ######### decohen-partners ##########
# Protein Ledger
"""Fetch credentials from a hosted broker instead of off this disk (GFP-101).

A customer cannot be asked to register their own Kroger application -- the
developer-portal signup is exactly the friction that made the Whole Foods
DevTools procedure unacceptable -- and a client_secret cannot be shipped inside
a binary, because a binary is not a secret. So the credential has to arrive
from somewhere at run time.

This is the CLIENT half of that: the seam, the cache, and the refresh. The
server half (SSM SecureString behind a Lambda Function URL) is infrastructure
and is a separate piece of work, against exactly this contract.

WHY OVER-THE-AIR IS THE POINT, not a nicety
--------------------------------------------
A Kroger secret can be rotated, revoked or rate-limited at any time, by Kroger
and not by us. Baked into a release, every such event needs a new build, a new
download, and every user to notice and act. Fetched at run time, the fix is
server-side and silent.

That also makes the quota manageable. The 10,000 calls/day limit is PER
CREDENTIAL, so a pool of upstream credentials is only useful if clients can be
moved between them without shipping anything -- and with the product not being
sold (GFP-119) there is no paid tier to escape to if the pool runs dry.

WHY A SILENT CREDENTIAL UPDATE IS FINE WHEN A SILENT BINARY UPDATE IS NOT
-------------------------------------------------------------------------
GFP-96 refuses to auto-install an application update, and deliberately: a
silent binary swap is a remote-code-execution path by design. This is a
different risk class. A credential is DATA the app already fetches and sends to
a third party at run time; it changes no code. The two must not later be "made
consistent" in the wrong direction.

TWO CONSTRAINTS THAT MUST SURVIVE ANY LATER CHANGE
---------------------------------------------------
* **The broker never sees client data.** It hands out credentials; it is not in
  the data path. Nothing in this module sends anything about a nutritionist's
  clients, and a test asserts the request body carries only the licence key.
* **Unreachable is an error, never a quiet fallback.** Silently reading a local
  secret when the broker cannot be reached would defeat the entire point of
  choosing the broker, and would do it invisibly.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import credentials, logs
from .paths import data_dir

log = logs.get_logger(__name__)

#: Where the broker lives. Environment-only and with no default: a build with
#: no broker configured must fail loudly when one is asked for, rather than
#: quietly pointing at somebody's guess of a URL.
BROKER_URL_ENV_VAR = "GROCERY_PLANNER_BROKER_URL"

#: Cached credential documents, keyed by credential name.
CACHE_FILENAME = "broker-cache.json"

#: How long a fetched credential is trusted when the document itself does not
#: say.
#:
#: Short enough that a rotation reaches every install within a day -- which is
#: the whole point of fetching at all -- and long enough that a broker outage
#: is survivable rather than immediately fatal.
CACHE_SECONDS = 12 * 60 * 60

#: Caching a token past its own expiry would turn a working scrape into a 401.
#: Trimmed so a token is refetched slightly before it dies rather than exactly
#: as it does -- the request it is used for takes time to arrive.
EXPIRY_MARGIN_SECONDS = 60.0

#: A broker that is slow is a scrape that appears to hang. The credential is
#: fetched before any store request, so this delay is paid up front.
TIMEOUT_SECONDS = 10.0

#: WHICH CREDENTIALS THE BROKER SUPPLIES. Selecting this provider does not
#: route everything through the network, and that is a product decision rather
#: than an oversight:
#:
#: * **Kroger is brokered.** Its credential is an OAuth2 client_id/secret tied
#:   to a developer-portal application. A customer cannot be asked to register
#:   one, and a shared secret must not sit on their disk.
#: * **Whole Foods is NOT brokered.** Its credential is a session cookie that
#:   is minted in a browser and is specific to the ZIP the user shops in
#:   (GFP-80). There is nothing central to hand out -- a cookie minted here
#:   would be for the wrong store -- so it stays a local file that the user
#:   creates from inside the app.
#: * **The licence is NOT brokered.** It is what this install presents TO the
#:   broker; asking the broker for it is a loop.
#:
#: Anything absent from this set is read locally. That is routing decided in
#: advance, not a fallback when the broker fails -- see the module docstring.
BROKERED = frozenset({"kroger"})


class BrokerError(credentials.CredentialError):
    """The broker could not supply a credential.

    A CredentialError rather than a bare RuntimeError so that callers which
    already handle "credentials did not work" keep handling it when the source
    changes -- which was the entire promise of the GFP-97 seam.
    """


class BrokerNotConfigured(BrokerError):
    """No broker URL is set in this build or environment."""


class LicenceMissing(BrokerError):
    """No licence key, so the broker cannot be asked for anything.

    Distinct from a network failure because the remedy is completely
    different: the user has to enter something, not wait.
    """


def broker_url() -> str:
    url = (os.environ.get(BROKER_URL_ENV_VAR) or "").strip()
    if not url:
        raise BrokerNotConfigured(
            "No credential broker is configured for this build "
            f"({BROKER_URL_ENV_VAR} is not set), so credentials cannot be "
            "fetched. Set a local credential file instead, or configure a "
            "broker."
        )
    return url


def cache_path() -> Path:
    return data_dir() / CACHE_FILENAME


# --------------------------------------------------------------------------- #
# The cache
# --------------------------------------------------------------------------- #
def _read_cache() -> dict[str, Any]:
    """Never raises. A corrupt cache is a cache miss, not a failure."""
    try:
        raw = cache_path().read_text(encoding="utf-8-sig")
        parsed = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _write_cache(cache: dict[str, Any]) -> None:
    try:
        target = cache_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(cache, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        # Losing the cache costs one extra fetch, never correctness.
        log.debug("could not write the broker cache: %s", exc)


def ttl_for(document: str) -> float:
    """How long ``document`` may be cached, honouring what it declares.

    A brokered Kroger credential is an OAuth2 token document, and those live
    30 minutes. Caching one for :data:`CACHE_SECONDS` would hand out a dead
    token for eleven and a half hours and surface as an unexplained 401 in the
    middle of a scrape. So an ``expires_in`` in the document wins over the
    default, and a document that declares nothing (an INI credential file, a
    cookie) gets the default.
    """
    try:
        parsed = json.loads(document)
    except (json.JSONDecodeError, TypeError):
        return CACHE_SECONDS
    if not isinstance(parsed, dict):
        return CACHE_SECONDS
    declared = parsed.get("expires_in")
    if not isinstance(declared, (int, float)) or isinstance(declared, bool):
        return CACHE_SECONDS
    # Never negative: a broker returning a nonsense expiry should cause a
    # refetch every time, not a cache entry that is treated as eternally fresh.
    return max(0.0, min(float(declared) - EXPIRY_MARGIN_SECONDS, CACHE_SECONDS))


def cached(name: str, now: float | None = None) -> str | None:
    """A still-fresh cached document for ``name``, or ``None``."""
    entry = _read_cache().get(name)
    if not isinstance(entry, dict):
        return None
    fetched = entry.get("fetched_at")
    document = entry.get("document")
    ttl = entry.get("ttl")
    if not isinstance(document, str) or not isinstance(fetched, (int, float)):
        return None
    if not isinstance(ttl, (int, float)):
        ttl = CACHE_SECONDS
    if (now if now is not None else time.time()) - fetched > ttl:
        return None
    return document


def remember(name: str, document: str, now: float | None = None) -> None:
    cache = _read_cache()
    cache[name] = {
        "document": document,
        "fetched_at": now if now is not None else time.time(),
        "ttl": ttl_for(document),
    }
    _write_cache(cache)


def forget(name: str | None = None) -> None:
    """Drop the cache, so the next call fetches.

    ``name=None`` drops everything. This is what makes a rotation take effect
    immediately rather than within CACHE_SECONDS -- `gplan credentials
    --refresh` calls it.
    """
    if name is None:
        _write_cache({})
        return
    cache = _read_cache()
    cache.pop(name, None)
    _write_cache(cache)


# --------------------------------------------------------------------------- #
# The provider
# --------------------------------------------------------------------------- #
@dataclass
class BrokerCredentialProvider:
    """Fetches credential documents from the broker, with a cache.

    Satisfies the same :class:`credentials.CredentialProvider` protocol as
    :class:`credentials.LocalFileProvider`, so selecting it changes nothing
    else in the app -- which is what GFP-97's seam was built for.
    """

    origin: str = "credential broker"
    #: Injected so tests do not need a network, and so a future transport
    #: change is one argument rather than a rewrite.
    transport: Any = None

    def fetch(self, spec: credentials.CredentialSpec) -> str | None:
        """The raw credential document, or ``None`` if the broker has none.

        ``None`` means "the broker answered, and does not hold this one" --
        Whole Foods, for instance, is deliberately not brokered (its cookie is
        browser-minted per ZIP, GFP-80). A broker that could not be REACHED
        raises instead, because those are different situations with different
        remedies and conflating them is how a silent fallback gets written.
        """
        if spec.name not in BROKERED:
            return credentials.LocalFileProvider().fetch(spec)

        # An environment override still wins, exactly as it does for local
        # files. Someone debugging with their own credential should not have
        # to reconfigure the provider to do it.
        override = spec.override()
        if override is not None and override.exists():
            log.debug("%s: using the %s override", spec.name, spec.env_var)
            return override.read_text(encoding="utf-8-sig")

        document = cached(spec.name)
        if document is not None:
            return document

        document = self._fetch_remote(spec)
        if document is not None:
            remember(spec.name, document)
        return document

    def describe(self, spec: credentials.CredentialSpec) -> str:
        if spec.name not in BROKERED:
            return credentials.LocalFileProvider().describe(spec)
        try:
            return f"{broker_url()} (cached at {cache_path()})"
        except BrokerNotConfigured:
            # describe() backs `gplan credentials`, which is the command
            # somebody runs precisely BECAUSE something is misconfigured. It
            # must never be the thing that raises.
            return f"credential broker (not configured: {BROKER_URL_ENV_VAR} is unset)"

    def hint(self, spec: credentials.CredentialSpec) -> str:
        """How to fix a missing credential, given that it is brokered.

        The spec's own hint says to register an application at
        developer.kroger.com -- which is true, and is exactly the friction
        brokering exists to remove. Telling a customer to do it would be
        actively wrong advice.
        """
        if spec.name not in BROKERED:
            return spec.obtain_hint
        if not licence_key():
            return (
                "Set your licence key: gplan credentials --set-licence <key>. "
                "This credential is supplied for you; you do not register "
                "anything yourself."
            )
        return (
            "The licence key is set, so this should be supplied automatically. "
            f"If a scrape still fails, check that {BROKER_URL_ENV_VAR} points "
            "at a reachable broker, then run: gplan credentials --refresh"
        )

    def configured(self, spec: credentials.CredentialSpec) -> bool:
        """Whether this install is SET UP to obtain ``spec``. No network.

        ``gplan credentials`` lists every credential, and answering it by
        actually fetching each one would mean a network round trip per row --
        on the one command somebody runs when the network is the thing that is
        broken. So this answers the question that can be answered locally: is
        there a broker, a licence, and no reason this would not work.

        It deliberately does not claim the fetch would SUCCEED -- only that
        nothing is missing at this end.
        """
        if spec.name not in BROKERED:
            return credentials.LocalFileProvider().fetch(spec) is not None
        override = spec.override()
        if override is not None and override.exists():
            return True
        if cached(spec.name) is not None:
            return True
        try:
            broker_url()
        except BrokerNotConfigured:
            return False
        return bool(licence_key())

    # ------------------------------------------------------------------ #
    def _fetch_remote(self, spec: credentials.CredentialSpec) -> str | None:
        licence = licence_key()
        if not licence:
            raise LicenceMissing(
                "No licence key is set, so the credential broker cannot be "
                "asked for anything. Enter the key you were given: "
                "gplan credentials --set-licence <key>"
            )

        send = self.transport or _http_post
        try:
            status, body = send(
                broker_url(),
                # THE ENTIRE REQUEST BODY. The broker is not in the data path
                # and must never see anything about a nutritionist's clients;
                # a test asserts these are the only two keys.
                {"licence_key": licence, "credential": spec.name},
                TIMEOUT_SECONDS,
            )
        except Exception as exc:            # noqa: BLE001
            raise BrokerError(
                "Could not reach the credential broker, so prices cannot be "
                "fetched right now. This is usually temporary; your stored "
                f"prices are untouched. ({type(exc).__name__})"
            ) from exc

        if status == 404:
            return None                     # answered; does not hold this one
        if status in (401, 403):
            raise BrokerError(
                "The credential broker rejected this licence key. Check the "
                "key, or ask for a new one -- keys can be revoked."
            )
        if status != 200:
            raise BrokerError(
                f"The credential broker returned an unexpected response "
                f"({status}). Prices cannot be fetched right now."
            )
        return body


def _http_post(url: str, payload: dict[str, str], timeout: float) -> tuple[int, str]:
    """The default transport. Separated so tests never touch a network."""
    import httpx

    response = httpx.post(url, json=payload, timeout=timeout)
    return response.status_code, response.text


# --------------------------------------------------------------------------- #
# The licence key
# --------------------------------------------------------------------------- #
#: The spec lives in :mod:`credentials` with every other credential, so that
#: listing them costs nothing and does not depend on this module having been
#: imported. Re-exported here because this is where it is used.
LICENCE = credentials.LICENCE


def licence_key() -> str | None:
    """This install's licence key, or ``None``.

    Read through the ordinary local provider -- never through :func:`provider`,
    which may BE the broker. Two document shapes are accepted because the key
    legitimately arrives two ways: bare from the environment variable, or as
    ``{"licence_key": ...}`` from the file :func:`set_licence_key` writes.
    """
    document = credentials.LocalFileProvider().fetch(LICENCE)
    if not document:
        return None
    text = document.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text or None            # the bare key, straight from the env
    if not isinstance(parsed, dict):
        return None
    value = parsed.get("licence_key")
    return str(value).strip() or None if value else None


def set_licence_key(key: str) -> Path:
    """Store the licence key. Returns where it was written."""
    value = (key or "").strip()
    if not value:
        raise LicenceMissing("A licence key cannot be empty.")
    target = data_dir() / LICENCE.filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"licence_key": value}, indent=2) + "\n", encoding="utf-8"
    )
    # A new key may map to a different upstream credential, so anything
    # cached under the old one is no longer trustworthy.
    forget()
    return target

