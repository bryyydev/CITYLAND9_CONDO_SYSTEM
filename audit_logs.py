"""/api/admin/audit-logs — the audit log, read-only (there is no route that edits or deletes entries).

    GET /api/admin/audit-logs?q=&user=&from=&to=&page=&perPage=   newest first, filtered, paginated
    GET /api/admin/audit-logs/export.csv?q=&user=&from=&to=       the same filter as a CSV download

Permission key `audit_logs` (Superadmin and Accounting), the same as the classic /audit screen.
Times are stored as UTC; `from`/`to` are Philippine dates (UTC+8, no daylight saving) and the CSV
shows Philippine time. `details` is the structured before/after the application recorded; audit()
never stores passwords or secrets. CSV cells that a spreadsheet would run as a formula are prefixed
with an apostrophe.
"""
import csv
import io
import json
import re
from datetime import date, datetime, time, timedelta

from flask import Blueprint, Response, jsonify, request
from sqlalchemy import func, or_

from ..utils.auth import json_error, permission_required, protect_api_blueprint

PH_OFFSET = timedelta(hours=8)
PER_PAGE_MAX = 200
EXPORT_MAX = 50000
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _iso(value):
    return f"{value.isoformat(timespec='seconds')}Z" if value else None


def _like(text):
    escaped = text.lower().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    return f"%{escaped}%"


def _safe_cell(value):
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(FORMULA_START) else text


def make_audit_logs_blueprint(legacy):
    AuditLog = legacy.AuditLog
    bp = protect_api_blueprint(Blueprint("api_audit_logs", __name__, url_prefix="/api/admin/audit-logs"))

    def filtered():
        """(query, error) for the request's q / user / from / to."""
        query = AuditLog.query
        q = request.args.get("q", "").strip()
        user = request.args.get("user", "").strip()
        if q:
            pattern = _like(q)
            query = query.filter(or_(func.lower(AuditLog.action).like(pattern, escape="\\"),
                                     func.lower(AuditLog.username).like(pattern, escape="\\"),
                                     func.lower(func.coalesce(AuditLog.reason, "")).like(pattern, escape="\\")))
        if user:
            query = query.filter(func.lower(AuditLog.username) == user.lower())
        bounds = {}
        for name in ("from", "to"):
            raw = request.args.get(name, "").strip()
            if not raw:
                continue
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
                return None, f"'{name}' must be a date like 2026-10-04."
            try:
                bounds[name] = date.fromisoformat(raw)
            except ValueError:
                return None, f"'{name}' is not a real date."
        if "from" in bounds and "to" in bounds and bounds["from"] > bounds["to"]:
            return None, "The start date is after the end date."
        if "from" in bounds:
            query = query.filter(AuditLog.created_at >= datetime.combine(bounds["from"], time.min) - PH_OFFSET)
        if "to" in bounds:
            query = query.filter(AuditLog.created_at < datetime.combine(bounds["to"] + timedelta(days=1), time.min) - PH_OFFSET)
        return query.order_by(AuditLog.id.desc()), None

    def details_of(entry):
        if not entry.details:
            return None
        try:
            return json.loads(entry.details)
        except (TypeError, ValueError):
            return {"text": entry.details}

    def row(entry):
        return {
            "id": entry.id,
            "at": _iso(entry.created_at),
            "username": entry.username or "system",
            "action": entry.action or "",
            "entityType": entry.entity_type,
            "entityId": entry.entity_id,
            "reason": entry.reason,
            "details": details_of(entry),
        }

    @bp.get("")
    @permission_required("audit_logs")
    def list_logs():
        try:
            page = max(int(request.args.get("page", 1)), 1)
            per_page = min(max(int(request.args.get("perPage", 50)), 1), PER_PAGE_MAX)
        except ValueError:
            return json_error(400, "page and perPage must be numbers.")
        query, problem = filtered()
        if problem:
            return json_error(400, problem)
        total = query.count()
        entries = query.offset((page - 1) * per_page).limit(per_page).all()
        users = [u for (u,) in legacy.db.session.query(AuditLog.username).filter(AuditLog.username.isnot(None))
                 .distinct().order_by(AuditLog.username).limit(500).all()]
        return jsonify({"entries": [row(e) for e in entries], "total": total, "page": page, "perPage": per_page, "users": users})

    @bp.get("/export.csv")
    @permission_required("audit_logs")
    def export_csv():
        query, problem = filtered()
        if problem:
            return json_error(400, problem)
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["When (Philippine time)", "User", "Action", "Reason", "Record", "Details"])
        for e in query.limit(EXPORT_MAX).all():
            when = (e.created_at + PH_OFFSET).strftime("%Y-%m-%d %H:%M:%S") if e.created_at else ""
            record = f"{e.entity_type} #{e.entity_id}" if e.entity_type and e.entity_id else (e.entity_type or "")
            writer.writerow([_safe_cell(v) for v in (when, e.username or "system", e.action, e.reason, record, e.details)])
        legacy.audit("Exported the audit log (CSV)", entity_type="audit_log",
                     details={k: request.args.get(k) for k in ("q", "user", "from", "to") if request.args.get(k)} or None)
        stamp = (datetime.utcnow() + PH_OFFSET).strftime("%Y%m%d-%H%M")
        # UTF-8 with BOM so Excel shows ₱ and accented names correctly.
        return Response("﻿" + out.getvalue(), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="cityland9-audit-log-{stamp}.csv"',
                                 "Cache-Control": "no-store"})

    return bp
