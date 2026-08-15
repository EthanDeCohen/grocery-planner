# ######### decohen-partners ##########
# Protein Ledger
"""Collect db_script's schema scripts for a PyInstaller bundle (GFP-318).

Resolved by PATH, never by import. Both specs used to call
``collect_data_files("db_script")``, which asks the import system where the
package lives -- and under ``pip install -e`` the editable finder exposes only
``grocery_planner``. Run from ``packaging/`` (which is what ci.yml and
release.yml do) the import failed, PyInstaller logged one WARNING, exited 0,
and shipped a binary containing no schema at all.

Same trap GFP-158 already fixed for the window icon: anything resolved against
the CWD differs between a repo-root build and the packaging/ build the
workflows use. This module applies that lesson to the schema.
"""
from __future__ import annotations

import pathlib

# Everything in the package except its Python. db.py only reads .ddl/.dml, but
# shipping the whole directory keeps parity with what collect_data_files used
# to bundle, so this change cannot drop a file something else depends on.
_SKIP_SUFFIXES = {".py", ".pyc"}


def schema_datas(spec_dir: pathlib.Path) -> list[tuple[str, str]]:
    """``(source, dest_dir)`` pairs for db_script/, rooted at the bundle's top.

    ``db.py._db_script_root`` resolves ``sys._MEIPASS / "db_script"`` in a
    frozen build, so the destination tree has to mirror that exactly.

    Raises if it finds no migrations. An empty collection is precisely what
    shipped a schema-less binary, and failing the build loudly is more of the
    point of this module than the path handling is.
    """
    root = pathlib.Path(spec_dir).parent / "db_script"
    files = [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.suffix not in _SKIP_SUFFIXES
        and "__pycache__" not in path.parts
    ]
    scripts = [path for path in files if path.suffix in {".ddl", ".dml"}]
    if not scripts:
        raise SystemExit(
            f"BUILD ABORTED: no .ddl/.dml schema scripts found under {root}. "
            "A binary without them cannot create or migrate a database "
            "(GFP-318)."
        )
    return [
        (str(path), str(pathlib.Path("db_script") / path.parent.relative_to(root)))
        for path in files
    ]
