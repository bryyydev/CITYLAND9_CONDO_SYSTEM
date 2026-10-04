"""/api/gate-passes, /api/certificates, /api/expenses — front-desk operations.

    GET  /api/gate-passes                        passes (resident requests and office passes), newest first
    POST /api/gate-passes                        issue a pass from the office (valid at once)
    POST /api/gate-passes/<id>/review            approve / reject a resident's request {decision, note}
    GET  /api/certificates                       issued Move In / Move Out certificates
    GET  /api/certificates/options               residential units with their owners and tenants (current and past)
    POST /api/certificates                       generate a numbered certificate (CL9-YYYY-NNNNN)
    GET  /api/certificates/<id>                  one certificate, for printing
    GET  /api/expenses?month=YYYY-MM             the month's expenses + totals by category
    POST /api/expenses                           record an expense

Permission keys are the classic screens' (backend/app/core/permissions.py): gate_pass,
gate_pass_review, move_certificate, move_certificates, expenses (Superadmin, Admin, Staff).
Rules kept from the classic screens; added: the unit must exist (gate passes used to accept any
text), text lengths are checked, and a reviewed request can't be reviewed again. Every change is
audited.
"""
import re
from datetime import date, datetime
from decimal import Decimal

from flask import Blueprint, jsonify, request
from sqlalchemy import func

from ..core.numbers import InputError, parse_amount
from ..services.soa import money
from ..utils.auth import json_error, permission_required, protect_api_blueprint, signed_in_user

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def field_errors(errors, status=400):
    response = jsonify({"error": {"status": status, "message": next(iter(errors.values())), "fields": errors}})
    response.status_code = status
    return response


def iso(value):
    return value.isoformat() if value else None


def a_date(data, key, label, errors, default=None):
    raw = str(data.get(key) or "").strip()
    if not raw:
        return default
    try:
        return date.fromisoformat(raw)
    except ValueError:
        errors[key] = f"{label} must be a date."
        return default


def text(data, key, label, errors, max_len, required=True):
    value = str(data.get(key) or "").strip()
    if required and not value:
        errors[key] = f"{label} is required."
    elif len(value) > max_len:
        errors[key] = f"{label} can't be longer than {max_len} characters."
    return value


def make_operations_blueprint(legacy):
    db, Unit, GatePass, MoveCertificate, Expense = legacy.db, legacy.Unit, legacy.GatePass, legacy.MoveCertificate, legacy.Expense
    bp = protect_api_blueprint(Blueprint("api_operations", __name__, url_prefix="/api"))

    # ---------------------------------------------------------------- gate passes
    def pass_row(p):
        return {"id": p.id, "type": p.pass_type or "Visitor", "date": iso(p.pass_date), "name": p.visitor_name or "",
                "purpose": p.purpose or "", "status": p.status or "Issued", "reviewNote": p.review_note or "",
                "requestedAt": p.requested_at.isoformat(timespec="minutes") if p.requested_at else None,
                "unitNo": p.unit_no or (p.unit.unit_no if p.unit else ""), "requestedBy": p.requested_by,
                "reviewedBy": p.reviewed_by, "source": "resident" if p.requested_by else "office"}

    @bp.get("/gate-passes")
    @permission_required("gate_pass")
    def gate_passes():
        rows = GatePass.query.order_by(GatePass.id.desc()).limit(1000).all()
        return jsonify({"passes": [pass_row(p) for p in rows], "types": list(legacy.GATE_PASS_TYPES)})

    @bp.post("/gate-passes")
    @permission_required("gate_pass")
    def issue_pass():
        data = request.get_json(silent=True) or {}
        errors = {}
        unit_no = str(data.get("unitNo") or "").strip()
        unit = Unit.query.filter(func.lower(Unit.unit_no) == unit_no.lower(), Unit.active.is_(True)).first() if unit_no else None
        if not unit:
            errors["unitNo"] = "Enter an existing unit number, e.g. TEST-501."
        kind = str(data.get("type") or "Visitor")
        if kind not in legacy.GATE_PASS_TYPES:
            errors["type"] = "Choose the pass type."
        when = a_date(data, "date", "Date", errors, default=date.today())
        name = text(data, "name", "Name", errors, 200)
        purpose = text(data, "purpose", "Purpose", errors, 300)
        if errors:
            return field_errors(errors)
        p = GatePass(pass_date=when, unit_no=unit.unit_no, unit_id=unit.id, pass_type=kind, visitor_name=name, purpose=purpose, status="Issued",
                     reviewed_by=signed_in_user().username)
        db.session.add(p)
        db.session.flush()
        legacy.audit(f"Created gate pass #{p.id} ({kind}, unit {unit.unit_no})", entity_type="gate_pass", entity_id=p.id, commit=False)
        db.session.commit()
        return jsonify({"pass": pass_row(p)}), 201

    @bp.post("/gate-passes/<int:pid>/review")
    @permission_required("gate_pass_review")
    def review_pass(pid):
        data = request.get_json(silent=True) or {}
        p = db.session.query(GatePass).filter_by(id=pid).with_for_update().first()
        decision = data.get("decision")
        if not p:
            return json_error(404, "Request not found.")
        if p.status != "Requested":
            return json_error(409, f"This request was already handled ({p.status.lower()}).")
        if decision not in ("approve", "reject"):
            return field_errors({"decision": "Approve or reject the request."})
        note = str(data.get("note") or "").strip()
        if len(note) > 300:
            return field_errors({"note": "Keep the note under 300 characters."})
        if decision == "reject" and not note:
            return field_errors({"note": "Give the resident a reason when rejecting a request."})
        p.status = "Issued" if decision == "approve" else "Rejected"
        p.reviewed_by = signed_in_user().username
        p.reviewed_at = datetime.utcnow()
        p.review_note = note or None
        legacy.audit(f"{'Approved' if decision == 'approve' else 'Rejected'} gate pass request #{p.id} ({p.pass_type}, unit {p.unit_no})",
                     entity_type="gate_pass", entity_id=p.id, reason=note or None, commit=False)
        db.session.commit()
        return jsonify({"pass": pass_row(p)})

    # ---------------------------------------------------------------- move certificates
    def cert_row(c):
        return {"id": c.id, "certificateNo": c.certificate_no, "moveType": c.move_type, "personType": c.person_type,
                "personName": c.person_name, "unitNo": c.unit.unit_no if c.unit else "", "moveDate": iso(c.move_date),
                "certificateDate": iso(c.certificate_date), "issuedBy": c.issued_by or ""}

    @bp.get("/certificates")
    @permission_required("move_certificates")
    def certificates():
        rows = MoveCertificate.query.order_by(MoveCertificate.id.desc()).limit(500).all()
        return jsonify({"certificates": [cert_row(c) for c in rows]})

    @bp.get("/certificates/<int:cid>")
    @permission_required("move_certificates")
    def certificate(cid):
        c = db.session.get(MoveCertificate, cid)
        if not c:
            return json_error(404, "Certificate not found.")
        model = legacy.Tenant if c.person_type == "Tenant" else legacy.Owner
        person = db.session.get(model, c.person_id) if c.person_id else None
        u = c.unit
        return jsonify({"certificate": cert_row(c), "corporation": legacy.setting("corporation_name", "CITYLAND 9 CONDOMINIUM CORPORATION"),
                        "address": legacy.setting("address", ""),
                        "unit": {"unitNo": u.unit_no if u else "", "type": (u.unit_type if u else "") or "",
                                 "areaSqm": float(u.area_sqm) if u and u.area_sqm is not None else None},
                        "person": {"contactNo": (person.contact_no if person else "") or "", "email": (person.email if person else "") or "",
                                   "representative": bool(getattr(person, "representative", False))}})

    @bp.get("/certificates/options")
    @permission_required("move_certificate")
    def certificate_options():
        units = []
        for u in Unit.query.filter(Unit.active.is_(True), Unit.unit_type.notin_(("PARKING", "STORAGE"))).order_by(Unit.unit_no).all():
            people = [{"id": o.id, "type": "Owner", "name": o.owner_name, "status": o.status or "Current", "moveIn": iso(o.move_in), "moveOut": iso(o.move_out)}
                      for o in legacy.Owner.query.filter_by(unit_id=u.id).order_by(legacy.Owner.id.desc())]
            people += [{"id": t.id, "type": "Tenant", "name": t.tenant_name, "status": t.status or "Current", "moveIn": iso(t.move_in), "moveOut": iso(t.move_out)}
                       for t in legacy.Tenant.query.filter_by(unit_id=u.id).order_by(legacy.Tenant.id.desc())]
            units.append({"id": u.id, "unitNo": u.unit_no, "people": people})
        return jsonify({"units": units})

    @bp.post("/certificates")
    @permission_required("move_certificate")
    def generate_certificate():
        data = request.get_json(silent=True) or {}
        errors = {}
        move_type = str(data.get("moveType") or "")
        if move_type not in ("Move In", "Move Out"):
            errors["moveType"] = "Choose Move In or Move Out."
        person_type = str(data.get("personType") or "")
        if person_type not in ("Owner", "Tenant"):
            errors["personType"] = "Choose Owner or Tenant."
        unit = db.session.get(Unit, data.get("unitId")) if isinstance(data.get("unitId"), int) else None
        if not unit or not unit.active:
            errors["unitId"] = "Choose the unit."
        model = legacy.Tenant if person_type == "Tenant" else legacy.Owner
        person = db.session.get(model, data.get("personId")) if isinstance(data.get("personId"), int) else None
        if not person or (unit and person.unit_id != unit.id):
            errors["personId"] = f"Choose the {person_type.lower() or 'person'} of this unit."
        cert_date = a_date(data, "certificateDate", "Certificate date", errors, default=date.today())
        if errors:
            return field_errors(errors)
        name = person.owner_name if person_type == "Owner" else person.tenant_name
        c = MoveCertificate(certificate_no="PENDING", move_type=move_type, person_type=person_type, unit_id=unit.id, person_id=person.id,
                            person_name=name, move_date=person.move_in if move_type == "Move In" else person.move_out,
                            certificate_date=cert_date, issued_by=signed_in_user().username)
        db.session.add(c)
        db.session.flush()
        c.certificate_no = f"CL9-{cert_date.year}-{c.id:05d}"    # same numbering as the classic screen
        legacy.audit(f"Generated {move_type} certificate {c.certificate_no} for {person_type.lower()} {person.id} in unit {unit.unit_no}",
                     entity_type="move_certificate", entity_id=c.id, commit=False)
        db.session.commit()
        return jsonify({"certificate": cert_row(c)}), 201

    # ---------------------------------------------------------------- expenses
    def expense_row(e):
        return {"id": e.id, "date": iso(e.expense_date), "category": e.category or "", "description": e.description or "", "amount": money(e.amount)}

    @bp.get("/expenses")
    @permission_required("expenses")
    def expenses():
        month = request.args.get("month", "").strip() or date.today().strftime("%Y-%m")
        if not MONTH_RE.match(month):
            return json_error(400, "month must look like 2026-10.")
        y, m = (int(x) for x in month.split("-"))
        start = date(y, m, 1)
        end = date(y + (m == 12), (m % 12) + 1, 1)
        rows = Expense.query.filter(Expense.expense_date >= start, Expense.expense_date < end).order_by(Expense.expense_date.desc(), Expense.id.desc()).all()
        by_cat = {}
        for e in rows:
            by_cat[e.category or "Other"] = by_cat.get(e.category or "Other", Decimal("0")) + Decimal(str(e.amount or 0))
        cats = [c for (c,) in db.session.query(Expense.category).filter(Expense.category.isnot(None)).distinct().all() if c]
        return jsonify({"month": month, "expenses": [expense_row(e) for e in rows],
                        "total": money(sum(by_cat.values(), Decimal("0"))), "byCategory": {k: money(v) for k, v in sorted(by_cat.items())},
                        "categories": sorted(set(cats))})

    @bp.post("/expenses")
    @permission_required("expenses")
    def add_expense():
        data = request.get_json(silent=True) or {}
        errors = {}
        when = a_date(data, "date", "Date", errors, default=date.today())
        category = text(data, "category", "Category", errors, 100)
        description = text(data, "description", "Description", errors, 300)
        try:
            amount = parse_amount(data.get("amount"), label="Amount")
        except InputError as exc:
            errors["amount"] = exc.message
            amount = None
        if errors:
            return field_errors(errors)
        e = Expense(expense_date=when, category=category, description=description, amount=amount)
        db.session.add(e)
        db.session.flush()
        legacy.audit(f"Added expense: {category} ₱{amount:,.2f} ({when.isoformat()})", entity_type="expense", entity_id=e.id,
                     details={"date": when.isoformat(), "category": category, "description": description, "amount": amount}, commit=False)
        db.session.commit()
        return jsonify({"expense": expense_row(e)}), 201

    return bp
