"""Restore a CityLand 9 backup into a SEPARATE database and verify it.

    .venv\\Scripts\\python.exe database\\restore.py <backup.sql.gz|.sql> [--into NAME] [--drop-after]
    .venv\\Scripts\\python.exe database\\restore.py <backup.sql.gz|.sql> --replace-live

  --into NAME     target database (default: cityland9_restore_test). Created if missing; must be empty.
                  The live database (MYSQL_DATABASE) is refused here.
  --drop-after    drop the target after a successful verification (a pure restore test)
  --replace-live  DISASTER RECOVERY: replace the live database with the backup. Refused while the web
                  server answers on FLASK_PORT; asks you to type the database name; takes a safety
                  backup of the current live data first (database/backups/cityland9_before_restore_*).
                  Restore-test the same file with --into first (docs/run-guide/07-OPERATIONS.md).

Verification: every table of the current schema exists, the schema version is recorded, the row
count of every table is printed, and every bill's computed figures are produced (bill_snapshot).
Uses the root account through a temporary private options file (never on the command line).
"""
import gzip
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "database"))
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    pass

import local_mysql as lm  # noqa: E402


def load(path, database):
    opener = gzip.open if path.endswith(".gz") else open
    with lm.client_options() as opt, opener(path, "rb") as dump:
        proc = subprocess.Popen([lm.exe("mysql"), f"--defaults-extra-file={opt}", "--default-character-set=utf8mb4", database],
                                stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        for chunk in iter(lambda: dump.read(1024 * 1024), b""):
            proc.stdin.write(chunk)
        proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "replace")
        if proc.wait() != 0:
            raise RuntimeError(f"restore failed: {err.strip()[:400]}")


def verify(database):
    con = lm.root_connect(database)
    try:
        with con.cursor() as cur:
            cur.execute("SHOW TABLES")
            tables = sorted(r[0] for r in cur.fetchall())
            counts = {}
            for t in tables:
                cur.execute(f"SELECT COUNT(*) FROM `{t}`")
                counts[t] = cur.fetchone()[0]
            revision = None
            if "alembic_version" in tables:
                cur.execute("SELECT version_num FROM alembic_version")
                row = cur.fetchone()
                revision = row[0] if row else None
    finally:
        con.close()
    with open(os.path.join(ROOT, "database", "schema.sql"), encoding="utf-8") as fh:
        expected = {line.split()[2] for line in fh if line.startswith("CREATE TABLE ")}
    missing = sorted(expected - set(tables))
    env = {**os.environ, "MYSQL_DATABASE": database}
    env.pop("DATABASE_URL", None)
    snap = subprocess.run([sys.executable, os.path.join(ROOT, "database", "tools", "bill_snapshot.py"), "--mysql"],
                          cwd=ROOT, env=env, capture_output=True, text=True)
    bills = len(json.loads(snap.stdout)) if snap.returncode == 0 else None
    return {"tables": len(tables), "missing": missing, "revision": revision, "rows": counts, "bills_computed": bills,
            "bill_error": None if snap.returncode == 0 else snap.stderr[-300:]}


def server_running():
    import socket
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", int(os.getenv("FLASK_PORT", "5000")))) == 0


def replace_live(path):
    database = lm.DATABASE
    if server_running():
        sys.exit("The CityLand 9 web server is running. Stop it first (STOP_WINDOWS.bat --keep-db), so nobody "
                 "enters data that the restore would erase.")
    print(f"This REPLACES ALL DATA in the live database `{database}` with:\n    {path}")
    if input(f"Type the database name ({database}) to continue: ").strip() != database:
        sys.exit("Not confirmed. Nothing was changed.")
    lm.start()
    print("Safety backup of the current data:", lm.backup(label="before_restore"))
    con = lm.root_connect()
    try:
        with con.cursor() as cur:
            cur.execute(f"DROP DATABASE `{database}`")   # account rights on the name are kept by MariaDB
            cur.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin")
    finally:
        con.close()
    print(f"Restoring into `{database}` ...")
    load(path, database)
    result = verify(database)
    print(f"Tables: {result['tables']}  schema version: {result['revision']}  bills computed: {result['bills_computed']}")
    if result["missing"] or result["bills_computed"] is None:
        print("MISSING TABLES: " + ", ".join(result["missing"]) if result["missing"] else result["bill_error"])
        sys.exit("RESTORE NOT VERIFIED. The safety backup above holds the data from before the restore. If the backup "
                 "is from an older version, run: database\\db_migrate.py upgrade")
    print("LIVE DATABASE RESTORED AND VERIFIED. Start the system again.")


def main():
    args = sys.argv[1:]
    if not args or args[0].startswith("-"):
        sys.exit(__doc__)
    path = os.path.abspath(args[0])
    if not os.path.exists(path):
        sys.exit(f"No such file: {path}")
    if "--replace-live" in args:
        replace_live(path)
        return
    target = args[args.index("--into") + 1] if "--into" in args else "cityland9_restore_test"
    if target == lm.DATABASE:
        sys.exit(f"Refusing to restore over the live database `{lm.DATABASE}`. Restore into a separate database first "
                 "(or, for disaster recovery, use --replace-live).")
    lm.start()
    con = lm.root_connect()
    try:
        with con.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{target}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin")
            cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s", (target,))
            if cur.fetchone()[0]:
                sys.exit(f"`{target}` is not empty. Choose another name or drop it first.")
            cur.execute("SELECT CONCAT(QUOTE(user),'@',QUOTE(host)) FROM mysql.user WHERE user=%s", (lm.APP_USER,))
            for account in [r[0] for r in cur.fetchall()]:
                cur.execute(f"GRANT SELECT ON `{target}`.* TO {account}")   # read access for the verification
    finally:
        con.close()
    print(f"Restoring {path} into `{target}` ...")
    load(path, target)
    result = verify(target)
    print(f"Tables: {result['tables']}  schema version: {result['revision']}  bills computed: {result['bills_computed']}")
    for table, n in result["rows"].items():
        print(f"  {table:32} {n:>8}")
    ok = not result["missing"] and result["bills_computed"] is not None
    if result["missing"]:
        print("MISSING TABLES: " + ", ".join(result["missing"]))
    if result["bill_error"]:
        print("Bill check failed: " + result["bill_error"])
    if ok and "--drop-after" in args:
        con = lm.root_connect()
        with con.cursor() as cur:
            cur.execute(f"DROP DATABASE `{target}`")
            cur.execute("SELECT CONCAT(QUOTE(user),'@',QUOTE(host)) FROM mysql.user WHERE user=%s", (lm.APP_USER,))
            for account in [r[0] for r in cur.fetchall()]:
                cur.execute(f"REVOKE ALL PRIVILEGES ON `{target}`.* FROM {account}")
        con.close()
        print(f"Verified and dropped `{target}`.")
    print("RESTORE VERIFIED" if ok else "RESTORE NOT VERIFIED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
