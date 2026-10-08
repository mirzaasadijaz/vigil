"""
Idempotent repair for the columns the false-detection layer and the Kohat POC need.

WHY THIS EXISTS INSTEAD OF JUST `alembic upgrade heads`
-------------------------------------------------------
`alembic upgrade heads` is still the correct thing to run, and you should try it first.
This script is the fallback for the case this repository is actually in:

  * the migration tree has TWO heads (9488212d6fbb and d4e5f6a0b1c2), so plain
    `alembic upgrade head` fails outright, and
  * deployed databases have been migrated by branches whose revision files are not
    present on the current branch. When alembic cannot find the revision recorded in
    `alembic_version`, it refuses to do anything at all — including applying a migration
    that is otherwise perfectly safe.

`create_tables()` does not help either: SQLAlchemy's `create_all` only issues
CREATE TABLE for tables that do not exist. It never issues ALTER TABLE, so a pre-existing
`alert_logs` keeps its old shape no matter how many times the app restarts. That is
exactly why `column alert_logs.severity does not exist` survives a redeploy.

WHAT IT DOES
    alert_logs.severity              VARCHAR(16) NULL  + index
    system_settings.false_detection  TEXT NULL
    system_settings.module_toggles   TEXT NULL

All three are nullable and additive. Nothing is dropped, nothing is backfilled, no data
is rewritten. Every statement uses IF NOT EXISTS, so running it twice is a no-op and
running it on an already-migrated database is harmless.

It does NOT touch `alembic_version`. Stamping a revision the database did not truly
travel through would make the divergence worse, not better — the lineage still needs
reconciling properly, and this script deliberately does not paper over that.

USAGE (from the repo root, on the machine with the database):
    python -m backend.scripts.apply_poc_columns            # apply
    python -m backend.scripts.apply_poc_columns --check    # report only, change nothing

Credentials are read from DATABASE_URL in your .env. Nothing is printed that could
expose them.
"""
from __future__ import annotations

import sys

# (table, column, DDL type) - checked and added one at a time so a partially-migrated
# database converges rather than failing on the first statement.
COLUMNS = [
    ("alert_logs", "severity", "VARCHAR(16)"),
    ("system_settings", "false_detection", "TEXT"),
    ("system_settings", "module_toggles", "TEXT"),
]

INDEXES = [
    ("ix_alert_logs_severity", "alert_logs", "severity"),
]


def _url() -> str:
    from backend.core.config import settings
    return settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")


def _redact(url: str) -> str:
    """Never print a live connection string."""
    import re
    return re.sub(r"://([^:/@]+):[^@]*@", r"://\1:***@", url)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    check_only = "--check" in argv

    from sqlalchemy import create_engine, text

    url = _url()
    print(f"database: {_redact(url)}")
    engine = create_engine(url, pool_pre_ping=True)

    missing_cols, missing_idx = [], []
    with engine.connect() as conn:
        for table, column, _ddl in COLUMNS:
            found = conn.execute(text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = :c"
            ), {"t": table, "c": column}).first()
            status = "present" if found else "MISSING"
            print(f"  {table}.{column:<16} {status}")
            if not found:
                missing_cols.append((table, column, _ddl))

        for name, table, _col in INDEXES:
            found = conn.execute(text(
                "SELECT 1 FROM pg_indexes WHERE indexname = :n"
            ), {"n": name}).first()
            print(f"  index {name:<26} {'present' if found else 'MISSING'}")
            if not found:
                missing_idx.append((name, table, _col))

    if not missing_cols and not missing_idx:
        print("\nNothing to do - the schema already has everything.")
        return 0

    if check_only:
        print(f"\n--check: {len(missing_cols)} column(s) and {len(missing_idx)} "
              f"index(es) would be added. Nothing was changed.")
        return 1

    with engine.begin() as conn:
        for table, column, ddl in missing_cols:
            conn.execute(text(
                f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}'))
            print(f"  added {table}.{column}")
        for name, table, col in missing_idx:
            conn.execute(text(
                f'CREATE INDEX IF NOT EXISTS {name} ON {table} ({col})'))
            print(f"  created index {name}")

    print("\nDone. Restart the application so the writer picks up the new schema.")
    print("The alembic lineage is NOT stamped by this script - see the module "
          "docstring; that still needs reconciling separately.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
