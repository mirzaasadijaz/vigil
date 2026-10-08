"""
Reconcile this application's models with a database whose schema it does not own.

WHY THIS EXISTS
---------------
The deployment points at a database whose schema is owned by a Laravel application.
Alembic has never run against it: there is no `alembic_version` row, and `alembic
upgrade` is not usable here anyway because the merged branch carries two migration
files sharing the revision id `b2c3d4e5f6a7`, which alembic refuses to load.

`create_tables()` in backend/core/database.py is deliberately a no-op on this branch
("remote schema already exists - connect-only"), so nothing reconciles the tables and
columns Laravel does not own. That is what this script is for.

It asks the DATABASE what exists (information_schema, via SQLAlchemy's inspector) and
compares that with Base.metadata. It never consults alembic_version and never writes
it: stamping a lineage this database never travelled through would make the divergence
harder to reconcile later, not easier.

THREE MODES, EACH MORE INVASIVE THAN THE LAST
    --check
        Report only. Creates nothing, alters nothing. Always start here.

    (no flags)
        CREATE the tables that are absent, in dependency order, one transaction each.
        Pre-existing tables are left completely alone - no ALTER, no DROP, no backfill.

    --add-columns --tables a,b
        ALTER the named existing tables to add columns the models declare and the
        database lacks. This is what fixes "column alert_logs.severity does not exist".
        --tables is REQUIRED and has no default: some tables here are Laravel's, and
        silently adding our columns to `users` or `employees` is not this script's
        decision to make. Only NULLABLE columns are added - a NOT NULL column with no
        server default cannot be added to a populated table without a value policy, so
        those are reported and skipped. Indexes the models declare on added columns are
        created too. Every statement is IF NOT EXISTS, so re-running is a no-op.

Nothing is ever dropped, renamed, retyped or backfilled. A column that exists but whose
type differs from the model is left alone - retyping a live column is a data decision.

USAGE (from the repo root, on the machine with the database):
    python -m backend.scripts.sync_missing_tables --check
    python -m backend.scripts.sync_missing_tables
    python -m backend.scripts.sync_missing_tables --add-columns --tables alert_logs

Exit codes: 0 = nothing pending / applied cleanly, 1 = work pending (--check) or
something failed.

Credentials are read from DATABASE_URL in your .env and are redacted in all output.
"""
from __future__ import annotations

import sys

# Model modules are DISCOVERED at runtime, not hardcoded. Different checkouts carry
# different model files - `.gitignore` has a bare `models` pattern that matches
# backend/models/ as well as the top-level weights directory, so some files exist only
# on the machine that wrote them. A hardcoded list crashes on whichever box is missing
# one; a discovered list adapts.
#
# NOTE: backend/alembic/env.py imports a hardcoded, shorter list, which is why
# autogenerate there proposes dropping the tables it omits. Existing bug in env.py.


def _url() -> str:
    from backend.core.config import settings
    return settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")


def _redact(url: str) -> str:
    """Never print a live connection string."""
    import re
    return re.sub(r"://([^:/@]+):[^@]*@", r"://\1:***@", url)


def _load_metadata():
    """Import every model module present in this checkout, then hand back the metadata.

    A module that fails to import is reported loudly rather than skipped quietly: its
    tables would otherwise be invisible here and get reported as "nothing to do", which
    is the one wrong answer this script must never give.
    """
    import importlib
    import pkgutil
    import backend.models
    from backend.core.database import Base

    names = sorted(m.name for m in pkgutil.iter_modules(backend.models.__path__)
                   if not m.name.startswith("_"))
    loaded, broken = [], []
    for name in names:
        try:
            importlib.import_module(f"backend.models.{name}")
            loaded.append(name)
        except Exception as exc:                  # noqa: BLE001 - reported below
            broken.append((name, exc))

    print(f"model modules: {len(loaded)} loaded ({', '.join(loaded)})")
    for name, exc in broken:
        print(f"  WARNING: backend.models.{name} failed to import "
              f"({type(exc).__name__}: {exc}) - its tables are NOT considered below")
    return Base.metadata, broken


def _arg(argv, name):
    """Read --name value or --name=value."""
    for i, a in enumerate(argv):
        if a == name and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return None


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    check_only = "--check" in argv
    add_columns = "--add-columns" in argv
    only = [t.strip() for t in (_arg(argv, "--tables") or "").split(",") if t.strip()]

    if add_columns and not only:
        print("--add-columns requires --tables (e.g. --tables alert_logs).\n"
              "There is no default: some tables in this database belong to Laravel, and\n"
              "adding our columns to them is not this script's call. Run --check first.")
        return 1

    from sqlalchemy import create_engine, inspect, text

    metadata, broken = _load_metadata()
    url = _url()
    print(f"database: {_redact(url)}")
    engine = create_engine(url, pool_pre_ping=True)
    dialect = engine.dialect

    inspector = inspect(engine)
    existing = set(inspector.get_table_names())

    missing, present = [], []
    for table in metadata.sorted_tables:          # dependency order: parents first
        (present if table.name in existing else missing).append(table)

    print(f"\nmodel tables: {len(metadata.sorted_tables)}   "
          f"in database: {len(present)}   missing: {len(missing)}")
    if broken:
        print(f"  (incomplete: {len(broken)} model module(s) failed to import - see the "
              "warnings above; treat this report as partial)")

    # Columns the models declare that the database does not have.
    drift = {}
    for table in present:
        db_cols = {c["name"] for c in inspector.get_columns(table.name)}
        absent = [c for c in table.columns if c.name not in db_cols]
        if absent:
            drift[table.name] = absent

    print("\nexisting tables:")
    for table in present:
        absent = drift.get(table.name)
        note = f"  <-- missing columns: {', '.join(c.name for c in absent)}" if absent else ""
        print(f"  {table.name:<28} present{note}")

    if missing:
        print("\nmissing tables:")
        for table in missing:
            print(f"  {table.name}")

    # ---- --add-columns -----------------------------------------------------------
    if add_columns:
        unknown = [t for t in only if t not in {x.name for x in present}]
        if unknown:
            print(f"\n--tables names not present in the database: {', '.join(unknown)}")
            return 1

        planned, skipped = [], []
        for tname in only:
            for col in drift.get(tname, []):
                if not col.nullable and col.server_default is None:
                    skipped.append((tname, col.name, "NOT NULL with no server default"))
                    continue
                planned.append((tname, col))

        if not planned and not skipped:
            print(f"\nNothing to add - {', '.join(only)} already has every model column.")
            return 0

        print("\ncolumns that WOULD be added:" if check_only else "\ncolumns to add:")
        for tname, col in planned:
            idx = "  (indexed)" if col.index else ""
            print(f"  {tname}.{col.name}  {col.type.compile(dialect)}{idx}")
        for tname, cname, why in skipped:
            print(f"  SKIP {tname}.{cname} - {why}; add it deliberately with a value policy")

        if check_only:
            print(f"\n--check: {len(planned)} column(s) would be added. Nothing was changed.")
            return 1

        added, failed = [], []
        for tname, col in planned:
            ddl = (f'ALTER TABLE "{tname}" ADD COLUMN IF NOT EXISTS '
                   f'"{col.name}" {col.type.compile(dialect)}')
            try:
                with engine.begin() as conn:
                    conn.execute(text(ddl))
                    if col.index:
                        name = f"ix_{tname}_{col.name}"
                        conn.execute(text(
                            f'CREATE INDEX IF NOT EXISTS "{name}" ON "{tname}" ("{col.name}")'))
                added.append(f"{tname}.{col.name}")
                print(f"  added {tname}.{col.name}")
            except Exception as exc:              # noqa: BLE001 - reported, not raised
                failed.append((f"{tname}.{col.name}", exc))
                print(f"  FAILED {tname}.{col.name}: {type(exc).__name__}: {exc}")

        print(f"\nAdded {len(added)} column(s). Restart the application so SQLAlchemy "
              "stops using the cached statement shape.")
        print("alembic_version was NOT written.")
        return 1 if failed else 0

    # ---- table creation ----------------------------------------------------------
    if drift:
        print("\nNOTE: the tables flagged above exist but lack columns the models expect.\n"
              "  This run does NOT alter them. Any read selecting those columns fails with\n"
              "  'column X does not exist' until they are added - either in a Laravel\n"
              "  migration (preferred for tables Laravel owns) or with:\n"
              "    python -m backend.scripts.sync_missing_tables --add-columns --tables <t>")

    if not missing:
        print("\nNo tables to create.")
        return 1 if (drift and check_only) else 0

    if check_only:
        print(f"\n--check: {len(missing)} table(s) would be created, "
              f"{len(drift)} existing table(s) have column drift. Nothing was changed.")
        return 1

    created, failed = [], []
    for table in missing:
        try:
            with engine.begin() as conn:
                table.create(bind=conn, checkfirst=True)
            created.append(table.name)
            print(f"  created {table.name}")
        except Exception as exc:                  # noqa: BLE001 - reported, not raised
            failed.append((table.name, exc))
            print(f"  FAILED  {table.name}: {type(exc).__name__}: {exc}")

    print(f"\nCreated {len(created)} table(s).")
    if failed:
        print(f"{len(failed)} failed - most often a foreign key referencing a "
              "Laravel-owned table whose key type does not match.")
    print("alembic_version was NOT written.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
