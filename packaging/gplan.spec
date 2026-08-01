# PyInstaller spec for the gplan CLI (GFP-10).
#
# Build:  pyinstaller packaging/gplan.spec --noconfirm
# Output: dist/gplan.exe (Windows) / dist/gplan (macOS, Linux)
#
# One file, no installer, no Python required on the target machine. The database
# still lives in the platformdirs user-data dir (grocery_planner/paths.py), NOT
# beside the executable, so replacing the binary never touches your data.
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# APScheduler resolves triggers/executors by name at runtime, so static analysis
# cannot see them; without this the scheduler raises LookupError only once a
# schedule actually fires — long after the build "succeeded".
hiddenimports = collect_submodules("apscheduler")
# tzdata ships the IANA database as package data. paths/scrapers fall back to a
# fixed offset without it, but schedules would silently drift.
datas = collect_data_files("tzdata")

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

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="gplan",
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
