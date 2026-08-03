"""Front-end-agnostic strings must survive a legacy Windows console (GFP-95).

Found by CI, not locally: `gplan trends` crashed on a Windows runner with
UnicodeEncodeError on U+25B8 (a small right-pointing triangle used as a menu
separator, "Data > Run scrape"). Windows consoles default to cp1252, which has
no such character; this dev machine's console is UTF-8, which hid it. A real
nutritionist on a default Windows console would have hit a traceback.

THE RULE THIS ENFORCES, and it is a boundary rather than a ban:

* ``service/`` and the modules the CLI prints from must use characters a cp1252
  console can encode. These strings reach a terminal.
* ``gui/`` may use whatever it likes -- Qt renders text itself and never encodes
  it to a console codepage. The page arrows, the back arrow and the minus sign
  there are correct typography and must NOT be flattened to ASCII.

Docstrings and comments are exempt everywhere: they are never written to a
stream. Only string literals that can become output are checked.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "grocery_planner"

#: Modules whose strings can reach a console. gui/ is deliberately absent.
CONSOLE_REACHABLE = ["cli.py", "bill.py", "savings.py", "records.py", "scheduler.py",
                     "jobs.py", "customers.py", "targets.py", "nutrition.py",
                     "preferences.py", "importers.py", "usda.py", "matching.py",
                     "formulas.py", "credentials.py", "protein_kind.py", "db.py",
                     # Both print: config.py's SettingError text and problem
                     # list are echoed verbatim by `gplan config`, and logs.py's
                     # by `gplan logs`. Clean today; listed so they stay that
                     # way, since neither module looks like output code.
                     "config.py", "logs.py", "paths.py", "install_paths.py"]

#: The console encoding to hold the line at. cp1252 is the default on a
#: US/Western-European Windows install, which is the shipping target.
CONSOLE_ENCODING = "cp1252"


def _console_reachable_files() -> list[pathlib.Path]:
    files = [PACKAGE / name for name in CONSOLE_REACHABLE]
    files += sorted((PACKAGE / "service").glob("*.py"))
    files += sorted((PACKAGE / "scrapers").glob("*.py"))
    return [f for f in files if f.exists()]


def _unsafe_literals(path: pathlib.Path) -> list[tuple[int, str]]:
    """Non-docstring string literals this console encoding cannot represent."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            text = ast.get_docstring(node, clean=False)
            if text:
                docstrings.add(text)

    offenders = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if node.value in docstrings:
            continue
        for char in node.value:
            try:
                char.encode(CONSOLE_ENCODING)
            except UnicodeEncodeError:
                offenders.append((node.lineno, f"U+{ord(char):04X}"))
                break
    return offenders


@pytest.mark.parametrize(
    "path", _console_reachable_files(), ids=lambda p: p.name
)
def test_console_reachable_strings_survive_a_legacy_windows_console(path):
    offenders = _unsafe_literals(path)
    assert not offenders, (
        f"{path.name} has string literal(s) a {CONSOLE_ENCODING} console cannot "
        f"encode: {offenders}.\n\n"
        "These modules print to a terminal, and Windows consoles default to "
        f"{CONSOLE_ENCODING} -- printing one of these raises UnicodeEncodeError "
        "for a real user. Use an ASCII equivalent ('>' rather than a triangle). "
        "grocery_planner/gui/ is exempt: Qt renders text itself and never "
        "encodes it to a console codepage."
    )


def test_the_gui_is_deliberately_exempt():
    """Guards the boundary itself: gui/ keeps its real typography.

    If someone ever 'fixes' the GUI to ASCII to make this file pass, this test
    fails and says why -- the page arrows and back arrow are correct there.
    """
    trends = (PACKAGE / "gui" / "trends.py").read_text(encoding="utf-8")
    assert chr(0x25C0) in trends and chr(0x25B6) in trends, (
        "gui/trends.py's paging arrows were flattened to ASCII. They should not "
        "be: Qt renders them fine, and this test file's rule applies only to "
        "strings that reach a console."
    )
