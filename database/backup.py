"""Scheduled backup of CityLand 9's database (run daily by Windows Task Scheduler).

    .venv\\Scripts\\python.exe database\\backup.py

What it does, in order (any failure -> exit code 1, status "failed", logged):
  1. checks free disk space (BACKUP_MIN_FREE_GB, default 2)
  2. mysqldump --single-transaction (consistent while people keep working), gzip-compressed:
       database/backups/cityland9_auto_<YYYYmmdd_HHMMSS>.sql.gz
     Credentials go through a temporary private options file, never the command line.
  3. verifies the dump is complete ("-- Dump completed" trailer) and not empty
  4. optionally copies it to BACKUP_COPY_DIR (USB drive / other PC / NAS share)
  5. retention: deletes ONLY earlier scheduled backups (cityland9_auto_*) older than
     BACKUP_RETENTION_DAYS (default 30), always keeping the newest BACKUP_KEEP_MIN (default 7).
     Manual and pre-migration backups are never deleted.
  6. writes database/backups/last_backup.json (read by /readyz) and logs to logs/backup.log
"""
import gzip
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "database"))
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    pass

import local_mysql as lm  # noqa: E402

PREFIX = "cityland9_auto_"
STATUS_FILE = os.path.join(lm.BACKUP_DIR, "last_backup.json")


def _logger():
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    log = logging.getLogger("cityland9.backup")
    if not log.handlers:
        handler = RotatingFileHandler(os.path.join(ROOT, "logs", "backup.log"), maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
        log.addHandler(handler)
        log.addHandler(logging.StreamHandler())
        log.setLevel(logging.INFO)
    return log


def _write_status(**data):
    os.makedirs(lm.BACKUP_DIR, exist_ok=True)
    tmp = STATUS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, STATUS_FILE)


def _verify(path):
    """The gzip must decompress and end with mysqldump's completion trailer."""
    size, tail = 0, b""
    with gzip.open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            size += len(chunk)
            tail = (tail + chunk)[-4096:]
    tail = tail.decode("utf-8", "replace")
    if size < 1024 or "-- Dump completed" not in tail:
        raise RuntimeError("the dump is incomplete (no completion trailer)")
    return size


def _retention(folder, log):
    days = int(os.getenv("BACKUP_RETENTION_DAYS", "30"))
    keep = int(os.getenv("BACKUP_KEEP_MIN", "7"))
    files = sorted((f for f in os.listdir(folder) if f.startswith(PREFIX) and f.endswith(".sql.gz")), reverse=True)
    cutoff = time.time() - days * 86400
    for name in files[keep:]:
        path = os.path.join(folder, name)
        if os.path.getmtime(path) < cutoff:
            os.remove(path)
            log.info("Retention: deleted %s", path)


def run():
    log = _logger()
    started = datetime.now(timezone.utc)
    target = None
    try:
        os.makedirs(lm.BACKUP_DIR, exist_ok=True)
        free_gb = shutil.disk_usage(lm.BACKUP_DIR).free / 1024 ** 3
        if free_gb < float(os.getenv("BACKUP_MIN_FREE_GB", "2")):
            raise RuntimeError(f"only {free_gb:.1f} GB free in {lm.BACKUP_DIR}")
        lm.start()
        target = os.path.join(lm.BACKUP_DIR, f"{PREFIX}{datetime.now():%Y%m%d_%H%M%S}.sql.gz")
        with lm.client_options() as opt, gzip.open(target + ".part", "wb", compresslevel=6) as out:
            proc = subprocess.Popen([lm.exe("mysqldump"), f"--defaults-extra-file={opt}", "--single-transaction", "--routines",
                                     "--triggers", "--hex-blob", "--default-character-set=utf8mb4", lm.DATABASE],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for chunk in iter(lambda: proc.stdout.read(1024 * 1024), b""):
                out.write(chunk)
            err = proc.stderr.read().decode("utf-8", "replace")
            if proc.wait() != 0:
                raise RuntimeError(f"mysqldump failed: {err.strip()[:300]}")
        os.replace(target + ".part", target)
        size = _verify(target)
        copied = None
        copy_dir = os.getenv("BACKUP_COPY_DIR", "").strip()
        if copy_dir:
            os.makedirs(copy_dir, exist_ok=True)
            copied = shutil.copy2(target, copy_dir)
            _retention(copy_dir, log)
        _retention(lm.BACKUP_DIR, log)
        _write_status(status="ok", started_at=started.isoformat(), finished_at=datetime.now(timezone.utc).isoformat(),
                      file=target, uncompressed_bytes=size, compressed_bytes=os.path.getsize(target), copied_to=copied, error=None)
        log.info("Backup OK: %s (%.1f MB compressed)%s", target, os.path.getsize(target) / 1024 ** 2,
                 f", copied to {copied}" if copied else "")
        return 0
    except Exception as exc:
        if target and os.path.exists(target + ".part"):
            os.remove(target + ".part")
        _write_status(status="failed", started_at=started.isoformat(), finished_at=datetime.now(timezone.utc).isoformat(),
                      file=None, error=str(exc)[:500])
        log.error("BACKUP FAILED: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(run())
