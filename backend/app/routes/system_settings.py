"""/api/admin/system — Settings: database backup status, Excel export and Excel import.

    GET  /api/admin/system               backup status, database engine, import limits
    POST /api/admin/system/import        multipart form, field "file": import an Excel workbook. LOCKED to
                                         CITYLAND9's own export of this database: the same file is
                                         checked first (cityland_roundtrip) and refused (409) unless every
                                         row safely updates an existing record (no new records, no id
                                         pointing elsewhere (blank cells keep saved values), no change in a
                                         closed month or to an issued/hand-corrected bill)
    POST /api/admin/system/import/check  multipart form, field "file": CHECK ONLY (dry run). Nothing is
                                         written: no records, backup, audit entry or file. The report
                                         gives sheet/row/column/error codes, never workbook values.
                                         (app/services/import_check.py, CITYLAND9_IMPORT_SAFEGUARDS.md)

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
        # The lock: check this exact file first (no writes). The classic importer below overwrites by id,
        # ignores closed periods and issued bills, so it only runs when the check proves none of
        # that can happen (CITYLAND9_IMPORT_SAFEGUARDS.md).
        from ..services import import_check
        try:
            gate_report = import_check.check_workbook(upload.stream, upload.filename, legacy=legacy, config={}, roundtrip=True)
        except import_check.CheckError as exc:
            return json_error(400, str(exc))
        gate = gate_report["importGate"]
        for issue in gate_report["issues"]:   # file limits keep their plain 400 answer
            if issue["code"] == "SHEET_TOO_MANY_ROWS":
                return json_error(400, f"Sheet {issue['sheet']} has more than {legacy.IMPORT_MAX_ROWS:,} rows. "
                                       "Import it in parts (IMPORT_MAX_ROWS in .env sets the limit).")
            if issue["code"] == "SHEET_TOO_MANY_COLUMNS":
                return json_error(400, f"Sheet {issue['sheet']} has more than {legacy.IMPORT_MAX_COLUMNS} columns.")
        if not gate["allowed"]:
            reasons = "; ".join(f"{b['message']} ({b['count']})" for b in gate["blockers"][:4])
            return json_error(409, "This workbook can't be imported: nothing was changed. " + reasons
                              + ". Use Check only to see the rows.")
        upload.stream.seek(0)
        ok, message, counts = legacy.run_excel_import(upload)
        if not ok:
            return json_error(400, message)
        return jsonify({"message": message, "counts": counts or {}})

    @bp.post("/import/check")
    @permission_required("database_import")
    def check_workbook():
        from ..services import import_check
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return json_error(400, "Choose an Excel .xlsx file to check.")
        if not upload.filename.lower().endswith((".xlsx", ".xlsm")):
            return json_error(400, "Please select an Excel .xlsx file.")
        config_path = os.getenv("IMPORT_CONFIG_PATH") or os.path.join(legacy.BASE_DIR, "migration_data", "import_config.json")
        try:
            config = import_check.load_config(config_path)
        except (OSError, ValueError):
            return json_error(400, "The import configuration file (migration_data/import_config.json) can't be read.")
        try:
            report = import_check.check_workbook(upload.stream, upload.filename, legacy=legacy, config=config)
        except import_check.CheckError as exc:
            return json_error(400, str(exc))
        report["configLoaded"] = bool(config)
        response = jsonify(report)
        response.headers["Cache-Control"] = "no-store"
        return response

    return bp
