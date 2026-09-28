"""Test database/schema.sql against a NEW, EMPTY local MySQL/MariaDB database.

    python database/tools/mysql_schema_test.py [--create] [--roundtrip-sqlite PATH]

Connection settings come from the environment (or .env): MYSQL_HOST, MYSQL_PORT,
MYSQL_DATABASE, MYSQL_USER, MYSQL_PASSWORD.

Steps:
  1. Refuse to continue if the target database already contains any table.
  2. Apply database/schema.sql.
  3. Reflect the created schema and compare it with the SQLAlchemy models:
     tables, columns, types, nullability, primary keys, foreign keys, unique
     constraints and indexes.
  4. Optional --roundtrip-sqlite PATH: copy every row of a TEST SQLite database into
     the new MySQL database (in foreign-key order, keeping ids), read it back and
     compare every value. Refuses to read the production cityland_condo_web.db.

It never touches cityland_condo_web.db and never drops or alters anything.
"""
import argparse
import os
import sys
import warnings
from datetime import date, datetime, time
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PRODUCTION_DB = os.path.normcase(os.path.abspath(os.path.join(ROOT, "cityland_condo_web.db")))
os.environ["DATABASE_URL"] = "sqlite://"  # models are imported against an in-memory DB
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
warnings.filterwarnings("ignore")

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    pass

from sqlalchemy import create_engine, inspect, text  # noqa: E402
from sqlalchemy.engine import URL  # noqa: E402

import generate_schema  # noqa: E402  (registers the DOUBLE / DATETIME(6) compile rules)

FAILURES = []


def fail(msg):
    FAILURES.append(msg)
    print("  FAIL", msg)


def mysql_url(database):
    return URL.create(
        "mysql+pymysql",
        username=os.getenv("MYSQL_USER", ""),
        password=os.getenv("MYSQL_PASSWORD", ""),
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        database=database,
        query={"charset": "utf8mb4"},
    )


def expected_type(col):
    """Normalised type family expected in MySQL for a model column."""
    name = type(col.type).__name__.upper()
    if name in ("INTEGER",):
        return "INT"
    if name in ("STRING", "VARCHAR"):
        return f"VARCHAR({col.type.length})"
    if name == "TEXT":
        return "TEXT"
    if name in ("NUMERIC", "DECIMAL"):
        return f"DECIMAL({col.type.precision},{col.type.scale})"
    if name == "FLOAT":
        return "DOUBLE"
    if name == "BOOLEAN":
        return "TINYINT"
    if name == "DATETIME":
        return "DATETIME(6)"
    return name  # DATE, TIME


def actual_type(reflected):
    t = reflected["type"]
    name = type(t).__name__.upper()
    if name in ("INTEGER", "INT"):
        return "INT"
    if name == "VARCHAR":
        return f"VARCHAR({t.length})"
    if name in ("DECIMAL", "NUMERIC"):
        return f"DECIMAL({t.precision},{t.scale})"
    if name == "DATETIME":
        return f"DATETIME({getattr(t, 'fsp', None) or 0})"
    if name in ("TINYINT", "BOOLEAN"):
        return "TINYINT"
    return name


def compare_schema(engine, metadata):
    insp = inspect(engine)
    live_tables = set(insp.get_table_names())
    model_tables = {t.name for t in metadata.sorted_tables}
    for missing in sorted(model_tables - live_tables):
        fail(f"table missing in MySQL: {missing}")
    for extra in sorted(live_tables - model_tables):
        fail(f"unexpected table in MySQL: {extra}")

    checked_cols = 0
    for table in metadata.sorted_tables:
        if table.name not in live_tables:
            continue
        live_cols = {c["name"]: c for c in insp.get_columns(table.name)}
        for col in table.columns:
            live = live_cols.get(col.name)
            if live is None:
                fail(f"{table.name}.{col.name}: column missing")
                continue
            checked_cols += 1
            if expected_type(col) != actual_type(live):
                fail(f"{table.name}.{col.name}: type {actual_type(live)} != expected {expected_type(col)}")
            want_nullable = col.nullable and not col.primary_key
            if bool(live["nullable"]) != want_nullable:
                fail(f"{table.name}.{col.name}: nullable {live['nullable']} != expected {want_nullable}")
        for extra in set(live_cols) - {c.name for c in table.columns}:
            fail(f"{table.name}.{extra}: unexpected column")

        pk = insp.get_pk_constraint(table.name)["constrained_columns"]
        if pk != [c.name for c in table.primary_key.columns]:
            fail(f"{table.name}: primary key {pk}")

        live_fks = {(tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"]))
                    for fk in insp.get_foreign_keys(table.name)}
        model_fks = {((fk.parent.name,), fk.column.table.name, (fk.column.name,)) for fk in table.foreign_keys}
        if live_fks != model_fks:
            fail(f"{table.name}: foreign keys {sorted(live_fks)} != expected {sorted(model_fks)}")

        live_unique = {tuple(u["column_names"]) for u in insp.get_unique_constraints(table.name)}
        live_unique |= {tuple(i["column_names"]) for i in insp.get_indexes(table.name) if i.get("unique")}
        model_unique = {(c.name,) for c in table.columns if c.unique}
        model_unique |= {tuple(c.name for c in i.columns) for i in table.indexes if i.unique}
        if not model_unique <= live_unique:
            fail(f"{table.name}: unique {sorted(live_unique)} missing {sorted(model_unique - live_unique)}")

        live_index_names = {i["name"] for i in insp.get_indexes(table.name)}
        wanted = {i.name for i in table.indexes}
        wanted |= {name for name, tbl, _ in generate_schema.RUNTIME_INDEXES if tbl == table.name}
        for name in sorted(wanted - live_index_names):
            fail(f"{table.name}: index {name} missing")

    print(f"  compared {len(model_tables)} tables / {checked_cols} columns")


def normalise(value, col):
    if value is None:
        return None
    type_name = type(col.type).__name__.upper()
    if type_name in ("NUMERIC", "DECIMAL"):
        return Decimal(str(value)).quantize(Decimal(1).scaleb(-(col.type.scale or 0)))
    if type_name == "BOOLEAN":
        return bool(value)
    if type_name == "FLOAT":
        return float(value)
    if isinstance(value, (datetime, date, time)):
        return value
    return value


def roundtrip(sqlite_path, mysql_engine, metadata):
    source = create_engine(f"sqlite:///file:{os.path.abspath(sqlite_path).replace(os.sep, '/')}?mode=ro&uri=true")
    total = rounded = 0
    with source.connect() as src, mysql_engine.begin() as dst:
        for table in metadata.sorted_tables:
            rows = [dict(r) for r in src.execute(table.select().order_by(*table.primary_key.columns)).mappings()]
            if rows:
                dst.execute(table.insert(), rows)
            total += len(rows)
            for r in rows:
                for col in table.columns:
                    v = r[col.name]
                    if v is not None and type(col.type).__name__.upper() in ("NUMERIC", "DECIMAL") and Decimal(str(v)) != normalise(v, col):
                        rounded += 1
    print(f"  copied {total} rows from {sqlite_path}")
    if rounded:
        print(f"  NOTE {rounded} DECIMAL value(s) were rounded to the column scale on insert")

    with source.connect() as src, mysql_engine.connect() as dst:
        mismatches = 0
        for table in metadata.sorted_tables:
            order = list(table.primary_key.columns)
            a = src.execute(table.select().order_by(*order)).mappings().all()
            b = dst.execute(table.select().order_by(*order)).mappings().all()
            if len(a) != len(b):
                fail(f"{table.name}: {len(a)} source rows vs {len(b)} in MySQL")
                continue
            for ra, rb in zip(a, b):
                for col in table.columns:
                    if normalise(ra[col.name], col) != normalise(rb[col.name], col):
                        mismatches += 1
                        if mismatches <= 20:
                            fail(f"{table.name} id={ra.get('id')} {col.name}: {ra[col.name]!r} -> {rb[col.name]!r}")
        print(f"  compared every value of {total} rows: {mismatches} mismatch(es)")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--create", action="store_true", help="create MYSQL_DATABASE if it does not exist")
    parser.add_argument("--roundtrip-sqlite", metavar="PATH", help="copy a TEST SQLite database and compare values")
    args = parser.parse_args()

    database = os.getenv("MYSQL_DATABASE", "")
    if not database or not os.getenv("MYSQL_USER"):
        print("Set MYSQL_HOST, MYSQL_PORT, MYSQL_DATABASE, MYSQL_USER and MYSQL_PASSWORD first.")
        return 2
    if args.roundtrip_sqlite and os.path.normcase(os.path.abspath(args.roundtrip_sqlite)) == PRODUCTION_DB:
        print("Refusing to read the production database. Use a test/seed SQLite file.")
        return 2

    server = create_engine(mysql_url(None))
    with server.connect() as conn:
        version = conn.execute(text("SELECT VERSION()")).scalar()
        exists = conn.execute(text("SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = :d"), {"d": database}).scalar()
        if not exists:
            if not args.create:
                print(f"Database {database!r} does not exist. Re-run with --create to create it.")
                return 2
            conn.execute(text(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"))
        tables = conn.execute(text("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = :d"), {"d": database}).scalar()
    print(f"Server: {version}   Database: {database}")
    if tables:
        print(f"Refusing to continue: {database!r} already contains {tables} table(s). Use a NEW empty database.")
        return 2

    engine = create_engine(mysql_url(database))
    schema_sql = open(generate_schema.SCHEMA_PATH, encoding="utf-8").read()
    statements = [s.strip() for s in schema_sql.split(";\n") if s.strip() and not all(
        line.startswith("--") or not line.strip() for line in s.strip().splitlines())]
    print(f"1. Applying schema.sql ({len(statements)} statements)")
    with engine.begin() as conn:
        for stmt in statements:
            conn.exec_driver_sql(stmt.rstrip(";"))

    import legacy_app
    metadata = legacy_app.db.metadata
    print("2. Comparing live schema with the models")
    compare_schema(engine, metadata)

    if args.roundtrip_sqlite:
        print("3. Round-trip of test data")
        roundtrip(args.roundtrip_sqlite, engine, metadata)

    print()
    if FAILURES:
        print(f"RESULT: FAILED ({len(FAILURES)} problem(s))")
        return 1
    print("RESULT: PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
