"""Checks SQLite can't make, on a DISPOSABLE MariaDB instance (opt-in, slow: about a minute).

    set CL9_MARIADB_TESTS=1 && .venv\\Scripts\\python.exe -m pytest tests\\test_mariadb_disposable.py -v

The module packs the current application (database/test_pc.py package-app), extracts it to a temporary
folder, and starts a NEW MariaDB instance there (its own data folder, its own free port, its own random
passwords in that copy's .env) with XAMPP's programs. The real database, its server and its .env are never
used. Everything is stopped and deleted afterwards. Synthetic data only (seed_test_data.py).
Skipped unless CL9_MARIADB_TESTS=1 and the MariaDB programs exist (MYSQL_BIN_DIR, default C:\\xampp\\mysql\\bin).
"""
import glob
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import zipfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.getenv("CL9_TEST_MYSQL_BIN_DIR", r"C:\xampp\mysql\bin")
pytestmark = pytest.mark.skipif(
    os.getenv("CL9_MARIADB_TESTS") != "1" or not os.path.exists(os.path.join(BIN, "mysqld.exe" if os.name == "nt" else "mysqld")),
    reason="opt-in: set CL9_MARIADB_TESTS=1 (needs XAMPP's MariaDB programs)")
CLEAN = ("MYSQL_", "DB_ENGINE", "DATABASE_URL", "CL9_TEST_INSTALL", "OUTBOUND_EMAIL", "APP_ENV", "SECRET_KEY", "FLASK_", "BACKUP_DIR")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Install:
    """A packaged copy of the app with its own disposable MariaDB."""

    def __init__(self, folder, port):
        self.folder, self.port = folder, port

    def run(self, *args, env=None, input_text=None, check=True):
        e = {k: v for k, v in os.environ.items() if not k.startswith(CLEAN)}
        e.update(env or {})
        r = subprocess.run([sys.executable, *args], cwd=self.folder, env=e, capture_output=True, text=True,
                           input=input_text, timeout=600)
        if check and r.returncode:
            raise AssertionError(f"{' '.join(args)} failed ({r.returncode}):\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
        return r

    def python(self, code, env=None):
        return self.run("-c", "import sys; sys.path.insert(0, 'database'); sys.path.insert(0, 'backend'); sys.path.insert(0, '.')\n" + code, env=env)


@pytest.fixture(scope="module")
def install():
    tmp = tempfile.mkdtemp(prefix="cl9_mariadb_")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "database", "test_pc.py"), "package-app", "--out", tmp],
                       cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    with zipfile.ZipFile(glob.glob(os.path.join(tmp, "cityland9_app_*.zip"))[0]) as z:
        z.extractall(os.path.join(tmp, "pc"))
    folder = os.path.join(tmp, "pc", "CITYLAND9_SYSTEM")
    port = free_port()
    text = open(os.path.join(folder, ".env.example"), encoding="utf-8").read()
    text = re.sub(r"^SECRET_KEY=.*$", "SECRET_KEY=" + secrets.token_hex(32), text, count=1, flags=re.M)
    text = re.sub(r"^MYSQL_BIN_DIR=.*$", "MYSQL_BIN_DIR=" + BIN.replace("\\", "\\\\"), text, count=1, flags=re.M)
    text += f"\nMYSQL_PORT={port}\nMYSQL_DATABASE=cityland9\nFLASK_PORT={free_port()}\n"
    open(os.path.join(folder, ".env"), "w", encoding="utf-8", newline="\n").write(text)
    inst = Install(folder, port)
    try:
        inst.run("database/local_mysql.py", "init")
        out = inst.run("database/db_migrate.py", "install").stdout
        assert "Matches the models: yes" in out, out
        inst.run("seed_test_data.py")
        yield inst
    finally:
        inst.run("database/local_mysql.py", "stop", check=False)
        shutil.rmtree(tmp, ignore_errors=True)


def test_the_disposable_instance_is_not_the_real_database(install):
    out = install.python("import local_mysql as lm; print(lm.PORT, lm.DATA_DIR)").stdout.split()
    assert int(out[0]) == install.port and os.path.normcase(install.folder) in os.path.normcase(out[1])


def test_migration_0012_downgrades_and_upgrades_with_bill_figures_unchanged(install):
    down = install.run("database/db_migrate.py", "downgrade", "0011_resident_provisioning").stdout
    assert "Bill figures unchanged" in down, down
    assert "0011_resident_provisioning" in install.run("database/db_migrate.py", "status").stdout
    up = install.run("database/db_migrate.py", "upgrade").stdout
    assert "Live schema matches the models: yes" in up and "Bill figures unchanged" in up, up
    assert "0012_import_crosswalk (head)" in install.run("database/db_migrate.py", "status").stdout


def test_crosswalk_uniqueness_is_enforced_by_mariadb(install):
    out = install.python('''
import legacy_app as m
from sqlalchemy.exc import IntegrityError
with m.app.app_context():
    src = m.ImportSource(code="synthetic-src", name="Synthetic")
    m.db.session.add(src); m.db.session.commit()
    m.db.session.add(m.ImportCrosswalk(source_id=src.id, entity_type="unit", external_id="A1", target_id=1)); m.db.session.commit()
    results = []
    for ext, target in (("A1", 2), ("B2", 1)):          # same external id again; same target again
        m.db.session.add(m.ImportCrosswalk(source_id=src.id, entity_type="unit", external_id=ext, target_id=target))
        try:
            m.db.session.commit(); results.append("accepted")
        except IntegrityError:
            m.db.session.rollback(); results.append("refused")
    m.db.session.add(m.ImportCrosswalk(source_id=src.id, entity_type="owner", external_id="A1", target_id=1)); m.db.session.commit()
    results.append("other-entity-ok")
    m.ImportCrosswalk.query.delete(); m.ImportSource.query.delete(); m.db.session.commit()
    print(",".join(results))
''').stdout.strip().splitlines()[-1]
    assert out == "refused,refused,other-entity-ok"


def test_test_package_restores_once_and_refuses_a_nonempty_database(install):
    pkgs = os.path.join(install.folder, "database", "test_packages")
    out = install.run("database/test_pc.py", "export-synthetic").stdout
    assert "Test package written" in out, out
    package = glob.glob(os.path.join(pkgs, "cityland9_testpkg_synthetic_*.sql.gz"))[0]
    target = {"CL9_TEST_INSTALL": "1", "OUTBOUND_EMAIL": "disabled", "MYSQL_DATABASE": "cityland9_test_disposable"}
    first = install.run("database/test_pc.py", "restore", package, env=target).stdout
    assert "RESTORE VERIFIED" in first and "No accounts were copied" in first, first
    again = install.run("database/test_pc.py", "restore", package, env=target, check=False)
    assert again.returncode != 0 and "already has" in (again.stdout + again.stderr) and "Nothing was changed" in (again.stdout + again.stderr)
    refused = install.run("database/test_pc.py", "restore", package, env={**target, "MYSQL_DATABASE": "cityland9"}, check=False)
    assert refused.returncode != 0 and "not a test name" in refused.stderr
    damaged = package.replace(".sql.gz", "_copy.sql.gz")
    shutil.copyfile(package, damaged)
    shutil.copyfile(package.replace(".sql.gz", ".manifest.json"), damaged.replace(".sql.gz", ".manifest.json"))
    with open(damaged, "ab") as fh:
        fh.write(b"x")
    bad = install.run("database/test_pc.py", "restore", damaged, env={**target, "MYSQL_DATABASE": "cityland9_test_other"}, check=False)
    assert bad.returncode != 0 and "Checksum mismatch" in bad.stderr
