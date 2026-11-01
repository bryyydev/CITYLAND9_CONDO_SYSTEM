"""Read-only preflight check of a SQLite database before migrating it to local MySQL.

    python database/tools/sqlite_preflight.py [PATH]     (default: cityland_condo_web.db)

The database file (plus its -wal/-shm files) is first COPIED to a temporary folder
and only the copy is opened, read-only. The original file is never opened.

Reports everything that MySQL/MariaDB in strict mode would reject or change:
  schema drift vs the models, row counts, strings longer than VARCHAR(n),
  decimals with more places than the column allows, wrongly typed values,
  invalid dates/times, NULLs in NOT NULL columns, orphaned foreign keys,
  duplicate values in unique columns, and duplicates in the combinations the
  application treats as unique (one bill per unit per month, etc.).

Exit code 0 = no blocking problems, 1 = blocking problems found.
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import warnings
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATABASE_URL"] = "sqlite://"
sys.path.insert(0, os.path.join(ROOT, "backend"))
warnings.filterwarnings("ignore")

# Combinations the application keeps unique in code only (checklist D12).
LOGICAL_UNIQUE = [
    ("billing", ("unit_id", "billing_month")),
    ("water_reading", ("unit_id", "reading_month")),
    ("parking_billing", ("parking_lot_id", "billing_month")),
    ("employee_attendance", ("employee_id", "attendance_date")),
    ("advance_application", ("advance_payment_id", "billing_id")),
]
MONTH_COLUMNS = [("billing", "billing_month"), ("water_reading", "reading_month"),
                 ("parking_billing", "billing_month"), ("advance_payment", "start_month"),
                 ("advance_application", "billing_month")]


class Report:
    def __init__(self):
        self.blocking, self.warnings, self.info = [], [], []

    def block(self, msg): self.blocking.append(msg)
    def warn(self, msg): self.warnings.append(msg)
    def note(self, msg): self.info.append(msg)


def copy_database(path):
    tmp = tempfile.mkdtemp(prefix="cityland9_preflight_")
    target = os.path.join(tmp, os.path.basename(path))
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            shutil.copy2(path + suffix, target + suffix)
    return target


def q(name):
    return '"' + name.replace('"', '""') + '"'


def check(path):
    import legacy_app
    metadata = legacy_app.db.metadata
    rep = Report()
    copy = copy_database(path)
    con = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    live_tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    model_tables = {t.name for t in metadata.sorted_tables}
    for t in sorted(model_tables - live_tables):
        rep.note(f"table {t} does not exist yet in SQLite (created on demand; nothing to migrate)")
    for t in sorted(live_tables - model_tables):
        rep.warn(f"table {t} exists in SQLite but not in the models: its data would NOT be migrated")
    if "parking_lot" in live_tables:   # retired in Phase B3 (migration 0005 converts lots on MySQL)
        lots = con.execute("SELECT COUNT(*) FROM parking_lot WHERE active = 1").fetchone()[0]
        if lots:
            rep.block(f"parking_lot has {lots} active lot(s): parking is now an assigned PARKING unit. "
                      "Assign PARKING units in the legacy app first, or the parking charges would be lost")
    unit_cols = {r[1] for r in con.execute("PRAGMA table_info(unit)")} if "unit" in live_tables else set()
    if "owner_name" in unit_cols and "owner" in live_tables:   # S4: the copy is no longer stored
        orphans = con.execute("SELECT COUNT(*) FROM unit WHERE TRIM(COALESCE(owner_name, '')) <> '' AND id NOT IN "
                              "(SELECT unit_id FROM owner)").fetchone()[0]
        if orphans:
            rep.block(f"{orphans} unit(s) have an owner name only on the unit (no owner record). "
                      "Add the owner on the unit page first, or the name would be lost")

    counts = {}
    for table in metadata.sorted_tables:
        if table.name not in live_tables:
            continue
        tq = q(table.name)
        counts[table.name] = con.execute(f"SELECT COUNT(*) FROM {tq}").fetchone()[0]
        live_cols = {r["name"] for r in con.execute(f"PRAGMA table_info({tq})")}
        for c in sorted(live_cols - {c.name for c in table.columns}):
            rep.warn(f"{table.name}.{c}: column exists in SQLite but not in the models (data not migrated)")
        for c in sorted({c.name for c in table.columns} - live_cols):
            rep.block(f"{table.name}.{c}: model column missing in SQLite (run the legacy app once so init_db() adds it)")

        for col in table.columns:
            if col.name not in live_cols:
                continue
            cq = q(col.name)
            col_type = col.type.impl if type(col.type).__name__ == "Variant" else col.type
            kind = type(col_type).__name__.upper()
            mysql_enum = getattr(col.type, "mapping", {}).get("mysql") if type(col.type).__name__ == "Variant" else None
            if mysql_enum is not None and type(mysql_enum).__name__ == "ENUM":
                bad = [v for (v,) in con.execute(f"SELECT DISTINCT {cq} FROM {tq} WHERE {cq} IS NOT NULL") if v not in mysql_enum.enums]
                if bad:
                    rep.block(f"{table.name}.{col.name}: value(s) {bad} are not allowed by the MySQL ENUM {list(mysql_enum.enums)}")
            if not col.nullable and not col.primary_key:
                n = con.execute(f"SELECT COUNT(*) FROM {tq} WHERE {cq} IS NULL").fetchone()[0]
                if n:
                    rep.block(f"{table.name}.{col.name}: {n} NULL value(s) in a NOT NULL column")
            if kind in ("STRING", "VARCHAR") and col_type.length:
                n, longest = con.execute(f"SELECT COUNT(*), MAX(LENGTH({cq})) FROM {tq} WHERE LENGTH({cq}) > ?", (col_type.length,)).fetchone()
                if n:
                    rep.block(f"{table.name}.{col.name}: {n} value(s) longer than VARCHAR({col_type.length}) (longest {longest})")
            if kind in ("INTEGER", "NUMERIC", "DECIMAL", "FLOAT", "BOOLEAN"):
                bad = con.execute(f"SELECT COUNT(*) FROM {tq} WHERE {cq} IS NOT NULL AND typeof({cq}) NOT IN ('integer','real')").fetchone()[0]
                if bad:
                    rep.block(f"{table.name}.{col.name}: {bad} non-numeric value(s) in a {kind} column")
            if kind == "BOOLEAN":
                bad = con.execute(f"SELECT COUNT(*) FROM {tq} WHERE {cq} IS NOT NULL AND {cq} NOT IN (0, 1)").fetchone()[0]
                if bad:
                    rep.block(f"{table.name}.{col.name}: {bad} boolean value(s) other than 0/1")
            if kind in ("NUMERIC", "DECIMAL"):
                scale, precision = col.type.scale or 0, col.type.precision or 12
                over_scale = too_big = 0
                for (v,) in con.execute(f"SELECT {cq} FROM {tq} WHERE {cq} IS NOT NULL AND typeof({cq}) IN ('integer','real')"):
                    try:
                        d = Decimal(str(v))
                    except InvalidOperation:
                        continue
                    if d != d.quantize(Decimal(1).scaleb(-scale)):
                        over_scale += 1
                    if abs(d) >= Decimal(10) ** (precision - scale):
                        too_big += 1
                if over_scale:
                    rep.warn(f"{table.name}.{col.name}: {over_scale} value(s) have more than {scale} decimal places and will be rounded by DECIMAL({precision},{scale})")
                if too_big:
                    rep.block(f"{table.name}.{col.name}: {too_big} value(s) exceed DECIMAL({precision},{scale})")
            if kind in ("DATE", "DATETIME", "TIME"):
                parse = {"DATE": date.fromisoformat, "DATETIME": datetime.fromisoformat, "TIME": time.fromisoformat}[kind]
                bad = 0
                for (v,) in con.execute(f"SELECT {cq} FROM {tq} WHERE {cq} IS NOT NULL"):
                    try:
                        parse(str(v))
                    except ValueError:
                        bad += 1
                if bad:
                    rep.block(f"{table.name}.{col.name}: {bad} invalid {kind} value(s)")

        for fk in table.foreign_keys:
            parent, ref = fk.parent.name, fk.column
            if parent not in live_cols or ref.table.name not in live_tables:
                continue
            n = con.execute(
                f"SELECT COUNT(*) FROM {tq} c WHERE c.{q(parent)} IS NOT NULL AND NOT EXISTS "
                f"(SELECT 1 FROM {q(ref.table.name)} p WHERE p.{q(ref.name)} = c.{q(parent)})").fetchone()[0]
            if n:
                rep.block(f"{table.name}.{parent}: {n} orphaned reference(s) to {ref.table.name}.{ref.name} (MySQL enforces foreign keys)")

        for col in table.columns:
            if col.unique and col.name in live_cols:
                cq = q(col.name)
                n = con.execute(f"SELECT COUNT(*) FROM (SELECT {cq} FROM {tq} WHERE {cq} IS NOT NULL GROUP BY {cq} HAVING COUNT(*) > 1)").fetchone()[0]
                if n:
                    rep.block(f"{table.name}.{col.name}: {n} duplicated value(s) in a UNIQUE column")
                n = con.execute(f"SELECT COUNT(*) FROM (SELECT LOWER({cq}) FROM {tq} WHERE {cq} IS NOT NULL GROUP BY LOWER({cq}) HAVING COUNT(*) > 1)").fetchone()[0]
                if n:
                    rep.note(f"{table.name}.{col.name}: {n} value(s) differ only by letter case (allowed by utf8mb4_bin)")

    for table, cols in LOGICAL_UNIQUE:
        if table in live_tables:
            n = con.execute(f"SELECT COUNT(*) FROM (SELECT 1 FROM {q(table)} GROUP BY {', '.join(map(q, cols))} HAVING COUNT(*) > 1)").fetchone()[0]
            if n:
                rep.warn(f"{table}: {n} duplicate {cols} combination(s); a future UNIQUE constraint needs a cleanup decision first")
    for table, col in MONTH_COLUMNS:
        if table in live_tables:
            n = con.execute(f"SELECT COUNT(*) FROM {q(table)} WHERE {q(col)} IS NOT NULL AND {q(col)} NOT GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]'").fetchone()[0]
            if n:
                rep.warn(f"{table}.{col}: {n} value(s) not in YYYY-MM format (month logic compares these as text)")

    con.close()
    shutil.rmtree(os.path.dirname(copy), ignore_errors=True)
    return rep, counts


def main():
    path = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "cityland_condo_web.db"))
    if not os.path.exists(path):
        print(f"Not found: {path}")
        return 2
    rep, counts = check(path)
    print(f"Preflight of {path}")
    print(f"Rows: {sum(counts.values())} in {len(counts)} tables  " + ", ".join(f"{t}={n}" for t, n in counts.items() if n))
    for title, items in (("BLOCKING", rep.blocking), ("WARNING", rep.warnings), ("INFO", rep.info)):
        print(f"\n{title} ({len(items)})")
        for item in items:
            print(f"  - {item}")
    print("\nRESULT:", "BLOCKED - fix the items above before migrating" if rep.blocking else "OK - no blocking problems")
    return 1 if rep.blocking else 0


if __name__ == "__main__":
    sys.exit(main())
