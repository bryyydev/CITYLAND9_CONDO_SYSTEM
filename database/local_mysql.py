"""CityLand 9's own local MySQL-compatible database server (MariaDB/MySQL binaries).

    python database/local_mysql.py init     one time: data folder, users, database, .env settings
    python database/local_mysql.py start    start the server in the background (does nothing if running)
    python database/local_mysql.py stop
    python database/local_mysql.py status
    python database/local_mysql.py backup   mysqldump -> database/backups/cityland9_<timestamp>.sql
    python database/local_mysql.py secure   least privilege: app account = data only, separate migration account

Accounts (secure): MYSQL_USER (the application) may only SELECT/INSERT/UPDATE/DELETE in the
CityLand database; MYSQL_MIGRATE_USER (schema migrations, db_migrate.py) may also change tables.
Root is used only for setup, backups and restores. Passwords are never put on a command line
(other users could see them in the process list): a temporary private options file is used.

It uses the MariaDB/MySQL programs in MYSQL_BIN_DIR (default: XAMPP's C:\\xampp\\mysql\\bin)
but keeps its OWN data folder (MYSQL_DATA_DIR, default database/mysql-data) and port
(MYSQL_PORT, default 3307), listening on 127.0.0.1 only. It never touches another
installation's data (e.g. XAMPP's own databases).

Secrets (root and application passwords) are generated here and written only to the
local .env file, which is excluded from git.
"""
import os
import secrets
import subprocess
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(ROOT, ".env")

try:
    from dotenv import load_dotenv
    load_dotenv(ENV_PATH)
except ImportError:
    pass

import pymysql  # noqa: E402

BIN_DIR = os.getenv("MYSQL_BIN_DIR", r"C:\xampp\mysql\bin")
DATA_DIR = os.getenv("MYSQL_DATA_DIR") or os.path.join(ROOT, "database", "mysql-data")
HOST = "127.0.0.1"
PORT = int(os.getenv("MYSQL_PORT", "3307"))
DATABASE = os.getenv("MYSQL_DATABASE", "cityland9")
APP_USER = os.getenv("MYSQL_USER", "cityland9_app")
BACKUP_DIR = os.getenv("BACKUP_DIR") or os.path.join(ROOT, "database", "backups")


def exe(name):
    path = os.path.join(BIN_DIR, name + ".exe" if os.name == "nt" else name)
    if not os.path.exists(path):
        sys.exit(f"{path} not found. Set MYSQL_BIN_DIR in .env to the folder that contains {name}.")
    return path


class client_options:
    """Temporary MySQL options file holding the credentials, so they never appear on a command line.
    with client_options() as opt: subprocess.run([exe("mysqldump"), f"--defaults-extra-file={opt}", ...])"""

    def __init__(self, user="root", password=None):
        self.user = user
        self.password = os.getenv("MYSQL_ROOT_PASSWORD", "") if password is None else password

    def __enter__(self):
        import tempfile
        fd, self.path = tempfile.mkstemp(prefix="cl9_", suffix=".cnf")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"[client]\nuser={self.user}\npassword=\"{self.password}\"\nhost={HOST}\nport={PORT}\n")
        return self.path

    def __exit__(self, *exc):
        try:
            os.remove(self.path)
        except OSError:
            pass


def ini_path():
    return os.path.join(DATA_DIR, "my.ini")


def root_connect(database=None):
    return pymysql.connect(host=HOST, port=PORT, user="root", password=os.getenv("MYSQL_ROOT_PASSWORD", ""),
                           database=database, charset="utf8mb4", connect_timeout=3)


def is_running():
    try:
        root_connect().close()
        return True
    except pymysql.err.OperationalError as exc:
        # 1045 = access denied -> a server IS answering on this port (wrong password)
        return exc.args and exc.args[0] == 1045


def set_env(values):
    """Add missing keys to .env (existing values are never overwritten)."""
    lines = open(ENV_PATH, encoding="utf-8").read().splitlines() if os.path.exists(ENV_PATH) else []
    present = {line.split("=", 1)[0].strip() for line in lines if "=" in line and not line.lstrip().startswith("#")}
    added = [f"{k}={v}" for k, v in values.items() if k not in present]
    if added:
        with open(ENV_PATH, "a", encoding="utf-8", newline="\n") as fh:
            if lines and lines[-1].strip():
                fh.write("\n")
            fh.write("\n".join(added) + "\n")
        for k, v in values.items():
            os.environ.setdefault(k, v)
    return [a.split("=", 1)[0] for a in added]


def start(wait=30):
    if is_running():
        return True
    if not os.path.exists(ini_path()):
        sys.exit("The CityLand database is not set up yet. Run: python database/local_mysql.py init")
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    subprocess.Popen([exe("mysqld"), f"--defaults-file={ini_path()}"], cwd=DATA_DIR, creationflags=flags,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, close_fds=True)
    for _ in range(wait * 2):
        if is_running():
            return True
        time.sleep(0.5)
    sys.exit(f"The database server did not start. See the log in {DATA_DIR}")


def stop():
    if not is_running():
        return
    with client_options() as opt:
        subprocess.run([exe("mysqladmin"), f"--defaults-extra-file={opt}", "shutdown"], check=True, capture_output=True)
    for _ in range(40):
        if not is_running():
            return
        time.sleep(0.5)


def init():
    if os.path.exists(os.path.join(DATA_DIR, "mysql")):
        print(f"Already initialised: {DATA_DIR}")
    else:
        os.makedirs(os.path.dirname(DATA_DIR), exist_ok=True)
        root_pw = secrets.token_urlsafe(24)
        subprocess.run([exe("mysql_install_db"), f"--datadir={DATA_DIR}", f"--port={PORT}", f"--password={root_pw}"],
                       check=True, capture_output=True)
        with open(ini_path(), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(
                "[mysqld]\n"
                f"datadir={DATA_DIR.replace(os.sep, '/')}\n"
                f"port={PORT}\n"
                "bind-address=127.0.0.1\n"            # this PC only; the browser never talks to the database
                "character-set-server=utf8mb4\n"
                "collation-server=utf8mb4_bin\n"
                "sql_mode=STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION\n"
                "default-storage-engine=InnoDB\n"
                "max_allowed_packet=64M\n"
                "[client]\n"
                f"port={PORT}\n")
        set_env({"MYSQL_ROOT_PASSWORD": root_pw})
        print(f"Created data folder {DATA_DIR}")

    set_env({"MYSQL_BIN_DIR": BIN_DIR, "MYSQL_DATA_DIR": DATA_DIR, "MYSQL_HOST": HOST, "MYSQL_PORT": str(PORT),
             "MYSQL_DATABASE": DATABASE, "MYSQL_USER": APP_USER, "MYSQL_PASSWORD": secrets.token_urlsafe(24)})
    start()
    app_pw = os.environ["MYSQL_PASSWORD"]
    con = root_connect()
    try:
        with con.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{DATABASE}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin")
            for host in ("localhost", "127.0.0.1"):
                cur.execute(f"CREATE USER IF NOT EXISTS '{APP_USER}'@'{host}' IDENTIFIED BY %s", (app_pw,))
                cur.execute(f"ALTER USER '{APP_USER}'@'{host}' IDENTIFIED BY %s", (app_pw,))
                # Least privilege: only this database. DROP is needed by schema migrations (Alembic downgrade).
                cur.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, REFERENCES, DROP "
                            f"ON `{DATABASE}`.* TO '{APP_USER}'@'{host}'")
        con.commit()
    finally:
        con.close()
    print(f"Database `{DATABASE}` and user `{APP_USER}` ready on {HOST}:{PORT}.")
    secure()


APP_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"
MIGRATE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, REFERENCES, DROP"


def secure(database=None):
    """Least privilege (idempotent): the application account keeps only data access; schema changes
    use a separate migration account. Adds MYSQL_MIGRATE_USER/MYSQL_MIGRATE_PASSWORD to .env."""
    database = database or DATABASE
    migrate_user = os.getenv("MYSQL_MIGRATE_USER") or f"{APP_USER}_migrate"
    added = set_env({"MYSQL_MIGRATE_USER": migrate_user, "MYSQL_MIGRATE_PASSWORD": secrets.token_urlsafe(24)})
    migrate_pw = os.environ["MYSQL_MIGRATE_PASSWORD"]
    start()
    con = root_connect()
    try:
        with con.cursor() as cur:
            for host in ("localhost", "127.0.0.1"):
                cur.execute(f"CREATE USER IF NOT EXISTS '{migrate_user}'@'{host}' IDENTIFIED BY %s", (migrate_pw,))
                cur.execute(f"ALTER USER '{migrate_user}'@'{host}' IDENTIFIED BY %s", (migrate_pw,))
                cur.execute(f"GRANT {MIGRATE_PRIVILEGES} ON `{database}`.* TO '{migrate_user}'@'{host}'")
                cur.execute(f"REVOKE ALL PRIVILEGES ON `{database}`.* FROM '{APP_USER}'@'{host}'")
                cur.execute(f"GRANT {APP_PRIVILEGES} ON `{database}`.* TO '{APP_USER}'@'{host}'")
        con.commit()
    finally:
        con.close()
    print(f"`{APP_USER}`: {APP_PRIVILEGES} only.  `{migrate_user}`: schema changes (migrations).")
    if added:
        print("Added to .env: " + ", ".join(added))


def backup(label="manual"):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    target = os.path.join(BACKUP_DIR, f"cityland9_{label}_{datetime.now():%Y%m%d_%H%M%S}.sql")
    with open(target, "wb") as fh, client_options() as opt:
        subprocess.run([exe("mysqldump"), f"--defaults-extra-file={opt}", "--single-transaction",
                        "--routines", "--default-character-set=utf8mb4", DATABASE], stdout=fh, check=True)
    return target


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else "status"
    if command == "init":
        init()
    elif command == "start":
        start(); print(f"Running on {HOST}:{PORT}")
    elif command == "stop":
        stop(); print("Stopped")
    elif command == "backup":
        start(); print(backup())
    elif command == "secure":
        secure()
    else:
        print(f"{'Running' if is_running() else 'Not running'} on {HOST}:{PORT} (data: {DATA_DIR})")


if __name__ == "__main__":
    main()
