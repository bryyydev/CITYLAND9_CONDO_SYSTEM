"""Operations: rotating log files, disk-space checks, backup status.

    configure_logging(app, root)   logs/cityland9.log (rotating) + console; never logs secrets
    disk_status(paths, minimum_gb) free space per folder
    backup_status(root)            what database/backup.py last reported (database/backups/last_backup.json)
"""
import json
import logging
import os
import shutil
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging(app, root):
    """Rotating application log in <root>/logs (LOG_MAX_MB per file, LOG_BACKUPS files kept)."""
    log_dir = os.path.join(root, "logs")
    os.makedirs(log_dir, exist_ok=True)
    handler = RotatingFileHandler(os.path.join(log_dir, "cityland9.log"),
                                  maxBytes=int(os.getenv("LOG_MAX_MB", "10")) * 1024 * 1024,
                                  backupCount=int(os.getenv("LOG_BACKUPS", "10")), encoding="utf-8", delay=True)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.setLevel(logging.INFO)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(LOG_FORMAT))
    console.setLevel(logging.WARNING)
    for name in (None, "waitress", app.logger.name):
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        if not any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
            logger.addHandler(handler)
        if name is None and not any(type(h) is logging.StreamHandler for h in logger.handlers):
            logger.addHandler(console)
    app.logger.propagate = False
    return os.path.join(log_dir, "cityland9.log")


def disk_status(paths, minimum_gb):
    result = {}
    for label, path in paths.items():
        try:
            usage = shutil.disk_usage(path)
        except OSError:
            result[label] = {"ok": False, "free_gb": None}
            continue
        free_gb = round(usage.free / 1024 ** 3, 1)
        result[label] = {"ok": free_gb >= minimum_gb, "free_gb": free_gb}
    return result


def backup_status(root, max_age_hours):
    """Status written by database/backup.py, with its age. ok=False when failed, missing or too old."""
    path = os.path.join(os.getenv("BACKUP_DIR") or os.path.join(root, "database", "backups"), "last_backup.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"ok": False, "detail": "no backup has been recorded yet"}
    finished = data.get("finished_at")
    try:
        age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(finished)).total_seconds() / 3600
    except (TypeError, ValueError):
        age_h = None
    ok = data.get("status") == "ok" and age_h is not None and age_h <= max_age_hours
    return {"ok": ok, "status": data.get("status"), "age_hours": None if age_h is None else round(age_h, 1),
            "detail": data.get("error") or None}


def now_ms():
    return int(time.perf_counter() * 1000)
