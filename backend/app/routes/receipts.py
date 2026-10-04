"""/api/receipts — Payments & Official Receipts: the receipts ledger, one receipt, and voiding.

    GET  /api/receipts?from=&to=&q=&method=&status=   receipts in a date range (newest number first) + totals
    GET  /api/receipts/<id>                           one printable receipt (+ void details, whether it can be voided)
    POST /api/receipts/<id>/void {reason, formToken}  void it and reverse what it paid

Permission keys are the classic screen's (backend/app/core/permissions.py): receipts (ledger),
receipt_detail, void_receipt (Superadmin and Accounting). Receipts are issued only when a payment is
recorded (Billing, Water, Advance Payments) and are never edited: a void keeps the number, marks the
receipt VOID with the reason and reverses its payments (legacy.void_receipt_record: row locks, one
void per form token, refused in a closed period or when its advance was already applied, audited).
Totals count only receipts that are not void; voided receipts stay in the list so numbers have no gaps.
"""
import re
from datetime import date, timedelta
from decimal import Decimal

from flask import Blueprint, jsonify, request
from sqlalchemy import func, or_

from ..services import receipts as receipt_json
from ..services.soa import money
from ..utils.auth import json_error, permission_required, protect_api_blueprint, signed_in_user

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
LIST_MAX = 1000
METHODS = ("CASH", "CHECK", "ONLINE")


def make_receipts_blueprint(legacy):
    db, Receipt, Unit = legacy.db, legacy.Receipt, legacy.Unit
    bp = protect_api_blueprint(Blueprint("api_receipts", __name__, url_prefix="/api/receipts"))

    def a_date(name, default):
        raw = request.args.get(name, "").strip()
        if not raw:
            return default
        return date.fromisoformat(raw)       # ValueError -> 400 below

    def row(r):
        return {**receipt_json.receipt_row(r), "unitId": r.unit_id, "unitNo": r.unit.unit_no,
                "receivedBy": r.received_by or "", "backfilled": r.source == "backfill",
                "voidedBy": r.voided_by or "", "voidedAt": f"{r.voided_at.isoformat(timespec='seconds')}Z" if r.voided_at else None}

    @bp.get("")
    @permission_required("receipts")
    def ledger():
        today = date.today()
        try:
            start = a_date("from", today.replace(day=1))
            end = a_date("to", today)
        except ValueError:
            return json_error(400, "from and to must be dates like 2026-10-04.")
        if end < start:
            start, end = end, start
        if (end - start) > timedelta(days=3700):
            return json_error(400, "Choose a range of at most 10 years.")
        query = Receipt.query.join(Unit).filter(Receipt.received_date >= start, Receipt.received_date <= end)
        q = request.args.get("q", "").strip()
        if q:
            like = f"%{q}%"
            query = query.filter(or_(Receipt.receipt_no.ilike(like), Unit.unit_no.ilike(like), Receipt.reference.ilike(like)))
        method = request.args.get("method", "").strip().upper()
        if method:
            if method not in METHODS:
                return json_error(400, "method must be CASH, CHECK or ONLINE.")
            query = query.filter(Receipt.payment_method == method)
        status = request.args.get("status", "").strip()
        if status == "valid":
            query = query.filter(Receipt.voided_at.is_(None))
        elif status == "void":
            query = query.filter(Receipt.voided_at.isnot(None))
        elif status:
            return json_error(400, "status must be valid or void.")

        # Totals over the whole range (not just the rows sent), counting only receipts that are not void.
        valid = query.filter(Receipt.voided_at.is_(None))
        by_method = {m: money(v) for m, v in valid.with_entities(Receipt.payment_method, func.coalesce(func.sum(Receipt.amount), 0))
                     .group_by(Receipt.payment_method).all()}
        count = query.count()
        rows = query.order_by(Receipt.receipt_year.desc(), Receipt.receipt_seq.desc()).limit(LIST_MAX).all()
        return jsonify({
            "from": start.isoformat(), "to": end.isoformat(),
            "receipts": [row(r) for r in rows],
            "total": count, "truncated": count > LIST_MAX,
            "collected": money(sum((Decimal(v) for v in by_method.values()), Decimal("0"))),
            "byMethod": {m: by_method.get(m, "0.00") for m in METHODS},
            "voidCount": query.filter(Receipt.voided_at.isnot(None)).count(),
        })

    def detail(r):
        closed = legacy.books_closed_through()
        return {**receipt_json.receipt_detail(legacy, r),
                "voidedBy": r.voided_by or "", "voidedAt": f"{r.voided_at.isoformat(timespec='seconds')}Z" if r.voided_at else None,
                "closed": bool(closed and r.received_date.strftime("%Y-%m") <= closed),
                "canVoid": legacy.can_access("void_receipt", signed_in_user()) and not r.voided_at}

    @bp.get("/<int:rid>")
    @permission_required("receipt_detail")
    def get_one(rid):
        r = db.session.get(Receipt, rid)
        if not r:
            return json_error(404, "Receipt not found.")
        return jsonify(detail(r))

    @bp.post("/<int:rid>/void")
    @permission_required("void_receipt")
    @legacy.retry_on_deadlock
    def void(rid):
        data = request.get_json(silent=True) or {}
        token = str(data.get("formToken") or "")
        if not TOKEN_RE.match(token):
            response = jsonify({"error": {"status": 400, "message": "This form is out of date. Reload and try again.",
                                          "fields": {"formToken": "This form is out of date. Reload and try again."}}})
            response.status_code = 400
            return response
        try:
            r = legacy.void_receipt_record(rid, data.get("reason"), token)
        except legacy.VoidRefused as exc:
            db.session.rollback()
            return json_error(409, str(exc))
        if r is None:
            return json_error(404, "Receipt not found.")
        return jsonify(detail(r))

    return bp
