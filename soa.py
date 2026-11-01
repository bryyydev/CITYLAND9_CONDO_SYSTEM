"""Statement of account (SOA) as JSON, shared by the resident portal (/api/resident), Billing
(/api/billing) and the SOA PDF (services/soa_pdf.py), so a resident, the office and the PDF always show
the same figures.

Amounts come from the billing engine (legacy._bill_calc), read-only: building an SOA never changes a
bill. Money is formatted half-up to the centavo. Balances are NOT clamped: a negative balance means
the statement is overpaid (credit) and is shown as such, never hidden as ₱0.00.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from flask import g

CENT = Decimal("0.01")
OFFICE_INSTRUCTIONS = "Pay at the Admin Office by cash, check or online transfer. Please bring this statement or quote the unit number and statement no."


def money(value):
    return str(Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP))


def _num(value, places="0.01"):
    return None if value is None else str(Decimal(str(value)).quantize(Decimal(places), rounding=ROUND_HALF_UP))


def _local_date(utc_dt):
    return utc_dt.replace(tzinfo=timezone.utc).astimezone().date() if utc_dt else None


def statement_no(bill):
    return f"{bill.billing_month.replace('-', '')}-{bill.id:05d}"


def _water_reading_period(reading):
    """Use the entered range, falling back to the water form's 16th-to-15th cycle."""
    if not reading:
        return None, None
    year, month = (int(part) for part in reading.reading_month.split("-"))
    previous_year, previous_month = (year - 1, 12) if month == 1 else (year, month - 1)
    start = reading.reading_period_start or date(previous_year, previous_month, 16)
    end = reading.reading_period_end or date(year, month, 15)
    return start.isoformat(), end.isoformat()


def soa_row(legacy, bill):
    calc = legacy._bill_calc(bill)
    vat = soa_vat(legacy, calc)
    total = Decimal(str(calc["total"])) + vat["amount"]
    balance = Decimal(str(calc["balance"])) + vat["amount"]
    status = legacy.bill_status(bill)
    if vat["amount"] > 0 and balance > 0 and status == "Paid":
        status = ("Partially Paid" if Decimal(str(bill.amount_paid or 0)) > 0 else
                  "Overdue" if bill.due_date and date.today() > bill.due_date else "Unpaid")
    return {
        "id": bill.id,
        "billingMonth": bill.billing_month,
        "dueDate": bill.due_date.isoformat() if bill.due_date else None,
        "status": status,
        "total": money(total),
        "amountPaid": money(bill.amount_paid),
        "balance": money(balance),
    }


VAT_CHARGES = {"condo": "Condo dues", "parking": "Parking", "storage": "Storage", "water": "Water", "other": "Other charges"}


def soa_vat(legacy, calc):
    """VAT is a presentation-only SOA amount; it never changes the stored bill calculation."""
    rate = Decimal(str(legacy.setting("vat_rate", "0")))
    selected = [kind for kind in VAT_CHARGES
                if legacy.setting(f"vat_include_{kind}", "0") == "1"]
    amounts = {
        "condo": Decimal(str(calc["condo"])),
        "parking": Decimal(str(calc["parking"])),
        "storage": Decimal(str(calc["storage"])),
        "water": Decimal("0") if calc["reading"] and calc["reading"].paid else Decimal(str(calc["water"])),
        "other": Decimal(str(calc.get("other", 0))),
    }
    taxable = [(kind, max(amounts[kind], Decimal("0"))) for kind in selected if amounts[kind] > 0]
    base = sum((amount for _, amount in taxable), Decimal("0"))
    amount = (base * rate / Decimal("100")).quantize(CENT, rounding=ROUND_HALF_UP)
    return {
        "amount": amount,
        "base": base.quantize(CENT, rounding=ROUND_HALF_UP),
        "rate": format(rate.normalize(), "f"),
        "eligible": [VAT_CHARGES[kind] for kind, _ in taxable],
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
    if (bill.dues_basis is not None or bill.parking_unit_no is not None or bill.parking_area is not None
            or bill.storage_unit_no is not None or bill.storage_basis is not None or bill.storage_area is not None):
        condo = _basis("condo", calc["condo"], bill.condo_area, bill.condo_rate, bill.dues_basis)
        parking_basis = ("manual" if bill.unit and bill.unit.unit_type == "PARKING" and bill.parking_area is None else "sqm")
        parking = _basis("parking", calc["parking"], bill.parking_area, bill.parking_rate, parking_basis, bill.parking_unit_no) if bill.parking_unit_no else None
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


def _soa_people(legacy, unit):
    if not unit:
        return None, None, None
    if "owners" in unit.__dict__:
        owners = sorted((p for p in unit.owners if p.status == "Current"), key=lambda p: p.id, reverse=True)
    else:
        owners = legacy.Owner.query.filter_by(unit_id=unit.id, status="Current").order_by(legacy.Owner.id.desc()).all()
    if "tenants" in unit.__dict__:
        tenants = sorted((p for p in unit.tenants if p.status == "Current"), key=lambda p: p.id)
    else:
        tenants = legacy.Tenant.query.filter_by(unit_id=unit.id, status="Current").order_by(legacy.Tenant.id.asc()).all()
    owner = next((person for person in owners if person.include_in_soa is not False), None)
    tenants = [person for person in tenants if person.include_in_soa is not False]
    tenant = tenants[0] if tenants else None
    contact = next((person for person in tenants if person.representative), None)
    if not contact:
        contact = next((person for person in tenants if person.email or person.contact_no), None)
    if not contact:
        contact = owner
    if not contact and unit.unit_type in ("PARKING", "STORAGE"):
        parents = getattr(g, "_asset_bill_parent_units", None)
        if parents is None:
            from sqlalchemy import or_
            from sqlalchemy.orm import selectinload

            rows = legacy.Unit.query.filter(
                legacy.Unit.active.is_(True),
                legacy.Unit.unit_type.notin_(("PARKING", "STORAGE")),
                or_(legacy.Unit.assigned_parking_unit_id.isnot(None),
                    legacy.Unit.assigned_storage_unit_id.isnot(None)),
            ).options(selectinload(legacy.Unit.owners), selectinload(legacy.Unit.tenants)).order_by(legacy.Unit.id).all()
            parents = {}
            for row in rows:
                for asset_id in (row.assigned_parking_unit_id, row.assigned_storage_unit_id):
                    if asset_id is not None:
                        parents.setdefault(asset_id, row)
            g._asset_bill_parent_units = parents
        residential = parents.get(unit.id)
        if residential:
            return _soa_people(legacy, residential)
    return owner, tenant, contact


def soa_contact_for_unit(legacy, unit):
    """The current owner/tenant contact allowed to appear beside or on this SOA."""
    return _soa_people(legacy, unit)[2]


def _account(legacy, bill):
    unit = bill.unit
    owner, tenant, contact = _soa_people(legacy, unit)
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


def soa_detail(legacy, bill, payer_type=None):
    if payer_type not in (None, "Owner", "Tenant"):
        raise ValueError("payer_type must be Owner or Tenant")
    if payer_type and not legacy.party_ledger_enabled(bill.billing_month):
        raise ValueError("Separate Owner/Tenant statements are available from the configured cutover month.")
    if payer_type and not legacy.party_statement_ready(bill.unit_id, bill.billing_month):
        raise ValueError("Payer assignment is required for earlier payments and advances before separate statements can be issued.")
    calc = legacy._bill_calc(bill)
    vat = soa_vat(legacy, calc)
    reading = calc.get("reading")
    commercial_reading = None
    water_allocation = None
    if bill.unit and bill.unit.unit_type == "COMMERCIAL" and bill.unit.floor and bill.water_reading_month:
        commercial_reading = legacy.CommercialWaterReading.query.filter_by(
            commercial_type=bill.unit.floor, reading_month=bill.water_reading_month,
        ).first()
        if commercial_reading:
            shared_amount = legacy.commercial_water_amount_for_reading(commercial_reading)
            shares, total_area = legacy.commercial_water_shares(bill.unit.floor, shared_amount)
            if shares.get(bill.unit_id, Decimal("0.00")) == Decimal(str(calc["water"])).quantize(CENT, rounding=ROUND_HALF_UP):
                water_allocation = {
                    "commercialType": bill.unit.floor,
                    "unitArea": _num(bill.unit.area_sqm) or "0.00",
                    "totalArea": _num(total_area) or "0.00",
                    "sharedAmount": money(shared_amount),
                    "unitAmount": money(calc["water"]),
                }
    y, mth = (int(x) for x in bill.billing_month.split("-"))
    period_end = date(y + (mth == 12), mth % 12 + 1, 1) - timedelta(days=1)
    previous = legacy.unpaid_previous_bills(bill.unit_id, bill.billing_month)
    issued = _local_date(bill.issued_at) or _local_date(bill.created_at)
    if commercial_reading:
        water_reading_month = commercial_reading.reading_month
    elif reading:
        water_reading_month = reading.reading_month
    elif bill.unit and bill.unit.unit_type == "COMMERCIAL":
        water_reading_month = bill.water_reading_month
    else:
        water_reading_month = None
    water_reading_from, water_reading_to = _water_reading_period(commercial_reading or reading)
    detail = {
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
            "waterReadingMonth": water_reading_month,
            "waterReadingFrom": water_reading_from,
            "waterReadingTo": water_reading_to,
            "waterUsage": round(commercial_reading.usage, 2) if commercial_reading else round(reading.usage, 2) if reading else None,
            "waterRate": money(commercial_reading.rate) if commercial_reading else money(reading.rate) if reading else None,
            "waterPaidSeparately": bool(reading and reading.paid),
            "waterAllocation": water_allocation,
            "other": money(calc.get("other", 0)),
            "otherItems": [
                {"description": row.description, "amount": money(row.amount), "payerType": row.payer_type}
                for row in bill.extra_charges
            ],
            "adjustment": money(calc.get("adjustment", 0)),
            "penalty": money(calc["penalty"]),
            "previousBalance": money(calc["previous"]),
            "advanceApplied": money(calc["advance"]),
            "vat": money(vat["amount"]),
            "vatBase": money(vat["base"]),
            "vatRate": vat["rate"],
            "vatEligible": vat["eligible"],
            "currentCharges": money(Decimal(str(calc["current"])) + vat["amount"]),
        },
        "bases": charge_bases(legacy, bill, calc),
        "penaltyInfo": _penalty(legacy, bill, calc),
        "previousUnpaid": [{"id": x.id, "statementNo": statement_no(x), "month": x.billing_month,
                            "dueDate": x.due_date.isoformat() if x.due_date else None,
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
    if payer_type:
        filtered = dict(calc)
        for category in ("condo", "parking", "storage", "water", "other", "adjustment"):
            if category == "other" and bill.extra_charges:
                extra_rows = list(bill.extra_charges)
                filtered[category] = sum(
                    (Decimal(str(row.amount or 0)) for row in extra_rows if row.payer_type == payer_type),
                    Decimal("0.00"),
                )
                if not extra_rows and legacy.party_charge_payer(bill, category) != payer_type:
                    filtered[category] = Decimal("0.00")
            elif legacy.party_charge_payer(bill, category) != payer_type:
                filtered[category] = Decimal("0.00")
        if payer_type != "Tenant":
            filtered["penalty"] = Decimal("0.00")
        vat = soa_vat(legacy, filtered)
        previous = legacy.party_balance_before(bill.unit_id, payer_type, bill.billing_month)
        current = legacy.party_current_charges(bill, payer_type)
        total = previous + current + vat["amount"]
        month_paid = legacy.party_month_payments(bill.unit_id, payer_type, bill.billing_month)
        balance = total - month_paid
        status = ("Paid" if balance <= 0 else "Partially Paid" if month_paid > 0 else
                  "Overdue" if bill.due_date and date.today() > bill.due_date else "Unpaid")
        person = None
        if payer_type == "Owner":
            person = legacy.Owner.query.filter_by(unit_id=bill.unit_id, status="Current").order_by(
                legacy.Owner.id.desc()).first()
            person_name = person.owner_name if person else None
        else:
            tenants = legacy.Tenant.query.filter_by(unit_id=bill.unit_id, status="Current").order_by(
                legacy.Tenant.id.asc()).all()
            person = next((item for item in tenants if item.representative), tenants[0] if tenants else None)
            person_name = person.tenant_name if person else None
        detail["billingMonth"] = bill.billing_month
        detail["statementNo"] = f"{detail['statementNo']}-{payer_type[:1].upper()}"
        detail["status"] = status
        detail["total"] = money(total)
        detail["amountPaid"] = money(total - balance)
        detail["balance"] = money(balance)
        detail["account"] = {**detail["account"], "billTo": person_name or payer_type}
        detail["charges"] = {
            **detail["charges"],
            "condoDues": money(filtered["condo"]),
            "parking": money(filtered["parking"]),
            "storage": money(filtered["storage"]),
            "water": money(filtered["water"]),
            "waterPaidSeparately": False,
            "other": money(filtered["other"]),
            "adjustment": money(filtered["adjustment"]),
            "penalty": money(filtered["penalty"]),
            "previousBalance": money(previous),
            "advanceApplied": "0.00",
            "vat": money(vat["amount"]),
            "vatBase": money(vat["base"]),
            "vatEligible": vat["eligible"],
            "currentCharges": money(Decimal(str(current)) - Decimal(str(filtered["penalty"])) + vat["amount"]),
            "otherItems": [
                {"description": row.description, "amount": money(row.amount), "payerType": row.payer_type}
                for row in bill.extra_charges if row.payer_type == payer_type
            ],
        }
        detail["previousUnpaid"] = []
        detail["payments"] = [
            {"date": payment.payment_date.isoformat() if payment.payment_date else None,
             "amount": money(payment.amount), "receiptNo": legacy.receipt_no_for("bill", payment.id) or None,
             "reversed": bool(payment.reversed_at), "method": payment.payment_method,
             "type": payment.payment_type, "reference": payment.reference or ""}
            for payment in legacy.Payment.query.join(legacy.Billing).filter(
                legacy.Billing.unit_id == bill.unit_id,
                legacy.Payment.payer_type == payer_type,
                legacy.Payment.billing_id == bill.id,
                legacy.Payment.reversed_at.is_(None),
            ).order_by(legacy.Payment.payment_date, legacy.Payment.id).all()
        ]
        detail["payments"].extend(
            {"date": allocation.receipt.received_date.isoformat(),
             "amount": money(allocation.amount), "receiptNo": allocation.receipt.receipt_no,
             "reversed": False, "method": allocation.receipt.payment_method,
             "type": "WATER", "reference": allocation.receipt.reference or ""}
            for allocation in legacy.ReceiptAllocation.query.join(
                legacy.Receipt, legacy.ReceiptAllocation.receipt_id == legacy.Receipt.id,
            ).join(
                legacy.WaterReading, legacy.ReceiptAllocation.water_reading_id == legacy.WaterReading.id,
            ).filter(
                legacy.WaterReading.unit_id == bill.unit_id,
                legacy.ReceiptAllocation.kind == "water",
                legacy.ReceiptAllocation.payer_type == payer_type,
                legacy.Receipt.voided_at.is_(None),
                legacy.Receipt.received_date >= date.fromisoformat(f"{bill.billing_month}-01"),
                legacy.Receipt.received_date < date.fromisoformat(f"{legacy.month_shift(bill.billing_month, 1)}-01"),
            ).all()
        )
        detail["payments"].sort(key=lambda payment: (payment["date"] or "", payment["receiptNo"] or ""))
        detail["payerType"] = payer_type
        detail["partyBalance"] = money(balance)
    return detail
