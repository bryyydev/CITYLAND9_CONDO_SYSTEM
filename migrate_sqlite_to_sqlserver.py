"""Migrate the existing CityLand 9 SQLite database to SQL Server.

Usage (from the system folder):
  set TARGET_DATABASE_URL=mssql+pyodbc://@localhost\\SQLEXPRESS/CityLand9?driver=ODBC+Driver+18+for+SQL+Server&trusted_connection=yes&TrustServerCertificate=yes
  .venv-sqlserver\\Scripts\\python.exe migrate_sqlite_to_sqlserver.py

The source SQLite database is cityland_condo_web.db in the project root unless
SOURCE_SQLITE_PATH is supplied. The script creates the SQL Server schema and
copies rows while preserving primary-key IDs, Unicode text and timestamp precision.
It refuses a non-empty target and verifies every copied row before reporting success.
"""
import os
import sys
from pathlib import Path

from sqlalchemy import DateTime, MetaData, String, Text, create_engine, inspect, text
from sqlalchemy.dialects.mssql import DATETIME2, NVARCHAR

BASE_DIR = Path(__file__).resolve().parent
source_path = Path(os.getenv("SOURCE_SQLITE_PATH", str(BASE_DIR / "cityland_condo_web.db")))
target_url = os.getenv("TARGET_DATABASE_URL", "").strip()

if not source_path.exists():
    raise SystemExit(f"Source SQLite database not found: {source_path}")
if not target_url:
    raise SystemExit("TARGET_DATABASE_URL is not set.")

# backend/legacy_app.py uses DATABASE_URL at import time. Force it to SQLite so we can reuse
# the exact model metadata already shipped with this application.
os.environ["DATABASE_URL"] = "sqlite:///" + source_path.as_posix()

sys.path.insert(0, str(BASE_DIR / "backend"))
from legacy_app import db  # noqa: E402

source = create_engine("sqlite:///" + source_path.as_posix())
target = create_engine(target_url, future=True)
target_metadata = MetaData()
for source_table in db.metadata.sorted_tables:
    target_table = source_table.to_metadata(target_metadata)
    for column in target_table.columns:
        if isinstance(column.type, Text):
            column.type = NVARCHAR()
        elif isinstance(column.type, String):
            column.type = NVARCHAR(column.type.length)
        elif isinstance(column.type, DateTime):
            column.type = DATETIME2()

print(f"Source: {source_path}")
print(f"Target: {target_url.split('@')[-1]}")

with source.connect() as src, target.begin() as dst:
    existing_tables = inspect(dst).get_table_names()
    if existing_tables:
        raise SystemExit(
            "Target database is not empty ("
            + ", ".join(sorted(existing_tables)[:8])
            + "). Use a new, empty SQL Server database; nothing was copied."
        )
    target_metadata.create_all(dst)
    tables = db.metadata.sorted_tables
    source_total = target_total = 0

    for table in tables:
        target_table = target_metadata.tables[table.key]
        rows = src.execute(table.select()).mappings().all()
        source_total += len(rows)
        if not rows:
            print(f"{table.name}: 0 rows")
            continue

        # SQL Server identity columns reject explicit IDs unless IDENTITY_INSERT
        # is enabled for that table on the same connection.
        identity_cols = [
            c for c in target_table.columns
            if c.primary_key and c.autoincrement is True
        ]
        if identity_cols:
            quoted_table = target.dialect.identifier_preparer.format_table(target_table)
            dst.execute(text(f"SET IDENTITY_INSERT {quoted_table} ON"))

        try:
            dst.execute(target_table.insert(), [dict(r) for r in rows])
        finally:
            if identity_cols:
                dst.execute(text(f"SET IDENTITY_INSERT {quoted_table} OFF"))

        copied = dst.execute(target_table.select()).mappings().all()
        target_total += len(copied)
        primary_key = list(target_table.primary_key.columns)

        def row_key(row):
            return tuple(row[column.name] for column in primary_key)

        copied_by_key = {row_key(row): row for row in copied}
        for row in rows:
            target_row = copied_by_key.get(row_key(row))
            if target_row is None:
                raise RuntimeError(f"{table.name}: copied row is missing its primary key.")
            differences = [
                column.name for column in table.columns
                if row[column.name] != target_row[column.name]
            ]
            if differences:
                raise RuntimeError(
                    f"{table.name}: copied values differ in column(s): {', '.join(differences)}."
                )

        print(f"{table.name}: {len(rows)} rows")

if source_total != target_total:
    raise RuntimeError(f"Row count mismatch after migration: source={source_total}, target={target_total}.")
print(f"Migration verified: {len(tables)} tables, {target_total} rows; every primary key and column value matches.")
