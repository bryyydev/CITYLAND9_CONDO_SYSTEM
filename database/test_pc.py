"""Prepare an independent TEST installation of CityLand 9 on another Windows PC.

Guide: CITYLAND9_TEST_PC_MIGRATION.md (project root). Run every command from the project root with
.venv\\Scripts\\python.exe. Passwords are read from .env and passed to the MariaDB programs through a
temporary private options file (never on a command line); they are never printed or logged.

SOURCE PC (this installation's own database is never changed)
    test_pc.py profile
        Read-only: does the database hold only synthetic (TEST-*) records? Prints counts only.
    test_pc.py export-synthetic [--out DIR]
        Builds the synthetic sample data (seed_test_data.py) in a THROWAWAY database on this PC's
        server - the real database is not even opened - and writes the test package:
            cityland9_testpkg_synthetic_<timestamp>.sql.gz  +  .manifest.json
    test_pc.py export-sanitized [--out DIR] [--include hr,vendors,community]
        ONLY after the owner approved transferring anonymized real data. Copies the live database
        (mysqldump --single-transaction: read-only) into a STAGING database, anonymizes it there,
        checks no original name/email/phone is left, writes the package and drops the staging copy.
        Without --include, HR/payroll, vendor and community records (announcements, tickets, gate
        passes, move certificates) are left out.
    test_pc.py export-copy [--out DIR]
        ONLY when every record is dummy/test data: the live database's records as they are (no
        anonymization), through the same staging database. Asks you to type DUMMY DATA.
    test_pc.py package-app [--out DIR]
        Zip of the application files: no .env, databases, backups, logs, .venv, node_modules or git history.

    Every package: no user accounts or password hashes, no resident profiles/links, no audit history,
    no form tokens, no SMTP settings/password, no online payment link, no uploaded-document records.

TARGET PC (its .env has CL9_TEST_INSTALL=1 and MYSQL_DATABASE=cityland9_test...)
    SETUP_TEST_PC.bat runs the whole sequence below (make-env, then setup); every step can be re-run.
    test_pc.py make-env
        Creates .env from .env.example for a test installation (new SECRET_KEY, cityland9_test,
        CL9_TEST_INSTALL=1, email disabled). An existing test .env is kept; the demo .env that
        START_WINDOWS.bat makes on a fresh copy is set aside as .env.demo-backup.
    test_pc.py setup [<package.sql.gz>]
        This PC's MariaDB (local_mysql.py init) -> restore the package found in this folder, its parent
        folder or database\\test_packages (unless already restored) -> create_admin.py when there is no
        Superadmin -> create-testers when there are none -> check-target.
    test_pc.py check-target [--package FILE]
        The database is this PC's own CityLand MariaDB (127.0.0.1), the database name is a test
        name, email is disabled, the schema is current. With --package: not the PC that exported it.
    test_pc.py restore <package.sql.gz> [--overwrite <database>]
        Only test packages (manifest + checksum). Restores into MYSQL_DATABASE when it is new or empty;
        refuses a database that has tables unless --overwrite names it AND you type its name (a safety
        backup is taken first). Re-applies the account rights, upgrades an older schema with the
        migration account (backup first), then verifies tables, bills and that no account came along.
    test_pc.py create-testers [--roles admin,accounting,staff,manager] [--residents N]
        Fresh tester accounts with temporary passwords (changed at first sign-in), shown ONCE; with
        --residents, portal accounts for N current owners/tenants (activation codes shown once).
        Never creates a Superadmin: the developer does that with database\\create_admin.py.
"""
import gzip
import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import zipfile
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "database"))
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    pass

import local_mysql as lm  # noqa: E402

FORMAT = "cityland9-testpkg/1"
PKG_PREFIX = "cityland9_testpkg_"
TEST_DB_RE = re.compile(r"^cityland9_test[a-z0-9_]*$")
LOOPBACK = ("127.0.0.1", "localhost", "::1")
TEST_EMAIL_DOMAINS = ("example.com", "example.org", "example.net", "example.invalid")

# Removed from EVERY package (synthetic or sanitized), in a throwaway/staging database only.
SCRUB = [
    ("maintenance tickets unlinked from resident accounts", "UPDATE maintenance_ticket SET resident_profile_id = NULL"),
    ("resident unit links", "DELETE FROM resident_unit_link"),
    ("resident profiles", "DELETE FROM resident_profile"),
    ("user accounts and password hashes (incl. activation codes)", "DELETE FROM `user`"),
    ("one-time form tokens", "DELETE FROM form_submission"),
    ("audit history", "DELETE FROM audit_log"),
    ("SMTP settings and stored SMTP password", "DELETE FROM setting WHERE `key` LIKE 'smtp%'"),
    ("online payment link", "DELETE FROM setting WHERE `key` = 'online_payment_url'"),
    ("uploaded-document records (the files are not transferred)", "DELETE FROM document_record"),
    ("data-migration crosswalk (source-system ids)", "DELETE FROM import_crosswalk"),
    ("data-migration batches (file names and fingerprints)", "DELETE FROM import_batch"),
    ("data-migration source systems", "DELETE FROM import_source"),
]
# Each must count 0 in a package (checked before writing and again after restoring).
SCRUB_CHECKS = {
    "user accounts": "SELECT COUNT(*) FROM `user`",
    "resident profiles": "SELECT COUNT(*) FROM resident_profile",
    "resident unit links": "SELECT COUNT(*) FROM resident_unit_link",
    "form tokens": "SELECT COUNT(*) FROM form_submission",
    "audit entries": "SELECT COUNT(*) FROM audit_log",
    "SMTP settings": "SELECT COUNT(*) FROM setting WHERE `key` LIKE 'smtp%'",
    "online payment link": "SELECT COUNT(*) FROM setting WHERE `key` = 'online_payment_url'",
    "document records": "SELECT COUNT(*) FROM document_record",
}
# Optional categories for export-sanitized (left out unless --include names them).
CATEGORIES = {
    "hr": ["employee_payroll_statutory", "employee_payroll", "employee_attendance", "employee_hr_loan", "employee_leave",
           "employee_overtime", "employee_hr_setting", "employee"],
    "vendors": ["vendor"],
    "community": ["announcement", "maintenance_ticket", "gate_pass", "move_certificate"],
}
# Columns holding staff usernames: replaced by a neutral value in a sanitized package.
ACTOR_COLUMNS = ("updated_by", "created_by", "received_by", "voided_by", "requested_by", "reviewed_by", "approved_by",
                 "issued_by", "uploaded_by", "ended_by")
# Anonymization, applied in the STAGING database only. Deterministic by id, so relationships and
# every amount, date, unit and status stay as they are.
ANONYMIZE = [
    "UPDATE owner SET owner_name = CONCAT('Owner ', LPAD(id, 4, '0')), contact_no = IF(COALESCE(contact_no,'') = '', contact_no, "
    "CONCAT('0900', LPAD(id, 7, '0'))), email = IF(COALESCE(email,'') = '', email, CONCAT('owner', id, '@example.invalid')), notes = NULL",
    "UPDATE tenant SET tenant_name = CONCAT('Tenant ', LPAD(id, 4, '0')), contact_no = IF(COALESCE(contact_no,'') = '', contact_no, "
    "CONCAT('0901', LPAD(id, 7, '0'))), email = IF(COALESCE(email,'') = '', email, CONCAT('tenant', id, '@example.invalid')), notes = NULL",
    "UPDATE billing SET soa_note = NULL",
    "UPDATE payment SET reference = IF(COALESCE(reference,'') = '', reference, CONCAT('REF-P', id)), remarks = NULL",
    "UPDATE receipt SET reference = IF(COALESCE(reference,'') = '', reference, CONCAT('REF-R', id)), remarks = NULL, "
    "void_reason = IF(void_reason IS NULL, NULL, 'Voided (reason removed for testing)')",
    "UPDATE advance_payment SET reference = IF(COALESCE(reference,'') = '', reference, CONCAT('REF-A', id)), remarks = NULL",
    "UPDATE water_reading SET payment_reference = IF(COALESCE(payment_reference,'') = '', payment_reference, CONCAT('REF-W', id))",
    "UPDATE expense SET description = CONCAT('Expense ', id)",
]
ANONYMIZE_OPTIONAL = {
    "hr": [
        "UPDATE employee SET full_name = CONCAT('Employee ', LPAD(id, 4, '0')), employee_no = CONCAT('EMP-', LPAD(id, 4, '0')), "
        "contact_no = NULL, email = IF(COALESCE(email,'') = '', email, CONCAT('employee', id, '@example.invalid')), notes = NULL",
        "UPDATE employee_attendance SET remarks = NULL",
        "UPDATE employee_hr_loan SET reference_no = NULL, notes = NULL",
        "UPDATE employee_leave SET reason = NULL",
        "UPDATE employee_overtime SET reason = NULL",
        "UPDATE employee_payroll SET remarks = NULL",
    ],
    "vendors": ["UPDATE vendor SET vendor_name = CONCAT('Vendor ', LPAD(id, 3, '0')), contact_person = NULL, contact_no = NULL, "
                "email = NULL, address = NULL, notes = NULL"],
    "community": [
        "UPDATE announcement SET title = CONCAT('Announcement ', id), message = 'Sample announcement text.'",
        "UPDATE maintenance_ticket SET title = CONCAT('Ticket ', id), description = 'Sample request text.', resolution = NULL, assigned_to = NULL",
        "UPDATE gate_pass SET visitor_name = CONCAT('Visitor ', id), purpose = 'Sample purpose', review_note = NULL",
        "UPDATE move_certificate SET person_name = CONCAT('Person ', LPAD(id, 4, '0'))",
    ],
}
# Values that must not survive anonymization (collected in memory before it, never printed).
SENSITIVE_SOURCES = [
    "SELECT owner_name, email, contact_no FROM owner", "SELECT tenant_name, email, contact_no FROM tenant",
    "SELECT full_name, email, contact_no FROM employee", "SELECT vendor_name, contact_person, email, contact_no FROM vendor",
    "SELECT visitor_name FROM gate_pass", "SELECT person_name FROM move_certificate",
    "SELECT display_name FROM resident_profile", "SELECT username FROM `user`",
]


# ------------------------------------------------------------------ helpers
def say(text=""):
    print(text, flush=True)


def head_revision():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    return ScriptDirectory.from_config(Config(os.path.join(ROOT, "database", "alembic.ini"))).get_current_head()


def known_revisions():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    return {r.revision for r in ScriptDirectory.from_config(Config(os.path.join(ROOT, "database", "alembic.ini"))).walk_revisions()}


def pc_id(name=None):
    """A non-reversible id of a computer name (the manifest never contains the name itself)."""
    return hashlib.sha256((name or socket.gethostname()).strip().lower().encode()).hexdigest()[:16]


def scalar(cur, sql, args=None):
    cur.execute(sql, args)
    row = cur.fetchone()
    return row[0] if row else None


def table_counts(cur):
    cur.execute("SHOW TABLES")
    tables = sorted(r[0] for r in cur.fetchall())
    return {t: scalar(cur, f"SELECT COUNT(*) FROM `{t}`") for t in tables}


def execute_if_table(cur, sql):
    """Run a scrub statement/check; a table an older schema doesn't have yet counts as empty."""
    import pymysql
    try:
        cur.execute(sql)
        return cur.fetchone()[0] if sql.lstrip().upper().startswith("SELECT") else cur.rowcount
    except pymysql.err.ProgrammingError as exc:
        if exc.args and exc.args[0] == 1146:   # table doesn't exist
            return 0
        raise


def scrub_problems(cur):
    return {label: n for label, sql in SCRUB_CHECKS.items() if (n := execute_if_table(cur, sql))}


def revision_of(cur):
    cur.execute("SHOW TABLES LIKE 'alembic_version'")
    return scalar(cur, "SELECT version_num FROM alembic_version") if cur.fetchone() else None


class ScratchDatabase:
    """A throwaway database on THIS installation's server with a temporary migration account and data
    rights for the app account. The installation's own database is never opened. Dropped on exit."""

    def __init__(self, prefix):
        self.name = f"{prefix}{secrets.token_hex(4)}"

    def __enter__(self):
        lm.start()
        self.root = lm.root_connect()
        self.mig_user, self.mig_pw = f"{self.name}_mig", secrets.token_urlsafe(18)
        with self.root.cursor() as cur:
            cur.execute(f"CREATE DATABASE `{self.name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin")
            cur.execute("SELECT CONCAT(QUOTE(user),'@',QUOTE(host)) FROM mysql.user WHERE user=%s", (lm.APP_USER,))
            self.accounts = [r[0] for r in cur.fetchall()]
            for account in self.accounts:
                cur.execute(f"GRANT {lm.APP_PRIVILEGES} ON `{self.name}`.* TO {account}")
            for host in ("localhost", "127.0.0.1"):
                cur.execute(f"CREATE USER '{self.mig_user}'@'{host}' IDENTIFIED BY %s", (self.mig_pw,))
                cur.execute(f"GRANT {lm.MIGRATE_PRIVILEGES} ON `{self.name}`.* TO '{self.mig_user}'@'{host}'")
        self.root.commit()
        self.root.select_db(self.name)
        self.env = {**os.environ, "MYSQL_DATABASE": self.name, "MYSQL_MIGRATE_USER": self.mig_user,
                    "MYSQL_MIGRATE_PASSWORD": self.mig_pw}
        self.env.pop("DATABASE_URL", None)
        return self

    def run(self, *args):
        r = subprocess.run([sys.executable, *args], cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=900)
        if r.returncode:
            raise RuntimeError(f"{' '.join(args[:2])} failed:\n{(r.stdout + r.stderr).strip()[-1500:]}")
        return r.stdout

    def __exit__(self, *exc):
        try:
            with self.root.cursor() as cur:
                cur.execute(f"DROP DATABASE IF EXISTS `{self.name}`")
                for account in self.accounts:
                    cur.execute(f"REVOKE ALL PRIVILEGES ON `{self.name}`.* FROM {account}")
                for host in ("localhost", "127.0.0.1"):
                    cur.execute(f"DROP USER IF EXISTS '{self.mig_user}'@'{host}'")
            self.root.commit()
        finally:
            self.root.close()


def dump_database(database, out_path):
    """mysqldump (consistent snapshot) -> gzip; must end with mysqldump's completion trailer."""
    part = out_path + ".part"
    with lm.client_options() as opt, gzip.open(part, "wb", compresslevel=6) as out:
        proc = subprocess.Popen([lm.exe("mysqldump"), f"--defaults-extra-file={opt}", "--single-transaction", "--routines",
                                 "--triggers", "--hex-blob", "--default-character-set=utf8mb4", database],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for chunk in iter(lambda: proc.stdout.read(1024 * 1024), b""):
            out.write(chunk)
        err = proc.stderr.read().decode("utf-8", "replace")
        failed = proc.wait() != 0
    if failed:
        os.remove(part)
        raise RuntimeError(f"mysqldump failed: {err.strip()[:300]}")
    os.replace(part, out_path)
    check_dump(out_path)


def check_dump(path):
    tail = b""
    with gzip.open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            tail = (tail + chunk)[-4096:]
    if b"-- Dump completed" not in tail:
        raise RuntimeError(f"{os.path.basename(path)} is incomplete (no completion trailer).")


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_path(package):
    return re.sub(r"\.sql\.gz$", "", package) + ".manifest.json"


def write_package(db, kind, out_dir, included=()):
    """Final checks on the throwaway/staging database, then the dump and its manifest."""
    with db.root.cursor() as cur:
        problems = scrub_problems(cur)
        if problems:
            raise RuntimeError(f"Not written: these were still present after scrubbing: {problems}")
        counts, revision = table_counts(cur), revision_of(cur)
        version = scalar(cur, "SELECT VERSION()")
    os.makedirs(out_dir, exist_ok=True)
    package = os.path.join(out_dir, f"{PKG_PREFIX}{kind}_{datetime.now():%Y%m%d_%H%M%S}.sql.gz")
    dump_database(db.name, package)
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    manifest = {
        "format": FORMAT, "kind": kind, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "schema_revision": revision, "app_commit": commit or None, "mariadb_version": version,
        "source_pc_id": pc_id(), "contains_accounts": False, "outbound_email": "removed",
        "included_optional": sorted(included), "removed": [label for label, _ in SCRUB],
        "sha256": sha256_of(package), "bytes": os.path.getsize(package), "tables": counts,
    }
    with open(manifest_path(package), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    return package, manifest


def print_package(package, manifest):
    rows = manifest["tables"]
    say(f"\nTest package written:\n    {package}\n    {manifest_path(package)}")
    say(f"  kind {manifest['kind']}, schema {manifest['schema_revision']}, {manifest['bytes'] / 1024:.0f} KB, sha256 {manifest['sha256'][:16]}...")
    say(f"  units {rows.get('unit', 0)}, owners {rows.get('owner', 0)}, tenants {rows.get('tenant', 0)}, bills {rows.get('billing', 0)}, "
        f"payments {rows.get('payment', 0)}, receipts {rows.get('receipt', 0)}, user accounts {rows.get('user', 0)}")
    say("Copy BOTH files to the test PC (USB drive). They contain no accounts, passwords or email settings.")


def app_connect(database=None):
    import pymysql
    return pymysql.connect(host=lm.HOST, port=lm.PORT, user=lm.APP_USER, password=os.getenv("MYSQL_PASSWORD", ""),
                           database=database or lm.DATABASE, charset="utf8mb4", connect_timeout=5)


def arg_value(args, flag, default=None):
    if flag in args:
        i = args.index(flag)
        if i + 1 >= len(args):
            sys.exit(f"{flag} needs a value.")
        return args[i + 1]
    return default


# ------------------------------------------------------------------ source PC
def profile():
    """Counts only; opens the live database read-only with the application account."""
    lm.start()
    con = app_connect()
    try:
        with con.cursor() as cur:
            test_domains = " AND ".join(f"LOWER(email) NOT LIKE '%%@{d}'" for d in TEST_EMAIL_DOMAINS)
            c = {
                "units": scalar(cur, "SELECT COUNT(*) FROM unit"),
                "non-TEST units": scalar(cur, "SELECT COUNT(*) FROM unit WHERE unit_no NOT LIKE '%%TEST%%'"),
                "owners/tenants on non-TEST units": scalar(cur, "SELECT (SELECT COUNT(*) FROM owner o JOIN unit u ON u.id=o.unit_id "
                                                           "WHERE u.unit_no NOT LIKE '%%TEST%%') + (SELECT COUNT(*) FROM tenant t "
                                                           "JOIN unit u ON u.id=t.unit_id WHERE u.unit_no NOT LIKE '%%TEST%%')"),
                "owner/tenant emails outside example domains": scalar(
                    cur, f"SELECT (SELECT COUNT(*) FROM owner WHERE COALESCE(email,'')<>'' AND {test_domains}) + "
                         f"(SELECT COUNT(*) FROM tenant WHERE COALESCE(email,'')<>'' AND {test_domains})"),
                "bills on non-TEST units": scalar(cur, "SELECT COUNT(*) FROM billing b JOIN unit u ON u.id=b.unit_id WHERE u.unit_no NOT LIKE '%%TEST%%'"),
                "receipts": scalar(cur, "SELECT COUNT(*) FROM receipt"),
                "employees (HR/payroll)": scalar(cur, "SELECT COUNT(*) FROM employee"),
                "vendors": scalar(cur, "SELECT COUNT(*) FROM vendor"),
                "maintenance tickets / gate passes": scalar(cur, "SELECT (SELECT COUNT(*) FROM maintenance_ticket) + (SELECT COUNT(*) FROM gate_pass)"),
                "SMTP password stored": scalar(cur, "SELECT COUNT(*) FROM setting WHERE `key`='smtp_password' AND COALESCE(value,'')<>''"),
            }
            cur.execute("SELECT role, COUNT(*) FROM `user` GROUP BY role ORDER BY role")
            roles = {r[0]: r[1] for r in cur.fetchall()}
            revision = revision_of(cur)
    finally:
        con.close()
    say(f"Database `{lm.DATABASE}` (read-only check, counts only), schema {revision}")
    for label, n in c.items():
        say(f"  {label:48} {n}")
    say(f"  {'user accounts by role (never exported)':48} {roles or 0}")
    real = any(c[k] for k in ("non-TEST units", "owners/tenants on non-TEST units", "owner/tenant emails outside example domains",
                              "bills on non-TEST units", "employees (HR/payroll)", "vendors"))
    if real:
        say("\nVERDICT: this database holds records that may be REAL (non-TEST units, people, HR or vendors).")
        say("  Use export-synthetic (recommended), or get the owner's approval before export-sanitized.")
    else:
        say("\nVERDICT: only synthetic TEST-* property records were found. export-synthetic is still recommended.")
    return 2 if real else 0


def export_synthetic(out_dir):
    say("Building the synthetic data set in a throwaway database (the real database is not opened)...")
    with ScratchDatabase("cl9_pkg_") as db:
        db.run("database/db_migrate.py", "install")
        say(f"  tables created ({head_revision()})")
        db.run("-c", "import seed_test_data; seed_test_data.add_if_missing()")
        say("  sample units TEST-501..504 with owners, tenants, bills, payments and water readings added")
        with db.root.cursor() as cur:
            for _, sql in SCRUB:
                execute_if_table(cur, sql)
        db.root.commit()
        package, manifest = write_package(db, "synthetic", out_dir)
    say("  throwaway database dropped")
    print_package(package, manifest)


def _sensitive_values(cur):
    values = set()
    for sql in SENSITIVE_SOURCES:
        cur.execute(sql)
        for row in cur.fetchall():
            for v in row:
                v = (str(v) if v is not None else "").strip().lower()
                if len(v) >= 5 and v not in ("owner", "tenant", "staff", "admin"):
                    values.add(v)
    return values


def _leaks(cur, database, values):
    """(table.column, count) of text cells that still contain an original value. Never returns the values."""
    if not values:
        return []
    cur.execute("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema=%s AND data_type IN "
                "('varchar','char','text','mediumtext','longtext','tinytext')", (database,))
    found = []
    for table, column in cur.fetchall():
        cur.execute(f"SELECT `{column}` FROM `{table}` WHERE `{column}` IS NOT NULL AND `{column}` <> ''")
        hits = sum(1 for (cell,) in cur.fetchall() if any(v in str(cell).lower() for v in values))
        if hits:
            found.append((f"{table}.{column}", hits))
    return found


def live_checksums():
    con = lm.root_connect(lm.DATABASE)
    try:
        with con.cursor() as cur:
            cur.execute("SHOW TABLES")
            tables = [r[0] for r in cur.fetchall()]
            cur.execute("CHECKSUM TABLE " + ", ".join(f"`{t}`" for t in tables))
            return {r[0]: r[1] for r in cur.fetchall()}
    finally:
        con.close()


def copy_live_into(db):
    """The live database -> the staging database: mysqldump --single-transaction (a read-only snapshot)
    piped straight into mysql, so no copy of the data is written to disk."""
    say(f"Copying `{lm.DATABASE}` into the staging database (read-only snapshot)...")
    with lm.client_options() as o1, lm.client_options() as o2:
        dump = subprocess.Popen([lm.exe("mysqldump"), f"--defaults-extra-file={o1}", "--single-transaction", "--routines",
                                 "--triggers", "--hex-blob", "--default-character-set=utf8mb4", lm.DATABASE],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        load = subprocess.Popen([lm.exe("mysql"), f"--defaults-extra-file={o2}", "--default-character-set=utf8mb4", db.name],
                                stdin=dump.stdout, stderr=subprocess.PIPE)
        dump.stdout.close()
        load_err = load.communicate()[1].decode("utf-8", "replace")
        dump_err = dump.stderr.read().decode("utf-8", "replace")
        if dump.wait() or load.returncode:
            raise RuntimeError(f"copy failed: {(dump_err + load_err).strip()[:400]}")


def export_copy(out_dir):
    """The live database's records AS THEY ARE (no anonymization), for a database that holds only dummy
    data. Accounts, passwords, tokens, audit history and email/payment settings are still removed."""
    say("COPY OF THE LIVE DATABASE WITHOUT ANONYMIZATION - only when every record in it is dummy/test data.")
    say("  Kept as they are: units, owners/tenants (names, emails, phones), bills, payments, receipts, advances,")
    say("  water readings, expenses, HR/payroll, vendors, announcements, tickets, gate passes, settings.")
    say("  Always removed: " + "; ".join(label for label, _ in SCRUB))
    if input("Type DUMMY DATA to confirm the database holds no real people's data: ").strip() != "DUMMY DATA":
        sys.exit("Not confirmed. Nothing was exported.")
    lm.start()
    before = live_checksums()
    with ScratchDatabase("cl9_stage_") as db:
        copy_live_into(db)
        with db.root.cursor() as cur:
            for _, statement in SCRUB:
                execute_if_table(cur, statement)
        db.root.commit()
        package, manifest = write_package(db, "copy", out_dir)
    say("  staging database dropped")
    after = live_checksums()
    say(f"  live database unchanged (table checksums before == after): {'yes' if before == after else 'NO - investigate'}")
    print_package(package, manifest)


def export_sanitized(out_dir, include):
    unknown = set(include) - set(CATEGORIES)
    if unknown:
        sys.exit(f"Unknown --include categories: {', '.join(sorted(unknown))} (choose from {', '.join(CATEGORIES)}).")
    say("ANONYMIZED COPY OF THE LIVE DATABASE - only with the owner's approval.")
    say("  Kept (anonymized): units, owners/tenants (names, emails, phones replaced), bills, payments, receipts,")
    say("  advances, water readings, expenses, settings (amounts, dates and statuses are kept as they are).")
    say("  Optional, " + ", ".join(f"{k}: {'INCLUDED (anonymized)' if k in include else 'left out'}" for k in CATEGORIES))
    say("  Always removed: " + "; ".join(label for label, _ in SCRUB))
    if input("Type ANONYMIZE to continue: ").strip() != "ANONYMIZE":
        sys.exit("Not confirmed. Nothing was exported.")
    lm.start()
    before = live_checksums()
    with ScratchDatabase("cl9_stage_") as db:
        copy_live_into(db)
        with db.root.cursor() as cur:
            values = _sensitive_values(cur)
            cur.execute("SET FOREIGN_KEY_CHECKS = 0")
            for category, tables in CATEGORIES.items():
                for statement in (ANONYMIZE_OPTIONAL[category] if category in include else [f"DELETE FROM `{t}`" for t in tables]):
                    cur.execute(statement)
            cur.execute("SET FOREIGN_KEY_CHECKS = 1")
            for statement in ANONYMIZE:
                cur.execute(statement)
            for _, statement in SCRUB:
                execute_if_table(cur, statement)
            cur.execute("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema=%s AND column_name IN ("
                        + ",".join(["%s"] * len(ACTOR_COLUMNS)) + ")", (db.name, *ACTOR_COLUMNS))
            for table, column in cur.fetchall():
                cur.execute(f"UPDATE `{table}` SET `{column}` = 'staff' WHERE COALESCE(`{column}`,'') <> ''")
            db.root.commit()
            leaks = _leaks(cur, db.name, values)
        if leaks:
            raise RuntimeError("Not written: original names/emails/phones are still present in "
                               + ", ".join(f"{col} ({n} cell(s))" for col, n in leaks) + ". Extend ANONYMIZE in database/test_pc.py.")
        say(f"  anonymized; no original name, email or phone found in any text column ({len(values)} values checked)")
        package, manifest = write_package(db, "sanitized", out_dir, include)
    say("  staging database dropped")
    after = live_checksums()
    say(f"  live database unchanged (table checksums before == after): {'yes' if before == after else 'NO - investigate'}")
    print_package(package, manifest)


DENY_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "logs", "test-output"}
DENY_PREFIXES = ("database/backups/", "database/mysql-data/", "backend/instance/", "uploads/")
DENY_SUFFIXES = (".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".sql.gz", ".bak", ".pyc", ".zip", ".log", ".cnf")
SECRET_RE = re.compile(r"^\s*(SECRET_KEY|[A-Z_]*PASSWORD)\s*=\s*([^\s#<]{6,})", re.M)
# Known non-secrets: the published demo password of the SQLite demo setup (not anyone's credential).
SECRET_SCAN_ALLOW = {"scripts/first_run_setup.py"}


def app_files():
    out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                         capture_output=True, check=True).stdout.decode("utf-8")
    keep = []
    for rel in sorted({p for p in out.split("\0") if p}):
        parts, name = rel.split("/"), rel.rsplit("/", 1)[-1]
        if (set(parts[:-1]) & DENY_DIRS or rel.startswith(DENY_PREFIXES) or rel.lower().endswith(DENY_SUFFIXES)
                or (name.startswith(".env") and name != ".env.example")
                or (rel.lower().endswith(".sql") and rel != "database/schema.sql")
                or not os.path.isfile(os.path.join(ROOT, rel))):
            continue
        keep.append(rel)
    return keep


def package_app(out_dir):
    files = app_files()
    flagged = []
    for rel in files:
        path = os.path.join(ROOT, rel)
        if os.path.getsize(path) < 2 * 1024 * 1024 and rel != ".env.example" and rel not in SECRET_SCAN_ALLOW:
            try:
                text = open(path, encoding="utf-8").read()
            except (UnicodeDecodeError, OSError):
                continue
            if SECRET_RE.search(text) and not rel.startswith(("tests/", "docs/")):
                flagged.append(rel)
    if flagged:
        sys.exit("Not packaged: these files look like they contain a password or secret key:\n  " + "\n  ".join(flagged))
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    os.makedirs(out_dir, exist_ok=True)
    target = os.path.join(out_dir, f"cityland9_app_{datetime.now():%Y%m%d_%H%M%S}.zip")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in files:
            z.write(os.path.join(ROOT, rel), "CITYLAND9_SYSTEM/" + rel)
    say(f"Application package: {target}\n  {len(files)} files, {os.path.getsize(target) / 1024 ** 2:.1f} MB")
    say("  Left out: .env, databases, backups, MariaDB data, logs, .venv, node_modules, git history.")
    if dirty:
        say("  Note: the working tree has uncommitted changes; they ARE included (the files as they are now).")


# ------------------------------------------------------------------ target PC
def target_problems():
    """Configuration that would make this NOT an independent local test installation."""
    problems = []
    if os.getenv("CL9_TEST_INSTALL", "").strip() != "1":
        problems.append("CL9_TEST_INSTALL=1 is not set in .env (this command is only for a test installation).")
    if os.getenv("DB_ENGINE", "").strip().lower() != "mysql":
        problems.append("DB_ENGINE=mysql is not set.")
    if os.getenv("DATABASE_URL"):
        problems.append("DATABASE_URL is set; remove it (the test installation uses its own local MariaDB).")
    if os.getenv("MYSQL_HOST", "127.0.0.1").strip().lower() not in LOOPBACK:
        problems.append("MYSQL_HOST is not 127.0.0.1 (a test installation must not connect to another PC).")
    if not TEST_DB_RE.match(lm.DATABASE):
        problems.append(f"MYSQL_DATABASE `{lm.DATABASE}` is not a test name (use cityland9_test, letters/digits/_ only).")
    return problems


def require_test_install():
    problems = target_problems()
    if problems:
        sys.exit("Refused:\n  " + "\n  ".join(problems))


def read_manifest(package):
    path = manifest_path(package)
    if not os.path.exists(path):
        sys.exit(f"No manifest next to the package ({os.path.basename(path)}). Only test packages made by "
                 "test_pc.py export-synthetic/export-sanitized can be restored here (never a production backup).")
    with open(path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    if manifest.get("format") != FORMAT or manifest.get("contains_accounts") is not False:
        sys.exit("This is not a CityLand 9 test package (wrong format, or it contains accounts).")
    if sha256_of(package) != manifest.get("sha256"):
        sys.exit("Checksum mismatch: the package was damaged or changed during the copy. Copy it again.")
    check_dump(package)
    return manifest


def check_target(package=None):
    results = []

    def check(label, ok, detail="", warn=False):
        results.append(ok or warn)
        say(f"{'PASS' if ok else ('WARN' if warn else 'FAIL')}  {label}{'' if not detail else ' - ' + detail}")

    for problem in target_problems():
        check("configuration", False, problem)
    secret = os.getenv("SECRET_KEY", "")
    check("SECRET_KEY set for this installation", len(secret) >= 32 and "<" not in secret)
    ini = lm.ini_path()
    bind = ""
    if os.path.exists(ini):
        bind = next((line.split("=", 1)[1].strip() for line in open(ini, encoding="utf-8") if line.startswith("bind-address")), "")
    check("own MariaDB data folder on this PC", os.path.isdir(lm.DATA_DIR) and os.path.exists(ini), lm.DATA_DIR)
    check("MariaDB listens on this PC only", bind == "127.0.0.1", f"bind-address={bind or '?'}, port {lm.PORT}")
    lm.start()
    con = lm.root_connect()
    try:
        with con.cursor() as cur:
            cur.execute("SELECT @@hostname, @@datadir, VERSION()")
            host, datadir, version = cur.fetchone()
            same_host = host.strip().lower() == socket.gethostname().strip().lower()
            check("database server is this computer", same_host, f"server {host}, MariaDB {version}")
            check("database files are this installation's",
                  os.path.normcase(os.path.abspath(datadir)).rstrip("\\/") == os.path.normcase(os.path.abspath(lm.DATA_DIR)).rstrip("\\/"),
                  datadir)
            exists = scalar(cur, "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name=%s", (lm.DATABASE,))
            revision = None
            if exists:
                cur.execute(f"USE `{lm.DATABASE}`")
                revision = revision_of(cur)
                if revision:
                    cur.execute("SELECT role, COUNT(*) FROM `user` WHERE active=1 GROUP BY role")
                    say(f"      active accounts by role: {dict(cur.fetchall()) or 'none yet'}")
    finally:
        con.close()
    head = head_revision()
    check(f"schema of `{lm.DATABASE}` matches this code", revision == head, f"database {revision or 'empty'}, code {head}",
          warn=revision is None)
    from_env = os.getenv("OUTBOUND_EMAIL", "").strip().lower() in ("disabled", "off", "0", "false")
    check("outbound email disabled", from_env or os.getenv("CL9_TEST_INSTALL", "").strip() == "1")
    if package:
        manifest = read_manifest(package)
        same = manifest.get("source_pc_id") == pc_id(host)
        check("this is not the PC the package was exported from", not same,
              "SAME PC as the source (expected only in a rehearsal on one PC)" if same else "", warn=same)
    say("\nRESULT: " + ("ready (independent local test installation)" if all(results) else "NOT ready - fix the FAIL lines"))
    return 0 if all(results) else 1


def restore_package(package, overwrite, show_next=True):
    require_test_install()
    import restore as rs
    package = os.path.abspath(package)
    if not os.path.exists(package):
        sys.exit(f"No such file: {package}")
    manifest = read_manifest(package)
    target = lm.DATABASE
    say(f"Package: {manifest['kind']} data, schema {manifest['schema_revision']}, {len(manifest['tables'])} tables (checksum OK)")
    lm.start()
    con = lm.root_connect()
    try:
        with con.cursor() as cur:
            exists = scalar(cur, "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name=%s", (target,))
            tables = scalar(cur, "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s", (target,))
            if tables:
                if overwrite != target:
                    sys.exit(f"`{target}` already has {tables} table(s). Nothing was changed.\n"
                             f"To REPLACE it, run again with  --overwrite {target}  (you'll be asked to type the name),\n"
                             "or set another MYSQL_DATABASE (e.g. cityland9_test2) and run local_mysql.py init.")
                print(f"This DELETES ALL DATA in the test database `{target}` and replaces it with the package.")
                if input(f"Type the database name ({target}) to continue: ").strip() != target:
                    sys.exit("Not confirmed. Nothing was changed.")
                say(f"Safety backup of `{target}`: {lm.backup(label='before_test_restore')}")
                cur.execute(f"DROP DATABASE `{target}`")
                exists = 0
            if not exists:
                cur.execute(f"CREATE DATABASE `{target}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin")
            # A database other than the one local_mysql.py init set up has no grants yet; secure() below
            # revokes before it grants, which needs an existing grant.
            cur.execute("SELECT CONCAT(QUOTE(user),'@',QUOTE(host)) FROM mysql.user WHERE user=%s", (lm.APP_USER,))
            for account in [r[0] for r in cur.fetchall()]:
                cur.execute(f"GRANT {lm.APP_PRIVILEGES} ON `{target}`.* TO {account}")
        con.commit()
    finally:
        con.close()
    say(f"Restoring into `{target}` ...")
    rs.load(package, target)
    lm.secure(target)   # app account: data only; migration account: schema changes
    con = lm.root_connect(target)
    try:
        with con.cursor() as cur:
            revision = revision_of(cur)
    finally:
        con.close()
    head = head_revision()
    if revision != head:
        if revision not in known_revisions():
            sys.exit(f"The package's schema ({revision}) is newer than this code ({head}). Copy the newer application first.")
        say(f"Upgrading the schema {revision} -> {head} (migration account, backup first)...")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "database", "db_migrate.py"), "upgrade"], cwd=ROOT)
        if r.returncode:
            sys.exit("Schema upgrade failed; see above. The restored data is in place; fix the error and run "
                     r"database\db_migrate.py upgrade again.")
    result = rs.verify(target)
    con = lm.root_connect(target)
    try:
        with con.cursor() as cur:
            leftovers = scrub_problems(cur)
            counts = table_counts(cur)
    finally:
        con.close()
    expected = {t: n for t, n in manifest["tables"].items() if t != "alembic_version"}
    mismatched = [t for t, n in expected.items() if counts.get(t) != n] if revision == manifest["schema_revision"] else []
    say(f"Tables: {result['tables']}  schema: {head}  bills computed: {result['bills_computed']}  "
        f"row counts match the manifest: {('yes' if not mismatched else 'NO: ' + ', '.join(mismatched)) if revision == manifest['schema_revision'] else 'not compared (schema upgraded)'}")
    ok = not result["missing"] and result["bills_computed"] is not None and not leftovers and not mismatched
    if result["missing"]:
        say("MISSING TABLES: " + ", ".join(result["missing"]))
    if result["bill_error"]:
        say("Bill check failed: " + result["bill_error"])
    if leftovers:
        say(f"UNEXPECTED DATA (should not be in a test package): {leftovers}")
    if not ok:
        sys.exit("RESTORE NOT VERIFIED")
    say("RESTORE VERIFIED. No accounts were copied." + (" Next:" if show_next else ""))
    if not show_next:
        return
    say(r"  1. Developer Superadmin:  .venv\Scripts\python.exe database\create_admin.py")
    say(r"  2. Tester accounts:       .venv\Scripts\python.exe database\test_pc.py create-testers --residents 2")
    say(r"  3. Check, then start:     .venv\Scripts\python.exe database\test_pc.py check-target   then  START_WINDOWS.bat")


TESTER_ROLES = ("admin", "accounting", "staff", "manager")
CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def temporary_password(username, security):
    while True:
        pw = "-".join("".join(secrets.choice(CODE_ALPHABET) for _ in range(4)) for _ in range(4))
        if not security.password_problem(pw, username):
            return pw


def create_testers(roles, residents):
    require_test_install()
    if "super_admin" in roles:
        sys.exit("Superadmin is not a tester role. The developer creates it on this PC with database\\create_admin.py.")
    bad = [r for r in roles if r not in TESTER_ROLES]
    if bad:
        sys.exit(f"Unknown role(s): {', '.join(bad)} (choose from {', '.join(TESTER_ROLES)}).")
    lm.start()
    sys.path.insert(0, os.path.join(ROOT, "backend"))
    from app.core import security
    from app.core.roles import ROLE_LABELS
    import legacy_app as m
    from app.services import resident_provisioning as prov
    made = []
    with m.app.app_context():
        for role in roles:
            username = f"tester.{role}"
            if m.User.query.filter(m.func.lower(m.User.username) == username).first():
                say(f"  {username} already exists - left as it is")
                continue
            password = temporary_password(username, security)
            user = m.User(username=username, role=role, active=True, password_hash="!")
            m.set_password(user, password, temporary=True)
            m.db.session.add(user)
            m.db.session.add(m.AuditLog(username="system", action=f"Tester account {username} ({role}) created by test_pc.py"))
            m.db.session.commit()
            made.append((username, ROLE_LABELS.get(role, role), password, "temporary password"))
        if residents:
            people = []
            for cls, kind in ((m.Owner, "Owner"), (m.Tenant, "Tenant")):
                for person in cls.query.filter_by(status="Current").order_by(cls.id).all():
                    unit = person.unit
                    if unit and unit.active and (unit.unit_type or "").upper() not in ("PARKING", "STORAGE"):
                        people.append((unit.unit_no, kind, person.id))
            for unit_no, kind, pid in sorted(people)[:residents]:
                with m.app.test_request_context():
                    try:
                        result = prov.provision(m, person_type=kind, person_id=pid, mode="new", link_user_id=None,
                                                actor="test_pc.py", form_token=secrets.token_urlsafe(24))
                    except prov.ProvisionError as exc:
                        say(f"  {kind} on {unit_no}: skipped ({exc})")
                        continue
                cred = result["credentials"]
                made.append((cred["username"], f"Resident ({kind}, {unit_no})", cred["activationCode"], f"activation code until {cred['expiresAt'][:10]}"))
    if not made:
        say("No new accounts.")
        return
    say("\nNEW TESTER ACCOUNTS - shown only now. Hand them over privately; each must choose a new password at first sign-in.")
    for username, label, secret, kind in made:
        say(f"  {username:28} {label:34} {secret}   ({kind})")
    say("These are not saved anywhere readable. Lost? Superadmin: Users & Access (staff) or Resident Accounts > New activation code.")


def make_env():
    path, example = os.path.join(ROOT, ".env"), os.path.join(ROOT, ".env.example")
    if os.path.exists(path):
        text = open(path, encoding="utf-8-sig").read()
        if re.search(r"^CL9_TEST_INSTALL=1\s*$", text, re.M):
            say("Configuration (.env): already set up for this test PC - kept as it is.")
            return
        if "first_run_setup.py" in text:
            backup = path + ".demo-backup"
            os.replace(path, backup)
            say(f"The demo configuration START_WINDOWS.bat made was set aside as {os.path.basename(backup)}.")
        else:
            sys.exit("This folder already has a .env that is not a test installation. Nothing was changed.\n"
                     "If this is the office server, don't run the test setup here. Otherwise rename .env and run again.")
    text = open(example, encoding="utf-8").read()
    text = re.sub(r"^SECRET_KEY=.*$", "SECRET_KEY=" + secrets.token_hex(32), text, count=1, flags=re.M)
    text = ("# Created by database/test_pc.py make-env for a TEST installation of CityLand 9 on this PC.\n"
            "# Holds this PC's own secrets: never copy or share it.\n\n" + text.rstrip() +
            "\n\n# --- Test installation (CITYLAND9_TEST_PC_MIGRATION.md) ---\n"
            "MYSQL_DATABASE=cityland9_test\nCL9_TEST_INSTALL=1\nOUTBOUND_EMAIL=disabled\n")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    say("Configuration (.env) created: new secret key, database cityland9_test, test installation, email disabled.")


def find_package(explicit=None):
    if explicit:
        return os.path.abspath(explicit)
    import glob
    found = []
    for folder in (ROOT, os.path.dirname(ROOT), os.path.join(ROOT, "database", "test_packages")):
        found += glob.glob(os.path.join(folder, f"{PKG_PREFIX}*.sql.gz"))
    return max(found, key=os.path.getmtime) if found else None


def setup(package=None):
    require_test_install()
    say("\n[1/5] This PC's own database server")
    lm.init()
    con = lm.root_connect(lm.DATABASE)
    try:
        with con.cursor() as cur:
            tables = scalar(cur, "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s", (lm.DATABASE,))
            revision = revision_of(cur) if tables else None
    finally:
        con.close()
    say("\n[2/5] Data")
    if tables and not revision:
        sys.exit(f"`{lm.DATABASE}` holds an incomplete earlier restore. Run:\n"
                 f"    .venv\\Scripts\\python.exe database\\test_pc.py restore <package.sql.gz> --overwrite {lm.DATABASE}")
    if tables:
        say(f"Already restored ({tables} tables, schema {revision}) - left as it is.")
    else:
        found = find_package(package)
        if not found:
            sys.exit("No data file found. Put the two files you received (cityland9_testpkg_....sql.gz and its .manifest.json)\n"
                     f"in this folder:\n    {ROOT}\nthen run SETUP_TEST_PC.bat again.")
        restore_package(found, None, show_next=False)
    con = lm.root_connect(lm.DATABASE)
    try:
        with con.cursor() as cur:
            supers = scalar(cur, "SELECT COUNT(*) FROM `user` WHERE role='super_admin' AND active=1")
            testers = scalar(cur, "SELECT COUNT(*) FROM `user` WHERE username LIKE 'tester.%'")
    finally:
        con.close()
    say("\n[3/5] Superadmin")
    if supers:
        say(f"{supers} active Superadmin account(s) already exist - none added.")
    else:
        say("Create the Superadmin for this PC (choose a username and a password; the password is not shown while typing).")
        if subprocess.run([sys.executable, os.path.join(ROOT, "database", "create_admin.py")], cwd=ROOT).returncode:
            sys.exit("The Superadmin was not created. Run SETUP_TEST_PC.bat again.")
    say("\n[4/5] Tester accounts")
    if testers:
        say("Tester accounts already exist - none added. (The Superadmin can reset their passwords.)")
    elif subprocess.run([sys.executable, os.path.abspath(__file__), "create-testers", "--residents", "2"], cwd=ROOT).returncode:
        sys.exit("Creating the tester accounts failed; see above.")
    say("\n[5/5] Check")
    return check_target()


# ------------------------------------------------------------------ main
def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        sys.exit(__doc__)
    command, rest = args[0], args[1:]
    out_dir = arg_value(rest, "--out", os.path.join(ROOT, "database", "test_packages"))
    if command == "profile":
        sys.exit(profile())
    if command == "export-synthetic":
        export_synthetic(out_dir)
    elif command == "export-sanitized":
        include = [c for c in (arg_value(rest, "--include", "") or "").split(",") if c]
        export_sanitized(out_dir, include)
    elif command == "export-copy":
        export_copy(out_dir)
    elif command == "package-app":
        package_app(out_dir)
    elif command == "check-target":
        sys.exit(check_target(arg_value(rest, "--package")))
    elif command == "restore":
        if not rest or rest[0].startswith("-"):
            sys.exit("Usage: test_pc.py restore <package.sql.gz> [--overwrite <database>]")
        restore_package(rest[0], arg_value(rest, "--overwrite"))
    elif command == "make-env":
        make_env()
    elif command == "setup":
        sys.exit(setup(rest[0] if rest and not rest[0].startswith("-") else None))
    elif command == "create-testers":
        roles = [r.strip() for r in (arg_value(rest, "--roles", ",".join(TESTER_ROLES))).split(",") if r.strip()]
        create_testers(roles, int(arg_value(rest, "--residents", "0")))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
