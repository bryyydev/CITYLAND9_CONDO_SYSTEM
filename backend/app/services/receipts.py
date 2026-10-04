"""Official receipts as JSON, shared by the resident portal (/api/resident) and Payments & ORs
(/api/receipts), so a resident and the office see the same receipt. Read-only."""
from .soa import money


def allocation_label(a):
    """(label, month) of what one receipt line paid for."""
    if a.kind == "bill":
        return f"Statement of Account — {a.payment.billing.billing_month}", a.payment.billing.billing_month
    if a.kind == "water":
        return f"Water — {a.water_reading.reading_month}", a.water_reading.reading_month
    return f"Advance condo dues from {a.advance_payment.start_month}", a.advance_payment.start_month


def receipt_row(r):
    return {"id": r.id, "receiptNo": r.receipt_no, "voided": bool(r.voided_at), "voidReason": r.void_reason or "",
            "date": r.received_date.isoformat(), "amount": money(r.amount), "method": r.payment_method,
            "reference": r.reference or "",
            "items": [{"kind": a.kind, "label": allocation_label(a)[0], "month": allocation_label(a)[1],
                       "amount": money(a.amount)} for a in r.allocations]}


def received_from(legacy, r):
    contact = legacy.current_contact_for_unit(r.unit)
    return getattr(contact, "tenant_name", None) or getattr(contact, "owner_name", None) or ""


def receipt_detail(legacy, r):
    return {
        "corporation": legacy.setting("corporation_name", "CITYLAND 9 CONDOMINIUM CORPORATION"),
        "address": legacy.setting("address", ""),
        "unit": {"id": r.unit.id, "unitNo": r.unit.unit_no},
        "receivedFrom": received_from(legacy, r),
        "receipt": {**receipt_row(r), "remarks": r.remarks or "", "receivedBy": r.received_by or "",
                    "backfilled": r.source == "backfill"},
    }
