"""Safe schema migrations for CityLand 9's MySQL database.

    python database/db_migrate.py status              current revision and pending ones
    python database/db_migrate.py install             NEW installation: create every table in an empty database
                                                      (database/schema.sql) and mark it as up to date
    python database/db_migrate.py check               exit 1 if the database is behind the code (used at startup)
    python database/db_migrate.py upgrade [REV]       default: head
    python database/db_migrate.py downgrade REV       e.g. 0001_baseline
    python database/db_migrate.py stamp REV           mark an existing database (no changes)

upgrade/downgrade always:
  1. take a mysqldump backup (database/backups/cityland9_before_migration_*.sql)
  2. snapshot every bill's computed figures (golden master)
  3. apply the migration(s)
  4. check the live schema matches the models (after upgrade to head)
  5. compare the bill figures again - any difference is reported loudly
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "database"))
sys.path.insert(0, os.path.join(ROOT, "database", "tools"))
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    pass

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from alembic.script import ScriptDirectory  # noqa: E402


def config():
    return Config(os.path.join(ROOT, "database", "alembic.ini"))


def bill_figures():
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    out = subprocess.run([sys.executable, os.path.join(ROOT, "database", "tools", "bill_snapshot.py"), "--mysql"],
                         capture_output=True, text=True, env=env, check=True).stdout
    return json.loads(out)


def verify_schema():
    import mysql_schema_test as mst
    import legacy_app
    from sqlalchemy import create_engine
    mst.compare_schema(create_engine(mst.mysql_url(os.getenv("MYSQL_DATABASE"))), legacy_app.db.metadata)
    return not mst.FAILURES


def current_revision():
    """The database's Alembic revision (None when never stamped)."""
    import mysql_schema_test as mst
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine
    engine = create_engine(mst.mysql_url(os.getenv("MYSQL_DATABASE")))
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()


def check(head):
    """Startup guard: refuse to run new code on an older schema."""
    if os.getenv("DB_ENGINE", "").strip().lower() != "mysql" or os.getenv("DATABASE_URL"):
        print("Schema check skipped (not CityLand's MariaDB).")
        return
    import local_mysql
    local_mysql.start()
    current = current_revision()
    if current == head:
        print(f"Database schema is up to date ({head}).")
        return
    sys.exit(f"The database schema ({current or 'not stamped'}) is older than this version of CityLand 9 ({head}).\n"
             "Upgrade it first (a backup is taken automatically):\n"
             r"    .venv\Scripts\python.exe database\db_migrate.py upgrade")


def install(cfg):
    """New installation only: refuses a database that already has tables (use upgrade for those)."""
    import local_mysql
    local_mysql.start()
    database = os.getenv("MYSQL_DATABASE")
    con = local_mysql.root_connect()
    try:
        with con.cursor() as cur:
            cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (database,))
            existing = [row[0] for row in cur.fetchall()]
    finally:
        con.close()
    if existing:
        sys.exit(f"`{database}` already has {len(existing)} table(s); install is only for an empty database.\n"
                 "To bring an existing database up to date use:  python database/db_migrate.py upgrade")
    user, password = os.getenv("MYSQL_MIGRATE_USER"), os.getenv("MYSQL_MIGRATE_PASSWORD")
    options = local_mysql.client_options(user, password) if user and password else local_mysql.client_options()
    with options as opt, open(os.path.join(ROOT, "database", "schema.sql"), "rb") as schema:
        result = subprocess.run([local_mysql.exe("mysql"), f"--defaults-extra-file={opt}", "--default-character-set=utf8mb4",
                                 database], stdin=schema, capture_output=True)
    if result.returncode:
        sys.exit("Creating the tables failed: " + result.stderr.decode("utf-8", "replace").strip()[:500])
    command.stamp(cfg, "head")
    ok = verify_schema()
    print(f"Tables created in `{database}`; schema version {ScriptDirectory.from_config(cfg).get_current_head()}. "
          f"Matches the models: {'yes' if ok else 'NO - see FAIL lines above'}")
    if not ok:
        sys.exit(1)
    print(r"Next: create the first Superadmin:  .venv\Scripts\python.exe database\create_admin.py")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("status", "upgrade", "downgrade", "stamp", "check", "install"):
        sys.exit(__doc__)
    action, target = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "head")
    cfg = config()
    head = ScriptDirectory.from_config(cfg).get_current_head()

    if action == "check":
        check(head)
        return
    if action == "install":
        install(cfg)
        return
    if action == "status":
        command.current(cfg)
        print(f"latest available: {head}")
        return
    if action == "stamp":
        command.stamp(cfg, target)
        return
    if action == "downgrade" and target == "head":
        sys.exit("Give the revision to go back to, e.g.: python database/db_migrate.py downgrade 0001_baseline")

    import local_mysql
    local_mysql.start()
    print("1. Backup:", local_mysql.backup(label="before_migration"))
    before = bill_figures()
    print(f"2. Golden master: {len(before)} bill(s) recorded")
    print(f"3. {action} -> {target}")
    (command.upgrade if action == "upgrade" else command.downgrade)(cfg, target)
    if action == "upgrade" and target in ("head", head):
        print("4. Live schema matches the models:", "yes" if verify_schema() else "NO - see FAIL lines above")
    after = bill_figures()
    changed = [bid for bid in before if before[bid] != after.get(bid)]
    if changed or set(before) != set(after):
        sys.exit(f"5. WARNING: bill figures changed for {changed}. Restore the backup above if this was not intended.")
    print(f"5. Bill figures unchanged ({len(after)} bills)")


if __name__ == "__main__":
    main()
