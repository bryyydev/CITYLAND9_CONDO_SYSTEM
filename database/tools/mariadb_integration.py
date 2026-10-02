"""Isolated MariaDB/MySQL integration checks (things SQLite can't verify).

    .venv\\Scripts\\python.exe database\\tools\\mariadb_integration.py [scenario ...]

Every run creates a THROWAWAY database (cl9_it_<random>) on CityLand's own server, grants it to
the app user, runs the scenarios with SYNTHETIC data only, and drops it again. The real
`cityland9` database is never opened. Needs MYSQL_ROOT_PASSWORD in .env and a running server
(started automatically).

Scenarios (default: all):
  migrate_0008   0007 schema + synthetic accounts -> upgrade -> checks -> downgrade -> upgrade
  migrate_0009   receipt counter backfill from existing receipts; downgrade refuses to lose voids
  concurrency    the real server (Waitress) on a throwaway database; parallel cashiers paying
                 the same bill, first receipts of a new year, and a form submitted 5 times at once
"""
import os
import secrets
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "database"))
sys.path.insert(0, os.path.join(ROOT, "backend"))
PY = sys.executable
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    pass

import local_mysql as lm  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'' if ok or not detail else ' — ' + str(detail)[:400]}")
    return ok


class ScratchDB:
    """A throwaway database the app user may use; dropped on exit."""

    def __init__(self):
        self.name = f"cl9_it_{secrets.token_hex(4)}"

    def __enter__(self):
        os.makedirs(lm.BACKUP_DIR, exist_ok=True)
        self._backups_before = set(os.listdir(lm.BACKUP_DIR))
        lm.start()
        self.root = lm.root_connect()
        cur = self.root.cursor()
        cur.execute(f"CREATE DATABASE {self.name} CHARACTER SET utf8mb4 COLLATE utf8mb4_bin")
        cur.execute("SELECT CONCAT(QUOTE(user),'@',QUOTE(host)) FROM mysql.user WHERE user=%s", (lm.APP_USER,))
        self.accounts = [r[0] for r in cur.fetchall()]
        # Least privilege, as in production (local_mysql.py secure): the app account may only read and
        # write data; a separate (temporary) migration account changes the schema.
        for a in self.accounts:
            cur.execute(f"GRANT {lm.APP_PRIVILEGES} ON {self.name}.* TO {a}")
        self.migrate_user, self.migrate_pw = f"{self.name}_mig", secrets.token_urlsafe(18)
        for host in ("localhost", "127.0.0.1"):
            cur.execute(f"CREATE USER '{self.migrate_user}'@'{host}' IDENTIFIED BY %s", (self.migrate_pw,))
            cur.execute(f"GRANT {lm.MIGRATE_PRIVILEGES} ON {self.name}.* TO '{self.migrate_user}'@'{host}'")
        self.root.select_db(self.name)
        self.env = {**os.environ, "MYSQL_DATABASE": self.name,
                    "MYSQL_MIGRATE_USER": self.migrate_user, "MYSQL_MIGRATE_PASSWORD": self.migrate_pw}
        self.env.pop("DATABASE_URL", None)
        return self

    def sql(self, statement, args=None, fetch=False):
        cur = self.root.cursor()
        cur.execute(statement, args)
        self.root.commit()
        return cur.fetchall() if fetch else cur.rowcount

    def run(self, *args, input_text=None):
        return subprocess.run([PY, *args], cwd=ROOT, env=self.env, capture_output=True, text=True, input=input_text, timeout=600)

    def load_sql(self, text):
        with lm.client_options() as opt:
            r = subprocess.run([lm.exe("mysql"), f"--defaults-extra-file={opt}", "--default-character-set=utf8mb4", self.name],
                               input=text, capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(r.stderr)

    def __exit__(self, *exc):
        cur = self.root.cursor()
        cur.execute(f"DROP DATABASE IF EXISTS {self.name}")
        for a in self.accounts:
            cur.execute(f"REVOKE ALL PRIVILEGES ON {self.name}.* FROM {a}")
        for host in ("localhost", "127.0.0.1"):
            cur.execute(f"DROP USER IF EXISTS '{self.migrate_user}'@'{host}'")
        self.root.close()
        # db_migrate.py backs up before every migration: remove the backups of this throwaway database.
        for f in set(os.listdir(lm.BACKUP_DIR)) - self._backups_before:
            os.remove(os.path.join(lm.BACKUP_DIR, f))


def committed_schema(revision_commit="HEAD"):
    return subprocess.run(["git", "show", f"{revision_commit}:database/schema.sql"], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", check=True).stdout


# ------------------------------------------------------------------ scenarios
def scenario_migrate_0008():
    from werkzeug.security import generate_password_hash
    from app.core import security
    with ScratchDB() as db:
        print(f"-- migrate_0008 on {db.name}")
        db.load_sql(committed_schema("e4c955b"))          # schema as of revision 0007 (before 0008)
        r = db.run("database/db_migrate.py", "stamp", "0007_gate_pass_requests")
        check("stamp 0007 on the old schema", r.returncode == 0, r.stderr)
        db.sql("INSERT INTO `user` (username, password_hash, role, active, created_at) VALUES "
               "(%s,%s,'super_admin',1,UTC_TIMESTAMP(6)), (%s,%s,'staff',1,UTC_TIMESTAMP(6))",
               ("it_default_admin", generate_password_hash("admin123"), "it_staff", generate_password_hash("Synthetic-Pass-1")))
        db.sql("INSERT INTO setting (`key`, value) VALUES ('smtp_password', 'synthetic-mail-secret')")

        r = db.run("database/db_migrate.py", "upgrade")
        check("upgrade 0007 -> head (backup, golden master, schema check)", r.returncode == 0 and "matches the models: yes" in r.stdout, r.stdout + r.stderr)
        rows = dict((u, (mc, sv)) for u, mc, sv in db.sql("SELECT username, must_change_password, session_version FROM `user`", fetch=True))
        check("account on the old default password flagged", rows.get("it_default_admin", (0,))[0] == 1, rows)
        check("other account untouched", rows.get("it_staff") == (0, 0), rows)
        stored = db.sql("SELECT value FROM setting WHERE `key`='smtp_password'", fetch=True)[0][0]
        check("SMTP password encrypted at rest", stored.startswith("enc:v1:") and "synthetic-mail-secret" not in stored)
        check("encrypted value decrypts with SECRET_KEY",
              security.decrypt_secret(stored, os.getenv("SECRET_KEY", "")) == "synthetic-mail-secret")
        audit = db.sql("SELECT action FROM audit_log WHERE username='system'", fetch=True)
        check("migration wrote audit entries", len(audit) == 2, audit)
        check("no secret in the migration output", "synthetic-mail-secret" not in r.stdout + r.stderr)

        r = db.run("database/db_migrate.py", "downgrade", "0007_gate_pass_requests")
        cols = {c[0] for c in db.sql("SHOW COLUMNS FROM `user`", fetch=True)}
        plain = db.sql("SELECT value FROM setting WHERE `key`='smtp_password'", fetch=True)[0][0]
        check("downgrade removes the columns and restores the setting",
              r.returncode == 0 and "session_version" not in cols and plain == "synthetic-mail-secret", r.stdout[-500:] + r.stderr[-500:])
        r = db.run("database/db_migrate.py", "upgrade")
        check("upgrade again after downgrade", r.returncode == 0 and "matches the models: yes" in r.stdout, r.stdout[-800:] + r.stderr[-800:])




# ------------------------------------------------------------------ concurrency (real server, real row locks)
import http.cookiejar  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import socket  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402

IT_ADMIN, IT_PASSWORD = "it_cashier_admin", "IT-Cashier-Pass-73"


class Browser:
    """A signed-in session against the test server (cookies + CSRF), like one cashier's PC."""

    def __init__(self, base):
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
                                                  _NoRedirect())
        self.csrf = self.get_json("/api/auth/csrf")["csrfToken"]
        resp = self.request("/api/auth/login", json_body={"username": IT_ADMIN, "password": IT_PASSWORD})
        self.csrf = json.loads(resp)["csrfToken"]

    def request(self, path, json_body=None, form=None):
        headers = {"X-CSRFToken": self.csrf}
        data = None
        if json_body is not None:
            data, headers["Content-Type"] = json.dumps(json_body).encode(), "application/json"
        elif form is not None:
            data = urllib.parse.urlencode({**form, "csrf_token": self.csrf}).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method="POST" if data else "GET")
        try:
            with self.opener.open(req, timeout=60) as r:
                return r.read().decode()
        except urllib.error.HTTPError as e:   # 302 after a form post is the normal outcome
            return e.read().decode()

    def get_json(self, path):
        with self.opener.open(self.base + path, timeout=60) as r:
            return json.loads(r.read().decode())


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _parallel(fns):
    barrier = threading.Barrier(len(fns))
    errors = []

    def run(fn):
        try:
            barrier.wait()
            fn()
        except Exception as exc:  # collected and reported
            errors.append(repr(exc))
    threads = [threading.Thread(target=run, args=(fn,)) for fn in fns]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    return errors


def scenario_concurrency():
    from werkzeug.security import generate_password_hash
    with ScratchDB() as db:
        print(f"-- concurrency on {db.name}")
        with open(os.path.join(ROOT, "database", "schema.sql"), encoding="utf-8") as fh:
            db.load_sql(fh.read())
        r = db.run("database/db_migrate.py", "stamp", "head")
        check("fresh schema at head", r.returncode == 0, r.stderr)
        env = {**db.env, "DB_ENGINE": "mysql"}
        r = subprocess.run([PY, "seed_test_data.py"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
        check("synthetic TEST units and bills loaded", r.returncode == 0, r.stdout[-400:] + r.stderr[-800:])
        db.sql("INSERT INTO `user` (username, password_hash, role, active, created_at, session_version, must_change_password) "
               "VALUES (%s, %s, 'super_admin', 1, UTC_TIMESTAMP(6), 0, 0)", (IT_ADMIN, generate_password_hash(IT_PASSWORD)))
        units = [row[0] for row in db.sql("SELECT id FROM unit WHERE unit_no LIKE 'TEST-%%' AND unit_type NOT IN ('PARKING','STORAGE') ORDER BY id", fetch=True)]
        # Bills of a far-future month, one per unit, 1,000.00 each and nothing carried over.
        for uid in units:
            db.sql("INSERT INTO billing (unit_id, billing_month, assessment, parking_dues, storage_dues, water, other, penalty, adjustment, "
                   "previous_balance, amount_paid, due_date, status, soa_note, soa_manual_override, created_at) "
                   "VALUES (%s,'2035-01',1000,0,0,0,0,0,0,0,0,'2035-01-08','Unpaid','',0,UTC_TIMESTAMP(6))", (uid,))
        bills = [row[0] for row in db.sql("SELECT id FROM billing WHERE billing_month='2035-01' ORDER BY id", fetch=True)]

        port = _free_port()
        server_env = {**env, "FLASK_HOST": "127.0.0.1", "FLASK_PORT": str(port), "WAITRESS_THREADS": "16", "APP_ENV": "production"}
        server = subprocess.Popen([PY, os.path.join("backend", "run.py"), "--lan"], cwd=ROOT, env=server_env,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        base = f"http://127.0.0.1:{port}"
        try:
            for _ in range(120):
                try:
                    urllib.request.urlopen(base + "/api/auth/csrf", timeout=2)
                    break
                except Exception:
                    time.sleep(0.5)
            grants = " ".join(g[0] for g in db.sql(f"SHOW GRANTS FOR '{lm.APP_USER}'@'127.0.0.1'", fetch=True) if db.name in g[0])
            check("app account has data-only rights on this database", "CREATE" not in grants and "ALTER" not in grants and "DROP" not in grants, grants)
            cashiers = [Browser(base) for _ in range(10)]
            check("10 cashier sessions signed in", len(cashiers) == 10)

            # A) 10 cashiers pay the SAME bill at the same moment (300 each = 3,000 for a 1,000 bill).
            bill = bills[0]
            balance_before = None
            fns = [lambda c=c, i=i: c.request(f"/billing/{bill}/pay", form={
                "amount": "300", "payment_method": "CASH", "payment_type": "PARTIAL", "payment_date": "2035-01-15",
                "remarks": f"it-same-bill-{i}", "form_token": secrets.token_urlsafe(24)}) for i, c in enumerate(cashiers)]
            errors = _parallel(fns)
            check("same bill: all requests completed", not errors, errors)
            sum_payments = db.sql("SELECT COALESCE(SUM(amount),0) FROM payment WHERE billing_id=%s", (bill,), fetch=True)[0][0]
            receipts = db.sql("SELECT r.receipt_seq, r.amount FROM receipt r WHERE r.remarks LIKE 'it-same-bill-%%' ORDER BY r.receipt_seq", fetch=True)
            excess = db.sql("SELECT COALESCE(SUM(amount),0) FROM advance_payment WHERE remarks LIKE 'it-same-bill-%%'", fetch=True)[0][0]
            # Concurrency guarantees (independent of how overpayments are applied, which is a pending
            # business decision): every peso received is in exactly one payment or advance row,
            # payments never exceed the bill's 1,000 balance, and the result equals the same ten
            # payments made one after another (payments 1,000 / advances 2,000).
            check("same bill: 10 receipts totalling 3,000", len(receipts) == 10 and sum(a for _, a in receipts) == 3000, receipts)
            check("same bill: every peso in exactly one payment or advance row", sum_payments + excess == 3000, (sum_payments, excess))
            check("same bill: payments never exceed the balance (no double application)", sum_payments == 1000, sum_payments)
            seqs = [s for s, _ in receipts]
            check("same bill: receipt numbers unique and consecutive", seqs == list(range(seqs[0], seqs[0] + 10)), seqs)

            # B) First receipts of a NEW year issued at the same moment (no counter row exists yet).
            fns = [lambda c=c, b=b: c.request(f"/billing/{b}/pay", form={
                "amount": "100", "payment_method": "CASH", "payment_type": "PARTIAL", "payment_date": "2036-01-01",
                "remarks": "it-new-year", "form_token": secrets.token_urlsafe(24)}) for c, b in zip(cashiers, (bills * 10)[1:11])]
            errors = _parallel(fns)
            seqs = [row[0] for row in db.sql("SELECT receipt_seq FROM receipt WHERE receipt_year=2036 ORDER BY receipt_seq", fetch=True)]
            check("new year: 10 first receipts numbered exactly 1..10", not errors and seqs == list(range(1, 11)), (errors, seqs))
            counter = db.sql("SELECT last_seq FROM receipt_counter WHERE year=2036", fetch=True)
            check("new year: one counter row at 10", counter == ((10,),), counter)

            # C) The same form submitted 5 times at once (double clicks / retries).
            token = secrets.token_urlsafe(24)
            target = bills[1 % len(bills)]
            before = db.sql("SELECT COUNT(*) FROM payment WHERE billing_id=%s", (target,), fetch=True)[0][0]
            fns = [lambda c=c: c.request(f"/billing/{target}/pay", form={
                "amount": "50", "payment_method": "CASH", "payment_type": "PARTIAL", "payment_date": "2035-02-01",
                "remarks": "it-duplicate", "form_token": token}) for c in cashiers[:5]]
            errors = _parallel(fns)
            after = db.sql("SELECT COUNT(*) FROM payment WHERE billing_id=%s", (target,), fetch=True)[0][0]
            check("duplicate form: exactly one payment recorded", not errors and after - before == 1, (errors, before, after))
        finally:
            server.terminate()
            try:
                out, _ = server.communicate(timeout=20)
            except subprocess.TimeoutExpired:
                server.kill()
                out, _ = server.communicate()
            if "Traceback" in out:
                print("   server log tail:\n" + out[-2000:])
            check("server log has no tracebacks", "Traceback" not in out)


def scenario_migrate_0009():
    """Receipt counter backfill from existing receipts; downgrade refuses to drop void history."""
    with ScratchDB() as db:
        print(f"-- migrate_0009 on {db.name}")
        db.load_sql(committed_schema("e4c955b"))
        check("stamp 0007", db.run("database/db_migrate.py", "stamp", "0007_gate_pass_requests").returncode == 0)
        db.sql("INSERT INTO unit (unit_no, unit_type, active, area_sqm, unit_rate_per_sqm) VALUES ('IT-1', '1 BEDROOM', 1, 10, 100)")
        uid = db.sql("SELECT id FROM unit WHERE unit_no='IT-1'", fetch=True)[0][0]
        for year, seqs in ((2025, (1, 2, 3)), (2026, (1, 2, 3, 4, 5))):
            for seq in seqs:
                db.sql("INSERT INTO receipt (receipt_year, receipt_seq, receipt_no, unit_id, received_date, amount, payment_method, source, created_at) "
                       "VALUES (%s,%s,%s,%s,%s,10,'CASH','cashier',UTC_TIMESTAMP(6))", (year, seq, f"OR-{year}-{seq:06d}", uid, f"{year}-03-01"))
        r = db.run("database/db_migrate.py", "upgrade")
        check("upgrade 0007 -> head with existing receipts", r.returncode == 0 and "matches the models: yes" in r.stdout, r.stdout[-600:] + r.stderr[-600:])
        counters = db.sql("SELECT year, last_seq FROM receipt_counter ORDER BY year", fetch=True)
        check("counter backfilled from the highest number per year", counters == ((2025, 3), (2026, 5)), counters)
        db.sql("UPDATE receipt SET voided_at = UTC_TIMESTAMP(6), void_reason = 'synthetic void' WHERE receipt_no = 'OR-2026-000002'")
        r = db.run("database/db_migrate.py", "downgrade", "0008_session_security")
        check("downgrade refused while a receipt is voided", r.returncode != 0 and "voided" in (r.stdout + r.stderr))
        cols = {c[0] for c in db.sql("SHOW COLUMNS FROM receipt", fetch=True)}
        check("void history still there after the refused downgrade", "voided_at" in cols)


def scenario_fresh_install():
    """New installation as documented: empty database -> db_migrate install -> server starts -> create_admin."""
    with ScratchDB() as db:
        print(f"-- fresh_install on {db.name}")
        r = db.run("database/db_migrate.py", "check")
        check("startup check refuses an empty (unstamped) database", r.returncode != 0, r.stdout + r.stderr)
        r = db.run("database/db_migrate.py", "install")
        check("install creates every table and stamps head", r.returncode == 0 and "Matches the models: yes" in r.stdout,
              r.stdout[-600:] + r.stderr[-600:])
        r = db.run("database/db_migrate.py", "check")
        check("startup check passes after install", r.returncode == 0, r.stdout + r.stderr)
        r = db.run("database/db_migrate.py", "install")
        check("install refuses a database that already has tables", r.returncode != 0 and "already has" in (r.stdout + r.stderr))
        env = {**db.env, "DB_ENGINE": "mysql"}
        r = subprocess.run([PY, "-c", "import sys; sys.path.insert(0, 'backend'); import run; run.init_db()"], cwd=ROOT, env=env,
                           capture_output=True, text=True, timeout=300)
        check("app starts without changing the schema, no default admin", r.returncode == 0 and "create_admin" in r.stdout
              and not db.sql("SELECT id FROM `user`", fetch=True), r.stdout[-400:] + r.stderr[-800:])
        r = subprocess.run([PY, "database/create_admin.py"], cwd=ROOT, env=env, input=f"it_first_admin\n{IT_PASSWORD}\n",
                           capture_output=True, text=True, timeout=300)
        rows = db.sql("SELECT role, active, must_change_password FROM `user` WHERE username='it_first_admin'", fetch=True)
        check("create_admin makes the first Superadmin", r.returncode == 0 and rows == (("super_admin", 1, 0),),
              f"{rows} {r.stdout[-300:]} {r.stderr[-500:]}")
        r = subprocess.run([PY, "database/create_admin.py"], cwd=ROOT, env=env, input="it_weak\nadmin123\n",
                           capture_output=True, text=True, timeout=300)
        check("create_admin rejects a weak password", r.returncode != 0
              and not db.sql("SELECT id FROM `user` WHERE username='it_weak'", fetch=True))


def scenario_backup_restore():
    """database/backup.py on a throwaway database, then database/restore.py into ANOTHER throwaway database:
    every bill's figures and every table's rows must match. Also failure reporting and retention."""
    import gzip
    import shutil
    import tempfile
    backup_dir = tempfile.mkdtemp(prefix="cl9_it_backups_")
    restored = None
    try:
        with ScratchDB() as db:
            print(f"-- backup_restore on {db.name}")
            env = {**db.env, "DB_ENGINE": "mysql", "BACKUP_DIR": backup_dir, "BACKUP_COPY_DIR": "", "BACKUP_MIN_FREE_GB": "0.1"}
            r = db.run("database/db_migrate.py", "install")
            check("schema installed", r.returncode == 0, r.stdout[-400:] + r.stderr[-400:])
            r = subprocess.run([PY, "seed_test_data.py"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
            check("synthetic TEST data loaded", r.returncode == 0, r.stdout[-400:] + r.stderr[-800:])
            uid = db.sql("SELECT id FROM unit WHERE unit_no LIKE 'TEST-%%' ORDER BY id LIMIT 1", fetch=True)[0][0]
            db.sql("INSERT INTO receipt (receipt_year, receipt_seq, receipt_no, unit_id, received_date, amount, payment_method, source, "
                   "created_at, void_reason) VALUES (2035, 7, 'OR-2035-000007', %s, '2035-02-01', 1234.56, 'CASH', 'cashier', "
                   "UTC_TIMESTAMP(6), 'Ñame — unicode check ₱')", (uid,))
            db.sql("INSERT INTO receipt_counter (year, last_seq) VALUES (2035, 7)")

            # Old scheduled backups (40 days) + one manual backup: retention must delete only old scheduled ones.
            old = time.time() - 40 * 86400
            for i in range(9):
                path = os.path.join(backup_dir, f"cityland9_auto_20200101_0000{i:02d}.sql.gz")
                with gzip.open(path, "wb") as fh:
                    fh.write(b"-- old")
                os.utime(path, (old, old))
            manual = os.path.join(backup_dir, "cityland9_manual_20200101_000000.sql")
            open(manual, "w").close()
            os.utime(manual, (old, old))

            r = subprocess.run([PY, "database/backup.py"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
            with open(os.path.join(backup_dir, "last_backup.json"), encoding="utf-8") as fh:
                status = json.load(fh)
            check("backup succeeds and records status ok", r.returncode == 0 and status["status"] == "ok", r.stdout[-400:] + r.stderr[-400:])
            dump = status.get("file") or ""
            check("backup is compressed and complete", dump.endswith(".sql.gz") and os.path.exists(dump))
            with open(dump, "rb") as fh:
                raw = fh.read()
            check("no password inside the backup file name/status", os.getenv("MYSQL_ROOT_PASSWORD", "x" * 40).encode() not in raw
                  and os.getenv("MYSQL_ROOT_PASSWORD", "x" * 40) not in json.dumps(status))
            autos = sorted(f for f in os.listdir(backup_dir) if f.startswith("cityland9_auto_"))
            check("retention: keeps the newest 7 scheduled backups, never the manual one",
                  len(autos) == 7 and os.path.exists(manual) and os.path.basename(dump) in autos, autos)
            ready = subprocess.run([PY, "-c", "import sys, json; sys.path.insert(0, 'backend'); from app.core import ops; "
                                    "print(json.dumps(ops.backup_status('.', 26)))"], cwd=ROOT, env=env, capture_output=True, text=True)
            check("/readyz backup check sees the fresh backup", '"ok": true' in ready.stdout, ready.stdout + ready.stderr[-300:])

            source = subprocess.run([PY, "database/tools/bill_snapshot.py", "--mysql"], cwd=ROOT, env=env, capture_output=True, text=True)
            source_rows = {t[0]: db.sql(f"SELECT COUNT(*) FROM `{t[0]}`", fetch=True)[0][0] for t in db.sql("SHOW TABLES", fetch=True)}

            restored = f"{db.name}_r"
            r = subprocess.run([PY, "database/restore.py", dump, "--into", restored], cwd=ROOT, env=env, capture_output=True,
                               text=True, timeout=600)
            check("restore into a separate database verified", r.returncode == 0 and "RESTORE VERIFIED" in r.stdout,
                  r.stdout[-600:] + r.stderr[-600:])
            copy = subprocess.run([PY, "database/tools/bill_snapshot.py", "--mysql"], cwd=ROOT,
                                  env={**env, "MYSQL_DATABASE": restored}, capture_output=True, text=True)
            check("every bill's figures identical after restore", source.returncode == 0 and source.stdout == copy.stdout
                  and len(json.loads(source.stdout)) > 0, copy.stderr[-400:])
            root = lm.root_connect(restored)
            with root.cursor() as cur:
                restored_rows = {}
                for (t,) in list(cur.execute("SHOW TABLES") and cur.fetchall()):
                    cur.execute(f"SELECT COUNT(*) FROM `{t}`")
                    restored_rows[t] = cur.fetchone()[0]
                cur.execute("SELECT receipt_no, amount, void_reason FROM receipt WHERE receipt_no='OR-2035-000007'")
                receipt = cur.fetchone()
                cur.execute("SELECT last_seq FROM receipt_counter WHERE year=2035")
                counter = cur.fetchone()
            root.close()
            check("row counts identical in every table", restored_rows == source_rows,
                  {t: (source_rows.get(t), restored_rows.get(t)) for t in set(source_rows) | set(restored_rows)
                   if source_rows.get(t) != restored_rows.get(t)})
            check("receipt numbers, amounts and unicode text preserved",
                  receipt is not None and str(receipt[1]) == "1234.56" and receipt[2] == "Ñame — unicode check ₱" and counter == (7,), receipt)

            r = subprocess.run([PY, "database/restore.py", dump, "--into", restored], cwd=ROOT, env=env, capture_output=True, text=True)
            check("restore refuses a database that is not empty", r.returncode != 0 and "not empty" in (r.stdout + r.stderr))
            r = subprocess.run([PY, "database/restore.py", dump, "--into", db.name], cwd=ROOT, env=env, capture_output=True, text=True)
            check("restore refuses to overwrite the configured (live) database", r.returncode != 0 and "Refusing" in (r.stdout + r.stderr))

            # Disaster recovery (--replace-live) on the THROWAWAY database, which plays the live one here.
            # Damage it after the backup, then replace it from the backup.
            db.sql("UPDATE billing SET amount_paid = amount_paid + 999 ORDER BY id LIMIT 1")
            db.sql("DELETE FROM receipt WHERE receipt_no = 'OR-2035-000007'")
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            busy = {**env, "FLASK_PORT": str(listener.getsockname()[1])}
            r = subprocess.run([PY, "database/restore.py", dump, "--replace-live"], cwd=ROOT, env=busy, input=f"{db.name}\n",
                               capture_output=True, text=True)
            listener.close()
            check("replace-live refused while the web server answers", r.returncode != 0 and "running" in (r.stdout + r.stderr))
            free = {**env, "FLASK_PORT": str(_free_port())}
            r = subprocess.run([PY, "database/restore.py", dump, "--replace-live"], cwd=ROOT, env=free, input="wrong-name\n",
                               capture_output=True, text=True)
            check("replace-live needs the typed database name", r.returncode != 0 and "Not confirmed" in (r.stdout + r.stderr)
                  and not db.sql("SELECT id FROM receipt WHERE receipt_no='OR-2035-000007'", fetch=True))
            r = subprocess.run([PY, "database/restore.py", dump, "--replace-live"], cwd=ROOT, env=free, input=f"{db.name}\n",
                               capture_output=True, text=True, timeout=600)
            db.root.close()
            db.root = lm.root_connect(db.name)
            after = subprocess.run([PY, "database/tools/bill_snapshot.py", "--mysql"], cwd=ROOT, env=env, capture_output=True, text=True)
            check("replace-live restores the backed-up data exactly", r.returncode == 0 and "RESTORED AND VERIFIED" in r.stdout
                  and after.stdout == source.stdout and db.sql("SELECT id FROM receipt WHERE receipt_no='OR-2035-000007'", fetch=True),
                  r.stdout[-500:] + r.stderr[-500:])
            safety = [f for f in os.listdir(backup_dir) if f.startswith("cityland9_before_restore_")]
            check("replace-live kept a safety backup of the replaced data", len(safety) == 1)
            grants = " ".join(g[0] for g in db.sql(f"SHOW GRANTS FOR '{lm.APP_USER}'@'127.0.0.1'", fetch=True) if db.name in g[0])
            check("app account rights unchanged after replace-live", "SELECT" in grants and "CREATE" not in grants, grants)

            bad = {**env, "MYSQL_DATABASE": f"{db.name}_missing"}
            before = set(os.listdir(backup_dir))
            r = subprocess.run([PY, "database/backup.py"], cwd=ROOT, env=bad, capture_output=True, text=True, timeout=300)
            with open(os.path.join(backup_dir, "last_backup.json"), encoding="utf-8") as fh:
                status = json.load(fh)
            check("a failed backup exits non-zero and records the failure", r.returncode != 0 and status["status"] == "failed"
                  and status["error"], status)
            check("a failed backup leaves no partial file", set(os.listdir(backup_dir)) - before == set())
            with open(os.path.join(ROOT, "logs", "backup.log"), encoding="utf-8") as fh:
                check("the failure is in logs/backup.log", "BACKUP FAILED" in fh.read()[-5000:])
    finally:
        if restored:
            con = lm.root_connect()
            with con.cursor() as cur:
                cur.execute(f"DROP DATABASE IF EXISTS `{restored}`")
                cur.execute("SELECT CONCAT(QUOTE(user),'@',QUOTE(host)) FROM mysql.user WHERE user=%s", (lm.APP_USER,))
                for account in [row[0] for row in cur.fetchall()]:
                    try:
                        cur.execute(f"REVOKE ALL PRIVILEGES ON `{restored}`.* FROM {account}")
                    except Exception:
                        pass
            con.close()
        shutil.rmtree(backup_dir, ignore_errors=True)


SCENARIOS = {"migrate_0008": scenario_migrate_0008, "migrate_0009": scenario_migrate_0009, "concurrency": scenario_concurrency,
             "fresh_install": scenario_fresh_install, "backup_restore": scenario_backup_restore}


def main():
    names = sys.argv[1:] or list(SCENARIOS)
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        sys.exit(f"Unknown scenario(s): {unknown}. Available: {list(SCENARIOS)}")
    for name in names:
        try:
            SCENARIOS[name]()
        except Exception as exc:   # report and continue with the next scenario
            check(f"{name} crashed", False, repr(exc))
    failed = RESULTS.count(False)
    print(f"\n{len(RESULTS) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
