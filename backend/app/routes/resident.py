"""/api/resident — the Resident Portal API.

Every unit-scoped route carries the unit id in the URL and is protected twice:
  @permission_required("api_resident")   who may use the portal API at all
  @require_unit_ownership("unit_id")     a RESIDENT may only open THEIR unit (bug D2 / IDOR)

    GET  /api/resident/units/<unit_id>/summary              unit, outstanding balance, latest SOA
    GET  /api/resident/units/<unit_id>/soa                  all statements of account
    GET  /api/resident/units/<unit_id>/soa/<bill_id>        one SOA with charges and payments
    GET  /api/resident/units/<unit_id>/maintenance          the unit's maintenance tickets
    POST /api/resident/units/<unit_id>/maintenance          file a maintenance request
    GET  /api/resident/notices                              published announcements

Amounts come from the existing billing engine (read-only), so they match the legacy SOA.
"""
from datetime import datetime
from decimal import Decimal

from flask import Blueprint, jsonify, request

from ..utils.auth import json_error, permission_required, protect_api_blueprint, require_unit_ownership, signed_in_user

CATEGORIES = ("General", "Plumbing", "Electrical", "Aircon", "Common Area", "Other")  # same options as the legacy form
PRIORITIES = ("Normal", "Low", "High", "Urgent")


def money(value):
    return str(Decimal(str(value or 0)).quantize(Decimal("0.01")))


def make_resident_blueprint(legacy):
    """`legacy` is the legacy_app module: models, billing engine and audit() are reused as-is."""
    bp = protect_api_blueprint(Blueprint("api_resident", __name__, url_prefix="/api/resident"))
    guard = [permission_required("api_resident"), require_unit_ownership("unit_id")]

    def unit_scoped(fn):
        for decorator in reversed(guard):
            fn = decorator(fn)
        return fn

    def get_unit(unit_id):
        unit = legacy.db.session.get(legacy.Unit, unit_id)
        return unit if unit and unit.active else None

    def bill_row(bill):
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

    def bill_detail(bill):
        calc = legacy._bill_calc(bill)
        reading = calc.get("reading")
        return {
            **bill_row(bill),
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
                "penalty": money(calc["penalty"]),
                "previousBalance": money(calc["previous"]),
                "advanceApplied": money(calc["advance"]),
                "currentCharges": money(calc["current"]),
            },
            "note": bill.soa_note or "",
            "payments": [
                {"date": p.payment_date.isoformat() if p.payment_date else None, "amount": money(p.amount),
                 "method": p.payment_method, "type": p.payment_type, "reference": p.reference or ""}
                for p in sorted(bill.payments, key=lambda p: (p.payment_date or datetime.min.date(), p.id))
            ],
        }

    def ticket_row(t):
        return {"ticketNo": t.ticket_no, "category": t.category, "title": t.title, "description": t.description,
                "priority": t.priority, "status": t.status, "resolution": t.resolution or "",
                "requestedAt": t.requested_at.isoformat(timespec="minutes") if t.requested_at else None}

    @bp.get("/units/<int:unit_id>/summary")
    @unit_scoped
    def summary(unit_id):
        unit = get_unit(unit_id)
        if not unit:
            return json_error(404, "Unit not found.")
        bills = legacy.Billing.query.filter_by(unit_id=unit_id).order_by(legacy.Billing.billing_month.desc(), legacy.Billing.id.desc()).all()
        latest = bills[0] if bills else None
        profile = getattr(signed_in_user(), "resident_profile", None)
        open_tickets = legacy.MaintenanceTicket.query.filter(
            legacy.MaintenanceTicket.unit_id == unit_id, legacy.MaintenanceTicket.status != "Closed").count()
        return jsonify({
            "unit": {"id": unit.id, "unitNo": unit.unit_no, "floor": unit.floor, "type": unit.unit_type, "status": unit.status},
            "residentName": profile.display_name if profile else None,
            # Each SOA already carries unpaid earlier months as "previous balance", so the
            # amount owed is the latest SOA's balance (adding all SOAs would count months twice).
            "outstandingBalance": bill_row(latest)["balance"] if latest else "0.00",
            "latestSoa": bill_row(latest) if latest else None,
            "openTickets": open_tickets,
        })

    @bp.get("/units/<int:unit_id>/soa")
    @unit_scoped
    def soa_list(unit_id):
        if not get_unit(unit_id):
            return json_error(404, "Unit not found.")
        bills = legacy.Billing.query.filter_by(unit_id=unit_id).order_by(legacy.Billing.billing_month.desc(), legacy.Billing.id.desc()).all()
        return jsonify({"statements": [bill_row(b) for b in bills]})

    @bp.get("/units/<int:unit_id>/soa/<int:bill_id>")
    @unit_scoped
    def soa_detail(unit_id, bill_id):
        bill = legacy.db.session.get(legacy.Billing, bill_id)
        # The bill must belong to the unit in the URL, so a resident can't read
        # another unit's SOA by changing only the bill id.
        if not bill or bill.unit_id != unit_id:
            return json_error(404, "Statement not found.")
        unit = bill.unit
        return jsonify({"unit": {"id": unit.id, "unitNo": unit.unit_no, "type": unit.unit_type},
                        "corporation": legacy.setting("corporation_name", "CITYLAND 9 CONDOMINIUM CORPORATION"),
                        "statement": bill_detail(bill)})

    @bp.get("/units/<int:unit_id>/maintenance")
    @unit_scoped
    def maintenance_list(unit_id):
        rows = legacy.MaintenanceTicket.query.filter_by(unit_id=unit_id).order_by(legacy.MaintenanceTicket.id.desc()).limit(100).all()
        return jsonify({"tickets": [ticket_row(t) for t in rows], "categories": CATEGORIES, "priorities": PRIORITIES})

    @bp.post("/units/<int:unit_id>/maintenance")
    @unit_scoped
    def maintenance_create(unit_id):
        if not get_unit(unit_id):
            return json_error(404, "Unit not found.")
        data = request.get_json(silent=True) or {}
        title = str(data.get("title", "")).strip()
        description = str(data.get("description", "")).strip()
        category = str(data.get("category", "General")).strip() or "General"
        priority = str(data.get("priority", "Normal")).strip() or "Normal"
        if not title or not description:
            return json_error(400, "Title and description are required.")
        if len(title) > 200:
            return json_error(400, "Title must be 200 characters or fewer.")
        if category not in CATEGORIES or priority not in PRIORITIES:
            return json_error(400, "Choose a category and priority from the list.")
        profile = getattr(signed_in_user(), "resident_profile", None)
        M = legacy.MaintenanceTicket
        # Same ticket number format as the legacy Maintenance page.
        ticket = M(ticket_no=f"MT-{datetime.now().strftime('%Y%m%d%H%M%S')}-{M.query.count() + 1:04d}",
                   unit_id=unit_id, resident_profile_id=profile.id if profile else None,
                   category=category, title=title, description=description, priority=priority)
        legacy.db.session.add(ticket)
        legacy.db.session.commit()
        legacy.audit(f"Created maintenance ticket {ticket.ticket_no}")
        return jsonify({"ticket": ticket_row(ticket)}), 201

    @bp.get("/notices")
    @permission_required("api_resident")
    def notices():
        A = legacy.Announcement
        rows = A.query.filter_by(published=True).order_by(A.publish_date.desc(), A.id.desc()).limit(50).all()
        return jsonify({"notices": [{"id": a.id, "title": a.title, "message": a.message,
                                     "publishDate": a.publish_date.isoformat() if a.publish_date else None} for a in rows]})

    return bp
