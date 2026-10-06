"""Test-PC transfer (database/test_pc.py, CITYLAND9_TEST_PC_MIGRATION.md): the safeguards that need no
MariaDB server. The MariaDB export/restore itself was rehearsed on disposable database instances
(see the guide's verification section); these tests never open a database server."""
import os
import subprocess
import sys
import zipfile

import pytest

from conftest import api

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


def run(args, **env_changes):
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "CL9_TEST_INSTALL", "OUTBOUND_EMAIL")}
    env.update(env_changes)
    return subprocess.run([PY, *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)


TEST_TARGET = {"CL9_TEST_INSTALL": "1", "DB_ENGINE": "mysql", "MYSQL_HOST": "127.0.0.1", "MYSQL_DATABASE": "cityland9_test_unit"}


@pytest.mark.parametrize("env", [{"CL9_TEST_INSTALL": "1"}, {"OUTBOUND_EMAIL": "disabled"}])
def test_a_test_installation_never_opens_an_smtp_connection(app_module, monkeypatch, env):
    m = app_module
    opened = []
    monkeypatch.setattr(m.smtplib, "SMTP", lambda *a, **k: opened.append(a))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with m.app.test_request_context():
        for k, v in (("smtp_host", "smtp.example"), ("smtp_sender", "billing@example.com")):
            row = m.Setting.query.filter_by(key=k).first() or m.Setting(key=k)
            row.value = v
            m.db.session.add(row)
        m.db.session.commit()
        bill = m.Billing.query.first()
        with pytest.raises(RuntimeError, match="disabled"):
            m.send_soa_email_to_contact(bill, {"name": "Test Owner", "email": "owner@example.com"})
    assert opened == []


def test_email_screens_say_disabled_on_a_test_installation(app_module, superadmin, monkeypatch):
    monkeypatch.setenv("CL9_TEST_INSTALL", "1")
    client = api(superadmin)
    with app_module.app.app_context():
        month = app_module.Billing.query.first().billing_month
    overview = client.get(f"/api/billing/email?month={month}")
    assert overview.status_code == 200 and overview.get_json()["smtpConfigured"] is False
    resp = client.post("/api/billing/email/send", json={"month": month})
    assert resp.status_code == 409 and "disabled" in resp.get_json()["error"]["message"]


def test_target_commands_refuse_an_installation_not_marked_as_test():
    r = run(["database/test_pc.py", "create-testers"], MYSQL_DATABASE="cityland9")
    assert r.returncode != 0
    assert "CL9_TEST_INSTALL=1 is not set" in r.stderr and "not a test name" in r.stderr


def test_target_commands_refuse_a_remote_database_host():
    r = run(["database/test_pc.py", "create-testers"], **{**TEST_TARGET, "MYSQL_HOST": "192.168.1.20"})
    assert r.returncode != 0 and "must not connect to another PC" in r.stderr


def test_superadmin_is_never_a_tester_role():
    r = run(["database/test_pc.py", "create-testers", "--roles", "super_admin"], **TEST_TARGET)
    assert r.returncode != 0 and "create_admin.py" in r.stderr


def test_restore_refuses_a_file_without_a_test_manifest(tmp_path):
    backup = tmp_path / "cityland9_auto_20261007_010000.sql.gz"   # e.g. a production backup
    backup.write_bytes(b"not used")
    r = run(["database/test_pc.py", "restore", str(backup)], **TEST_TARGET)
    assert r.returncode != 0 and "never a production backup" in r.stderr


def test_restore_refuses_a_package_whose_checksum_does_not_match(tmp_path):
    package = tmp_path / "cityland9_testpkg_synthetic_20261007_010000.sql.gz"
    package.write_bytes(b"changed during the copy")
    (tmp_path / "cityland9_testpkg_synthetic_20261007_010000.manifest.json").write_text(
        '{"format": "cityland9-testpkg/1", "contains_accounts": false, "sha256": "0000"}', encoding="utf-8")
    r = run(["database/test_pc.py", "restore", str(package)], **TEST_TARGET)
    assert r.returncode != 0 and "Checksum mismatch" in r.stderr


def test_the_server_refuses_to_start_a_test_installation_on_a_remote_database():
    r = run(["backend/run.py", "--lan"], **{**TEST_TARGET, "MYSQL_HOST": "10.0.0.5"})
    assert r.returncode != 0 and "own database on this PC" in r.stderr


def test_the_application_package_leaves_out_secrets_data_and_history(tmp_path):
    r = run(["database/test_pc.py", "package-app", "--out", str(tmp_path)])
    assert r.returncode == 0, r.stdout + r.stderr
    [archive] = list(tmp_path.glob("cityland9_app_*.zip"))
    names = zipfile.ZipFile(archive).namelist()
    assert "CITYLAND9_SYSTEM/.env.example" in names and "CITYLAND9_SYSTEM/frontend/dist/index.html" in names
    assert "CITYLAND9_SYSTEM/database/test_pc.py" in names and "CITYLAND9_SYSTEM/database/schema.sql" in names
    forbidden = [n for n in names if n.rsplit("/", 1)[-1] == ".env" or "/.git/" in n or "/.venv/" in n or "/node_modules/" in n
                 or "/mysql-data/" in n or "/database/backups/" in n or "/database/test_packages/" in n
                 or n.endswith((".db", ".db-wal", ".sql.gz", ".bak")) or (n.endswith(".sql") and not n.endswith("database/schema.sql"))]
    assert forbidden == []


def test_export_copy_needs_the_dummy_data_confirmation():
    r = subprocess.run([PY, "database/test_pc.py", "export-copy"], cwd=ROOT, input="yes\n", capture_output=True, text=True, timeout=120,
                       env={k: v for k, v in os.environ.items() if k != "DATABASE_URL"})
    assert r.returncode != 0 and "Not confirmed. Nothing was exported." in r.stderr
