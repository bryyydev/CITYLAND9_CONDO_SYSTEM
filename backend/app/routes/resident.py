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
    GET  /api/resident/units/<unit_id>/receipts             payment history (official receipts)
    GET  /api/resident/units/<unit_id>/receipts/<rid>       one printable official receipt
    GET  /api/resident/units/<unit_id>/water                monthly water readings and usage
    GET  /api/resident/units/<unit_id>/gate-passes          the unit's gate pass / move requests
    POST /api/resident/units/<unit_id>/gate-passes          request a gate pass or move-in/out pass
    POST /api/resident/units/<unit_id>/gate-passes/<pid>/cancel   cancel a request not yet handled
    GET  /api/resident/units/<unit_id>/profile              the unit and the resident's contact details
    PUT  /api/resident/units/<unit_id>/profile              update own contact number / email

Amounts come from the existing billing engine (read-only), so they match the legacy SOA.
"""
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from flask import Blueprint, jsonify, request

from ..core.roles import RESIDENT
from ..services import receipts as receipt_json
from ..services import soa as soa_json
from ..utils.auth import json_error, permission_required, protect_api_blueprint, require_unit_ownership, signed_in_user

CATEGORIES = ("General", "Plumbing", "Electrical", "Aircon", "Common Area", "Other")  # same options as the legacy form
PRIORITIES = ("Normal", "Low", "High", "Urgent")
GATE_PASS_MAX_DAYS_AHEAD = 90
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


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

    # The SOA JSON is shared with Billing (app/services/soa.py): residents and the office see the same figures.
    def bill_row(bill):
        return soa_json.soa_row(legacy, bill)

    def bill_detail(bill):
        return soa_json.soa_detail(legacy, bill)

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

    @bp.get("/units/<int:unit_id>/documents")
    @unit_scoped
    def documents(unit_id):
        """Document records meant for residents: shared with all residents, or with this unit."""
        D = legacy.DocumentRecord
        rows = D.query.filter(D.active.is_(True), (D.audience == "residents") | ((D.audience == "unit") & (D.unit_id == unit_id))) \
            .order_by(D.created_at.desc()).limit(200).all()
        return jsonify({"documents": [{"id": d.id, "title": d.title, "category": d.category or "General", "description": d.description or "",
                                       "fileName": d.file_name or "", "filePath": d.file_path or "", "forUnit": d.audience == "unit",
                                       "addedAt": d.created_at.replace(tzinfo=timezone.utc).astimezone().date().isoformat() if d.created_at else None} for d in rows]})

    @bp.get("/notices")
    @permission_required("api_resident")
    def notices():
        A = legacy.Announcement
        # Staff-only announcements are not shown to residents (audience "residents", "all", or unset).
        rows = A.query.filter(A.published.is_(True), (A.audience.is_(None)) | (A.audience.in_(("residents", "all"))))             .order_by(A.publish_date.desc(), A.id.desc()).limit(50).all()
        return jsonify({"notices": [{"id": a.id, "title": a.title, "message": a.message,
                                     "publishDate": a.publish_date.isoformat() if a.publish_date else None} for a in rows]})

    # ---- Payment history (official receipts) --------------------------------------------
    # Receipt JSON is shared with Payments & ORs (app/services/receipts.py).
    receipt_row = receipt_json.receipt_row

    @bp.get("/units/<int:unit_id>/receipts")
    @unit_scoped
    def receipts(unit_id):
        if not get_unit(unit_id):
            return json_error(404, "Unit not found.")
        R = legacy.Receipt
        rows = R.query.filter_by(unit_id=unit_id).order_by(R.received_date.desc(), R.id.desc()).limit(500).all()
        return jsonify({"receipts": [receipt_row(r) for r in rows],
                        # Voided receipts are listed (marked) but are not money received.
                        "totalPaid": money(sum((Decimal(str(r.amount)) for r in rows if not r.voided_at), Decimal("0")))})

    @bp.get("/units/<int:unit_id>/receipts/<int:receipt_id>")
    @unit_scoped
    def receipt_detail(unit_id, receipt_id):
        r = legacy.db.session.get(legacy.Receipt, receipt_id)
        # Same rule as the SOA: the receipt must belong to the unit in the URL.
        if not r or r.unit_id != unit_id:
            return json_error(404, "Receipt not found.")
        return jsonify(receipt_json.receipt_detail(legacy, r))

    # ---- Water usage ----------------------------------------------------------------------
    @bp.get("/units/<int:unit_id>/water")
    @unit_scoped
    def water(unit_id):
        if not get_unit(unit_id):
            return json_error(404, "Unit not found.")
        W = legacy.WaterReading
        rows = W.query.filter_by(unit_id=unit_id).order_by(W.reading_month.desc()).limit(24).all()
        return jsonify({"readings": [{
            "month": w.reading_month, "readingDate": w.reading_date.isoformat() if w.reading_date else None,
            "previous": round(float(w.previous_reading or 0), 2), "current": round(float(w.current_reading or 0), 2),
            "usage": round(w.usage, 2), "rate": money(w.rate), "amount": money(w.bill_amount),
            "paidSeparately": bool(w.paid)} for w in rows]})

    # ---- Gate pass / move requests --------------------------------------------------------
    def pass_row(p):
        return {"id": p.id, "type": p.pass_type or "Visitor", "date": p.pass_date.isoformat() if p.pass_date else None,
                "name": p.visitor_name or "", "purpose": p.purpose or "", "status": p.status,
                "reviewNote": p.review_note or "",
                "requestedAt": p.requested_at.isoformat(timespec="minutes") if p.requested_at else None}

    @bp.get("/units/<int:unit_id>/gate-passes")
    @unit_scoped
    def gate_passes(unit_id):
        G = legacy.GatePass
        rows = G.query.filter_by(unit_id=unit_id).order_by(G.id.desc()).limit(100).all()
        return jsonify({"passes": [pass_row(p) for p in rows], "types": legacy.GATE_PASS_TYPES,
                        "maxDaysAhead": GATE_PASS_MAX_DAYS_AHEAD})

    @bp.post("/units/<int:unit_id>/gate-passes")
    @unit_scoped
    def gate_pass_create(unit_id):
        unit = get_unit(unit_id)
        if not unit:
            return json_error(404, "Unit not found.")
        data = request.get_json(silent=True) or {}
        pass_type = str(data.get("type", "")).strip()
        name = str(data.get("name", "")).strip()
        purpose = str(data.get("purpose", "")).strip()
        try:
            pass_date = date.fromisoformat(str(data.get("date", "")))
        except ValueError:
            return json_error(400, "Choose the date the pass is needed.")
        if pass_type not in legacy.GATE_PASS_TYPES:
            return json_error(400, "Choose a pass type from the list.")
        if not name or not purpose:
            return json_error(400, "Name and details are required.")
        if len(name) > 200 or len(purpose) > 300:
            return json_error(400, "Name must be 200 characters or fewer, details 300 or fewer.")
        if pass_date < date.today() or pass_date > date.today() + timedelta(days=GATE_PASS_MAX_DAYS_AHEAD):
            return json_error(400, f"The date must be between today and {GATE_PASS_MAX_DAYS_AHEAD} days from now.")
        user = signed_in_user()
        p = legacy.GatePass(pass_date=pass_date, unit_no=unit.unit_no, unit_id=unit.id, pass_type=pass_type,
                            visitor_name=name, purpose=purpose, status="Requested",
                            requested_by=user.username, requested_at=datetime.utcnow())
        legacy.db.session.add(p)
        legacy.db.session.commit()
        legacy.audit(f"Requested {pass_type} gate pass #{p.id} for unit {unit.unit_no}")
        return jsonify({"pass": pass_row(p)}), 201

    @bp.post("/units/<int:unit_id>/gate-passes/<int:pass_id>/cancel")
    @unit_scoped
    def gate_pass_cancel(unit_id, pass_id):
        p = legacy.db.session.get(legacy.GatePass, pass_id)
        if not p or p.unit_id != unit_id:
            return json_error(404, "Request not found.")
        if p.status != "Requested":
            return json_error(409, f"This request is already {p.status.lower()} and can no longer be cancelled.")
        p.status = "Cancelled"
        legacy.db.session.commit()
        legacy.audit(f"Cancelled gate pass request #{p.id}")
        return jsonify({"pass": pass_row(p)})

    # ---- Profile & contact details --------------------------------------------------------
    def linked_person(user):
        """The owner/tenant record the resident account is linked to, or None."""
        profile = getattr(user, "resident_profile", None)
        if not profile or not profile.person_id:
            return None
        model = legacy.Tenant if (profile.person_type or "").lower() == "tenant" else legacy.Owner
        person = legacy.db.session.get(model, profile.person_id)
        return person if person and person.unit_id == profile.unit_id else None

    def profile_payload(user, unit):
        profile = getattr(user, "resident_profile", None)
        person = linked_person(user) if user.role == RESIDENT else None
        return {
            "username": user.username,
            "displayName": profile.display_name if profile else user.username,
            "unit": {"unitNo": unit.unit_no, "floor": unit.floor, "type": unit.unit_type, "areaSqm": unit.area_sqm},
            "personType": (profile.person_type if profile else None) or "Owner",
            "name": (getattr(person, "tenant_name", None) or getattr(person, "owner_name", None)) if person else None,
            "contactNo": (person.contact_no or "") if person else "",
            "email": (person.email or "") if person else "",
            "moveIn": person.move_in.isoformat() if person and person.move_in else None,
            "canEdit": person is not None,
        }

    @bp.get("/units/<int:unit_id>/profile")
    @unit_scoped
    def profile_get(unit_id):
        unit = get_unit(unit_id)
        if not unit:
            return json_error(404, "Unit not found.")
        return jsonify({"profile": profile_payload(signed_in_user(), unit)})

    @bp.put("/units/<int:unit_id>/profile")
    @unit_scoped
    def profile_update(unit_id):
        unit = get_unit(unit_id)
        if not unit:
            return json_error(404, "Unit not found.")
        user = signed_in_user()
        person = linked_person(user) if user.role == RESIDENT else None
        if not person:
            return json_error(409, "Your account is not linked to an owner or tenant record, so the office "
                                   "must update your contact details. Please contact the administrator.")
        data = request.get_json(silent=True) or {}
        contact_no = str(data.get("contactNo", "")).strip()
        email = str(data.get("email", "")).strip()
        if len(contact_no) > 80 or not re.fullmatch(r"[0-9+()\-\s/]*", contact_no):
            return json_error(400, "Enter a valid contact number (digits, spaces, + ( ) - /).")
        if email and (len(email) > 160 or not EMAIL_RE.match(email)):
            return json_error(400, "Enter a valid email address.")
        person.contact_no = contact_no or None
        person.email = email or None
        legacy.db.session.commit()
        legacy.audit(f"Resident {user.username} updated own contact details (unit {unit.unit_no})")
        return jsonify({"profile": profile_payload(user, unit)})

    return bp
