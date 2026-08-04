"""Global settings, in one JSON file the user can edit (GFP-85).

The concrete problem this starts with: **27401 is hard-coded in four places** --
both Flipp ``StoreConfig``s in ``scrapers/base.py``, ``kroger.DEFAULT_POSTAL_CODE``
and ``wholefoods.DEFAULT_POSTAL_CODE``. A nutritionist in another city currently
has to edit source code. (The ticket said three; GFP-98 added the fourth.)

Where it lives
--------------
``<user-data-dir>/config.json`` -- the folder ``gplan db-path`` prints, next to
the database. Same directory as the credentials, resolved through
``paths.data_dir()`` so there is one definition of "the user's data lives here".

**The Whole Foods session cookie deliberately does NOT move in here.** It is
credential-shaped, store-specific and per-ZIP (GFP-83), and it belongs to the
GFP-97 credential seam, not to general settings.

Precedence: environment > file > built-in default
-------------------------------------------------
Which keeps the existing ``GROCERY_PLANNER_DB`` pattern working, gives CI and
debugging an override that needs no file, and means a broken config file can
always be worked around without editing it.

Never crashing is a hard requirement
------------------------------------
A hand-edited JSON file WILL eventually have a trailing comma in it, and the
app must still start. So:

* A missing file is normal, not an error -- defaults apply and the file is
  written on first use.
* Malformed JSON falls back to defaults for EVERY key, and records a problem.
* A single bad value falls back for THAT KEY ONLY, and records a problem naming
  it. One bad ZIP must not also cost you your logging settings.
* Problems are reported through :func:`problems`, never printed from here --
  this module is imported by the GUI, and a library that prints owns a decision
  it should not.

That last rule is why loading returns a result rather than raising: the caller
decides whether a bad key is a warning in a status bar or a red line in a
terminal.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .paths import data_dir

CONFIG_FILENAME = "config.json"

#: Override the whole file's location, for tests and for an operator running
#: several configurations against one install.
CONFIG_ENV_VAR = "GROCERY_PLANNER_CONFIG"


class SettingError(ValueError):
    """A value that cannot be used. Carries the key so a message can name it."""

    def __init__(self, key: str, value: Any, expected: str) -> None:
        super().__init__(
            f"{key}: {value!r} is not valid — expected {expected}."
        )
        self.key = key
        self.value = value
        self.expected = expected


# --------------------------------------------------------------------------- #
# Value parsers. Each RAISES SettingError with a human explanation, which is
# what makes "name the bad key" possible rather than a generic parse failure.
# --------------------------------------------------------------------------- #
_POSTAL_CODE_RE = re.compile(r"^\d{5}$")


def _postal_code(key: str, value: Any) -> str:
    text = str(value).strip()
    if not _POSTAL_CODE_RE.match(text):
        raise SettingError(key, value, "a 5-digit US ZIP code, e.g. \"27401\"")
    return text


def _boolean(key: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise SettingError(key, value, "true or false")


#: Moved here from scrapers/base.py (GFP-87). It identifies this app to a
#: store, and a debugging session sometimes needs to change it -- which is a
#: bad reason to edit source.
DEFAULT_USER_AGENT = "grocery-planner/0.1 (+local personal use)"

_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def _log_level(key: str, value: Any) -> str:
    text = str(value).strip().upper()
    if text not in _LOG_LEVELS:
        raise SettingError(key, value, "one of " + ", ".join(_LOG_LEVELS))
    return text


def _non_empty(key: str, value: Any) -> str:
    text = str(value).strip()
    if not text:
        raise SettingError(key, value, "a non-empty string")
    return text


def _positive_int(key: str, value: Any) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise SettingError(key, value, "a whole number") from None
    if number < 1:
        raise SettingError(key, value, "a whole number of 1 or more")
    return number


@dataclass(frozen=True)
class Setting:
    """One setting: its name in the file, its override, and how to read it."""

    key: str
    default: Any
    parse: Callable[[str, Any], Any]
    env_var: str
    describe: str


#: Every setting the app has. Adding one is a line here plus a default -- the
#: file, the environment override, validation and `gplan config` all follow.
SETTINGS: tuple[Setting, ...] = (
    Setting(
        key="postal_code",
        default="27401",
        parse=_postal_code,
        env_var="GROCERY_PLANNER_POSTAL_CODE",
        describe="ZIP code prices are fetched for.",
    ),
    Setting(
        key="auto_refresh",
        default=True,
        parse=_boolean,
        env_var="GROCERY_PLANNER_AUTO_REFRESH",
        describe="Fetch prices automatically on first run and on a new day (GFP-105).",
    ),
    Setting(
        key="background_refresh",
        default=True,
        parse=_boolean,
        env_var="GROCERY_PLANNER_BACKGROUND_REFRESH",
        describe="Let the OS timer refresh prices when the app is closed (GFP-102).",
    ),
    Setting(
        key="update_check",
        default=True,
        parse=_boolean,
        env_var="GROCERY_PLANNER_UPDATE_CHECK",
        describe="Check GitHub once a day for a newer version (GFP-96).",
    ),
    Setting(
        key="log_level",
        default="WARNING",
        parse=_log_level,
        env_var="GROCERY_PLANNER_LOG_LEVEL",
        describe="Console log level: DEBUG, INFO, WARNING or ERROR (GFP-87).",
    ),
    Setting(
        key="user_agent",
        default=DEFAULT_USER_AGENT,
        parse=_non_empty,
        env_var="GROCERY_PLANNER_USER_AGENT",
        describe="How the scrapers identify themselves to a store (GFP-87).",
    ),
    Setting(
        key="log_retention_days",
        default=7,
        parse=_positive_int,
        env_var="GROCERY_PLANNER_LOG_RETENTION_DAYS",
        describe="How many days of logs to keep (GFP-86).",
    ),
)

BY_KEY: dict[str, Setting] = {s.key: s for s in SETTINGS}


@dataclass(frozen=True)
class Config:
    """Resolved settings, plus anything that went wrong resolving them."""

    values: dict[str, Any] = field(default_factory=dict)
    #: Human-readable problems, in the order found. Empty is the normal case.
    problems: list[str] = field(default_factory=list)
    #: Where the file was looked for, whether or not it existed.
    source: Path | None = None

    def __getitem__(self, key: str) -> Any:
        return self.values[key]

    @property
    def ok(self) -> bool:
        return not self.problems


def path() -> Path:
    """Where the config file lives, honouring :data:`CONFIG_ENV_VAR`."""
    override = os.environ.get(CONFIG_ENV_VAR)
    if override:
        return Path(override).expanduser()
    return data_dir() / CONFIG_FILENAME


def defaults() -> dict[str, Any]:
    return {s.key: s.default for s in SETTINGS}


def is_first_run() -> bool:
    """Whether this install has never been configured (GFP-122).

    Deliberately "no config file", NOT "no database". Someone who clears their
    data to start over has already answered the setup questions, and asking
    again would be the app forgetting something it was told. Conversely a
    config file with nothing useful in it still counts as configured -- the
    user has been through setup and chose the defaults.

    ``postal_code`` is the reason this exists. It defaults to 27401, which is
    the DEVELOPER's ZIP: an install that never asks silently prices a different
    city, and a wrong ZIP does not error, it just answers the wrong question.
    """
    return not path().exists()


def _read_file(target: Path, problems: list[str]) -> dict[str, Any]:
    """The file's contents, or ``{}`` with a problem recorded.

    A missing file is NOT a problem -- it is the normal state of a fresh
    install, and defaults are the right answer.
    """
    if not target.exists():
        return {}
    try:
        # utf-8-sig: a user editing this in Notepad on Windows gets a BOM, and
        # json.loads chokes on it. Same edge GFP-93 hit with the Whole Foods
        # cookie, so it is a known failure here rather than a hypothetical one.
        raw = target.read_text(encoding="utf-8-sig")
    except OSError as exc:
        problems.append(f"Could not read {target}: {exc}. Using defaults.")
        return {}
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        problems.append(
            f"{target} is not valid JSON ({exc.msg}, line {exc.lineno}). "
            "Using defaults for every setting — fix the file or delete it to "
            "have a fresh one written."
        )
        return {}
    if not isinstance(parsed, dict):
        problems.append(
            f"{target} should contain a JSON object, not "
            f"{type(parsed).__name__}. Using defaults."
        )
        return {}
    return parsed


def load() -> Config:
    """Resolve every setting: environment, then file, then default.

    Never raises. A bad value costs only its own key.
    """
    problems: list[str] = []
    target = path()
    from_file = _read_file(target, problems)

    values: dict[str, Any] = {}
    for setting in SETTINGS:
        raw = os.environ.get(setting.env_var)
        origin = "environment"
        if raw is None:
            if setting.key in from_file:
                raw = from_file[setting.key]
                origin = "config file"
            else:
                values[setting.key] = setting.default
                continue
        try:
            values[setting.key] = setting.parse(setting.key, raw)
        except SettingError as exc:
            problems.append(
                f"{exc} (from the {origin}). Using the default "
                f"{setting.default!r}."
            )
            values[setting.key] = setting.default

    unknown = sorted(set(from_file) - set(BY_KEY))
    if unknown:
        # A warning, not an error: an unknown key is usually a typo or a
        # setting from a newer version, and neither should stop the app.
        problems.append(
            f"{target} has setting(s) this version does not know: "
            f"{', '.join(unknown)}. Ignored."
        )
    return Config(values=values, problems=problems, source=target)


def get(key: str) -> Any:
    """One resolved setting. Convenience over :func:`load` for a single read."""
    if key not in BY_KEY:
        raise KeyError(f"unknown setting {key!r}; known: {sorted(BY_KEY)}")
    return load().values[key]


def postal_code() -> str:
    """The ZIP prices are fetched for — the setting this ticket exists for."""
    return get("postal_code")


def auto_refresh() -> bool:
    return get("auto_refresh")


def user_agent() -> str:
    """How the scrapers identify themselves (GFP-87)."""
    return get("user_agent")


def log_level() -> str:
    return get("log_level")


# --------------------------------------------------------------------------- #
# Endpoint overrides (GFP-87)
#
# DEBUG-ONLY, and deliberately environment-variable-only rather than settings in
# config.json. Two reasons:
#   * Pointing the app at a different host is a debugging action, not a
#     preference. Putting it in the file a user edits invites someone to be
#     talked through setting it, which is the shape of a phishing instruction.
#   * A stale override in a config file would be invisible and permanent; an
#     environment variable dies with the shell.
# Undocumented in `gplan config` output for the same reason.
# --------------------------------------------------------------------------- #
def endpoint_override(name: str) -> str | None:
    """A debug base-URL override for ``name``, or None.

    ``name`` is a short source key -- "flipp", "kroger", "wholefoods" -- so the
    variable is GROCERY_PLANNER_ENDPOINT_KROGER. Returns None unless explicitly
    set, so the real endpoint is always the default and never something a
    partially-configured machine drifts into.
    """
    raw = os.environ.get(f"GROCERY_PLANNER_ENDPOINT_{name.strip().upper()}")
    return raw.strip() or None if raw else None


def write_defaults(overwrite: bool = False) -> Path:
    """Create the config file with current defaults. Returns its path.

    Called on first run so the user has something to edit rather than having to
    know the schema. Refuses to clobber an existing file unless asked, because
    that file is hand-edited and irreplaceable in the same way client records
    are.
    """
    target = path()
    if target.exists() and not overwrite:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    body = {s.key: s.default for s in SETTINGS}
    target.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return target


def set_value(key: str, value: Any) -> Any:
    """Validate one setting and write it to the file. Returns the parsed value.

    Added for GFP-91: the installer's last act is to tell a new user how to set
    their ZIP code, and "open this JSON file in a text editor" is not an
    instruction a nutritionist should be given by an installer.

    Two things it is careful about, both because this file is hand-edited:

    * **Unknown keys survive.** A key this version does not recognise is
      usually a setting from a newer one, and silently dropping it on an
      unrelated write would be a nasty surprise on downgrade-then-upgrade.
      :func:`load` already warns about them rather than treating them as an
      error; this matches that stance.
    * **A malformed file is not overwritten.** Rewriting it would destroy
      whatever the user was in the middle of typing, and they can still fix it
      by hand -- which is the whole reason the format is JSON.
    """
    if key not in BY_KEY:
        raise KeyError(f"unknown setting {key!r}; known: {sorted(BY_KEY)}")
    parsed = BY_KEY[key].parse(key, value)      # raises SettingError if bad

    target = path()
    problems: list[str] = []
    body = _read_file(target, problems)
    if problems:
        raise SettingError(
            key, value,
            f"a readable config file — {target} could not be parsed, so it was "
            "left untouched. Fix or delete it, then try again",
        )

    # Store the PARSED value, so the file ends up with a real bool/int rather
    # than the string a shell handed us.
    body[key] = parsed
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return parsed


def describe() -> list[tuple[str, Any, str, str]]:
    """``(key, value, origin, description)`` for every setting, for display.

    Origin is worth showing: "why is it using that ZIP" is answered by knowing
    whether the value came from the environment, the file, or a default.
    """
    resolved = load()
    from_file = _read_file(path(), [])
    rows = []
    for setting in SETTINGS:
        if os.environ.get(setting.env_var) is not None:
            origin = f"env {setting.env_var}"
        elif setting.key in from_file:
            origin = "config file"
        else:
            origin = "default"
        rows.append((setting.key, resolved.values[setting.key], origin, setting.describe))
    return rows
