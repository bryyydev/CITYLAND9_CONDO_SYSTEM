"""/api/maintenance, /api/announcements, /api/vendors, /api/documents — community screens (office side).

    GET  /api/maintenance                 tickets (all units), newest first, + vendors/units/choices for the forms
    POST /api/maintenance                 file a ticket for a unit (office)
    PUT  /api/maintenance/<id>            update status, priority, assignee, vendor, update for the resident
    GET  /api/announcements               announcements (published and drafts)
    POST /api/announcements               publish (or save as draft)
    GET  /api/vendors                     vendor directory
    POST /api/vendors                     add a vendor
    PUT  /api/vendors/<id>                edit a vendor (incl. Active / Inactive)
    GET  /api/documents                   document records (title, category, audience, file name / shared-folder path)
    POST /api/documents                   add a document record

Permission keys are the classic screens' (backend/app/core/permissions.py): maintenance and
maintenance_update, announcements, vendors, documents. Residents use their own portal API instead
(/api/resident: only their unit's tickets, published notices and documents meant for them), so these
office routes refuse resident accounts. Text lengths and choices are checked; every change is audited.
"""
from datetime import date, datetime, timezone

from flask import Blueprint, jsonify, request

from ..core.roles import RESIDENT
from ..utils.auth import json_error, permission_required, protect_api_blueprint, signed_in_user

CATEGORIES = ("General", "Plumbing", "Electrical", "Aircon", "Common Area", "Other")   # same as the resident form
PRIORITIES = ("Low", "Normal", "High", "Urgent")
STATUSES = ("Open", "In Progress", "Resolved", "Closed")
AUDIENCES = ("residents", "staff", "all")
DOC_AUDIENCES = ("admin", "residents", "unit")


def field_errors(errors, status=400):
    response = jsonify({"error": {"status": status, "message": next(iter(errors.values())), "fields": errors}})
    response.status_code = status
    return response


def local_date(utc_value):
    """created_at is stored in UTC; show the date on the office PC's clock."""
    return utc_value.replace(tzinfo=timezone.utc).astimezone().date().isoformat() if utc_value else None


def text(data, key, label, errors, max_len, required=False):
    value = str(data.get(key) or "").strip()
    if required and not value:
        errors[key] = f"{label} is required."
    elif max_len and len(value) > max_len:
        errors[key] = f"{label} can't be longer than {max_len} characters."
    return value


def make_community_blueprint(legacy):
    db, Unit, Ticket, Announcement, Vendor, Doc = legacy.db, legacy.Unit, legacy.MaintenanceTicket, legacy.Announcement, legacy.Vendor, legacy.DocumentRecord
    bp = protect_api_blueprint(Blueprint("api_community", __name__, url_prefix="/api"))

    def office_only():
        u = signed_in_user()
        return json_error(403, "Residents use the resident portal for this.") if u and u.role == RESIDENT else None

    # ---------------------------------------------------------------- maintenance
    def ticket_row(t):
        vendor = db.session.get(Vendor, t.vendor_id) if t.vendor_id else None
        return {"id": t.id, "ticketNo": t.ticket_no, "unitId": t.unit_id, "unitNo": t.unit.unit_no if t.unit else "—",
                "category": t.category or "General", "title": t.title, "description": t.description or "", "priority": t.priority or "Normal",
                "status": t.status or "Open", "resolution": t.resolution or "", "assignedTo": t.assigned_to or "",
                "vendorId": t.vendor_id, "vendor": vendor.vendor_name if vendor else "",
                "requestedAt": t.requested_at.isoformat(timespec="minutes") if t.requested_at else None,
                "source": "resident" if t.resident_profile_id else "office"}

    @bp.get("/maintenance")
    @permission_required("maintenance")
    def tickets():
        refused = office_only()
        if refused:
            return refused
        rows = Ticket.query.order_by(Ticket.id.desc()).limit(1000).all()
        return jsonify({"tickets": [ticket_row(t) for t in rows], "categories": list(CATEGORIES), "priorities": list(PRIORITIES), "statuses": list(STATUSES),
                        "vendors": [{"id": v.id, "name": v.vendor_name, "serviceType": v.service_type or ""} for v in Vendor.query.filter_by(status="Active").order_by(Vendor.vendor_name)],
                        "units": [{"id": u.id, "unitNo": u.unit_no} for u in Unit.query.filter_by(active=True).order_by(Unit.unit_no)]})

    @bp.post("/maintenance")
    @permission_required("maintenance")
    def file_ticket():
        refused = office_only()
        if refused:
            return refused
        data = request.get_json(silent=True) or {}
        errors = {}
        unit = db.session.get(Unit, data.get("unitId")) if isinstance(data.get("unitId"), int) else None
        if not unit or not unit.active:
            errors["unitId"] = "Choose the unit."
        category = str(data.get("category") or "General")
        if category not in CATEGORIES:
            errors["category"] = "Choose a category."
        priority = str(data.get("priority") or "Normal")
        if priority not in PRIORITIES:
            errors["priority"] = "Choose a priority."
        title = text(data, "title", "Title", errors, 200, required=True)
        description = text(data, "description", "Description", errors, 4000, required=True)
        if errors:
            return field_errors(errors)
        t = Ticket(ticket_no="PENDING", unit_id=unit.id, category=category, title=title, description=description, priority=priority, status="Open")
        db.session.add(t)
        db.session.flush()
        t.ticket_no = f"MT-{datetime.now():%Y%m%d}-{t.id:05d}"
        legacy.audit(f"Created maintenance ticket {t.ticket_no} for unit {unit.unit_no}", entity_type="maintenance", entity_id=t.id, commit=False)
        db.session.commit()
        return jsonify({"ticket": ticket_row(t)}), 201

    @bp.put("/maintenance/<int:tid>")
    @permission_required("maintenance_update")
    def update_ticket(tid):
        t = db.session.get(Ticket, tid)
        if not t:
            return json_error(404, "Maintenance ticket not found.")
        data = request.get_json(silent=True) or {}
        errors = {}
        status = str(data.get("status") or t.status)
        if status not in STATUSES:
            errors["status"] = "Choose a status."
        priority = str(data.get("priority") or t.priority)
        if priority not in PRIORITIES:
            errors["priority"] = "Choose a priority."
        assigned = text(data, "assignedTo", "Assigned to", errors, 120)
        resolution = text(data, "resolution", "Update for the resident", errors, 4000)
        vendor_id = data.get("vendorId")
        if vendor_id not in (None, "", 0):
            if not isinstance(vendor_id, int) or not db.session.get(Vendor, vendor_id):
                errors["vendorId"] = "Choose a vendor from the list."
        else:
            vendor_id = None
        if errors:
            return field_errors(errors)
        before = {"status": t.status, "priority": t.priority, "assignedTo": t.assigned_to, "vendorId": t.vendor_id, "resolution": t.resolution}
        t.status, t.priority, t.assigned_to, t.vendor_id, t.resolution = status, priority, assigned or None, vendor_id, resolution or None
        after = {"status": t.status, "priority": t.priority, "assignedTo": t.assigned_to, "vendorId": t.vendor_id, "resolution": t.resolution}
        changed = {k: {"before": before[k], "after": after[k]} for k in after if before[k] != after[k]}
        if changed:
            legacy.audit(f"Updated maintenance ticket {t.ticket_no}: {', '.join(changed)}", entity_type="maintenance", entity_id=t.id,
                         details=changed, commit=False)
        db.session.commit()
        return jsonify({"ticket": ticket_row(t)})

    # ---------------------------------------------------------------- announcements
    def ann_row(a):
        return {"id": a.id, "title": a.title, "message": a.message, "publishDate": a.publish_date.isoformat() if a.publish_date else None,
                "audience": a.audience or "residents", "published": bool(a.published), "createdBy": a.created_by or ""}

    @bp.get("/announcements")
    @permission_required("announcements")
    def announcements():
        refused = office_only()
        if refused:
            return refused
        rows = Announcement.query.order_by(Announcement.publish_date.desc(), Announcement.id.desc()).limit(500).all()
        return jsonify({"announcements": [ann_row(a) for a in rows]})

    @bp.post("/announcements")
    @permission_required("announcements")
    def publish():
        refused = office_only()
        if refused:
            return refused
        data = request.get_json(silent=True) or {}
        errors = {}
        title = text(data, "title", "Title", errors, 200, required=True)
        message = text(data, "message", "Message", errors, 10000, required=True)
        audience = str(data.get("audience") or "residents")
        if audience not in AUDIENCES:
            errors["audience"] = "Choose who it is for."
        if errors:
            return field_errors(errors)
        a = Announcement(title=title, message=message, audience=audience, published=data.get("published", True) is not False,
                         publish_date=date.today(), created_by=signed_in_user().username)
        db.session.add(a)
        db.session.flush()
        legacy.audit(f"Created announcement: {title}", entity_type="announcement", entity_id=a.id, commit=False)
        db.session.commit()
        return jsonify({"announcement": ann_row(a)}), 201

    # ---------------------------------------------------------------- vendors
    def vendor_row(v):
        return {"id": v.id, "name": v.vendor_name, "serviceType": v.service_type or "", "contactPerson": v.contact_person or "",
                "contactNo": v.contact_no or "", "email": v.email or "", "address": v.address or "", "notes": v.notes or "",
                "status": v.status if v.status in ("Active", "Inactive") else "Active"}

    def vendor_values(data, errors):
        v = {"vendor_name": text(data, "name", "Vendor name", errors, 200, required=True),
             "service_type": text(data, "serviceType", "Service", errors, 120),
             "contact_person": text(data, "contactPerson", "Contact person", errors, 160),
             "contact_no": text(data, "contactNo", "Phone", errors, 80),
             "email": text(data, "email", "Email", errors, 160),
             "address": text(data, "address", "Address", errors, 300),
             "notes": text(data, "notes", "Notes", errors, 2000),
             "status": str(data.get("status") or "Active")}
        if v["email"] and "@" not in v["email"]:
            errors["email"] = "Enter a valid email address."
        if v["status"] not in ("Active", "Inactive"):
            errors["status"] = "Choose Active or Inactive."
        return v

    @bp.get("/vendors")
    @permission_required("vendors")
    def vendors():
        return jsonify({"vendors": [vendor_row(v) for v in Vendor.query.order_by(Vendor.vendor_name).all()]})

    @bp.post("/vendors")
    @permission_required("vendors")
    def add_vendor():
        errors = {}
        values = vendor_values(request.get_json(silent=True) or {}, errors)
        if errors:
            return field_errors(errors)
        v = Vendor(**values)
        db.session.add(v)
        db.session.flush()
        legacy.audit(f"Created vendor: {v.vendor_name}", entity_type="vendor", entity_id=v.id, commit=False)
        db.session.commit()
        return jsonify({"vendor": vendor_row(v)}), 201

    @bp.put("/vendors/<int:vid>")
    @permission_required("vendors")
    def edit_vendor(vid):
        v = db.session.get(Vendor, vid)
        if not v:
            return json_error(404, "Vendor not found.")
        errors = {}
        values = vendor_values(request.get_json(silent=True) or {}, errors)
        if errors:
            return field_errors(errors)
        for k, val in values.items():
            setattr(v, k, val)
        legacy.audit(f"Updated vendor: {v.vendor_name} ({v.status})", entity_type="vendor", entity_id=v.id, commit=False)
        db.session.commit()
        return jsonify({"vendor": vendor_row(v)})

    # ---------------------------------------------------------------- documents
    def doc_row(d):
        return {"id": d.id, "title": d.title, "category": d.category or "General", "description": d.description or "",
                "audience": d.audience if d.audience in DOC_AUDIENCES else "admin", "unitId": d.unit_id,
                "unitNo": d.unit.unit_no if d.unit else None, "fileName": d.file_name or "", "filePath": d.file_path or "",
                "addedAt": local_date(d.created_at), "addedBy": d.uploaded_by or ""}

    @bp.get("/documents")
    @permission_required("documents")
    def documents():
        rows = Doc.query.filter_by(active=True).order_by(Doc.created_at.desc()).all()
        return jsonify({"documents": [doc_row(d) for d in rows],
                        "units": [{"id": u.id, "unitNo": u.unit_no} for u in Unit.query.filter_by(active=True).order_by(Unit.unit_no)]})

    @bp.post("/documents")
    @permission_required("documents")
    def add_document():
        data = request.get_json(silent=True) or {}
        errors = {}
        title = text(data, "title", "Title", errors, 200, required=True)
        category = text(data, "category", "Category", errors, 100) or "General"
        description = text(data, "description", "Description", errors, 4000)
        file_name = text(data, "fileName", "File name", errors, 300)
        file_path = text(data, "filePath", "Where the file is kept", errors, 500)
        audience = str(data.get("audience") or "admin")
        unit = None
        if audience not in DOC_AUDIENCES:
            errors["audience"] = "Choose who can see it."
        elif audience == "unit":
            unit = db.session.get(Unit, data.get("unitId")) if isinstance(data.get("unitId"), int) else None
            if not unit:
                errors["unitId"] = "Choose the unit."
        if errors:
            return field_errors(errors)
        d = Doc(title=title, category=category, description=description, file_name=file_name, file_path=file_path, audience=audience,
                unit_id=unit.id if unit else None, uploaded_by=signed_in_user().username)
        db.session.add(d)
        db.session.flush()
        legacy.audit(f"Added document record: {title}", entity_type="document", entity_id=d.id, commit=False)
        db.session.commit()
        return jsonify({"document": doc_row(d)}), 201

    return bp
