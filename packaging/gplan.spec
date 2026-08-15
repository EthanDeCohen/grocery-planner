# PyInstaller spec for the gplan CLI (GFP-10).
#
# Build:  pyinstaller packaging/gplan.spec --noconfirm
# Output: dist/gplan/gplan.exe + dist/gplan/_internal_cli/ (Windows)
#         dist/gplan/gplan     + dist/gplan/_internal_cli/ (macOS, Linux)
#
# One directory, no installer, no Python required on the target machine. The
# database still lives in the platformdirs user-data dir
# (grocery_planner/paths.py), NOT beside the executable, so replacing the
# binary never touches your data.
import os
import pathlib
import sys

# GFP-158. PyInstaller runs a spec with exec() and does not define __file__,
# so the icon path is resolved from SPECPATH -- the one variable PyInstaller
# guarantees, holding the directory of this spec. A relative "icons/icon.ico"
# would resolve against the CWD, which differs between a local build (repo
# root) and the release workflow (packaging/).
SPEC_DIR = pathlib.Path(SPECPATH)

# GFP-318: the schema collector lives beside this spec, so it is importable
# only once SPEC_DIR is on the path -- the specs are exec'd, not imported.
if str(SPEC_DIR) not in sys.path:
    sys.path.insert(0, str(SPEC_DIR))
from _schema_datas import schema_datas

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# APScheduler resolves triggers/executors by name at runtime, so static analysis
# cannot see them; without this the scheduler raises LookupError only once a
# schedule actually fires — long after the build "succeeded".
hiddenimports = collect_submodules("apscheduler")
# tzdata ships the IANA database as package data. paths/scrapers fall back to a
# fixed offset without it, but schedules would silently drift.
# db_script ships the .ddl/.dml schema scripts (GFP-59); grocery_planner/db.py
# resolves their on-disk location via sys._MEIPASS in a frozen build, so they
# must land at "db_script/..." under the extraction root, not be silently
# dropped like any other non-code data would be. Same reasoning applies to
# grocery_planner/data/*.json (GFP-24's vendored USDA snapshot), which
# grocery_planner/usda.py reads at runtime instead of calling the network.
#
# GFP-318: the schema comes from schema_datas(), NOT collect_data_files. The
# latter resolves by import and returned nothing under `pip install -e`, which
# shipped binaries with no schema and only a WARNING to show for it.
datas = (
    # GFP-158: the window icon, read at run time by gui.app.app_icon().
    # The .ico/.icns above are consumed by PyInstaller itself; this PNG
    # is what the running process loads, so it has to be a data file.
    [(str(SPEC_DIR / "icons" / "icon-256.png"), "icons")]
    + collect_data_files("tzdata")
    + schema_datas(SPEC_DIR)
    + collect_data_files("grocery_planner", includes=["data/*.json"])
)

a = Analysis(
    ["gplan_entry.py"],
    pathex=[".."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # PySide6 is the GUI's business; keeping it out holds the CLI near 15 MB
    # instead of dragging in all of Qt.
    excludes=["PySide6", "shiboken6", "tkinter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

# GFP-320: ONE DIRECTORY, not one file. Windows Defender quarantined the
# one-file build as Behavior:Win32/Execution.A!ml -- a behavioural detection,
# not a signature: the bootloader unpacks ~700 files into %TEMP% and runs them
# from there, which is what a dropper does. COLLECT keeps the launcher beside
# its dependencies and never writes to TEMP. The trade is that a release ships
# a folder rather than a single file, which the installers handle.
#
# CONTENTS_DIRECTORY IS NOT COSMETIC. Both installers flatten their payload
# into ONE install root so that gplan.exe and gplan-gui.exe stay siblings --
# PATH, the Start Menu shortcut and background.py's windowless-launcher lookup
# all depend on that. Under the default name both apps would write
# `_internal/`, and the second copy would overwrite the first's
# base_library.zip with one built from a different module set. Distinct names
# keep them side by side.
CONTENTS_DIR = "_internal_cli"

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    contents_directory=CONTENTS_DIR,
    name="gplan",
    icon=str(SPEC_DIR / "icons" / "icon.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX trips antivirus heuristics for no real size win
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    contents_directory=CONTENTS_DIR,
    name="gplan",
)
