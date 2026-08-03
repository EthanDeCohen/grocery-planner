"""Frozen-GUI entry point. See ``gplan_entry.py``; same rules apply.

Also carries the WINDOWLESS BACKGROUND REFRESH (GFP-102). This binary is built
with ``console=False``, which makes it a GUI-subsystem executable that Windows
never gives a console to -- so a Scheduled Task invoking it shows no window.
That is a property of the executable format, not a task setting that some group
policy can withdraw.

The documented alternative, ``schtasks /RU <user> /NP`` (S4U -- run whether
logged on or not, without a stored password), was measured and refused on a
normal unelevated Windows 11 account: it prompts for a password and then
reports "Access is denied". Since GFP-91's installer deliberately needs no
administrator rights, S4U could not be the answer.

The argv check happens BEFORE the GUI import, so a background refresh never
loads Qt. A daily unattended run then costs what the CLI costs, not what a
desktop app costs.
"""
import sys
from multiprocessing import freeze_support

REFRESH_FLAG = "--refresh-once"


def _run() -> int:
    if REFRESH_FLAG in sys.argv:
        from grocery_planner import background
        return background.refresh_once()
    from grocery_planner.gui.app import main
    return main()


if __name__ == "__main__":
    freeze_support()
    raise SystemExit(_run())
