"""/api/advances — Advance Payments (prepaid condo dues).

    GET  /api/advances?unit=&status=     advances (newest first) with applied / remaining amounts
    GET  /api/advances/options           residential units (for the form) with their monthly condo dues
    POST /api/advances                   record an advance and issue its official receipt

Permission key advance_payments (Superadmin, Admin, Accounting), the same as the classic screen.
The rules are the classic ones, in one place (legacy.record_advance_payment): split evenly per
month, applied automatically to the unit's condo dues from the start month, one payment per form
token, refused in a closed period, receipt + audit saved together. Advances are never edited here:
voiding the receipt (Payments & ORs) reverses one that hasn't been applied yet.
"""
import re
from datetime import date
from decimal import Decimal

from flask import Blueprint, jsonify, request

from ..core.numbers import InputError, parse_amount
from ..services.soa import money
from ..utils.auth import json_error, permission_required, protect_api_blueprint

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def field_errors(errors, status=400):
    response = jsonify({"error": {"status": status, "message": next(iter(errors.values())), "fields": errors}})
    response.status_code = status
    return response


def make_advances_blueprint(legacy):
    db, Adv, Unit = legacy.db, legacy.AdvancePayment, legacy.Unit
    bp = protect_api_blueprint(Blueprint("api_advances", __name__, url_prefix="/api/advances"))

    def payer(unit):
        c = legacy.current_contact_for_unit(unit)
        return (getattr(c, "tenant_name", None) or getattr(c, "owner_name", None) or "") if c else ""

    def row(a):
        remaining = Decimal("0") if a.reversed_at else max(legacy.advance_balance(a), Decimal("0"))
        amount = money(a.amount)
        receipt_no = legacy.receipt_no_for("advance", a.id) or ""
        receipt = legacy.Receipt.query.filter_by(receipt_no=receipt_no).first() if receipt_no else None
        months = max(int(a.coverage_months or 1), 1)
        return {
            "id": a.id, "unitId": a.unit_id, "unitNo": a.unit.unit_no, "payerName": payer(a.unit),
            "date": a.payment_date.isoformat() if a.payment_date else None,
            "amount": amount, "startMonth": a.start_month, "endMonth": legacy.month_shift(a.start_month, months - 1),
            "months": months, "monthlyAmount": money(a.monthly_amount),
            "method": a.payment_method, "reference": a.reference or "", "remarks": a.remarks or "",
            "applied": "0.00" if a.reversed_at else money(Decimal(amount) - remaining),
            "remaining": money(remaining), "receiptNo": receipt_no, "receiptId": receipt.id if receipt else None,
            "reversed": bool(a.reversed_at),
        }

    @bp.get("")
    @permission_required("advance_payments")
    def list_advances():
        query = Adv.query.join(Unit)
        unit = request.args.get("unit", type=int)
        if unit:
            query = query.filter(Adv.unit_id == unit)
        rows = [row(a) for a in query.order_by(Adv.payment_date.desc(), Adv.id.desc()).limit(2000).all()]
        status = request.args.get("status", "").strip()
        if status == "open":
            rows = [r for r in rows if not r["reversed"] and Decimal(r["remaining"]) > 0]
        elif status == "used":
            rows = [r for r in rows if not r["reversed"] and Decimal(r["remaining"]) <= 0]
        elif status == "reversed":
            rows = [r for r in rows if r["reversed"]]
        elif status:
            return json_error(400, "status must be open, used or reversed.")
        valid = [r for r in rows if not r["reversed"]]
        return jsonify({"advances": rows,
                        "totals": {"count": len(valid), "received": money(sum((Decimal(r["amount"]) for r in valid), Decimal("0"))),
                                   "remaining": money(sum((Decimal(r["remaining"]) for r in valid), Decimal("0")))}})

    @bp.get("/options")
    @permission_required("advance_payments")
    def options():
        units = Unit.query.filter(Unit.active.is_(True), Unit.unit_type.notin_(("PARKING", "STORAGE"))).order_by(Unit.unit_no).all()
        return jsonify({"units": [{"id": u.id, "unitNo": u.unit_no, "payerName": payer(u), "monthlyDues": money(legacy.unit_dues(u))} for u in units]})

    @bp.post("")
    @permission_required("advance_payments")
    @legacy.retry_on_deadlock
    def record():
        data = request.get_json(silent=True) or {}
        errors = {}
        try:
            amount = parse_amount(data.get("amount"), label="Amount")
        except InputError as exc:
            errors["amount"] = exc.message
            amount = None
        months = data.get("months")
        if not isinstance(months, int) or isinstance(months, bool):
            errors["months"] = "Months covered must be a whole number."
        raw_date = str(data.get("date") or "").strip()
        pay_date = date.today()
        if raw_date:
            try:
                pay_date = date.fromisoformat(raw_date)
            except ValueError:
                errors["date"] = "Choose the payment date."
        token = str(data.get("formToken") or "")
        if not TOKEN_RE.match(token):
            errors["formToken"] = "This form is out of date. Reload and try again."
        for key, limit in (("reference", 100), ("remarks", 300)):
            if len(str(data.get(key) or "")) > limit:
                errors[key] = f"Keep it under {limit} characters."
        if errors:
            return field_errors(errors)
        adv, receipt = legacy.record_advance_payment(
            unit_id=data.get("unitId"), amount=amount, start_month=str(data.get("startMonth") or ""), months=months,
            method=data.get("method"), reference=data.get("reference"), remarks=data.get("remarks"),
            payment_date=pay_date, form_token=token)
        legacy.reset_financial_caches()   # balances cached during the request predate the application
        return jsonify({"advance": row(adv), "receiptNo": receipt.receipt_no, "receiptId": receipt.id}), 201

    return bp
