#!/usr/bin/env python3
"""
check_schema_drift.py — Schema single-source-of-truth guard

Alembic migrations (migrations/versions/) are the ONLY place a table should
ever be defined (see db.py's bootstrap_schema(), which runs `alembic upgrade
head` and nothing else). This script scans every other .py file for a
`CREATE TABLE` statement and fails if it finds one whose table name isn't
already present in the migration history — i.e. a module quietly defining
its own schema again, invisible to Alembic.

This exists because of a real bug: 12 tables across 4 modules (branch_module,
donor_module, enterprise_modules, password_reset) were once defined only in
each module's own lazy _ensure_tables(), never in any migration — meaning a
production deploy with DISABLE_BOOTSTRAP=true would never create them.

Usage: python scripts/check_schema_drift.py
Exit code 0 = clean, 1 = drift found.
"""
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TABLE_RE = re.compile(r"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+(\w+)", re.IGNORECASE)

EXCLUDED_DIRS = {"migrations", "tests", "node_modules", ".git", "venv", ".venv"}


def _tables_in(path: str) -> set:
    try:
        src = open(path, encoding="utf-8").read()
    except (UnicodeDecodeError, OSError):
        return set()
    return {m.group(1).lower() for m in TABLE_RE.finditer(src)}


def main() -> int:
    migration_tables = set()
    for path in glob.glob(os.path.join(ROOT, "migrations", "versions", "*.py")):
        migration_tables |= _tables_in(path)

    drift = {}
    for path in glob.glob(os.path.join(ROOT, "**", "*.py"), recursive=True):
        rel = os.path.relpath(path, ROOT)
        top_dir = rel.split(os.sep)[0]
        if top_dir in EXCLUDED_DIRS:
            continue
        tables = _tables_in(path)
        missing = tables - migration_tables
        if missing:
            drift[rel] = missing

    if drift:
        print("Schema drift detected — these files define tables that are")
        print("NOT present in any Alembic migration (migrations/versions/):\n")
        for rel, tables in sorted(drift.items()):
            print(f"  {rel}: {sorted(tables)}")
        print(
            "\nFix: add a migration in migrations/versions/ for these tables, "
            "or remove the local CREATE TABLE if it's dead code."
        )
        return 1

    print(f"OK — no schema drift ({len(migration_tables)} tables tracked in migrations).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
