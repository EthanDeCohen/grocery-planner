# PyInstaller spec for the gplan-gui desktop app (GFP-10).
#
# Build:  pyinstaller packaging/gplan-gui.spec --noconfirm
# Output: dist/gplan-gui.exe (Windows) / dist/gplan-gui.app (macOS bundle)
#
# Needs the `gui` extra installed (PySide6). This binary is an order of
# magnitude larger than the CLI because it carries Qt; that is expected.
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = collect_submodules("apscheduler")
# db_script ships the .ddl/.dml schema scripts (GFP-59); see gplan.spec for
# why this must be explicit rather than assumed to "just come along". Same
# reasoning covers grocery_planner/data/*.json, GFP-24's vendored USDA snapshot.
datas = (
    collect_data_files("tzdata")
    + collect_data_files("db_script")
    + collect_data_files("grocery_planner", includes=["data/*.json"])
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
    # Qt modules this app never touches; dropping them saves ~100 MB.
    excludes=[
        "tkinter", "pytest",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
        "PySide6.QtMultimedia", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtCharts",
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
        icon=None,
        bundle_identifier="com.grocery-planner.gui",
        info_plist={"NSHighResolutionCapable": True},
    )
