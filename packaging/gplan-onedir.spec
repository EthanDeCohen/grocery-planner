# PyInstaller spec for the gplan CLI, one-DIRECTORY build (GFP-320).
#
# Build:  pyinstaller packaging/gplan-onedir.spec --noconfirm
# Output: dist/gplan/gplan.exe plus dist/gplan/_internal/
#
# Same Analysis as gplan.spec. The only difference is the packaging mode, and
# that is the entire point: Defender's Behavior:Win32/Execution.A!ml fires on
# the one-file bootloader unpacking ~700 files into %TEMP% and executing from
# there, which is what a dropper does. A one-directory build loads the same
# files from a sibling _internal/ folder and never writes to TEMP at all.
#
# Kept as a separate spec while the two are being compared. Whichever wins,
# only one of these files should survive.
import pathlib
import sys

SPEC_DIR = pathlib.Path(SPECPATH)

# GFP-318: the schema collector is importable only once SPEC_DIR is on the
# path -- specs are exec'd, not imported.
if str(SPEC_DIR) not in sys.path:
    sys.path.insert(0, str(SPEC_DIR))
from _schema_datas import schema_datas

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = collect_submodules("apscheduler")

datas = (
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
    excludes=["PySide6", "shiboken6", "tkinter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

# One-file passes a.binaries and a.datas to EXE. One-directory passes them to
# COLLECT instead, so the launcher stays a small stub beside its dependencies.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
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
    name="gplan",
)
