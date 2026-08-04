"""Credential provider seam (GFP-97): where secrets come from, in one place.

**This deliberately does NOT build a token broker.** There is one operator
today, so a hosted broker would be infrastructure with no user. What this
module does is make sure the app never *assumes* the local file is the only
possible source, so a broker can be dropped in later as a new
:class:`CredentialProvider` plus a config value rather than a refactor of
every caller.

Why a broker will eventually be needed (from the ticket, recorded here so the
reason survives): Kroger authenticates with an OAuth2 client_id/client_secret.
A secret compiled into a PyInstaller binary is recoverable with ``strings``,
and one abuser gets the key revoked for *everybody*. Kroger's 10,000 calls/day
ceiling is also **per credential**, so a shared key means every install draws
down one shared pool. At one operator neither matters. At N operators both do,
and by then the seam has to already exist.

What this module is
-------------------
- :class:`CredentialSpec` — metadata about one credential: what it is called,
  which file holds it today, which environment variable overrides that, and
  how a human gets one. Pure data, so it can be listed without reading any
  secret.
- :class:`CredentialProvider` — the seam itself. A provider answers "give me
  the credential document for this spec". Today there is exactly one
  implementation, :class:`LocalFileProvider`.
- :func:`provider` — the single selection point. A broker becomes a new class
  registered here; no caller changes.
- :func:`status` — what is configured and what is missing, *without* revealing
  any secret. This is what makes the seam earn its keep immediately: it backs
  ``gplan credentials``, which is how you diagnose a remote install without
  asking someone to read their client_secret down the phone.

Why the provider returns a *document* rather than parsed values
---------------------------------------------------------------
Each credential already has a format its consumer knows how to parse, with
error messages tuned to that format (``kroger.py`` accepts INI with or without
a section header and copes with PowerShell's UTF-8 BOM; ``wholefoods.py``
reads JSON and distinguishes "no file" from "file with no cookie"). Moving
that parsing here would either flatten those messages into something vaguer or
drag format knowledge into a module that should not care. So a provider hands
back the raw document and the consumer parses it exactly as it does today. A
future broker serialises to the same format the consumer already reads, which
keeps the contract to one method.

Secrets and logging
-------------------
Nothing here logs, prints or puts a credential value in an exception message.
:func:`status` reports presence and location only. If you add a provider, hold
that line: a secret in a log file is a secret on disk in a second place nobody
is rotating.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from .paths import data_dir

#: Selects the provider. Today ``local`` is the only implementation; the
#: variable exists so that turning a broker on later is configuration rather
#: than a code change (see :func:`provider`).
PROVIDER_ENV_VAR = "GROCERY_PLANNER_CREDENTIAL_PROVIDER"
LOCAL_PROVIDER = "local"
#: GFP-101. Fetches credentials from a hosted broker over the network. Still
#: opt-in and still not the default: a build with no broker deployed must not
#: start failing because a default changed underneath it.
BROKER_PROVIDER = "broker"


class CredentialError(RuntimeError):
    """Base class for credential resolution failures."""


class CredentialsMissingError(CredentialError):
    """The credential is not configured. Never a transient failure."""


class UnknownProviderError(CredentialError):
    """A provider was requested that this build does not implement."""


@dataclass(frozen=True)
class CredentialSpec:
    """What a credential is called, where it lives, and how to get one.

    Pure metadata — deliberately holds no secret, so the whole registry can be
    listed, logged and shown in a UI safely.
    """

    name: str
    #: The keys a caller expects inside the document. Documentation and
    #: diagnostics only; parsing belongs to the consumer (see module docstring).
    keys: tuple[str, ...]
    filename: str
    env_var: str
    #: One line telling a human how to obtain this credential.
    obtain_hint: str
    #: Whether :attr:`env_var` holds the credential ITSELF rather than a path
    #: to a file containing it. False for every credential whose document is a
    #: multi-line INI or JSON blob -- those belong in a file. True only for
    #: :data:`LICENCE`, which is one short opaque string.
    #:
    #: This is not cosmetic: without it, ``override()`` would hand a licence
    #: key to ``Path()`` and the app would look for a file named after the
    #: secret, which is both wrong and a way to leak it into an error message.
    env_holds_value: bool = False

    def override(self) -> Path | None:
        """The path this spec's environment variable points at, if set.

        Always ``None`` when the variable holds the value rather than a path.
        """
        if self.env_holds_value:
            return None
        raw = os.environ.get(self.env_var)
        return Path(raw).expanduser() if raw else None

    def env_value(self) -> str | None:
        """The credential straight from the environment, for specs that allow it."""
        if not self.env_holds_value:
            return None
        return (os.environ.get(self.env_var) or "").strip() or None


#: The credentials this app knows about. Registered here rather than in the
#: scraper modules so listing them costs no scraper imports and cannot create
#: an import cycle (a scraper imports this module, never the reverse).
KROGER = CredentialSpec(
    name="kroger",
    keys=("client_id", "client_secret"),
    filename="kroger-env.config",
    env_var="GROCERY_PLANNER_KROGER_CONFIG",
    obtain_hint=(
        "Register an application at https://developer.kroger.com and write its "
        "client_id/client_secret into that file as INI."
    ),
)
WHOLEFOODS = CredentialSpec(
    name="wholefoods",
    keys=("wfm_store_d8",),
    filename="wholefoods_session.json",
    env_var="GROCERY_PLANNER_WHOLEFOODS_SESSION",
    obtain_hint=(
        "Mint a session cookie by hand in a real browser -- see the 'Minting "
        "the session cookie' section of grocery_planner/scrapers/wholefoods.py."
    ),
)

#: The broker's OWN auth (GFP-101). Without it the broker is an open relay for
#: whatever upstream secret it holds -- anyone who found the URL could draw on
#: the shared Kroger quota, which since GFP-119 has no paid tier to escape to.
#:
#: Registered here like any other credential so it is stored, listed and
#: redacted by machinery that already exists, rather than getting its own
#: half-considered handling. Two things make it unlike the others, both handled
#: in :mod:`grocery_planner.broker`:
#:
#: * Its environment variable holds the KEY ITSELF, not a path to a file. A
#:   licence key is one short opaque string and demanding a file for it would
#:   be friction with no benefit.
#: * It is the one credential the broker can never supply, because it is what
#:   you present TO the broker. ``BrokerCredentialProvider`` reads it locally.
LICENCE = CredentialSpec(
    name="licence",
    keys=("licence_key",),
    filename="licence.json",
    env_var="GROCERY_PLANNER_LICENCE_KEY",
    obtain_hint=(
        "The key you were given with the app. It identifies this install to "
        "the credential broker."
    ),
    env_holds_value=True,
)

SPECS: dict[str, CredentialSpec] = {s.name: s for s in (KROGER, WHOLEFOODS, LICENCE)}


@runtime_checkable
class CredentialProvider(Protocol):
    """Where credential documents come from.

    A future ``BrokerProvider`` implements exactly this and is selected in
    :func:`provider`. Nothing else in the app changes.
    """

    #: Short human label for diagnostics, e.g. ``"local file"``.
    origin: str

    def fetch(self, spec: CredentialSpec) -> str | None:
        """The raw credential document, or ``None`` if not configured."""

    def describe(self, spec: CredentialSpec) -> str:
        """Where this credential is expected to come from. Never a secret."""


class LocalFileProvider:
    """Today's behaviour: a file in the per-OS user-data dir.

    The user-data dir rather than the repo or the install directory, for two
    reasons that have both already bitten this project: a file next to the
    executable is destroyed by the next update, and a file in the repo is one
    ``git add -f`` away from being published forever.
    """

    origin = "local file"

    def path(self, spec: CredentialSpec) -> Path:
        return spec.override() or (data_dir() / spec.filename)

    def fetch(self, spec: CredentialSpec) -> str | None:
        # A spec whose environment variable carries the value skips the disk
        # entirely -- there is no file to read and nothing to write one from.
        from_env = spec.env_value()
        if from_env:
            return from_env

        target = self.path(spec)
        if not target.exists():
            return None
        # utf-8-sig: PowerShell 5.1's `Out-File -Encoding utf8` writes a BOM,
        # which breaks both an INI section header and json.loads. GFP-93 was
        # exactly this bug for the Whole Foods cookie.
        return target.read_text(encoding="utf-8-sig")

    def describe(self, spec: CredentialSpec) -> str:
        if spec.env_holds_value and spec.env_value():
            return f"${spec.env_var}"
        return str(self.path(spec))


_provider: CredentialProvider | None = None


def provider() -> CredentialProvider:
    """The active provider — the one place a broker would be wired in.

    Selected by :data:`PROVIDER_ENV_VAR`. An unrecognised value raises rather
    than silently falling back to local files: someone who sets
    ``...=broker`` has said they do not want secrets read off this disk, and
    quietly doing it anyway would be the wrong way to fail.
    """
    if _provider is not None:
        return _provider
    choice = os.environ.get(PROVIDER_ENV_VAR, LOCAL_PROVIDER).strip().lower()
    if choice == LOCAL_PROVIDER:
        return LocalFileProvider()
    if choice == BROKER_PROVIDER:
        # Imported here, not at module scope: broker.py imports this module,
        # and the seam must stay usable in a build where the broker is never
        # selected.
        from .broker import BrokerCredentialProvider

        return BrokerCredentialProvider()
    raise UnknownProviderError(
        f"{PROVIDER_ENV_VAR}={choice!r} but this build implements only "
        f"{LOCAL_PROVIDER!r} and {BROKER_PROVIDER!r}."
    )


def set_provider(new: CredentialProvider | None) -> None:
    """Install a provider explicitly, overriding the environment.

    Exists for tests and for a future broker's own wiring. Pass ``None`` to
    fall back to :data:`PROVIDER_ENV_VAR` selection.
    """
    global _provider
    _provider = new


def spec(name: str) -> CredentialSpec:
    try:
        return SPECS[name]
    except KeyError:
        raise KeyError(
            f"unknown credential {name!r}; known: {sorted(SPECS)}"
        ) from None


def fetch(name: str) -> str:
    """The credential document for ``name``, or raise.

    The error names the credential, where it was looked for and how to obtain
    one — never what was found, because what was found is a secret.
    """
    target = spec(name)
    active = provider()
    document = active.fetch(target)
    if document is None:
        raise CredentialsMissingError(
            f"No {target.name} credentials at {active.describe(target)}. "
            f"{target.obtain_hint} Never commit it."
        )
    return document


@dataclass(frozen=True)
class CredentialStatus:
    """Whether one credential is configured, and where it is expected.

    Carries no secret, by construction — this is what a UI, a support request
    or a log may safely show.
    """

    name: str
    configured: bool
    location: str
    origin: str
    obtain_hint: str
    overridden: bool


def _is_configured(active: CredentialProvider, target: CredentialSpec) -> bool:
    """Whether ``target`` is set up, without letting the check itself fail.

    A provider may answer cheaply via an optional ``configured`` method -- the
    broker does, because fetching every credential merely to LIST them would
    put a network round trip in the middle of the command you run when the
    network is what is broken. Otherwise fall back to actually fetching.

    Either way a failure reports "not configured" rather than propagating:
    ``gplan credentials`` exists to diagnose a broken install and must survive
    one.
    """
    cheap = getattr(active, "configured", None)
    try:
        if callable(cheap):
            return bool(cheap(target))
        return active.fetch(target) is not None
    except CredentialError:
        return False


def _hint(active: CredentialProvider, target: CredentialSpec) -> str:
    """The provider's advice for obtaining ``target``, or the spec's own."""
    override = getattr(active, "hint", None)
    return override(target) if callable(override) else target.obtain_hint


def status(name: str | None = None) -> list[CredentialStatus]:
    """Presence and location of every known credential. Reveals no values."""
    active = provider()
    names = [name] if name else sorted(SPECS)
    out = []
    for key in names:
        target = spec(key)
        out.append(CredentialStatus(
            name=target.name,
            configured=_is_configured(active, target),
            location=active.describe(target),
            origin=active.origin,
            # A provider may override the advice, because the right remedy
            # depends on where the credential was meant to come from: "register
            # an application at developer.kroger.com" is correct for a local
            # file and wrong for a brokered customer, who registers nothing.
            obtain_hint=_hint(active, target),
            overridden=target.override() is not None,
        ))
    return out
