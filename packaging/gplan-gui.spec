# PyInstaller spec for the gplan-gui desktop app (GFP-10).
#
# Build:  pyinstaller packaging/gplan-gui.spec --noconfirm
# Output: dist/gplan-gui.exe (Windows) / dist/gplan-gui.app (macOS bundle)
#
# Needs the `gui` extra installed (PySide6). This binary is an order of
# magnitude larger than the CLI because it carries Qt; that is expected.
import sys

import os
import pathlib

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

hiddenimports = collect_submodules("apscheduler") + [
    # GFP-80: Qt WebEngine, for in-app Whole Foods session minting. Named
    # explicitly because PyInstaller's analysis only reaches it through a
    # deferred import inside the minting dialog -- which is deliberate, so the
    # 195 MB Chromium is not loaded by every launch of the app.
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebChannel",
]
# db_script ships the .ddl/.dml schema scripts (GFP-59); see gplan.spec for
# why this must be explicit rather than assumed to "just come along". Same
# reasoning covers grocery_planner/data/*.json, GFP-24's vendored USDA snapshot.
#
# GFP-318: schema_datas() resolves by path and aborts the build if it finds
# nothing. collect_data_files("db_script") resolved by import and returned
# nothing under `pip install -e`, silently shipping a schema-less binary.
datas = (
    # GFP-158: the window icon, read at run time by gui.app.app_icon().
    # The .ico/.icns above are consumed by PyInstaller itself; this PNG
    # is what the running process loads, so it has to be a data file.
    [(str(SPEC_DIR / "icons" / "icon-256.png"), "icons")]
    + collect_data_files("tzdata")
    + schema_datas(SPEC_DIR)
    + collect_data_files("grocery_planner", includes=["data/*.json"])
    # GFP-47's default client avatar. GUI-only, so it is not in gplan.spec.
    + collect_data_files("grocery_planner", includes=["assets/*.png"])
)

a = Analysis(
    ["gplan_gui_entry.py"],
    pathex=[".."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Qt modules this app never touches.
    #
    # QtWebEngine WAS excluded here, and that exclusion is what kept this
    # binary at 50.6 MB. GFP-80 removes it deliberately: the user chose in-app
    # Whole Foods session minting over a copy-paste walkthrough, with the
    # measured cost in front of them -- 50.6 MB -> 180.1 MB, 3.56x, on every
    # download and every update. Do not "optimise" it back out; that would
    # remove the feature, not just the bytes.
    #
    # QtQuick and QtQml also come OUT of this list, which is not obvious.
    # QtWebEngineWidgets is the widget API, so it looks like it should need
    # neither -- but WebEngine is built on Quick underneath, and excluding
    # them produces a binary that BUILDS and then fails when the minting
    # window is opened. A packaging break that only appears at the moment a
    # user tries to use the feature is the worst kind, so they stay in.
    excludes=[
        "tkinter", "pytest",
        "PySide6.Qt3DCore",
        "PySide6.QtMultimedia", "PySide6.QtCharts",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="gplan-gui",
    # GFP-158. Windows reads the icon out of the EXE itself, which is what
    # puts it on the taskbar and in Explorer. Built from packaging/icon.svg by
    # scripts/build_icons.py and CHECKED IN, so the release runners need no
    # rasteriser -- adding an image toolchain to a release job is a new way for
    # a release to fail for a reason unrelated to the code.
    icon=str(SPEC_DIR / "icons" / "icon.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,       # no console window behind the GUI
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# macOS wants a real .app bundle, not a bare executable.
if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="gplan-gui.app",
        icon=str(SPEC_DIR / "icons" / "icon.icns"),
        bundle_identifier="com.proteinledger.gui",
        info_plist={"NSHighResolutionCapable": True},
    )
