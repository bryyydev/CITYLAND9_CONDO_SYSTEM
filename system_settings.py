"""/api/admin/system — Settings: database backup status, Excel export and Excel import.

    GET  /api/admin/system               backup status, database engine, import limits
    POST /api/admin/system/backup        create and verify a manual local database backup
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
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, send_file
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.engine import make_url

from ..core import ops
from ..utils.auth import json_error, permission_required, protect_api_blueprint


def make_system_settings_blueprint(legacy):
    bp = protect_api_blueprint(Blueprint("api_system_settings", __name__, url_prefix="/api/admin/system"))

    def manual_backup_available(uri):
        url = make_url(uri)
        if url.get_backend_name() == "sqlite":
            return bool(url.database and url.database != ":memory:")
        if url.get_backend_name() != "mysql":
            if url.get_backend_name() != "mssql":
                return False
            sys.path.insert(0, os.path.join(legacy.BASE_DIR, "database"))
            import sqlserver
            return sqlserver.supports_local_database(uri)
        sys.path.insert(0, os.path.join(legacy.BASE_DIR, "database"))
        import local_mysql
        return (url.host in ("127.0.0.1", "localhost", "::1")
                and (url.port or 3306) == local_mysql.PORT
                and url.database == local_mysql.DATABASE)

    def create_manual_backup():
        uri = make_url(legacy.app.config.get("SQLALCHEMY_DATABASE_URI", ""))
        backup_dir = os.getenv("BACKUP_DIR") or os.path.join(legacy.BASE_DIR, "database", "backups")
        os.makedirs(backup_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if uri.get_backend_name() == "sqlite":
            source_path = uri.database
            if not source_path or source_path == ":memory:":
                raise RuntimeError("A persistent SQLite database file is required for a manual backup.")
            if not os.path.isabs(source_path):
                source_path = os.path.abspath(source_path)
            if not os.path.isfile(source_path):
                raise RuntimeError("The configured SQLite database file does not exist.")
            filename = f"cityland9_manual_{stamp}.db"
            target_path = os.path.join(backup_dir, filename)
            partial_path = target_path + ".part"
            source = sqlite3.connect(source_path, timeout=10)
            try:
                target = sqlite3.connect(partial_path, timeout=10)
                try:
                    source.backup(target)
                    target.commit()
                    if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise RuntimeError("The SQLite backup did not pass its integrity check.")
                finally:
                    target.close()
                os.replace(partial_path, target_path)
            except (OSError, RuntimeError, sqlite3.Error):
                if os.path.exists(partial_path):
                    os.remove(partial_path)
                raise
            finally:
                source.close()
        elif uri.get_backend_name() == "mysql":
            sys.path.insert(0, os.path.join(legacy.BASE_DIR, "database"))
            import local_mysql

            if (uri.host not in ("127.0.0.1", "localhost", "::1")
                    or (uri.port or 3306) != local_mysql.PORT
                    or uri.database != local_mysql.DATABASE):
                raise RuntimeError("Manual backup is supported only for the configured local CityLand database.")
            local_mysql.start()
            target_path = local_mysql.backup(label="manual")
            filename = os.path.basename(target_path)
            size = os.path.getsize(target_path)
            with open(target_path, "rb") as dump:
                dump.seek(max(0, size - 4096))
                completed = b"-- Dump completed" in dump.read()
            if size < 1024 or not completed:
                raise RuntimeError("The MySQL backup did not pass its completeness check.")
        elif uri.get_backend_name() == "mssql":
            sys.path.insert(0, os.path.join(legacy.BASE_DIR, "database"))
            import sqlserver
            filename = f"cityland9_manual_{stamp}.bak"
            target_path = os.path.join(backup_dir, filename)
            sqlserver.backup_database(uri.render_as_string(hide_password=False), target_path)
        else:
            raise RuntimeError("Manual backup is not supported for this database engine.")

        finished = datetime.now(timezone.utc).isoformat()
        status_path = os.path.join(backup_dir, "last_backup.json")
        temporary_status = status_path + ".tmp"
        with open(temporary_status, "w", encoding="utf-8") as status_file:
            json.dump({"status": "ok", "started_at": finished, "finished_at": finished,
                       "file": target_path, "error": None}, status_file)
        os.replace(temporary_status, status_path)
        return filename

    @bp.get("")
    @permission_required("database_export")
    def overview():
        max_age = float(os.getenv("BACKUP_MAX_AGE_HOURS", "26"))
        status = ops.backup_status(legacy.BASE_DIR, max_age)
        uri = make_url(legacy.app.config.get("SQLALCHEMY_DATABASE_URI", ""))
        backend = uri.get_backend_name()
        return jsonify({
            "backup": {"ok": bool(status.get("ok")), "status": status.get("status"), "ageHours": status.get("age_hours"),
                       "detail": status.get("detail"), "maxAgeHours": max_age, "manualAllowed": manual_backup_available(uri)},
            "database": {"engine": "MySQL / MariaDB" if backend == "mysql" else "SQL Server" if backend == "mssql" else "SQLite" if backend == "sqlite" else "Other"},
            "export": {"url": "/database/export.xlsx"},
            "import": {"templateUrl": "/api/admin/system/import-template",
                       "maxUploadMb": legacy.app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024),
                       "maxUnpackedMb": legacy.IMPORT_MAX_UNCOMPRESSED_MB, "maxRowsPerSheet": legacy.IMPORT_MAX_ROWS,
                       "allowed": legacy.can_access("database_import")},
        })

    @bp.get("/import-template")
    @permission_required("database_export")
    def import_template():
        try:
            from ..services.excel_schema import build_import_template
            workbook = build_import_template()
        except ImportError:
            legacy.app.logger.exception("Excel import template generation failed")
            return json_error(500, "The Excel template could not be generated because the Excel library is unavailable.")
        return send_file(
            workbook,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name="CITYLAND9_Import_Template.xlsx",
        )

    @bp.post("/backup")
    @permission_required("database_export")
    def manual_backup():
        try:
            filename = create_manual_backup()
        except (OSError, RuntimeError, sqlite3.Error, subprocess.SubprocessError, SQLAlchemyError, ValueError, SystemExit):
            legacy.app.logger.exception("Manual database backup failed")
            return json_error(500, "The backup could not be completed. Check the server log; no success was reported.")
        return jsonify({"message": "Database backup completed and verified.", "file": filename}), 201

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
