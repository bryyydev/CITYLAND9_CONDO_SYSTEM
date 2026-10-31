"""/api/billing — Billing & SOA: monthly bills, generation, SOA detail, payments, corrections, email.

    GET  /api/billing?month=YYYY-MM                 the month's bills (status, totals, who is billed)
    GET  /api/billing/print-statements?month=YYYY-MM the month's SOA details for one-click printing
    GET  /api/billing/month.pdf?month=YYYY-MM       all the month's SOAs in one printable PDF
    GET  /api/billing/preview?month=                what "Generate bills" would do (units, readings, already billed)
    POST /api/billing/generate {month}              create the month's missing bills
    POST /api/billing/delete-generated {month, billIds?}  Super Admin-only guarded SOA deletion
    GET  /api/billing/<id>                          one SOA: charges, payments, previous unpaid, contacts
    GET  /api/billing/<id>/party-balance            current balance for the payer selected on a new payment
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

from flask import Blueprint, g, jsonify, request
from sqlalchemy import or_, true
from sqlalchemy.orm import selectinload

from ..core.numbers import InputError, parse_amount
from ..services.soa_pdf import pdf_response, build_combined_soa_pdf
from ..services.soa import money, soa_contact_for_unit, soa_detail, soa_row
from ..utils.auth import json_error, permission_required, protect_api_blueprint, role_required

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
        c = soa_contact_for_unit(legacy, unit)
        if not c:
            return None
        return {"name": getattr(c, "tenant_name", None) or getattr(c, "owner_name", None) or "",
                "kind": "Tenant" if hasattr(c, "tenant_name") else "Owner",
                "contactNo": c.contact_no or "", "email": c.email or ""}

    def row(b):
        p = payer(b.unit)
        return {**soa_row(legacy, b), "unitId": b.unit_id, "unitNo": b.unit.unit_no, "unitType": b.unit.unit_type or "",
                "payerName": p["name"] if p else "—", "manualOverride": bool(b.soa_manual_override),
                "generated": getattr(b, "issued_at", None) is not None,
                "partyLedgerEnabled": legacy.party_ledger_enabled(b.billing_month),
                "payerRequired": (legacy.party_ledger_enabled(b.billing_month)
                                  or legacy.party_ledger_enabled(date.today().isoformat()[:7]))}

    def detail(b, payer_type=None):
        p = payer(b.unit)
        soa = soa_detail(legacy, b, payer_type=payer_type)
        return {
            **soa, "unitId": b.unit_id, "unitNo": b.unit.unit_no, "unitType": b.unit.unit_type or "",
            "payerName": soa["account"]["billTo"] if payer_type else p["name"] if p else "—",
            "contact": p, "manualOverride": bool(b.soa_manual_override),
            "partyLedgerEnabled": legacy.party_ledger_enabled(b.billing_month),
            "payerRequired": (legacy.party_ledger_enabled(b.billing_month)
                              or legacy.party_ledger_enabled(date.today().isoformat()[:7])),
            "overdueMonths": legacy.overdue_months_for_unit(b.unit_id, b.billing_month),
            # The stored amounts, for the Edit SOA form.
            "stored": {k: money(getattr(b, col)) for k, (col, _, _) in SOA_INPUT.items()},
            "emailRecipients": [{"name": c["name"], "email": c["email"], "type": c["type"]} for c in legacy.soa_email_contacts(b.unit)],
            "qrUrl": f"/billing/{b.id}/qr",
            "closed": bool(legacy.books_closed_through() and b.billing_month <= legacy.books_closed_through()),
            "corporation": legacy.setting("corporation_name", "CITYLAND 9 CONDOMINIUM CORPORATION"),
            "address": legacy.setting("address", ""),
            "paymentInstructions": legacy.setting("online_payment_instructions", ""),
            "otherPayerType": b.other_charge_payer or b.unit.other_charge_payer or "Owner",
        }

    def get_bill(bid):
        return db.session.get(Billing, bid)

    def bills_for_month(month, *, include_payments=False):
        query = Billing.query.join(Unit).options(
            selectinload(Billing.unit).selectinload(Unit.owners),
            selectinload(Billing.unit).selectinload(Unit.tenants),
        )
        if include_payments:
            query = query.options(selectinload(Billing.payments))
        return query.filter(Billing.billing_month == month).order_by(Unit.unit_no).all()

    @bp.get("/<int:bid>/soa.pdf")
    @permission_required("billing")
    def soa_pdf(bid):
        """The SOA as a PDF download (same figures as GET /api/billing/<id>)."""
        b = get_bill(bid)
        if not b:
            return json_error(404, "Statement not found.")
        payer_type = request.args.get("payer")
        if payer_type not in (None, "Owner", "Tenant"):
            return json_error(400, "payer must be Owner or Tenant.")
        return pdf_response(legacy, b, inline=request.args.get("inline") == "1", payer_type=payer_type)

    @bp.get("/month.pdf")
    @permission_required("billing")
    def month_soa_pdf():
        month = month_arg()
        if not month:
            return json_error(400, "month must look like 2026-10.")
        bills = bills_for_month(month)
        if not bills:
            return json_error(404, f"No statements are available for {month}.")
        try:
            statements = []
            for bill in bills:
                if legacy.party_ledger_enabled(month):
                    if not legacy.party_statement_ready(bill.unit_id, month):
                        return json_error(409, f"Separate SOAs cannot be issued for unit {bill.unit.unit_no} until October payer assignments are resolved.")
                    statements.extend(soa_detail(legacy, bill, payer_type=kind) for kind in ("Owner", "Tenant"))
                else:
                    statements.append(soa_detail(legacy, bill))
            data = build_combined_soa_pdf(statements)
        except Exception:
            from flask import current_app
            current_app.logger.exception("Combined SOA PDF generation failed for %s", month)
            return json_error(500, "The SOA PDF could not be generated. Nothing was changed; please try again or contact the administrator.")
        from flask import make_response
        response = make_response(data)
        response.headers["Content-Type"] = "application/pdf"
        disposition = "inline" if request.args.get("inline") == "1" else "attachment"
        response.headers["Content-Disposition"] = f'{disposition}; filename="SOA_{month}_all.pdf"'
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @bp.get("/print-statements")
    @permission_required("billing_detail")
    def print_statements():
        month = month_arg()
        if not month:
            return json_error(400, "month must look like 2026-10.")
        payer_type = request.args.get("payer")
        if payer_type not in (None, "Owner", "Tenant"):
            return json_error(400, "payer must be Owner or Tenant.")
        if payer_type and not legacy.party_ledger_enabled(month):
            return json_error(409, "Separate Owner/Tenant statements are not available for this billing month.")
        bills = bills_for_month(month, include_payments=True)
        if bills:
            legacy.prime_setting_cache()
            legacy._prepare_bill_calculation_cache({bill.unit_id for bill in bills})
        statements = []
        try:
            for bill in bills:
                if legacy.party_ledger_enabled(month):
                    if not legacy.party_statement_ready(bill.unit_id, month):
                        return json_error(409, f"Separate SOAs cannot be issued for unit {bill.unit.unit_no} until payer assignments are resolved.")
                    if payer_type:
                        statements.append(detail(bill, payer_type=payer_type))
                    else:
                        statements.extend(detail(bill, payer_type=kind) for kind in ("Owner", "Tenant"))
                else:
                    statements.append(detail(bill))
        except ValueError as exc:
            return json_error(409, str(exc))
        return jsonify({"statements": statements})

    # ---------------------------------------------------------------- list / preview / generate
    @bp.get("")
    @permission_required("billing")
    def list_bills():
        month = month_arg()
        if not month:
            return json_error(400, "month must look like 2026-10.")
        bills = bills_for_month(month)
        if bills:
            legacy.prime_setting_cache()
            legacy._prepare_bill_calculation_cache({bill.unit_id for bill in bills})
        return jsonify({"month": month, "bills": [row(b) for b in bills],
                        "closedThrough": legacy.books_closed_through() or None})

    @bp.get("/preview")
    @permission_required("billing")
    def preview():
        month = month_arg()
        if not month:
            return json_error(400, "month must look like 2026-10.")
        residential = Unit.query.filter(Unit.active == true(), Unit.unit_type.notin_(("PARKING", "STORAGE"))).all()
        ids = {u.id for u in residential}
        assets = Unit.query.filter(Unit.active == true(), Unit.unit_type.in_(("PARKING", "STORAGE"))).all()
        asset_ids = {u.id for u in assets}
        month_bills = Billing.query.filter_by(billing_month=month).all()
        month_billed = {b.unit_id for b in month_bills}
        billed = month_billed & ids
        assets_billed = month_billed & asset_ids
        deferred_asset_ids = (legacy.assets_with_legacy_residential_charges(month, bills=month_bills) & asset_ids) - assets_billed
        deferred_assets = sorted(u.unit_no for u in assets if u.id in deferred_asset_ids)
        water_rows = legacy.WaterReading.query.filter(
            legacy.WaterReading.unit_id.in_(ids), legacy.WaterReading.reading_month < month,
        ).order_by(legacy.WaterReading.unit_id.asc(), legacy.WaterReading.reading_month.desc(), legacy.WaterReading.id.desc()).all() if ids else []
        read = {}
        for water_reading in water_rows:
            read.setdefault(water_reading.unit_id, water_reading.reading_month)
        closed = legacy.books_closed_through()
        zero = [u.unit_no for u in residential if u.id not in billed and legacy.unit_dues(u) == 0 and float(u.area_sqm or 0) > 0]
        return jsonify({"month": month, "residentialUnits": len(ids), "alreadyBilled": len(billed), "toCreate": len(ids - billed),
                        "assetUnits": len(asset_ids), "assetsAlreadyBilled": len(assets_billed),
                        "assetsDeferred": deferred_assets, "assetsToCreate": len(asset_ids - assets_billed - deferred_asset_ids),
                        "readings": len(read), "missingReadings": sorted(u.unit_no for u in residential if u.id not in read and u.id not in billed)[:50],
                        "zeroDuesUnits": sorted(zero)[:50], "dueDate": legacy.due_date_for(month).isoformat(),
                        "closed": bool(closed and month <= closed), "closedThrough": closed or None})

    @bp.post("/generate")
    @permission_required("billing")
    def generate():
        month = str((request.get_json(silent=True) or {}).get("month", "")).strip()
        created, skipped, due_date = legacy.generate_bills(month)      # InputError -> 400 with the reason
        return jsonify({"month": month, "created": created, "skipped": skipped, "dueDate": due_date.isoformat(),
                        "deferredAssets": getattr(g, "_assets_deferred_by_legacy_soa", [])})

    @bp.post("/delete-generated")
    @role_required(["super_admin"])
    def delete_generated():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return json_error(400, "A billing month is required.")
        month = str(data.get("month") or "").strip()
        if not MONTH_RE.fullmatch(month):
            return json_error(400, "month must look like 2026-10.")

        raw_ids = data.get("billIds")
        if raw_ids is None:
            bill_ids = None
        elif (not isinstance(raw_ids, list) or not raw_ids
              or any(type(bill_id) is not int or bill_id <= 0 for bill_id in raw_ids)
              or len(set(raw_ids)) != len(raw_ids)):
            return json_error(400, "billIds must be a non-empty list of unique positive bill IDs.")
        else:
            bill_ids = raw_ids

        closed_through = legacy.books_closed_through()
        if closed_through and month <= closed_through:
            return json_error(409, f"SOAs for {month} cannot be deleted because the books are closed through {closed_through}.")

        query = Billing.query.join(Unit).filter(Billing.billing_month == month)
        if bill_ids is not None:
            query = query.filter(Billing.id.in_(bill_ids))
        bills = [bill for bill in query.order_by(Billing.id).with_for_update().all()
                 if getattr(bill, "issued_at", None) is not None]
        if bill_ids is not None and len(bills) != len(bill_ids):
            return json_error(404, "One or more selected SOAs are not system-generated or do not exist in the selected billing month.")
        if not bills:
            return json_error(404, f"No SOAs exist for {month}.")

        bill_id_set = [bill.id for bill in bills]
        payments = (legacy.Payment.query.filter(legacy.Payment.billing_id.in_(bill_id_set))
                    .order_by(legacy.Payment.billing_id).all())
        advance_applications = (legacy.AdvanceApplication.query.filter(
            legacy.AdvanceApplication.billing_id.in_(bill_id_set))
            .order_by(legacy.AdvanceApplication.billing_id).all())
        payment_ids = [payment.id for payment in payments]
        allocated_payment_ids = set()
        if payment_ids:
            allocated_payment_ids = {
                allocation.payment_id
                for allocation in legacy.ReceiptAllocation.query.filter(
                    legacy.ReceiptAllocation.kind == "bill",
                    legacy.ReceiptAllocation.payment_id.in_(payment_ids),
                ).all()
            }
        payments_by_bill = {}
        for payment in payments:
            payments_by_bill.setdefault(payment.billing_id, []).append(payment)
        advances_by_bill = {}
        for allocation in advance_applications:
            advances_by_bill.setdefault(allocation.billing_id, []).append(allocation)

        blocked = []
        for bill in bills:
            reasons = []
            if legacy.money(bill.amount_paid) > 0 or legacy.bill_status(bill) == "Paid":
                reasons.append("not unpaid")
            if payments_by_bill.get(bill.id):
                reasons.append("has payment records")
            if any(payment.id in allocated_payment_ids for payment in payments_by_bill.get(bill.id, [])):
                reasons.append("has receipt allocations")
            if advances_by_bill.get(bill.id):
                reasons.append("has advance-payment allocations")
            if reasons:
                blocked.append({"billId": bill.id, "unitNo": bill.unit.unit_no, "reasons": reasons})
        if blocked:
            return jsonify({"error": {"status": 409,
                                      "message": "Nothing was deleted. Every selected SOA must be unpaid and have no payment or advance allocations.",
                                      "blocked": blocked}}), 409

        removed = [{"id": bill.id, "unitNo": bill.unit.unit_no, "month": bill.billing_month} for bill in bills]
        legacy.audit(
            f"Deleted {len(removed)} generated SOA(s) for {month}",
            entity_type="billing_batch",
            details={"month": month, "bills": removed},
            commit=False,
        )
        for bill in bills:
            db.session.delete(bill)
        db.session.commit()
        return jsonify({"month": month, "deleted": len(removed), "billIds": [item["id"] for item in removed]})

    # ---------------------------------------------------------------- one bill
    @bp.get("/<int:bid>")
    @permission_required("billing_detail")
    def get_one(bid):
        b = get_bill(bid)
        if not b:
            return json_error(404, "Bill not found.")
        payer_type = request.args.get("payer")
        if payer_type not in (None, "Owner", "Tenant"):
            return json_error(400, "payer must be Owner or Tenant.")
        try:
            return jsonify({"bill": detail(b, payer_type=payer_type)})
        except ValueError as exc:
            return json_error(409, str(exc))

    @bp.get("/<int:bid>/party-transactions")
    @permission_required("billing_detail")
    def party_transactions(bid):
        bill = get_bill(bid)
        if not bill:
            return json_error(404, "Bill not found.")
        first_date = date.fromisoformat(f"{legacy.party_ledger_cutover_month()}-01")
        payments = legacy.Payment.query.join(Billing).filter(
            Billing.unit_id == bill.unit_id,
            or_(legacy.Payment.payment_date >= first_date, Billing.billing_month >= legacy.party_ledger_cutover_month()),
            legacy.Payment.reversed_at.is_(None),
        ).order_by(legacy.Payment.payment_date, legacy.Payment.id).all()
        advances = legacy.AdvancePayment.query.filter(
            legacy.AdvancePayment.unit_id == bill.unit_id,
            or_(legacy.AdvancePayment.payment_date >= first_date,
                legacy.AdvancePayment.start_month >= legacy.party_ledger_cutover_month()),
            legacy.AdvancePayment.reversed_at.is_(None),
        ).order_by(legacy.AdvancePayment.payment_date, legacy.AdvancePayment.id).all()
        water_allocations = legacy.ReceiptAllocation.query.join(
            legacy.Receipt, legacy.ReceiptAllocation.receipt_id == legacy.Receipt.id,
        ).join(
            legacy.WaterReading, legacy.ReceiptAllocation.water_reading_id == legacy.WaterReading.id,
        ).filter(
            legacy.WaterReading.unit_id == bill.unit_id,
            or_(legacy.Receipt.received_date >= first_date,
                legacy.WaterReading.reading_month >= legacy.party_ledger_cutover_month()),
            legacy.ReceiptAllocation.kind == "water",
            legacy.Receipt.voided_at.is_(None),
        ).order_by(legacy.Receipt.received_date, legacy.ReceiptAllocation.id).all()
        transactions = [
            {"kind": "payment", "id": payment.id, "date": payment.payment_date.isoformat(),
             "month": payment.billing.billing_month, "amount": money(payment.amount),
             "payerType": payment.payer_type, "receiptNo": legacy.receipt_no_for("bill", payment.id)}
            for payment in payments
        ] + [
            {"kind": "advance", "id": advance.id, "date": advance.payment_date.isoformat(),
             "month": advance.start_month, "amount": money(advance.amount),
             "payerType": advance.payer_type, "receiptNo": legacy.receipt_no_for("advance", advance.id)}
            for advance in advances
        ] + [
            {"kind": "water", "id": allocation.id, "date": allocation.receipt.received_date.isoformat(),
             "month": allocation.water_reading.reading_month, "amount": money(allocation.amount),
             "payerType": allocation.payer_type, "receiptNo": allocation.receipt.receipt_no}
            for allocation in water_allocations
        ]
        transactions.sort(key=lambda item: (item["date"], item["kind"], item["id"]))
        return jsonify({"transactions": transactions,
                        "ready": legacy.party_statement_ready(bill.unit_id, bill.billing_month)})

    @bp.put("/<int:bid>/party-transactions/<kind>/<int:transaction_id>")
    @permission_required("edit_soa")
    def assign_party_transaction(bid, kind, transaction_id):
        bill = get_bill(bid)
        if not bill:
            return json_error(404, "Bill not found.")
        data = request.get_json(silent=True) or {}
        payer_type = data.get("payerType")
        if payer_type not in ("Owner", "Tenant"):
            return field_errors({"payerType": "Choose Owner or Tenant."})
        if kind == "payment":
            transaction = legacy.lock_row(legacy.Payment, transaction_id)
            if not transaction or transaction.billing.unit_id != bill.unit_id:
                return json_error(404, "Payment not found for this unit.")
            transaction_date = transaction.payment_date
            reversed_transaction = transaction.reversed_at
            split_transaction = (transaction_date >= date.fromisoformat(f"{legacy.party_ledger_cutover_month()}-01")
                                 or transaction.billing.billing_month >= legacy.party_ledger_cutover_month())
        elif kind == "advance":
            transaction = legacy.lock_row(legacy.AdvancePayment, transaction_id)
            if not transaction or transaction.unit_id != bill.unit_id:
                return json_error(404, "Advance payment not found for this unit.")
            transaction_date = transaction.payment_date
            reversed_transaction = transaction.reversed_at
            split_transaction = (transaction_date >= date.fromisoformat(f"{legacy.party_ledger_cutover_month()}-01")
                                 or transaction.start_month >= legacy.party_ledger_cutover_month())
        elif kind == "water":
            transaction = legacy.lock_row(legacy.ReceiptAllocation, transaction_id)
            if (not transaction or transaction.kind != "water" or
                    transaction.receipt.unit_id != bill.unit_id):
                return json_error(404, "Water payment not found for this unit.")
            transaction_date = transaction.receipt.received_date
            reversed_transaction = transaction.receipt.voided_at
            split_transaction = (transaction_date >= date.fromisoformat(f"{legacy.party_ledger_cutover_month()}-01")
                                 or transaction.water_reading.reading_month >= legacy.party_ledger_cutover_month())
            assigned_payer = transaction.water_reading.charge_payer
            if assigned_payer in ("Owner", "Tenant") and assigned_payer != payer_type:
                return field_errors({"payerType": f"This water charge is assigned to the {assigned_payer}."})
        else:
            return json_error(404, "Transaction type not found.")
        if reversed_transaction:
            return json_error(409, "A reversed transaction cannot be assigned to a party ledger.")
        if not split_transaction:
            return json_error(409, "Transactions before the party-ledger cutover do not need payer assignment.")
        legacy.check_open_period(transaction_date, "Payer assignment")
        previous = transaction.payer_type
        transaction.payer_type = payer_type
        if kind == "payment":
            for allocation in legacy.ReceiptAllocation.query.filter_by(payment_id=transaction.id).all():
                allocation.payer_type = payer_type
        elif kind == "advance":
            for allocation in legacy.ReceiptAllocation.query.filter_by(advance_payment_id=transaction.id).all():
                allocation.payer_type = payer_type
        legacy.reset_financial_caches()
        legacy.audit(
            f"Assigned {kind} transaction {transaction_id} for unit {bill.unit.unit_no} to the {payer_type} ledger",
            entity_type="receipt_allocation" if kind == "water" else kind, entity_id=transaction_id, reason="Party ledger assignment",
            commit=False, details={"payerType": {"before": previous, "after": payer_type}},
        )
        db.session.commit()
        return jsonify({"payerType": payer_type,
                        "ready": legacy.party_statement_ready(bill.unit_id, bill.billing_month)})

    @bp.get("/<int:bid>/party-balance")
    @permission_required("billing_detail")
    def party_balance(bid):
        bill = get_bill(bid)
        if not bill:
            return json_error(404, "Bill not found.")
        payer_type = request.args.get("payerType")
        if payer_type not in ("Owner", "Tenant"):
            return json_error(400, "payerType must be Owner or Tenant.")
        raw_date = str(request.args.get("date") or "").strip()
        try:
            as_of = date.fromisoformat(raw_date) if raw_date else date.today()
        except ValueError:
            return json_error(400, "date must be a valid ISO date.")
        cutover = legacy.party_ledger_cutover_month()
        if as_of.isoformat()[:7] < cutover:
            return jsonify({"balance": money(bill.balance)})
        readiness_month = bill.billing_month if legacy.party_ledger_enabled(bill.billing_month) else cutover
        if not legacy.party_statement_ready(bill.unit_id, readiness_month):
            return json_error(409, "Payer assignment is required for earlier transactions before this unit can use separate ledgers.")
        balance = max(legacy.party_balance_as_of(bill.unit_id, payer_type, as_of), Decimal("0.00"))
        return jsonify({"balance": money(balance)})

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
                                            payment_date=pay_date, form_token=token,
                                            payer_type=data.get("payerType"))
        if result is None:
            return json_error(404, "Bill not found.")
        b = result["bill"]
        return jsonify({"receiptNo": result["receipt"].receipt_no, "receiptId": result["receipt"].id,
                        "appliedToBill": result["applied"], "excessToAdvance": result["excess"],
                        "status": legacy.bill_status(b), "bill": detail(b)}), 201

    @bp.put("/<int:bid>/soa")
    @permission_required("edit_soa")
    def edit_soa(bid):
        bill = get_bill(bid)
        if not bill:
            return json_error(404, "Bill not found.")
        data = request.get_json(silent=True) or {}
        errors, amounts = {}, {}
        for key, (col, label, negative) in SOA_INPUT.items():
            try:
                amounts[col] = parse_amount(data.get(key), label=label, allow_zero=True, allow_empty=True, allow_negative=negative)
            except InputError as exc:
                errors[key] = exc.message
        if (legacy.party_ledger_enabled(bill.billing_month)
                and amounts.get("previous_balance") != legacy.money(bill.previous_balance)):
            errors["previousBalance"] = "Previous balance is calculated from the separate Owner and Tenant ledgers and cannot be edited here."
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
        other_items = None
        if "otherItems" in data:
            raw_items = data.get("otherItems")
            other_items = []
            if not isinstance(raw_items, list) or len(raw_items) > 50:
                errors["otherItems"] = "Enter no more than 50 named other-charge lines."
            else:
                for index, item in enumerate(raw_items):
                    key = f"otherItems.{index}"
                    if not isinstance(item, dict):
                        errors[key] = "Each other-charge line needs a name, amount and payer."
                        continue
                    description = str(item.get("description") or "").strip()
                    if not description or len(description) > 100:
                        errors[f"{key}.description"] = "Enter a name up to 100 characters."
                    try:
                        amount = parse_amount(item.get("amount"), label="Other-charge amount", allow_zero=True)
                    except InputError as exc:
                        errors[f"{key}.amount"] = exc.message
                        amount = None
                    payer_type = item.get("payerType")
                    if payer_type not in ("Owner", "Tenant"):
                        errors[f"{key}.payerType"] = "Choose Owner or Tenant."
                    if description and len(description) <= 100 and amount is not None and payer_type in ("Owner", "Tenant"):
                        other_items.append({"description": description, "amount": amount, "payerType": payer_type})
                if not any(key.startswith("otherItems.") for key in errors):
                    amounts["other"] = sum((item["amount"] for item in other_items), Decimal("0.00"))
        if errors:
            return field_errors(errors)
        b = legacy.correct_soa(bid, amounts, due, note, other_items=other_items)
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
        party_specific = bool(bills and legacy.party_ledger_enabled(bills[0].billing_month))
        if party_specific:
            unresolved = next((bill for bill in bills if not legacy.party_statement_ready(bill.unit_id, bill.billing_month)), None)
            if unresolved:
                return json_error(409, f"Separate SOAs cannot be emailed for unit {unresolved.unit.unit_no} until payer assignments are resolved.")
        sent, skipped, failed, errors = legacy.email_soas(bills, party_specific=party_specific)
        return jsonify({"sent": sent, "skipped": skipped, "failed": failed, "errors": errors[:5]})

    @bp.post("/<int:bid>/email")
    @permission_required("email_bill")
    def email_one(bid):
        b = get_bill(bid)
        if not b:
            return json_error(404, "Bill not found.")
        if legacy.outbound_email_disabled():
            return json_error(409, "Email sending is disabled on this test installation.")
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
