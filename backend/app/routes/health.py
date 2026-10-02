"""Health checks for monitoring (no sign-in needed; no sensitive details).

    GET /healthz   liveness: the server process answers            -> 200 {"status": "ok"}
    GET /readyz    readiness: database, schema version, disk, backup -> 200 ready / 503 not ready

Readiness fails (503) when the database doesn't answer, the schema is older than the code, or
free disk space is below DISK_MIN_FREE_GB. A missing/old backup is reported as a WARNING (200),
so monitoring can alert on it without taking the system "down".
"""
import os

from flask import Blueprint, jsonify
from sqlalchemy import text

from ..core import ops


def make_health_blueprint(*, db, root, expected_revision):
    bp = Blueprint("health", __name__)

    @bp.get("/healthz")
    def healthz():
        return jsonify({"status": "ok"})

    @bp.get("/readyz")
    def readyz():
        checks, ready, warnings = {}, True, []
        start = ops.now_ms()
        try:
            db.session.execute(text("SELECT 1"))
            checks["database"] = {"ok": True, "ms": ops.now_ms() - start}
        except Exception:
            db.session.rollback()
            checks["database"] = {"ok": False}
            ready = False
        if checks["database"]["ok"] and db.engine.url.drivername.startswith("mysql") and expected_revision:
            try:
                current = db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()
            except Exception:
                db.session.rollback()
                current = None
            checks["schema"] = {"ok": current == expected_revision, "current": current, "expected": expected_revision}
            ready = ready and checks["schema"]["ok"]
        minimum = float(os.getenv("DISK_MIN_FREE_GB", "2"))
        checks["disk"] = ops.disk_status({"app": root, "backups": os.path.join(root, "database", "backups")
                                          if os.path.isdir(os.path.join(root, "database", "backups")) else root}, minimum)
        if not all(v["ok"] for v in checks["disk"].values()):
            ready = False
        checks["backup"] = ops.backup_status(root, float(os.getenv("BACKUP_MAX_AGE_HOURS", "26")))
        if not checks["backup"]["ok"]:
            warnings.append("backup")
        response = jsonify({"status": "ready" if ready else "not ready", "warnings": warnings, "checks": checks})
        response.status_code = 200 if ready else 503
        response.headers["Cache-Control"] = "no-store"
        return response

    return bp
