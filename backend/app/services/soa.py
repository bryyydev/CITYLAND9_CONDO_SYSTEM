"""Statement of account (SOA) as JSON, shared by the resident portal (/api/resident) and Billing
(/api/billing) so a resident and the office always see the same figures.

Amounts come from the billing engine (legacy._bill_calc), read-only: building an SOA never
changes a bill.
"""
from datetime import datetime
from decimal import Decimal


def money(value):
    return str(Decimal(str(value or 0)).quantize(Decimal("0.01")))


def soa_row(legacy, bill):
    calc = legacy._bill_calc(bill)
    return {
        "id": bill.id,
        "billingMonth": bill.billing_month,
        "dueDate": bill.due_date.isoformat() if bill.due_date else None,
        "status": legacy.bill_status(bill),
        "total": money(calc["total"]),
        "amountPaid": money(bill.amount_paid),
        "balance": money(max(calc["balance"], Decimal("0"))),
    }


def soa_detail(legacy, bill):
    calc = legacy._bill_calc(bill)
    reading = calc.get("reading")
    return {
        **soa_row(legacy, bill),
        "charges": {
            "condoDues": money(calc["condo"]),
            "parking": money(calc["parking"]),
            "storage": money(bill.storage_dues),
            "storageIncluded": legacy.storage_charged(bill),   # D13: only from the cut-off month
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
        "note": bill.soa_note or "",
        "payments": [
            {"date": p.payment_date.isoformat() if p.payment_date else None, "amount": money(p.amount),
             "receiptNo": legacy.receipt_no_for("bill", p.id) or None,
             "reversed": bool(getattr(p, "reversed_at", None)),
             "method": p.payment_method, "type": p.payment_type, "reference": p.reference or ""}
            for p in sorted(bill.payments, key=lambda p: (p.payment_date or datetime.min.date(), p.id))
        ],
    }
