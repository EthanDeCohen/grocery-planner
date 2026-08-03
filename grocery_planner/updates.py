"""Tell the user a newer version exists. Nothing more (GFP-96).

The app is a binary downloaded from a GitHub page, so there is no package
manager to notice a stale copy. Without this an install silently rots: someone
keeps running a build whose scraper broke months ago against a source that has
since changed shape.

That is not hypothetical. GFP-93 was exactly it -- Whole Foods changed its
session cookie encoding under a shipped decoder, and the only symptom was an
error telling the user to do something that could not work. Somebody on an old
binary had no way to learn that a fix existed.

FOUR RULES, all from the ticket and all with teeth here.

**It never installs anything.** It reports, links the page, and stops. A silent
self-update is a remote-code-execution path by design, and on a tool holding
health-adjacent data about third parties that is a much bigger trust ask than
on a text editor. This module has no code that writes an executable, and it
should stay that way.

**It never raises.** An update check that produces an error because the wifi is
down is worse than no update check. Every failure -- offline, DNS, a rate
limit, GitHub returning something unexpected, a corrupt state file -- returns
``None`` and is logged at DEBUG. The caller shows something only when there is
genuinely something to show.

**Once a day at most**, tracked in a small state file next to the database.
Unauthenticated GitHub allows 60 requests an hour per IP, so this is nowhere
near a limit -- the cap is about not making a network call every time somebody
opens the app.

**Opt-out**, via GFP-85's config. This is the first outbound call the app makes
that is not a store scrape, and the user should be able to say no to it
separately from saying no to everything.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import __version__, config, logs
from .paths import data_dir

#: Unauthenticated and public. 60 requests/hour per IP is ample for a
#: once-a-day check, and requiring a token would mean shipping one.
LATEST_RELEASE_URL = (
    "https://api.github.com/repos/EthanDeCohen/grocery-planner/releases/latest"
)

#: Where the user is sent. Deliberately the human page, not a binary URL --
#: this module hands over to a browser and a person, never to a downloader.
RELEASES_PAGE = "https://github.com/EthanDeCohen/grocery-planner/releases/latest"

STATE_FILENAME = "update-check.json"

#: Short. This runs on app startup, and a slow check must not become a slow
#: launch -- the whole thing is optional and the user is waiting.
TIMEOUT_SECONDS = 5.0

#: A whole-string version match. `v` prefix optional, at least two
#: dot-separated numbers, an optional pre-release/build suffix that is
#: discarded. Anything else is not a version.
_VERSION_RE = re.compile(r"^[vV]?(\d+(?:\.\d+)+)(?:[-+].*)?$")

log = logs.get_logger(__name__)


@dataclass(frozen=True)
class Update:
    """A newer release than the one running."""

    current: str
    latest: str
    url: str

    @property
    def message(self) -> str:
        """One line, for a status bar. No exclamation marks, no urgency."""
        return f"Version {self.latest} is available (you have {self.current})."


def state_path() -> Path:
    return data_dir() / STATE_FILENAME


# --------------------------------------------------------------------------- #
# Version comparison
# --------------------------------------------------------------------------- #
def parse_version(text: str) -> tuple[int, ...] | None:
    """``"v1.2.3"`` -> ``(1, 2, 3)``, or ``None`` if it is not a version.

    Deliberately strict about what it accepts and silent about what it does
    not. A tag someone pushed as ``"nightly"`` or ``"2026-08-03-hotfix"`` must
    make this return None -- and therefore report no update -- rather than
    guess a number out of it. Claiming an update exists when it does not sends
    a user to a download page for nothing; missing one costs a day.
    """
    if not text:
        return None
    # Matched as a WHOLE, not split-then-salvaged. Splitting on "-" first and
    # keeping the head turns the tag `2026-08-03-hotfix` into version 2026 --
    # which is newer than everything, so every user is told forever that an
    # update exists. Found by a test, and it is the worst failure this
    # function has: wrong in the direction that nags.
    #
    # At least two dot-separated numbers are required, so a bare `v3` is
    # refused too. A pre-release or build suffix is allowed and DISCARDED:
    # 1.2.3-rc1 compares as 1.2.3, because offering somebody a release
    # candidate as though it were a release is worse than staying quiet.
    match = _VERSION_RE.match(text.strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(latest: str, current: str) -> bool:
    """Is ``latest`` a higher version than ``current``?

    False whenever either is unparseable. An unknown version is not an excuse
    to nag somebody.
    """
    a, b = parse_version(latest), parse_version(current)
    if a is None or b is None:
        return False
    # Pad so (1, 2) and (1, 2, 0) compare equal rather than by length.
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


# --------------------------------------------------------------------------- #
# The once-a-day state
# --------------------------------------------------------------------------- #
def _read_state() -> dict:
    try:
        raw = state_path().read_text(encoding="utf-8-sig")
        parsed = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _write_state(state: dict) -> None:
    try:
        target = state_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        # Losing the state file costs one extra HTTP request tomorrow.
        log.debug("could not write %s: %s", STATE_FILENAME, exc)


def checked_today(today: date | None = None) -> bool:
    stamp = _read_state().get("last_checked")
    if not stamp:
        return False
    try:
        return date.fromisoformat(stamp) == (today or date.today())
    except ValueError:
        return False


def mark_checked(latest: str | None, today: date | None = None) -> None:
    state = _read_state()
    state["last_checked"] = (today or date.today()).isoformat()
    if latest:
        state["last_seen_version"] = latest
    _write_state(state)


# --------------------------------------------------------------------------- #
# The check
# --------------------------------------------------------------------------- #
def _fetch_latest_tag() -> str | None:
    """The latest published release tag, or None. Never raises."""
    try:
        import httpx
    except ImportError:                                   # pragma: no cover
        return None
    try:
        response = httpx.get(
            LATEST_RELEASE_URL,
            timeout=TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={
                "Accept": "application/vnd.github+json",
                # Identifying the app is politeness, and GitHub asks for it.
                # It carries the version, which is not sensitive -- unlike the
                # postal code, which must never leave the machine.
                "User-Agent": f"grocery-planner/{__version__}",
            },
        )
        if response.status_code != 200:
            # 404 is the normal state before the first release is published,
            # and 403 is the rate limit. Neither is worth telling a user about.
            log.debug("update check: HTTP %s", response.status_code)
            return None
        tag = response.json().get("tag_name")
    except Exception as exc:                # noqa: BLE001 -- see the docstring
        # Offline, DNS, TLS, a proxy, malformed JSON. All the same to a user
        # who did not ask for this and should not hear about it.
        log.debug("update check failed: %s: %s", type(exc).__name__, exc)
        return None
    return tag if isinstance(tag, str) else None


def check(force: bool = False, today: date | None = None) -> Update | None:
    """An :class:`Update` if a newer release exists, else ``None``.

    ``force`` skips the once-a-day gate but not the config opt-out -- a user
    who turned this off means it, and ``gplan update`` should say so rather
    than quietly doing it anyway.
    """
    if not config.get("update_check"):
        log.debug("update check disabled by config")
        return None
    if not force and checked_today(today):
        log.debug("update already checked today")
        return None

    tag = _fetch_latest_tag()
    mark_checked(tag, today)
    if tag is None:
        return None
    if not is_newer(tag, __version__):
        log.debug("running %s, latest is %s -- up to date", __version__, tag)
        return None

    log.info("update available: %s (running %s)", tag, __version__)
    return Update(current=__version__, latest=tag.lstrip("vV"), url=RELEASES_PAGE)


def check_quietly(today: date | None = None) -> Update | None:
    """:func:`check`, guaranteed not to raise for any reason at all.

    The startup path uses this. :func:`check` is already written not to raise,
    but this is the call sitting between a user and their app opening, and a
    bug in *this* module must not be why the app fails to start.
    """
    try:
        return check(today=today)
    except Exception as exc:                # noqa: BLE001
        log.debug("update check raised: %s: %s", type(exc).__name__, exc)
        return None
