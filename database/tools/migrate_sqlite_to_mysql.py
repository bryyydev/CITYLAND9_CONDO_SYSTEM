"""Copy the CityLand 9 SQLite database into a NEW, EMPTY local MySQL/MariaDB database.

    python database/tools/migrate_sqlite_to_mysql.py --confirm [--source PATH]

Target = MYSQL_HOST/PORT/DATABASE/USER/PASSWORD from .env. Steps (docs/migration.md):
  1. refuse if the target database already has tables
  2. back up the SQLite file to database/backups/ (the original is only read, never changed)
  3. preflight the backup copy - stop on any blocking problem
  4. create the schema from database/schema.sql
  5. copy every row in foreign-key order, keeping ids (units in two passes), one transaction
  6. verify row counts and every value
  7. golden master: every bill's computed figures must be identical on both databases
Nothing is switched: afterwards set DB_ENGINE=mysql in .env to use the new database.
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    pass

import mysql_schema_test as mst  # noqa: E402  (models on in-memory SQLite, normalise(), mysql_url())
import sqlite_preflight  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

SELF_REFS = ("assigned_parking_unit_id", "assigned_storage_unit_id")


def step(n, msg):
    print(f"{n}. {msg}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default=os.path.join(ROOT, "cityland_condo_web.db"))
    parser.add_argument("--confirm", action="store_true", help="required: actually write to the MySQL database")
    args = parser.parse_args()
    if not args.confirm:
        sys.exit("Nothing done. Re-run with --confirm to copy the data.")

    database = os.getenv("MYSQL_DATABASE", "")
    target = create_engine(mst.mysql_url(database), future=True)
    with target.connect() as conn:
        tables = conn.execute(text("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = :d"), {"d": database}).scalar()
    if tables:
        sys.exit(f"Refusing: `{database}` already has {tables} table(s). Migrate only into a new, empty database.")

    step(1, f"Backing up {args.source}")
    os.makedirs(os.path.join(ROOT, "database", "backups"), exist_ok=True)
    backup = os.path.join(ROOT, "database", "backups", f"cityland_condo_web_before_mysql_{datetime.now():%Y%m%d_%H%M%S}.db")
    src = sqlite3.connect(f"file:{os.path.abspath(args.source)}?mode=ro", uri=True)
    dst = sqlite3.connect(backup)
    src.backup(dst); dst.close(); src.close()
    print(f"   {backup}")

    step(2, "Preflight of the backup copy")
    rep, counts = sqlite_preflight.check(backup)
    for w in rep.warnings:
        print(f"   WARNING {w}")
    if rep.blocking:
        for b in rep.blocking:
            print(f"   BLOCKING {b}")
        sys.exit("Stopped: fix the blocking items first (nothing was written to MySQL).")
    print(f"   OK - {sum(counts.values())} rows in {len(counts)} tables")

    step(3, "Creating the schema")
    schema = open(os.path.join(ROOT, "database", "schema.sql"), encoding="utf-8").read()
    statements = [s.strip() for s in schema.split(";\n") if s.strip() and not all(
        line.startswith("--") or not line.strip() for line in s.strip().splitlines())]
    with target.begin() as conn:
        for stmt in statements:
            conn.exec_driver_sql(stmt.rstrip(";"))
    import legacy_app
    metadata = legacy_app.db.metadata
    mst.compare_schema(target, metadata)
    if mst.FAILURES:
        sys.exit("Stopped: the created schema does not match the models.")

    step(4, "Copying rows (one transaction, foreign keys checked)")
    source = create_engine("sqlite:///file:" + os.path.abspath(backup).replace("\\", "/") + "?mode=ro&uri=True")
    total = 0
    with source.connect() as s, target.begin() as t:
        for table in metadata.sorted_tables:
            rows = [dict(r) for r in s.execute(table.select().order_by(*table.primary_key.columns)).mappings()]
            if not rows:
                continue
            if table.name == "unit":
                # Pass 1 without the self-references, pass 2 fills them in (an apartment may point
                # to a parking/storage unit with a higher id).
                t.execute(table.insert(), [{**r, **{c: None for c in SELF_REFS}} for r in rows])
                for r in rows:
                    if any(r[c] for c in SELF_REFS):
                        t.execute(table.update().where(table.c.id == r["id"]).values({c: r[c] for c in SELF_REFS}))
            else:
                t.execute(table.insert(), rows)
            total += len(rows)
            print(f"   {table.name}: {len(rows)}")
    print(f"   {total} rows copied")

    step(5, "Verifying every value")
    mismatches = 0
    with source.connect() as s, target.connect() as t:
        for table in metadata.sorted_tables:
            order = list(table.primary_key.columns)
            a = s.execute(table.select().order_by(*order)).mappings().all()
            b = t.execute(table.select().order_by(*order)).mappings().all()
            if len(a) != len(b):
                mismatches += 1
                print(f"   {table.name}: {len(a)} rows in SQLite vs {len(b)} in MySQL")
                continue
            for ra, rb in zip(a, b):
                for col in table.columns:
                    if mst.normalise(ra[col.name], col) != mst.normalise(rb[col.name], col):
                        mismatches += 1
                        print(f"   {table.name} id={ra.get('id')} {col.name}: {ra[col.name]!r} -> {rb[col.name]!r}")
    if mismatches:
        sys.exit(f"Stopped: {mismatches} difference(s). The SQLite database is unchanged; do not switch.")
    print("   all values identical")

    step(6, "Golden master: billing engine on both databases")
    snap = os.path.join(TOOLS, "bill_snapshot.py")
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    old = json.loads(subprocess.run([sys.executable, snap, "--sqlite", backup], capture_output=True, text=True, env=env, check=True).stdout)
    new = json.loads(subprocess.run([sys.executable, snap, "--mysql"], capture_output=True, text=True, env=env, check=True).stdout)
    diffs = [(bid, old[bid], new.get(bid)) for bid in old if old[bid] != new.get(bid)]
    if diffs or set(old) != set(new):
        for bid, a, b in diffs[:10]:
            print(f"   bill {bid}: {a} -> {b}")
        sys.exit("Stopped: bill figures differ. Do not switch.")
    print(f"   {len(old)} bill(s): condo, parking, water, penalty, previous, advance, total, balance and status identical")

    print("\nMIGRATION VERIFIED. To use MySQL set DB_ENGINE=mysql in .env and restart the server.")


if __name__ == "__main__":
    main()
