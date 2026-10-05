"""Statement of account (SOA) as JSON, shared by the resident portal (/api/resident), Billing
(/api/billing) and the SOA PDF (services/soa_pdf.py), so a resident, the office and the PDF always show
the same figures.

Amounts come from the billing engine (legacy._bill_calc), read-only: building an SOA never changes a
bill. Money is formatted half-up to the centavo. Balances are NOT clamped: a negative balance means
the statement is overpaid (credit) and is shown as such, never hidden as ₱0.00.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
OFFICE_INSTRUCTIONS = "Pay at the Admin Office by cash, check or online transfer. Please bring this statement or quote the unit number and statement no."


def money(value):
    return str(Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP))


def _num(value, places="0.01"):
    return None if value is None else str(Decimal(str(value)).quantize(Decimal(places), rounding=ROUND_HALF_UP))


def _local_date(utc_dt):
    return utc_dt.replace(tzinfo=timezone.utc).astimezone().date() if utc_dt else None


def statement_no(bill):
    return f"SOA-{bill.billing_month.replace('-', '')}-{bill.id:05d}"


def soa_row(legacy, bill):
    calc = legacy._bill_calc(bill)
    return {
        "id": bill.id,
        "billingMonth": bill.billing_month,
        "dueDate": bill.due_date.isoformat() if bill.due_date else None,
        "status": legacy.bill_status(bill),
        "total": money(calc["total"]),
        "amountPaid": money(bill.amount_paid),
        "balance": money(calc["balance"]),
    }


def _basis(kind, amount, area, rate, basis, unit_no=None):
    """How one dues line was priced, only when it really produces the charged amount (a hand-corrected
    or older statement may not have a recorded basis; nothing is invented)."""
    amount = Decimal(str(amount or 0)).quantize(CENT, rounding=ROUND_HALF_UP)
    if basis == "manual":
        return {"kind": kind, "basis": "manual", "unitNo": unit_no, "area": None, "rate": None}
    if area is None or rate is None:
        return None
    computed = (Decimal(str(area)) * Decimal(str(rate))).quantize(CENT, rounding=ROUND_HALF_UP)
    if computed != amount:
        return None
    rate_text = format(Decimal(str(rate)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP).normalize(), "f")
    return {"kind": kind, "basis": "sqm", "unitNo": unit_no, "area": _num(area), "rate": rate_text}


def charge_bases(legacy, bill, calc):
    """Area × rate per sqm for condo / parking / storage: the issue-time snapshot when recorded,
    otherwise the unit's current record if (and only if) it gives exactly the charged amount."""
    if bill.dues_basis is not None or bill.parking_area is not None or bill.storage_area is not None:
        condo = _basis("condo", calc["condo"], bill.condo_area, bill.condo_rate, bill.dues_basis)
        parking = _basis("parking", calc["parking"], bill.parking_area, bill.parking_rate, "sqm", bill.parking_unit_no) if bill.parking_unit_no else None
        storage = _basis("storage", calc["storage"] or bill.storage_dues, bill.storage_area, bill.storage_rate, bill.storage_basis, bill.storage_unit_no) if bill.storage_unit_no else None
        recorded = True
    else:
        cur = legacy.charge_basis(bill.unit) if bill.unit else {"condo": None, "parking": None, "storage": None}
        c, p, s = cur["condo"] or {}, cur["parking"] or {}, cur["storage"] or {}
        condo = _basis("condo", calc["condo"], c.get("area"), c.get("rate"), c.get("basis")) if c else None
        if condo and condo["basis"] == "manual" and legacy.money(bill.assessment) != legacy.money(legacy.unit_dues(bill.unit)):
            condo = None
        parking = _basis("parking", calc["parking"], p.get("area"), p.get("rate"), "sqm", p.get("unit_no")) if p else None
        storage = _basis("storage", bill.storage_dues, s.get("area"), s.get("rate"), s.get("basis"), s.get("unit_no")) if s else None
        recorded = False
    return {"condo": condo, "parking": parking, "storage": storage, "recorded": recorded}


def _account(legacy, bill):
    unit = bill.unit
    owner = legacy.Owner.query.filter_by(unit_id=unit.id, status="Current").order_by(legacy.Owner.id.desc()).first() if unit else None
    tenant = legacy.Tenant.query.filter_by(unit_id=unit.id, status="Current").order_by(legacy.Tenant.id.desc()).first() if unit else None
    contact = legacy.current_contact_for_unit(unit) if unit else None
    bill_to = (getattr(contact, "tenant_name", None) or getattr(contact, "owner_name", None) or "") if contact else ""
    return {"unitNo": unit.unit_no if unit else "", "unitType": (unit.unit_type or "") if unit else "", "floor": (unit.floor or "") if unit else "",
            "billTo": bill_to, "ownerName": owner.owner_name if owner else "", "tenantName": tenant.tenant_name if tenant else ""}


def _penalty(legacy, bill, calc):
    rate, kinds = legacy.bill_penalty_rules(bill)
    pct = (rate * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP).normalize()
    labels = {"condo": "condo dues", "parking": "parking", "storage": "storage"}
    eligible = [labels[k] for k in ("condo", "parking", "storage") if k in kinds]
    overdue = legacy.is_overdue(bill)
    amount = Decimal(str(calc["penalty"]))
    if bill.soa_manual_override:
        explanation = "Penalty as entered on this corrected statement."
    elif amount > 0:
        explanation = (f"{format(pct, 'f')}% of the unpaid {', '.join(eligible) or 'eligible charges'} from earlier statements "
                       f"(₱{Decimal(str(calc['penalty_base'])):,.2f}), assessed because this statement is past its due date.")
    elif overdue:
        explanation = "No penalty: nothing eligible was unpaid from earlier statements."
    else:
        explanation = (f"A {format(pct, 'f')}% penalty on unpaid {', '.join(eligible) or 'eligible charges'} applies after the due date. "
                       "Water and other charges are not penalized.")
    return {"amount": money(amount), "rate": format(pct, "f"), "base": money(calc["penalty_base"]), "eligible": eligible,
            "overdue": overdue, "recorded": bill.penalty_rate is not None, "explanation": explanation}


def payment_guidance(legacy):
    """Resident-facing payment instructions; the QR is offered only when a payment link is configured."""
    url = (legacy.setting("online_payment_url", "") or "").strip()
    text = (legacy.setting("online_payment_instructions", "") or "").strip()
    if not text or (not url and "qr" in text.lower()):
        text = OFFICE_INSTRUCTIONS
    return {"instructions": text, "onlinePaymentUrl": url or None, "qrAvailable": bool(url)}


def soa_detail(legacy, bill):
    calc = legacy._bill_calc(bill)
    reading = calc.get("reading")
    y, mth = (int(x) for x in bill.billing_month.split("-"))
    period_end = date(y + (mth == 12), mth % 12 + 1, 1) - timedelta(days=1)
    previous = legacy.unpaid_previous_bills(bill.unit_id, bill.billing_month)
    issued = _local_date(bill.issued_at) or _local_date(bill.created_at)
    return {
        **soa_row(legacy, bill),
        "statementNo": statement_no(bill),
        "issueDate": issued.isoformat() if issued else None,
        "periodStart": f"{bill.billing_month}-01",
        "periodEnd": period_end.isoformat(),
        "issuedAmount": money(bill.issued_amount) if bill.issued_amount is not None else None,
        "account": _account(legacy, bill),
        "property": {"name": legacy.setting("corporation_name", "CITYLAND 9 CONDOMINIUM CORPORATION"), "address": legacy.setting("address", "")},
        "charges": {
            "condoDues": money(calc["condo"]),
            "parking": money(calc["parking"]),
            "storage": money(bill.storage_dues),
            "storageIncluded": legacy.storage_charged(bill),   # recorded at issue; older bills: the cut-off month
            "storageFrom": legacy.storage_cutoff(),
            "water": money(calc["water"]),
            "waterUsage": round(reading.usage, 2) if reading else None,
            "waterRate": money(reading.rate) if reading else None,
            "waterPaidSeparately": bool(reading and reading.paid),
            "other": money(calc.get("other", 0)),
            "adjustment": money(calc.get("adjustment", 0)),
            "penalty": money(calc["penalty"]),
            "previousBalance": money(calc["previous"]),
            "advanceApplied": money(calc["advance"]),
            "currentCharges": money(calc["current"]),
        },
        "bases": charge_bases(legacy, bill, calc),
        "penaltyInfo": _penalty(legacy, bill, calc),
        "previousUnpaid": [{"id": x.id, "month": x.billing_month, "dueDate": x.due_date.isoformat() if x.due_date else None,
                            "balance": money(legacy.bill_unpaid_part(x))} for x in previous],
        "correctedByHand": bool(bill.soa_manual_override),
        "payment": payment_guidance(legacy),
        "note": bill.soa_note or "",
        "payments": [
            {"date": p.payment_date.isoformat() if p.payment_date else None, "amount": money(p.amount),
             "receiptNo": legacy.receipt_no_for("bill", p.id) or None,
             "reversed": bool(getattr(p, "reversed_at", None)),
             "method": p.payment_method, "type": p.payment_type, "reference": p.reference or ""}
            for p in sorted(bill.payments, key=lambda p: (p.payment_date or datetime.min.date(), p.id))
        ],
    }
