"""Safe schema migrations for CityLand 9's MySQL database.

    python database/db_migrate.py status              current revision and pending ones
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


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("status", "upgrade", "downgrade", "stamp"):
        sys.exit(__doc__)
    action, target = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "head")
    cfg = config()
    head = ScriptDirectory.from_config(cfg).get_current_head()

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
