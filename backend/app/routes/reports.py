"""/api/reports — the condo management report for a period (replaces the classic /reports page).

    GET /api/reports?period=daily|weekly|monthly|yearly|custom[&from=YYYY-MM-DD&to=YYYY-MM-DD]
    GET /api/reports/monthly?months=6                              billed vs collected per month (Financial Reports)
    GET /api/reports/export.xlsx?from=YYYY-MM-DD&to=YYYY-MM-DD     the period's transactions as Excel

Permission keys are the classic ones (backend/app/core/permissions.py): reports and reports_export.
Read-only: nothing is recalculated or saved.

Kept from the classic page: the period presets, bills generated in the period with their charges,
collections by type and payment method, expenses, net cash flow, the outstanding balance and the
Excel export. Corrected (docs/module-migration-checklist.md, R1-R3):
  R1  "Total amount billed" added each bill's previous balance, so unpaid months were counted again
      in every later bill. Now: the charges of the period's bills (dues, parking, storage, water,
      other, adjustment, penalty); advance credits are shown on their own.
  R2  "Current outstanding" added the balances of ALL bills, and each bill's balance already contains
      the earlier unpaid ones. Now: what each unit still owes (its latest bill's balance).
  R3  Water collections used each reading's cumulative paid amount on its last payment date, so a
      partly paid reading was counted in full in the month of its last payment. Collections now come
      from the official receipts (as Payments & ORs), split by what each receipt paid; voided receipts
      are not counted. Payments recorded without a receipt (e.g. imported from Excel) are listed and
      counted separately so nothing is left out.
"""
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from io import BytesIO

from flask import Blueprint, jsonify, request, send_file
from sqlalchemy import exists

from ..services.receipts import allocation_label
from ..services.soa import money as text_money
from ..utils.auth import json_error, permission_required, protect_api_blueprint

PERIODS = ("daily", "weekly", "monthly", "yearly", "custom")
METHODS = ("CASH", "CHECK", "ONLINE")
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ZERO = Decimal("0.00")


def period_range(period, start_raw, end_raw, today=None):
    """(period, start, end, label) for a preset or a custom range (swapped if reversed)."""
    today = today or date.today()
    if period == "daily":
        return period, today, today, today.strftime("%B %d, %Y").replace(" 0", " ")
    if period == "weekly":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        return period, start, end, f"Week of {start.strftime('%b %d, %Y')}"
    if period == "yearly":
        return period, date(today.year, 1, 1), date(today.year, 12, 31), str(today.year)
    if period == "custom":
        def parse(raw, default):
            if not raw:
                return default
            if not ISO.match(raw):
                raise ValueError(raw)
            return date.fromisoformat(raw)
        start = parse(start_raw, date(today.year, today.month, 1))
        end = parse(end_raw, today)
        if end < start:
            start, end = end, start
        if (end - start).days > 3660:
            raise ValueError("range")
        return period, start, end, f"{start.strftime('%b %d, %Y')} – {end.strftime('%b %d, %Y')}"
    start = date(today.year, today.month, 1)
    end = date(today.year + (today.month == 12), today.month % 12 + 1, 1) - timedelta(days=1)
    return "monthly", start, end, today.strftime("%B %Y")


def utc_bounds(start, end):
    """The local-date range as naive UTC datetimes (created_at columns hold UTC)."""
    local = datetime.now().astimezone().tzinfo
    lo = datetime.combine(start, time.min, tzinfo=local).astimezone(timezone.utc).replace(tzinfo=None)
    hi = datetime.combine(end + timedelta(days=1), time.min, tzinfo=local).astimezone(timezone.utc).replace(tzinfo=None)
    return lo, hi


def make_reports_blueprint(legacy):
    db = legacy.db
    Unit, Tenant, Billing, Receipt, Alloc = legacy.Unit, legacy.Tenant, legacy.Billing, legacy.Receipt, legacy.ReceiptAllocation
    Payment, AdvancePayment, WaterReading, Expense = legacy.Payment, legacy.AdvancePayment, legacy.WaterReading, legacy.Expense
    bp = protect_api_blueprint(Blueprint("api_reports", __name__, url_prefix="/api/reports"))
    num = legacy.money          # Decimal, for sums; text_money() formats the JSON output

    def report(start, end):
        legacy.reset_financial_caches()
        lo, hi = utc_bounds(start, end)

        # ---- bills generated in the period (R1: charges only, no previous balances)
        bills = Billing.query.filter(Billing.created_at >= lo, Billing.created_at < hi).all()
        charges = dict.fromkeys(("condo", "parking", "storage", "water", "other", "adjustment", "penalty"), ZERO)
        advance_credits = ZERO
        for b in bills:
            c = legacy._bill_calc(b)
            for key in charges:
                charges[key] += c[key]
            advance_credits += c["advance"]
        charged = sum(charges.values(), ZERO)

        # ---- collections: official receipts (R3), voided ones excluded
        receipts = (Receipt.query.filter(Receipt.received_date >= start, Receipt.received_date <= end)
                    .order_by(Receipt.received_date, Receipt.receipt_seq).all())
        by_kind = {"bill": ZERO, "water": ZERO, "advance": ZERO}
        by_method = dict.fromkeys(METHODS, ZERO)
        transactions, voided = [], {"count": 0, "amount": ZERO}
        for r in receipts:
            if r.voided_at:
                voided["count"] += 1
                voided["amount"] += num(r.amount)
                continue
            for a in r.allocations:
                by_kind[a.kind] += num(a.amount)
            method = (r.payment_method or "CASH").upper()
            by_method[method] = by_method.get(method, ZERO) + num(r.amount)
            transactions.append({"date": r.received_date.isoformat(), "receiptNo": r.receipt_no, "receiptId": r.id,
                                 "unitNo": r.unit.unit_no if r.unit else "", "method": method, "reference": r.reference or "",
                                 "description": "; ".join(allocation_label(a)[0] for a in r.allocations), "amount": text_money(r.amount)})

        # Money recorded without an official receipt (imports, records older than receipts).
        unreceipted = []
        for p in Payment.query.filter(Payment.payment_date >= start, Payment.payment_date <= end, Payment.reversed_at.is_(None),
                                      Payment.amount > 0, ~exists().where(Alloc.payment_id == Payment.id)).all():
            unreceipted.append(("bill", p.payment_date, p.billing.unit.unit_no if p.billing and p.billing.unit else "",
                                f"Statement of Account — {p.billing.billing_month}" if p.billing else "Bill payment",
                                p.payment_method, p.reference, num(p.amount)))
        for w in WaterReading.query.filter(WaterReading.paid_date >= start, WaterReading.paid_date <= end, WaterReading.paid_amount > 0,
                                           ~exists().where(Alloc.water_reading_id == WaterReading.id)).all():
            unreceipted.append(("water", w.paid_date, w.unit.unit_no if w.unit else "", f"Water — {w.reading_month}",
                                w.payment_method, w.payment_reference, num(w.paid_amount)))
        for a in AdvancePayment.query.filter(AdvancePayment.payment_date >= start, AdvancePayment.payment_date <= end,
                                             AdvancePayment.reversed_at.is_(None), AdvancePayment.amount > 0,
                                             ~exists().where(Alloc.advance_payment_id == AdvancePayment.id)).all():
            unreceipted.append(("advance", a.payment_date, a.unit.unit_no if a.unit else "", f"Advance condo dues from {a.start_month}",
                                a.payment_method, a.reference, num(a.amount)))
        for kind, when, unit_no, label, method, ref, amount in sorted(unreceipted, key=lambda x: x[1]):
            by_kind[kind] += amount
            method = (method or "CASH").upper()
            by_method[method] = by_method.get(method, ZERO) + amount
            transactions.append({"date": when.isoformat(), "receiptNo": None, "receiptId": None, "unitNo": unit_no, "method": method,
                                 "reference": ref or "", "description": label, "amount": text_money(amount)})
        transactions.sort(key=lambda t: (t["date"], t["receiptNo"] or ""))
        collected = sum(by_kind.values(), ZERO)

        # ---- expenses
        expenses = Expense.query.filter(Expense.expense_date >= start, Expense.expense_date <= end).order_by(Expense.expense_date, Expense.id).all()
        expense_total = sum((num(e.amount) for e in expenses), ZERO)
        by_category = {}
        for e in expenses:
            by_category[e.category or "Other"] = by_category.get(e.category or "Other", ZERO) + num(e.amount)

        # ---- receivables now (R2): each unit's unpaid charges, counted once
        owed_by_unit = {}
        for b in Billing.query.all():
            part = legacy.bill_unpaid_part(b)
            if part > 0:
                owed_by_unit[b.unit_id] = owed_by_unit.get(b.unit_id, ZERO) + part
        overdue_units = {b.unit_id for b in Billing.query.filter(Billing.due_date < date.today()).all() if legacy.bill_unpaid_part(b) > 0}

        residential = Unit.query.filter(Unit.active.is_(True), Unit.unit_type.notin_(("PARKING", "STORAGE"))).all()
        occupied = sum(1 for u in residential if (u.status or "").lower() == "occupied")
        return {
            "from": start.isoformat(), "to": end.isoformat(),
            "property": {"residentialUnits": len(residential), "occupied": occupied, "vacant": len(residential) - occupied,
                         "currentTenants": Tenant.query.filter_by(status="Current").count(),
                         "withParking": sum(1 for u in residential if u.assigned_parking_unit_id)},
            "billing": {"bills": len(bills), "charged": text_money(charged), "advanceCredits": text_money(advance_credits),
                        "charges": {k: text_money(v) for k, v in charges.items()}},
            "collections": {"total": text_money(collected), "bills": text_money(by_kind["bill"]), "water": text_money(by_kind["water"]),
                            "advances": text_money(by_kind["advance"]), "byMethod": {k: text_money(v) for k, v in by_method.items()},
                            "receipts": sum(1 for t in transactions if t["receiptNo"]), "withoutReceipt": len(unreceipted),
                            "voided": {"count": voided["count"], "amount": text_money(voided["amount"])}},
            "expenses": {"total": text_money(expense_total), "byCategory": {k: text_money(v) for k, v in sorted(by_category.items())},
                         "rows": [{"id": e.id, "date": e.expense_date.isoformat(), "category": e.category or "", "description": e.description or "",
                                   "amount": text_money(e.amount)} for e in expenses]},
            "netCashFlow": text_money(collected - expense_total),
            "receivables": {"total": text_money(sum(owed_by_unit.values(), ZERO)), "units": len(owed_by_unit), "overdueUnits": len(overdue_units)},
            "transactions": transactions,
        }

    def requested_range():
        period = (request.args.get("period") or "monthly").lower()
        if period not in PERIODS:
            period = "monthly"
        return period_range(period, request.args.get("from", "").strip(), request.args.get("to", "").strip())

    @bp.get("")
    @permission_required("reports")
    def get_report():
        try:
            period, start, end, label = requested_range()
        except ValueError:
            return json_error(400, "Enter the dates as YYYY-MM-DD, at most 10 years apart.")
        return jsonify({"period": period, "label": label, **report(start, end)})

    @bp.get("/monthly")
    @permission_required("reports")
    def monthly():
        """Billed vs collected per month, and receivables at the end of each month (Financial Reports).

        billed       charges of the month's bills (dues, parking, storage, water, other, adjustment,
                     penalty), without earlier balances
        collected    money received in the month: official receipts (voided ones excluded) plus
                     payments recorded without a receipt, as in the period report
        outstanding  what units owed at the end of the month: charges of bills up to that month (less
                     advance credits) minus bill payments dated up to then, per unit, never below 0
                     (the current month equals the period report's receivables)
        """
        try:
            count = int(request.args.get("months", "6"))
        except ValueError:
            return json_error(400, "months must be a number from 1 to 36.")
        if not 1 <= count <= 36:
            return json_error(400, "months must be a number from 1 to 36.")
        legacy.reset_financial_caches()
        today = date.today()
        months = []
        y, m = today.year, today.month
        for _ in range(count):
            months.append(f"{y:04d}-{m:02d}")
            y, m = (y - 1, 12) if m == 1 else (y, m - 1)
        months.reverse()

        def month_end(month):
            yy, mm = (int(x) for x in month.split("-"))
            return date(yy + (mm == 12), mm % 12 + 1, 1) - timedelta(days=1)

        # Charges per bill (no earlier balances) and per unit/month.
        bills = Billing.query.all()
        charged_by_month, unit_charges = {}, []
        for b in bills:
            c = legacy._bill_calc(b)
            charged_by_month[b.billing_month] = charged_by_month.get(b.billing_month, ZERO) + sum(
                (c[k] for k in ("condo", "parking", "storage", "water", "other", "adjustment", "penalty")), ZERO)
            unit_charges.append((b.unit_id, b.billing_month, c["charge"]))
        # Bill payments with their dates. Reversed ones (voided receipts) never count: the money did not
        # arrive, the same rule as for collections.
        payments = [(p.billing.unit_id, p.payment_date, num(p.amount))
                    for p in Payment.query.filter(Payment.amount > 0, Payment.reversed_at.is_(None)).all() if p.billing]

        first = date.fromisoformat(months[0] + "-01")
        last = month_end(months[-1])
        received = {}
        for r in Receipt.query.filter(Receipt.received_date >= first, Receipt.received_date <= last, Receipt.voided_at.is_(None)).all():
            key = r.received_date.strftime("%Y-%m")
            received[key] = received.get(key, ZERO) + num(r.amount)
        for p in Payment.query.filter(Payment.payment_date >= first, Payment.payment_date <= last, Payment.reversed_at.is_(None),
                                      Payment.amount > 0, ~exists().where(Alloc.payment_id == Payment.id)).all():
            key = p.payment_date.strftime("%Y-%m")
            received[key] = received.get(key, ZERO) + num(p.amount)
        for w in WaterReading.query.filter(WaterReading.paid_date >= first, WaterReading.paid_date <= last, WaterReading.paid_amount > 0,
                                           ~exists().where(Alloc.water_reading_id == WaterReading.id)).all():
            key = w.paid_date.strftime("%Y-%m")
            received[key] = received.get(key, ZERO) + num(w.paid_amount)
        for a in AdvancePayment.query.filter(AdvancePayment.payment_date >= first, AdvancePayment.payment_date <= last,
                                             AdvancePayment.reversed_at.is_(None), AdvancePayment.amount > 0,
                                             ~exists().where(Alloc.advance_payment_id == AdvancePayment.id)).all():
            key = a.payment_date.strftime("%Y-%m")
            received[key] = received.get(key, ZERO) + num(a.amount)

        rows = []
        for month in months:
            end = month_end(month)
            owed = {}
            for unit_id, bill_month, charge in unit_charges:
                if bill_month <= month:
                    owed[unit_id] = owed.get(unit_id, ZERO) + charge
            for unit_id, paid_on, amount in payments:
                if paid_on <= end:
                    owed[unit_id] = owed.get(unit_id, ZERO) - amount
            billed = charged_by_month.get(month, ZERO)
            collected = received.get(month, ZERO)
            rows.append({"month": month, "billed": text_money(billed), "collected": text_money(collected),
                         "outstanding": text_money(sum((max(v, ZERO) for v in owed.values()), ZERO)),
                         "collectionRate": float(min(collected / billed, Decimal("1"))) if billed > 0 else 0.0})
        return jsonify({"months": rows})

    @bp.get("/export.xlsx")
    @permission_required("reports_export")
    def export():
        try:
            period, start, end, label = requested_range()
        except ValueError:
            return json_error(400, "Enter the dates as YYYY-MM-DD, at most 10 years apart.")
        if legacy.Workbook is None:
            return json_error(500, "Excel export needs the openpyxl package.")
        data = report(start, end)
        wb = legacy.Workbook()
        ws = wb.active
        ws.title = "Summary"
        rows = [
            [legacy.setting("corporation_name", "CITYLAND 9 CONDOMINIUM CORPORATION")],
            ["Report period", f"{start.isoformat()} to {end.isoformat()}"],
            [],
            ["Bills generated", data["billing"]["bills"]],
            ["Charges billed", float(data["billing"]["charged"])],
            ["Advance credits applied", float(data["billing"]["advanceCredits"])],
            ["Collections (total)", float(data["collections"]["total"])],
            ["  Bill payments", float(data["collections"]["bills"])],
            ["  Water payments", float(data["collections"]["water"])],
            ["  Advance payments", float(data["collections"]["advances"])],
            ["Expenses", float(data["expenses"]["total"])],
            ["Net cash flow", float(data["netCashFlow"])],
            ["Receivables now", float(data["receivables"]["total"])],
            ["Voided receipts (not counted)", data["collections"]["voided"]["count"], float(data["collections"]["voided"]["amount"])],
        ]
        for row in rows:
            ws.append(row)
        tx = wb.create_sheet("Transactions")
        tx.append(["Type", "Date", "OR No.", "Unit", "Description", "Payment method", "Reference", "Amount"])
        for t in data["transactions"]:
            tx.append(["Collection", date.fromisoformat(t["date"]), t["receiptNo"] or "(no receipt)", t["unitNo"], t["description"],
                       t["method"], t["reference"], float(t["amount"])])
        for e in data["expenses"]["rows"]:
            tx.append(["Expense", date.fromisoformat(e["date"]), "", "", e["description"] or e["category"], "", "", -float(e["amount"])])
        for sheet in wb.worksheets:
            for cell in sheet[1]:
                cell.font = cell.font.copy(bold=True)
            for col in sheet.columns:
                sheet.column_dimensions[col[0].column_letter].width = min(max(max(len(str(c.value or "")) for c in col) + 2, 12), 48)
        out = BytesIO()
        wb.save(out)
        out.seek(0)
        legacy.audit(f"Exported the condo report {start.isoformat()} to {end.isoformat()}", entity_type="report")
        return send_file(out, as_attachment=True, download_name=f"CityLand9_Report_{start.isoformat()}_{end.isoformat()}.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    return bp
