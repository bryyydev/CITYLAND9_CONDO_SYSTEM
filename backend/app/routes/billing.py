"""/api/billing — Billing & SOA: monthly bills, generation, SOA detail, payments, corrections, email.

    GET  /api/billing?month=YYYY-MM                 the month's bills (status, totals, who is billed)
    GET  /api/billing/preview?month=                what "Generate bills" would do (units, readings, already billed)
    POST /api/billing/generate {month}              create the month's missing bills
    GET  /api/billing/<id>                          one SOA: charges, payments, previous unpaid, contacts
    POST /api/billing/<id>/payments                 record a payment and issue the official receipt
    PUT  /api/billing/<id>/soa                      manual SOA correction (audited, reason required)
    POST /api/billing/<id>/recalculate              re-price from the current rates (audited)
    POST /api/billing/<id>/email                    email this SOA to the opted-in owners/tenants
    GET  /api/billing/email?month=                  who would receive the month's SOAs
    POST /api/billing/email/send {month, billIds?}  email the month's SOAs (all, or the selected bills)

Permission keys are the classic screen's (backend/app/core/permissions.py): billing (list, preview,
generate), billing_detail, mark_bill_paid, edit_soa, recalculate_soa, email_bill, billing_email,
send_billing_emails. The money rules are the classic ones, in ONE place (legacy_app):
generate_bills, record_bill_payment (row lock, one payment per form token, excess -> advance,
gap-free OR numbers), correct_soa, recalculate_bill, email_soas; closed periods are refused there.
Reading never changes a bill: amounts come from the billing engine (legacy._bill_calc). (The classic
SOA page also re-saved the due date, water, penalty and status on every view; the figures shown are
the same, computed, so nothing is written here.) Added: an SOA correction needs a reason.
"""
import re
from datetime import date
from decimal import Decimal

from flask import Blueprint, jsonify, request

from ..core.numbers import InputError, parse_amount
from ..services.soa_pdf import pdf_response
from ..services.soa import money, soa_detail, soa_row
from ..utils.auth import json_error, permission_required, protect_api_blueprint

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
# Edit SOA fields: API field -> (Billing column, label, may be negative). All are counted in the
# SOA total (other charges and the adjustment since 2026-10-04; a negative adjustment is a credit).
SOA_INPUT = {
    "condoDues": ("assessment", "Condo dues", False), "parking": ("parking_dues", "Parking", False),
    "storage": ("storage_dues", "Storage", False), "water": ("water", "Water", False),
    "other": ("other", "Other charges", False), "adjustment": ("adjustment", "Adjustment", True),
    "penalty": ("penalty", "Penalty", False), "previousBalance": ("previous_balance", "Previous balance", False),
}


def field_errors(errors, status=400):
    response = jsonify({"error": {"status": status, "message": next(iter(errors.values())), "fields": errors}})
    response.status_code = status
    return response


def make_billing_blueprint(legacy):
    db, Billing, Unit = legacy.db, legacy.Billing, legacy.Unit
    bp = protect_api_blueprint(Blueprint("api_billing", __name__, url_prefix="/api/billing"))

    def month_arg():
        month = request.args.get("month", "").strip() or date.today().strftime("%Y-%m")
        return month if MONTH_RE.match(month) else None

    def payer(unit):
        c = legacy.current_contact_for_unit(unit)
        if not c:
            return None
        return {"name": getattr(c, "tenant_name", None) or getattr(c, "owner_name", None) or "",
                "kind": "Tenant" if hasattr(c, "tenant_name") else "Owner",
                "contactNo": c.contact_no or "", "email": c.email or ""}

    def row(b):
        p = payer(b.unit)
        return {**soa_row(legacy, b), "unitId": b.unit_id, "unitNo": b.unit.unit_no, "unitType": b.unit.unit_type or "",
                "payerName": p["name"] if p else "—", "manualOverride": bool(b.soa_manual_override)}

    def detail(b):
        p = payer(b.unit)
        return {
            **soa_detail(legacy, b), "unitId": b.unit_id, "unitNo": b.unit.unit_no, "unitType": b.unit.unit_type or "",
            "payerName": p["name"] if p else "—", "contact": p, "manualOverride": bool(b.soa_manual_override),
            "overdueMonths": legacy.overdue_months_for_unit(b.unit_id, b.billing_month),
            # The stored amounts, for the Edit SOA form.
            "stored": {k: money(getattr(b, col)) for k, (col, _, _) in SOA_INPUT.items()},
            "emailRecipients": [{"name": c["name"], "email": c["email"], "type": c["type"]} for c in legacy.soa_email_contacts(b.unit)],
            "qrUrl": f"/billing/{b.id}/qr",
            "closed": bool(legacy.books_closed_through() and b.billing_month <= legacy.books_closed_through()),
            "corporation": legacy.setting("corporation_name", "CITYLAND 9 CONDOMINIUM CORPORATION"),
            "address": legacy.setting("address", ""),
            "paymentInstructions": legacy.setting("online_payment_instructions", ""),
        }

    def get_bill(bid):
        return db.session.get(Billing, bid)

    @bp.get("/<int:bid>/soa.pdf")
    @permission_required("billing")
    def soa_pdf(bid):
        """The SOA as a PDF download (same figures as GET /api/billing/<id>)."""
        b = get_bill(bid)
        if not b:
            return json_error(404, "Statement not found.")
        return pdf_response(legacy, b)

    # ---------------------------------------------------------------- list / preview / generate
    @bp.get("")
    @permission_required("billing")
    def list_bills():
        month = month_arg()
        if not month:
            return json_error(400, "month must look like 2026-10.")
        bills = Billing.query.join(Unit).filter(Billing.billing_month == month).order_by(Unit.unit_no).all()
        return jsonify({"month": month, "bills": [row(b) for b in bills],
                        "closedThrough": legacy.books_closed_through() or None})

    @bp.get("/preview")
    @permission_required("billing")
    def preview():
        month = month_arg()
        if not month:
            return json_error(400, "month must look like 2026-10.")
        residential = Unit.query.filter(Unit.active.is_(True), Unit.unit_type.notin_(("PARKING", "STORAGE"))).all()
        ids = {u.id for u in residential}
        billed = {b.unit_id for b in Billing.query.filter_by(billing_month=month).all()} & ids
        read = {r.unit_id for r in legacy.WaterReading.query.filter_by(reading_month=month).all()} & ids
        closed = legacy.books_closed_through()
        zero = [u.unit_no for u in residential if u.id not in billed and legacy.unit_dues(u) == 0 and float(u.area_sqm or 0) > 0]
        return jsonify({"month": month, "residentialUnits": len(ids), "alreadyBilled": len(billed), "toCreate": len(ids - billed),
                        "readings": len(read), "missingReadings": sorted(u.unit_no for u in residential if u.id not in read and u.id not in billed)[:50],
                        "zeroDuesUnits": sorted(zero)[:50], "dueDate": legacy.due_date_for(month).isoformat(),
                        "closed": bool(closed and month <= closed), "closedThrough": closed or None})

    @bp.post("/generate")
    @permission_required("billing")
    def generate():
        month = str((request.get_json(silent=True) or {}).get("month", "")).strip()
        created, skipped, due_date = legacy.generate_bills(month)      # InputError -> 400 with the reason
        return jsonify({"month": month, "created": created, "skipped": skipped, "dueDate": due_date.isoformat()})

    # ---------------------------------------------------------------- one bill
    @bp.get("/<int:bid>")
    @permission_required("billing_detail")
    def get_one(bid):
        b = get_bill(bid)
        if not b:
            return json_error(404, "Bill not found.")
        return jsonify({"bill": detail(b)})

    @bp.post("/<int:bid>/payments")
    @permission_required("mark_bill_paid")
    @legacy.retry_on_deadlock
    def pay(bid):
        data = request.get_json(silent=True) or {}
        errors = {}
        try:
            amount = parse_amount(data.get("amount"), label="Amount")
        except InputError as exc:
            errors["amount"] = exc.message
            amount = None
        raw_date = str(data.get("date") or "").strip()
        pay_date = None
        try:
            pay_date = date.fromisoformat(raw_date) if raw_date else date.today()
        except ValueError:
            errors["date"] = "Choose the payment date."
        token = str(data.get("formToken") or "")
        if not TOKEN_RE.match(token):
            errors["formToken"] = "This form is out of date. Reload the page and enter the payment again."
        if len(str(data.get("remarks") or "")) > 300:
            errors["remarks"] = "Keep remarks under 300 characters."
        if len(str(data.get("reference") or "")) > 120:
            errors["reference"] = "Keep the reference under 120 characters."
        if errors:
            return field_errors(errors)
        result = legacy.record_bill_payment(bid, amount=amount, payment_method=data.get("method"), payment_type=data.get("type"),
                                            reference=data.get("reference"), remarks=data.get("remarks"),
                                            payment_date=pay_date, form_token=token)
        if result is None:
            return json_error(404, "Bill not found.")
        b = result["bill"]
        return jsonify({"receiptNo": result["receipt"].receipt_no, "receiptId": result["receipt"].id,
                        "appliedToBill": result["applied"], "excessToAdvance": result["excess"],
                        "status": legacy.bill_status(b), "bill": detail(b)}), 201

    @bp.put("/<int:bid>/soa")
    @permission_required("edit_soa")
    def edit_soa(bid):
        data = request.get_json(silent=True) or {}
        errors, amounts = {}, {}
        for key, (col, label, negative) in SOA_INPUT.items():
            try:
                amounts[col] = parse_amount(data.get(key), label=label, allow_zero=True, allow_empty=True, allow_negative=negative)
            except InputError as exc:
                errors[key] = exc.message
        due = None
        raw_due = str(data.get("dueDate") or "").strip()
        if raw_due:
            try:
                due = date.fromisoformat(raw_due)
            except ValueError:
                errors["dueDate"] = "Choose a due date."
        note = str(data.get("note") or "").strip()
        if not note:
            errors["note"] = "Explain the correction (it is shown on the SOA and kept in the audit log)."
        elif len(note) > 500:
            errors["note"] = "Keep the note under 500 characters."
        if errors:
            return field_errors(errors)
        b = legacy.correct_soa(bid, amounts, due, note)
        if b is None:
            return json_error(404, "Bill not found.")
        return jsonify({"bill": detail(b)})

    @bp.post("/<int:bid>/recalculate")
    @permission_required("recalculate_soa")
    def recalculate(bid):
        try:
            result = legacy.recalculate_bill(bid)
        except legacy.SoaCorrectedByHand:
            db.session.rollback()
            return json_error(409, "This SOA was corrected by hand. Use Edit SOA to change its amounts.")
        if result is None:
            return json_error(404, "Bill not found.")
        b, changed = result
        return jsonify({"changed": changed, "bill": detail(b)})

    # ---------------------------------------------------------------- email
    def smtp_ready():
        return not legacy.outbound_email_disabled() and bool(legacy.setting("smtp_host", "").strip() and legacy.setting("smtp_sender", "").strip())

    def email_result(bills):
        if legacy.outbound_email_disabled():
            return json_error(409, "Email sending is disabled on this test installation.")
        if not smtp_ready():
            return json_error(409, "Set the SMTP host and sender email under Rates & Rules before sending SOAs.")
        sent, skipped, failed, errors = legacy.email_soas(bills)
        return jsonify({"sent": sent, "skipped": skipped, "failed": failed, "errors": errors[:5]})

    @bp.post("/<int:bid>/email")
    @permission_required("email_bill")
    def email_one(bid):
        b = get_bill(bid)
        if not b:
            return json_error(404, "Bill not found.")
        if not legacy.soa_email_contacts(b.unit):
            return json_error(409, "No owner or tenant of this unit has an email address opted in to receive SOAs. Turn it on under Units Directory.")
        return email_result([b])

    @bp.get("/email")
    @permission_required("billing_email")
    def email_overview():
        month = month_arg()
        if not month:
            return json_error(400, "month must look like 2026-10.")
        bills = Billing.query.join(Unit).filter(Billing.billing_month == month).order_by(Unit.unit_no).all()
        rows = [{"billId": b.id, "unitNo": b.unit.unit_no, "status": legacy.bill_status(b),
                 "recipients": [{"name": c["name"], "email": c["email"], "type": c["type"]} for c in legacy.soa_email_contacts(b.unit)]}
                for b in bills]
        return jsonify({"month": month, "smtpConfigured": smtp_ready(), "rows": rows,
                        "optedIn": sum(len(r["recipients"]) for r in rows)})

    @bp.post("/email/send")
    @permission_required("send_billing_emails")
    def email_send():
        data = request.get_json(silent=True) or {}
        month = str(data.get("month") or "").strip()
        if not MONTH_RE.match(month):
            return field_errors({"month": "Choose a valid billing month."})
        bills = Billing.query.join(Unit).filter(Billing.billing_month == month).order_by(Unit.unit_no).all()
        ids = data.get("billIds")
        if isinstance(ids, list):
            wanted = {int(i) for i in ids if str(i).isdigit()}
            bills = [b for b in bills if b.id in wanted]
        if not bills:
            return json_error(400, "No bills selected for that month.")
        return email_result(bills)

    return bp
