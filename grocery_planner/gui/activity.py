# ######### decohen-partners ##########
# Protein Ledger
"""Every control the user operates, in the app's own log (GFP-337).

In:  a widget tree.
Out: one timestamped INFO line per menu action, button press and toggle.

WHY THIS IS A HOOK AND NOT FIFTY logger.info CALLS
--------------------------------------------------
The obvious implementation is a line in each handler. It is also the one that
rots: a control added next month is a control nobody remembered to log, and the
gap is invisible until the day somebody needs the record and it is not there.
Walking the tree once means a new button is covered by existing code, and the
convention cannot be forgotten because there is nothing to remember.

It also keeps the logging out of the handlers, so reading `on_scrape` tells you
what scraping does rather than what it records.

WHAT IS LOGGED, AND WHAT DELIBERATELY IS NOT
--------------------------------------------
The CONTROL, never its contents. "the user pressed Export" is what a support
question needs; "the user pressed Export for Fiona" adds a client's name to a
file that gets emailed around when something goes wrong.

:class:`~grocery_planner.logs.RedactingFilter` already drops records that look
like secrets, but it knows nothing about personal data -- so this does not rely
on it. It prefers ``objectName``, falls back to the control's own label, and
truncates: a caption is a designer's string, not a record.

Checkbox state IS included. "ticked beef" and "unticked beef" are different
actions and the difference is the whole point of logging a toggle.

LEVEL AND VOLUME
----------------
INFO, so it survives at the default level -- a record only kept when somebody
thought to raise the level is not a record. Volume is bounded by the rotation
that already exists (2 MB x 5 files, pruned after seven days), and a person
clicking a desktop app produces a trivial number of lines next to a scrape.
"""
from __future__ import annotations

from .. import logs

#: Longest control label written to the log. Long enough to identify anything
#: in this app, short enough that a pathological caption cannot fill the file.
MAX_LABEL = 60

_INSTALLED = "_gfp_activity_logged"


def _label(obj) -> str:
    """How this control should appear in the log.

    ``objectName`` first because it is a developer's stable identifier, and it
    survives the label being reworded or translated. The visible text is the
    fallback, with its keyboard ampersands stripped -- "&Quit" is a rendering
    detail and reads as noise in a log.
    """
    name = ""
    try:
        name = obj.objectName() or ""
    except Exception:                      # a C++ object already deleted
        return "<gone>"
    if not name:
        try:
            name = (obj.text() or "").replace("&", "")
        except Exception:
            name = ""
    name = " ".join(name.split())          # collapse newlines in a button label
    if len(name) > MAX_LABEL:
        name = name[:MAX_LABEL] + "…"
    return name or obj.__class__.__name__


def _log(kind: str, obj, suffix: str = "") -> None:
    # Resolved at call time, not at install time: the logger a frozen GUI ends
    # up with depends on logs.setup() having run, and install() may be called
    # from a constructor that runs first.
    logs.get_logger("grocery_planner.gui.activity").info(
        "%s %s%s", kind, _label(obj), suffix
    )


def install(root) -> int:
    """Wire every action and button under ``root``. Returns how many were wired.

    Safe to call more than once on the same tree -- each object is marked, so a
    dialog reopened from the cache is not connected twice and does not produce
    duplicate lines. That matters because :class:`MainWindow` keeps its dialogs
    rather than rebuilding them.
    """
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QAbstractButton

    wired = 0
    for obj in [root, *root.findChildren(QAction), *root.findChildren(QAbstractButton)]:
        if not isinstance(obj, (QAction, QAbstractButton)):
            continue
        if obj.property(_INSTALLED):
            continue
        obj.setProperty(_INSTALLED, True)
        wired += 1
        if obj.isCheckable():
            # Bind the object into the default argument rather than closing over
            # the loop variable, which would leave every connection pointing at
            # the last control.
            obj.toggled.connect(
                lambda on, o=obj: _log("toggled", o, f" -> {'on' if on else 'off'}")
            )
        elif isinstance(obj, QAction):
            obj.triggered.connect(lambda _checked=False, o=obj: _log("action", o))
        else:
            obj.clicked.connect(lambda _checked=False, o=obj: _log("clicked", o))
    return wired
