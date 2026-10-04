"""/api/water — Water Readings: monthly meter readings, corrections and water payments.

    GET  /api/water?month=YYYY-MM              the month's readings, units still to read, unpaid water (any month)
    GET  /api/water/previous?unit=&month=      the reading to start from (last month's current reading)
    POST /api/water                            save a unit's reading for a month (creates or corrects it)
    PUT  /api/water/<id>                       correct an existing reading
    POST /api/water/<id>/payments              record a water payment and issue the official receipt

Permission keys are the classic screen's (Superadmin and Admin): water, water_previous, edit_water,
mark_water_paid. The rules are the classic ones, in one place (legacy.save_water_reading,
legacy.record_water_payment): current >= previous, rate = the reading's own rate or the Rates &
Rules water rate, one reading per unit and month, an already-generated bill gets the new water
amount (refused when that month's books are closed), payments apply at most the balance, one per
form token, receipt + audit saved together.
"""
import re
from datetime import date
from decimal import Decimal

from flask import Blueprint, jsonify, request

from ..core.numbers import InputError, parse_amount, parse_decimal
from ..services.soa import money
from ..utils.auth import json_error, permission_required, protect_api_blueprint

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def field_errors(errors, status=400):
    response = jsonify({"error": {"status": status, "message": next(iter(errors.values())), "fields": errors}})
    response.status_code = status
    return response


def rate_text(value):
    """A rate as typed: 50, 52.5 (str(Decimal(50).normalize()) would give "5E+1")."""
    return format(Decimal(str(value or 0)).normalize(), "f")


def num(value):
    """A reading as a plain number for JSON (3 decimals at most, no float noise)."""
    return float(Decimal(str(value or 0)).quantize(Decimal("0.001")).normalize())


def make_water_blueprint(legacy):
    db, W, Unit, Billing = legacy.db, legacy.WaterReading, legacy.Unit, legacy.Billing
    bp = protect_api_blueprint(Blueprint("api_water", __name__, url_prefix="/api/water"))

    def payer(unit):
        c = legacy.current_contact_for_unit(unit)
        return (getattr(c, "tenant_name", None) or getattr(c, "owner_name", None) or "") if c else ""

    def row(r):
        total = money(r.bill_amount)
        paid = money(r.paid_amount)
        balance = max(Decimal(total) - Decimal(paid), Decimal("0"))
        bill = Billing.query.filter_by(unit_id=r.unit_id, billing_month=r.reading_month).first()
        return {
            "id": r.id, "unitId": r.unit_id, "unitNo": r.unit.unit_no, "payerName": payer(r.unit), "month": r.reading_month,
            "previous": num(r.previous_reading), "current": num(r.current_reading), "usage": num(r.usage),
            "rate": rate_text(r.rate), "amount": total, "paidAmount": paid, "balance": money(balance),
            "status": "Paid" if balance <= 0 and Decimal(total) > 0 else "Partially Paid" if Decimal(paid) > 0 else "Unpaid",
            "readingDate": r.reading_date.isoformat() if r.reading_date else None,
            "paidDate": r.paid_date.isoformat() if r.paid_date else None,
            "billId": bill.id if bill else None,
            "receipts": [legacy.receipt_no_for("water", r.id)] if legacy.receipt_no_for("water", r.id) else [],
        }

    def residential():
        return Unit.query.filter(Unit.active.is_(True), Unit.unit_type.notin_(("PARKING", "STORAGE"))).order_by(Unit.unit_no).all()

    @bp.get("")
    @permission_required("water")
    def month_view():
        month = request.args.get("month", "").strip() or date.today().strftime("%Y-%m")
        if not MONTH_RE.match(month):
            return json_error(400, "month must look like 2026-10.")
        readings = W.query.filter_by(reading_month=month).join(Unit).order_by(Unit.unit_no).all()
        done = {r.unit_id for r in readings}
        missing = []
        for u in residential():
            if u.id in done:
                continue
            prev = legacy.previous_water_reading(u.id, month)
            missing.append({"unitId": u.id, "unitNo": u.unit_no, "payerName": payer(u),
                            "previous": num(prev.current_reading) if prev else 0, "previousMonth": prev.reading_month if prev else None})
        unpaid = [row(r) for r in W.query.join(Unit).order_by(W.reading_month.desc(), Unit.unit_no).limit(2000).all()
                  if Decimal(money(r.bill_amount)) - Decimal(money(r.paid_amount)) > 0]
        rows = [row(r) for r in readings]
        return jsonify({
            "month": month, "readings": rows, "missing": missing, "unpaid": unpaid[:500],
            "defaultRate": rate_text(legacy.setting_float("water_rate", 50)),
            "autoCompute": legacy.setting("water_auto_compute", "1") == "1",
            "totals": {"read": len(rows), "toRead": len(missing), "usage": num(sum(r["usage"] for r in rows)),
                       "amount": money(sum((Decimal(r["amount"]) for r in rows), Decimal("0"))),
                       "unpaid": money(sum((Decimal(r["balance"]) for r in unpaid), Decimal("0")))},
        })

    @bp.get("/previous")
    @permission_required("water_previous")
    def previous():
        unit = request.args.get("unit", type=int)
        month = request.args.get("month", "").strip()
        if not unit or not MONTH_RE.match(month):
            return json_error(400, "unit and month are required.")
        prev = legacy.previous_water_reading(unit, month)
        return jsonify({"previous": num(prev.current_reading) if prev else 0, "found": bool(prev),
                        "sourceMonth": prev.reading_month if prev else None})

    def reading_values(data, errors):
        def dec(key, label, places, required, maximum):
            try:
                return parse_decimal(data.get(key), label=label, places=places, minimum=Decimal("0"), maximum=Decimal(maximum),
                                     allow_empty=not required, default=None)
            except InputError as exc:
                errors[key] = exc.message
                return None
        current = dec("current", "Current reading", 3, True, "99999999")
        previous = dec("previous", "Previous reading", 3, False, "99999999")
        rate = dec("rate", "Water rate", 4, False, "100000")
        raw_date = str(data.get("readingDate") or "").strip()
        reading_date = None
        if raw_date:
            try:
                reading_date = date.fromisoformat(raw_date)
            except ValueError:
                errors["readingDate"] = "Choose the reading date."
        return previous, current, rate, reading_date

    def save(unit_id, month, data):
        errors = {}
        previous, current, rate, reading_date = reading_values(data, errors)
        if errors:
            return field_errors(errors)
        if previous is None:
            prev = legacy.previous_water_reading(unit_id, month) if unit_id else None
            previous = Decimal(str(prev.current_reading)) if prev else Decimal("0")
        reading, created, bill_updated = legacy.save_water_reading(unit_id=unit_id, month=month, previous=previous, current=current,
                                                                   rate=rate, reading_date=reading_date)
        return jsonify({"reading": row(reading), "created": created, "billUpdated": bill_updated}), (201 if created else 200)

    @bp.post("")
    @permission_required("water")
    def create_or_correct():
        data = request.get_json(silent=True) or {}
        unit_id = data.get("unitId") if isinstance(data.get("unitId"), int) else None
        return save(unit_id, str(data.get("month") or ""), data)

    @bp.put("/<int:rid>")
    @permission_required("edit_water")
    def correct(rid):
        r = db.session.get(W, rid)
        if not r:
            return json_error(404, "Water reading not found.")
        return save(r.unit_id, r.reading_month, request.get_json(silent=True) or {})

    @bp.post("/<int:rid>/payments")
    @permission_required("mark_water_paid")
    @legacy.retry_on_deadlock
    def pay(rid):
        data = request.get_json(silent=True) or {}
        errors = {}
        try:
            amount = parse_amount(data.get("amount"), label="Amount")
        except InputError as exc:
            errors["amount"] = exc.message
            amount = None
        raw_date = str(data.get("date") or "").strip()
        paid_date = date.today()
        if raw_date:
            try:
                paid_date = date.fromisoformat(raw_date)
            except ValueError:
                errors["date"] = "Choose the payment date."
        token = str(data.get("formToken") or "")
        if not TOKEN_RE.match(token):
            errors["formToken"] = "This form is out of date. Reload and try again."
        if errors:
            return field_errors(errors)
        result = legacy.record_water_payment(rid, amount=amount, payment_method=data.get("method"), payment_type=data.get("type"),
                                             reference=data.get("reference"), paid_date=paid_date, form_token=token)
        if result is None:
            return json_error(404, "Water reading not found.")
        reading, receipt, applied = result
        return jsonify({"reading": row(reading), "receiptNo": receipt.receipt_no, "receiptId": receipt.id, "applied": applied}), 201

    return bp
