"""Stage 3 operations: service supervisor, backup status/retention, Windows scripts (no database needed).
The real backup -> restore round trip runs on MariaDB: database/tools/mariadb_integration.py backup_restore."""
import gzip
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "database"))

from app.core import ops  # noqa: E402


def test_service_restarts_the_server_and_backs_off(monkeypatch, tmp_path):
    import service
    monkeypatch.setattr(service, "ROOT", str(tmp_path))
    calls, sleeps = [], []
    monkeypatch.setattr(service.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "up to date", ""))
    monkeypatch.setattr(service.subprocess, "call", lambda *a, **k: calls.append(a[0]) or 1)
    monkeypatch.setattr(service.time, "sleep", sleeps.append)
    service.main(max_restarts=3)
    assert len(calls) == 4 and all(c[-2:] == [os.path.join("backend", "run.py"), "--lan"] for c in calls)
    assert sleeps == [10, 20, 40, 80]          # quick crashes back off
    log = (tmp_path / "logs" / "service.log").read_text(encoding="utf-8")
    assert log.count("The web server stopped (exit code 1") == 4


def test_service_does_not_start_on_an_old_schema(monkeypatch, tmp_path):
    import service
    monkeypatch.setattr(service, "ROOT", str(tmp_path))
    started, sleeps = [], []
    monkeypatch.setattr(service.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "schema is older"))
    monkeypatch.setattr(service.subprocess, "call", lambda *a, **k: started.append(a) or 0)
    monkeypatch.setattr(service.time, "sleep", sleeps.append)
    service.main(max_restarts=1)
    assert started == [] and sleeps == [300, 300]
    assert "schema is older" in (tmp_path / "logs" / "service.log").read_text(encoding="utf-8")


def _status(folder, **data):
    (folder / "last_backup.json").write_text(json.dumps(data), encoding="utf-8")


def test_backup_status_ok_old_failed_and_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    assert ops.backup_status(ROOT, 26)["ok"] is False                     # nothing recorded yet
    now = datetime.now(timezone.utc)
    _status(tmp_path, status="ok", finished_at=now.isoformat())
    assert ops.backup_status(ROOT, 26)["ok"] is True
    _status(tmp_path, status="ok", finished_at=(now - timedelta(hours=30)).isoformat())
    assert ops.backup_status(ROOT, 26)["ok"] is False                     # too old
    _status(tmp_path, status="failed", finished_at=now.isoformat(), error="disk full")
    result = ops.backup_status(ROOT, 26)
    assert result["ok"] is False and result["detail"] == "disk full"


def test_readyz_reports_backup_failure_as_warning(app_module, monkeypatch, tmp_path):
    client = app_module.app.test_client()
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    _status(tmp_path, status="failed", finished_at=datetime.now(timezone.utc).isoformat(), error="mysqldump failed")
    r = client.get("/readyz")
    body = r.get_json()
    assert "backup" in body["warnings"] and body["checks"]["backup"]["detail"] == "mysqldump failed"


def test_backup_retention_only_touches_old_scheduled_backups(monkeypatch, tmp_path):
    import backup
    monkeypatch.setenv("BACKUP_RETENTION_DAYS", "30")
    monkeypatch.setenv("BACKUP_KEEP_MIN", "3")
    old, recent = time.time() - 40 * 86400, time.time() - 86400
    names = []
    for i, when in enumerate([old] * 5 + [recent] * 2):
        p = tmp_path / f"cityland9_auto_2020010{i}_000000.sql.gz"
        p.write_bytes(b"x")
        os.utime(p, (when, when))
        names.append(p.name)
    for other in ("cityland9_manual_20200101_000000.sql", "cityland9_before_migration_20200101_000000.sql"):
        (tmp_path / other).write_bytes(b"x")
        os.utime(tmp_path / other, (old, old))
    backup._retention(str(tmp_path), backup._logger())
    left = sorted(os.listdir(tmp_path))
    # newest 3 kept (2 recent + newest old); the other old scheduled ones deleted; others never touched
    assert left == sorted(names[4:] + ["cityland9_manual_20200101_000000.sql", "cityland9_before_migration_20200101_000000.sql"])


def test_backup_verification_rejects_truncated_dumps(tmp_path):
    import backup
    good, bad = tmp_path / "good.sql.gz", tmp_path / "bad.sql.gz"
    with gzip.open(good, "wb") as fh:
        fh.write(b"-- dump\n" + b"INSERT INTO t VALUES (1);\n" * 100 + b"-- Dump completed on 2026-10-02\n")
    with gzip.open(bad, "wb") as fh:
        fh.write(b"-- dump\n" + b"INSERT INTO t VALUES (1);\n" * 100)
    assert backup._verify(str(good)) > 1024
    with pytest.raises(RuntimeError):
        backup._verify(str(bad))


def test_restore_refuses_the_live_database(tmp_path):
    env = {**os.environ, "MYSQL_DATABASE": "cityland9"}
    dump = tmp_path / "dump.sql"
    dump.write_text("-- nothing\n")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "database", "restore.py"), str(dump), "--into", "cityland9"],
                       cwd=ROOT, env=env, capture_output=True, text=True)
    assert r.returncode != 0 and "Refusing" in r.stderr


@pytest.mark.skipif(not shutil.which("powershell"), reason="Windows PowerShell not available")
def test_register_tasks_script_parses():
    script = os.path.join(ROOT, "scripts", "windows", "register_tasks.ps1")
    command = ("$e=$null; [System.Management.Automation.Language.Parser]::ParseFile('%s',[ref]$null,[ref]$e) | Out-Null; "
               "if ($e) { $e | ForEach-Object { $_.Message }; exit 1 }" % script)
    r = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_batch_files_use_windows_line_endings():
    for name in ("START_WINDOWS.bat", "STOP_WINDOWS.bat"):
        data = open(os.path.join(ROOT, name), "rb").read()
        assert data.count(b"\n") == data.count(b"\r\n"), name
