"""The CLI's command surface, declared once (GFP-306).

`cli.py` is 1,792 lines and grows with every capability -- `evaluate` was added
the day this was written. Splitting it into a package is the point of GFP-306,
and the risk of that split is silent: a command that fails to register does not
raise, it simply stops existing, and no other test would notice.

This is the same guard `service/` got when GFP-43 split it
(`test_public_api_surface_is_unchanged`), which has since caught two real
omissions. It is written BEFORE the split so the split has something to prove
itself against.

DECLARED BY HAND, like the scraper registry (GFP-303). Deriving this list from
the app would assert `app == app`. A human writing the name down is the check.
"""
import typer

from grocery_planner.cli import app

#: Every command the CLI exposes, "sub-app command" for nested ones.
#: Adding a command means adding it here. That is deliberate.
COMMANDS = {
    # top level
    "best", "categories", "cheapest", "credentials", "db-path", "export",
    "import", "jobs", "list", "logs", "records", "scrape", "stores", "trends",
    "uninstall-plan", "update", "version",
    # clients (GFP-33)
    "client", "client add", "client delete", "client edit", "client groceries",
    "client list", "client restore", "client show",
    # global settings (GFP-85)
    "config", "config set",
    # the matcher's evaluation harness (GFP-281)
    "evaluate", "evaluate report", "evaluate run",
    # user-defined formulas
    "formula", "formula eval", "formula list", "formula set",
    # nutrition catalog (GFP-23/24)
    "nutrition", "nutrition classify", "nutrition sync",
    # profile values used as formula variables
    "profile", "profile list", "profile set",
    # background refresh (GFP-7)
    "schedule", "schedule list", "schedule remove", "schedule run",
    "schedule set",
    # the OS timer (GFP-102)
    "timer", "timer install", "timer remove", "timer status",
}


def _registered() -> set[str]:
    """Every command name reachable from the assembled app."""
    def walk(command, prefix=""):
        found = set()
        for name, sub in getattr(command, "commands", {}).items():
            full = f"{prefix}{name}"
            found.add(full)
            found |= walk(sub, full + " ")
        return found

    return walk(typer.main.get_command(app))


def test_the_command_surface_is_unchanged():
    """A command that stops registering disappears silently. This is the noise."""
    registered = _registered()
    assert registered == COMMANDS, (
        f"missing: {sorted(COMMANDS - registered)}  "
        f"unexpected: {sorted(registered - COMMANDS)}"
    )


def test_the_entry_point_still_resolves():
    """`gplan = grocery_planner.cli:app` is in pyproject and PyInstaller builds
    from it, so the import path must survive becoming a package."""
    import importlib

    module = importlib.import_module("grocery_planner.cli")
    assert isinstance(module.app, typer.Typer)


def test_every_command_carries_help_text():
    """A command with no help is undiscoverable, which for a CLI is broken.

    Checked here rather than per-command because the whole point of this file
    is that the surface is inspectable as a whole.
    """
    command = typer.main.get_command(app)
    missing = [
        name for name, sub in command.commands.items()
        if not (sub.help or sub.short_help)
    ]
    assert not missing, f"no help text: {missing}"
