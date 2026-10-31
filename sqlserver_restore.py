"""Restore-test or replace a local SQL Server CityLand database.

    .venv-sqlserver\\Scripts\\python.exe database\\sqlserver_restore.py <backup.bak> [--into NAME] [--drop-after]
    .venv-sqlserver\\Scripts\\python.exe database\\sqlserver_restore.py <backup.bak> --replace-live

Restore tests always use a new database. Replacing the configured database requires the
server to be stopped and an exact typed confirmation; a verified safety backup is made first.
"""
import os
import socket
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sqlserver  # noqa: E402


def _server_running():
    with socket.socket() as connection:
        connection.settimeout(1)
        return connection.connect_ex(("127.0.0.1", int(os.getenv("FLASK_PORT", "5000")))) == 0


def main(args=None):
    args = list(sys.argv[1:] if args is None else args)
    if not args or args[0].startswith("-"):
        sys.exit(__doc__)
    backup_path = Path(args.pop(0)).resolve()
    if not backup_path.is_file():
        sys.exit(f"No such backup file: {backup_path}")
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        sys.exit("DATABASE_URL is not set.")

    if "--replace-live" in args:
        if "--drop-after" in args or "--into" in args:
            sys.exit("--replace-live cannot be combined with --into or --drop-after.")
        if _server_running():
            sys.exit("CityLand is running. Stop it first with STOP_WINDOWS.bat --keep-db.")
        from sqlalchemy.engine import make_url
        database_name = make_url(database_url).database
        print(f"This REPLACES ALL DATA in the live SQL Server database `{database_name}` with:\n    {backup_path}")
        if input(f"Type the database name ({database_name}) to continue: ").strip() != database_name:
            sys.exit("Not confirmed. Nothing was changed.")
        backup_dir = Path(os.getenv("BACKUP_DIR") or ROOT / "database" / "backups")
        backup_dir.mkdir(parents=True, exist_ok=True)
        safety = backup_dir / f"cityland9_before_restore_{datetime.now():%Y%m%d_%H%M%S}.bak"
        safety_path = sqlserver.restore_live_database(database_url, backup_path, safety)
        print(f"LIVE DATABASE RESTORED. Verified safety backup: {safety_path}")
        return 0

    target = "CityLand9_Restore_Test"
    if "--into" in args:
        index = args.index("--into")
        if index + 1 >= len(args):
            sys.exit("--into requires a database name.")
        target = args[index + 1]
        del args[index:index + 2]
    drop_after = "--drop-after" in args
    args = [arg for arg in args if arg != "--drop-after"]
    if args:
        sys.exit("Unknown option(s): " + " ".join(args))

    result = sqlserver.restore_test_database(database_url, backup_path, target)
    print(f"Restore test: {result['database']}; tables: {result['tables']}")
    for table, count in result["rows"].items():
        print(f"  {table:32} {count:>8}")
    if result["tables"] == 0:
        sys.exit("RESTORE NOT VERIFIED: the restored database has no tables.")
    if drop_after:
        sqlserver.drop_database(database_url, target)
        print(f"Verified and dropped `{target}`.")
    print("RESTORE VERIFIED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
