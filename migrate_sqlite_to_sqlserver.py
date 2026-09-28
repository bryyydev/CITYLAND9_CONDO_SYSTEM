"""Migrate the existing CityLand 9 SQLite database to SQL Server.

Usage (from the system folder):
  set TARGET_DATABASE_URL=mssql+pyodbc://@localhost\\SQLEXPRESS/CityLand9?driver=ODBC+Driver+18+for+SQL+Server&trusted_connection=yes&TrustServerCertificate=yes
  .venv\\Scripts\\python.exe migrate_sqlite_to_sqlserver.py

The source SQLite database is cityland_condo_web.db in the project root unless
SOURCE_SQLITE_PATH is supplied. The script creates the SQL Server schema and
copies rows while preserving primary-key IDs.
"""
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

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

print(f"Source: {source_path}")
print(f"Target: {target_url.split('@')[-1]}")

with source.connect() as src, target.begin() as dst:
    db.metadata.create_all(dst)
    tables = db.metadata.sorted_tables

    for table in tables:
        rows = src.execute(table.select()).mappings().all()
        if not rows:
            print(f"{table.name}: 0 rows")
            continue

        # SQL Server identity columns reject explicit IDs unless IDENTITY_INSERT
        # is enabled for that table on the same connection.
        identity_cols = [
            c for c in table.columns
            if c.primary_key and c.autoincrement is True
        ]
        if identity_cols:
            dst.execute(text(f"SET IDENTITY_INSERT {table.name} ON"))

        try:
            dst.execute(table.insert(), [dict(r) for r in rows])
        finally:
            if identity_cols:
                dst.execute(text(f"SET IDENTITY_INSERT {table.name} OFF"))

        print(f"{table.name}: {len(rows)} rows")

print("Migration completed successfully.")
