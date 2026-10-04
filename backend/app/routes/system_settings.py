"""/api/admin/system — Settings: database backup status, Excel export and Excel import.

    GET  /api/admin/system           backup status, database engine, import limits
    POST /api/admin/system/import    multipart form, field "file": import an Excel workbook

The Excel export stays the existing download GET /database/export.xlsx (permission database_export).
Permission keys are the classic ones (backend/app/core/permissions.py): the Settings page needs
`database_export`, the import needs `database_import` (both Superadmin only). The import is the
classic importer, now one shared function (legacy.run_excel_import): a database backup is taken
first and if it can't be taken nothing is imported; size, sheet and row limits apply; everything is
committed in one transaction or not at all; the import is recorded in the audit log.
"""
import os

from flask import Blueprint, jsonify, request

from ..core import ops
from ..utils.auth import json_error, permission_required, protect_api_blueprint


def make_system_settings_blueprint(legacy):
    bp = protect_api_blueprint(Blueprint("api_system_settings", __name__, url_prefix="/api/admin/system"))

    @bp.get("")
    @permission_required("database_export")
    def overview():
        max_age = float(os.getenv("BACKUP_MAX_AGE_HOURS", "26"))
        status = ops.backup_status(legacy.BASE_DIR, max_age)
        uri = legacy.app.config.get("SQLALCHEMY_DATABASE_URI", "")
        return jsonify({
            "backup": {"ok": bool(status.get("ok")), "status": status.get("status"), "ageHours": status.get("age_hours"),
                       "detail": status.get("detail"), "maxAgeHours": max_age},
            "database": {"engine": "MySQL / MariaDB" if uri.startswith("mysql") else "SQLite" if uri.startswith("sqlite") else "Other"},
            "export": {"url": "/database/export.xlsx"},
            "import": {"maxUploadMb": legacy.app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024),
                       "maxUnpackedMb": legacy.IMPORT_MAX_UNCOMPRESSED_MB, "maxRowsPerSheet": legacy.IMPORT_MAX_ROWS,
                       "allowed": legacy.can_access("database_import")},
        })

    @bp.post("/import")
    @permission_required("database_import")
    def import_workbook():
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return json_error(400, "Choose an Excel .xlsx file to import.")
        ok, message, counts = legacy.run_excel_import(upload)
        if not ok:
            return json_error(400, message)
        return jsonify({"message": message, "counts": counts or {}})

    return bp
