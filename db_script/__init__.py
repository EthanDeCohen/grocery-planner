"""SQL schema scripts, shipped as package data (GFP-59).

This package holds no Python logic. It exists only so that setuptools and
PyInstaller treat ``db_script/`` as installable/bundlable data alongside the
``grocery_planner`` package rather than as loose repo-root files that would
otherwise be silently dropped from wheels and frozen binaries. See
``db_script/README.md`` for the directory convention.
"""
