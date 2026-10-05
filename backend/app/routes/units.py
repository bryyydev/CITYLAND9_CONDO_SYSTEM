"""/api/units — Units Directory: units, owners, tenants, parking and storage.

    GET  /api/units?q=&kind=&floor=&status=&past=&page=&perPage=   list (kind: residential | PARKING | STORAGE;
                                                             past=1: the search also finds past owners/tenants)
    GET  /api/units/options                                  parking/storage units for assignment, unit types, floors
    POST /api/units                                          create a unit (optionally with a first owner and tenant)
    GET  /api/units/<id>                                     one unit: dues, owners, tenants, recent bills
    PUT  /api/units/<id>                                     edit a unit (the unit number can't change)
    POST /api/units/<id>/owners            PUT /api/units/<id>/owners/<oid>
    POST /api/units/<id>/tenants           PUT /api/units/<id>/tenants/<tid>

Permission keys are the classic screen's (backend/app/core/permissions.py; Superadmin and Admin):
units (list, create), unit_detail, edit_unit, add_owner, edit_owner, add_tenant, edit_tenant.
The business rules are the classic screen's, reused from legacy_app: rate_for_type (auto rate),
unit_dues / parking_dues / storage_dues / asset_unit_charge (the amounts billing charges), the
"representative tenant" rule, Past -> move-out date today, Current -> no move-out date.
Added checks: field lengths and formats, a parking/storage unit must be an active unit of that type
and can't be assigned to two units at once, a unit can't be assigned to itself.
Nothing here writes bills: an issued bill keeps its stored dues (billing's own rules apply).
Marking an owner/tenant Past ends their resident portal access at their next request
(legacy.resident_access_problem). Every change is saved together with its audit entry.
"""
import re
from datetime import date
from decimal import Decimal

from flask import Blueprint, jsonify, request
from sqlalchemy import func, or_
from sqlalchemy.orm import selectinload

from ..core.numbers import InputError, parse_decimal
from ..utils.auth import json_error, permission_required, protect_api_blueprint

UNIT_TYPES = ("STUDIO TYPE", "1 BEDROOM", "2 BEDROOM", "3 BEDROOM", "PARKING", "STORAGE")  # the classic form's list
ASSETS = ("PARKING", "STORAGE")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PER_PAGE_MAX = 100


def money(value):
    return str(Decimal(str(value or 0)).quantize(Decimal("0.01")))


def rate_text(value):
    """A rate as typed: 50, 85.15, 0.5 (no float noise, no trailing zeros)."""
    d = Decimal(str(value or 0)).normalize()
    return format(d, "f")


def field_errors(errors, status=400):
    response = jsonify({"error": {"status": status, "message": next(iter(errors.values())), "fields": errors}})
    response.status_code = status
    return response


def make_units_blueprint(legacy):
    db, Unit, Owner, Tenant, Billing = legacy.db, legacy.Unit, legacy.Owner, legacy.Tenant, legacy.Billing
    bp = protect_api_blueprint(Blueprint("api_units", __name__, url_prefix="/api/units"))

    # ---------------------------------------------------------------- reading
    def effective_rate(u):
        return legacy.dec(u.unit_rate_per_sqm) or legacy.dec(legacy.rate_for_type(u.unit_type))

    def monthly(u):
        """What billing charges for this unit each month (classic engine functions)."""
        if u.unit_type in ASSETS:
            key = "parking_rate_per_sqm" if u.unit_type == "PARKING" else "storage_rate_per_sqm"
            return {"condo": money(legacy.asset_unit_charge(u, key)), "parking": "0.00", "storage": "0.00"}
        return {"condo": money(legacy.unit_dues(u)), "parking": money(legacy.parking_dues(u)), "storage": money(legacy.storage_dues(u))}

    def brief(u):
        return {"id": u.id, "unitNo": u.unit_no} if u else None

    def row(u, assigned_to=None, latest=None):
        parking = legacy.assigned_asset(u, "assigned_parking_unit_id") if u.include_parking else None
        storage = legacy.assigned_asset(u, "assigned_storage_unit_id") if u.include_storage else None
        dues = monthly(u)
        return {
            "id": u.id, "unitNo": u.unit_no, "floor": u.floor or "", "type": u.unit_type or "",
            "areaSqm": float(u.area_sqm or 0), "ratePerSqm": rate_text(u.unit_rate_per_sqm),
            "effectiveRatePerSqm": rate_text(effective_rate(u)), "autoRate": bool(u.auto_rate),
            "duesMode": u.dues_mode or "per_sqm", "manualMonthlyDues": money(u.manual_monthly_dues),
            "occupancy": u.occupancy_type or "Owner", "status": u.status or "Vacant", "active": bool(u.active),
            "ownerName": u.owner_name, "tenantName": u.tenant_name, "contactNo": u.contact_no, "email": u.email,
            "parkingUnit": brief(parking), "storageUnit": brief(storage), "assignedTo": brief(assigned_to),
            "dues": dues,
            "monthlyTotal": money(sum(Decimal(v) for v in dues.values())),
            "zeroDues": Decimal(dues["condo"]) == 0 and float(u.area_sqm or 0) > 0,
            "latestBill": {"id": latest.id, "month": latest.billing_month, "status": legacy.bill_status(latest)} if latest else None,
        }

    def assigned_map():
        out = {}
        for u in Unit.query.filter(or_(Unit.assigned_parking_unit_id.isnot(None), Unit.assigned_storage_unit_id.isnot(None))).all():
            if u.include_parking and u.assigned_parking_unit_id:
                out[u.assigned_parking_unit_id] = u
            if u.include_storage and u.assigned_storage_unit_id:
                out[u.assigned_storage_unit_id] = u
        return out

    @bp.get("")
    @permission_required("units")
    def list_units():
        q = request.args.get("q", "").strip()
        kind = request.args.get("kind", "residential").strip()
        floor = request.args.get("floor", "").strip()
        status = request.args.get("status", "").strip()
        try:
            page = max(int(request.args.get("page", 1)), 1)
            per_page = min(max(int(request.args.get("perPage", 20)), 1), PER_PAGE_MAX)
        except ValueError:
            return json_error(400, "page and perPage must be numbers.")
        if kind not in ("residential",) + ASSETS:
            return json_error(400, "kind must be residential, PARKING or STORAGE.")
        base = Unit.query.filter(Unit.active.is_(True))
        counts = {
            "residential": base.filter(or_(Unit.unit_type.is_(None), Unit.unit_type.notin_(ASSETS))).count(),
            "PARKING": base.filter(Unit.unit_type == "PARKING").count(),
            "STORAGE": base.filter(Unit.unit_type == "STORAGE").count(),
        }
        query = base.options(selectinload(Unit.owners), selectinload(Unit.tenants),
                             selectinload(Unit.assigned_parking_unit), selectinload(Unit.assigned_storage_unit))
        query = query.filter(Unit.unit_type == kind) if kind in ASSETS else \
            query.filter(or_(Unit.unit_type.is_(None), Unit.unit_type.notin_(ASSETS)))
        if q:
            like = f"%{q}%"
            if request.args.get("past") in ("1", "true"):
                # Also past owners/tenants and contact details (the old Tenants list could do this).
                owner_match = Owner.owner_name.ilike(like) | Owner.contact_no.ilike(like) | Owner.email.ilike(like)
                tenant_match = Tenant.tenant_name.ilike(like) | Tenant.contact_no.ilike(like) | Tenant.email.ilike(like)
            else:
                owner_match = (Owner.status == "Current") & Owner.owner_name.ilike(like)
                tenant_match = (Tenant.status == "Current") & Tenant.tenant_name.ilike(like)
            query = query.filter(or_(Unit.unit_no.ilike(like), Unit.owners.any(owner_match), Unit.tenants.any(tenant_match)))
        if floor:
            query = query.filter(Unit.floor == floor)
        if status in ("Occupied", "Vacant"):
            query = query.filter(Unit.status == status)
        total = query.count()
        units = query.order_by(Unit.unit_no).offset((page - 1) * per_page).limit(per_page).all()
        latest = {}
        ids = [u.id for u in units]
        if ids:
            for b in Billing.query.filter(Billing.unit_id.in_(ids)).order_by(Billing.billing_month.desc(), Billing.id.desc()).all():
                latest.setdefault(b.unit_id, b)
        assigned = assigned_map() if kind in ASSETS else {}
        return jsonify({"units": [row(u, assigned.get(u.id), latest.get(u.id)) for u in units],
                        "total": total, "page": page, "perPage": per_page, "counts": counts})

    @bp.get("/options")
    @permission_required("units")
    def options():
        assigned = assigned_map()
        assets = {k: [{"id": a.id, "unitNo": a.unit_no, "floor": a.floor or "", "assignedTo": brief(assigned.get(a.id))}
                      for a in Unit.query.filter_by(active=True, unit_type=k).order_by(Unit.unit_no).all()] for k in ASSETS}
        floors = sorted({f for (f,) in db.session.query(Unit.floor).filter(Unit.floor.isnot(None)).distinct().all() if f})
        return jsonify({"parking": assets["PARKING"], "storage": assets["STORAGE"], "unitTypes": list(UNIT_TYPES), "floors": floors,
                        "typeRates": {t: rate_text(legacy.rate_for_type(t)) for t in UNIT_TYPES if t not in ASSETS}})

    def person(p, kind):
        return {
            "id": p.id, "kind": kind,
            "name": p.owner_name if kind == "Owner" else p.tenant_name,
            "contactNo": p.contact_no or "", "email": p.email or "",
            "moveIn": p.move_in.isoformat() if p.move_in else None,
            "moveOut": p.move_out.isoformat() if p.move_out else None,
            "status": p.status or "Current", "notes": p.notes or "",
            "receiveSoaEmail": bool(p.receive_soa_email), "includeInSoa": p.include_in_soa is not False,
            "representative": bool(getattr(p, "representative", False)),
        }

    def detail(u):
        assigned_to = assigned_map().get(u.id) if u.unit_type in ASSETS else None
        latest = Billing.query.filter_by(unit_id=u.id).order_by(Billing.billing_month.desc(), Billing.id.desc()).first()
        bills = []
        for b in Billing.query.filter_by(unit_id=u.id).order_by(Billing.billing_month.desc(), Billing.id.desc()).limit(24).all():
            calc = legacy._bill_calc(b)
            bills.append({"id": b.id, "month": b.billing_month, "dueDate": b.due_date.isoformat() if b.due_date else None,
                          "total": money(calc["total"]), "paid": money(b.amount_paid),
                          "balance": money(calc["balance"]), "status": legacy.bill_status(b)})
        owners = Owner.query.filter_by(unit_id=u.id).order_by(Owner.status.desc(), Owner.id.desc()).all()
        tenants = Tenant.query.filter_by(unit_id=u.id).order_by(Tenant.status.desc(), Tenant.id.desc()).all()
        return {**row(u, assigned_to, latest), "owners": [person(o, "Owner") for o in owners],
                "tenants": [person(t, "Tenant") for t in tenants], "bills": bills}

    def get_unit(uid):
        u = db.session.get(Unit, uid)
        return u if u else None

    @bp.get("/<int:uid>")
    @permission_required("unit_detail")
    def get_one(uid):
        u = get_unit(uid)
        if not u:
            return json_error(404, "Unit not found.")
        return jsonify({"unit": detail(u)})

    # ---------------------------------------------------------------- validation helpers
    def text(data, key, label, errors, max_len, required=False):
        value = str(data.get(key) or "").strip()
        if required and not value:
            errors[key] = f"{label} is required."
        elif len(value) > max_len:
            errors[key] = f"{label} can't be longer than {max_len} characters."
        return value

    def a_date(data, key, label, errors):
        raw = str(data.get(key) or "").strip()
        if not raw:
            return None
        if not DATE_RE.match(raw):
            errors[key] = f"{label} must be a date."
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError:
            errors[key] = f"{label} is not a real date."
            return None

    def number(data, key, label, errors, places, allow_empty=True, default=None):
        try:
            return parse_decimal(data.get(key), label=label, places=places, minimum=Decimal("0"),
                                 maximum=Decimal("100000"), allow_empty=allow_empty, default=default)
        except InputError as exc:
            errors[key] = exc.message
            return default

    def asset_choice(data, key, kind, errors, unit_id=None):
        raw = data.get(key)
        if raw in (None, "", 0):
            return None
        try:
            asset = db.session.get(Unit, int(raw))
        except (TypeError, ValueError):
            asset = None
        label = "parking" if kind == "PARKING" else "storage"
        if not asset or not asset.active or asset.unit_type != kind:
            errors[key] = f"Choose an active {label} unit."
            return None
        if unit_id is not None and asset.id == unit_id:
            errors[key] = "A unit can't be assigned to itself."
            return None
        field = Unit.assigned_parking_unit_id if kind == "PARKING" else Unit.assigned_storage_unit_id
        flag = Unit.include_parking if kind == "PARKING" else Unit.include_storage
        other = Unit.query.filter(field == asset.id, flag.is_(True), Unit.id != (unit_id or 0)).first()
        if other:
            errors[key] = f"{asset.unit_no} is already assigned to unit {other.unit_no}."
            return None
        return asset

    def unit_fields(data, errors, existing=None):
        """Validated unit values (the classic form's fields)."""
        utype = str(data.get("type") or "").strip()
        if utype not in UNIT_TYPES and not (existing is not None and utype == (existing.unit_type or "")):
            errors["type"] = "Choose a unit type."
        values = {
            "floor": text(data, "floor", "Floor", errors, 20),
            "unit_type": utype,
            "area_sqm": number(data, "areaSqm", "Area (sqm)", errors, 2, default=Decimal("0")),
            "rate": number(data, "ratePerSqm", "Rate per sqm", errors, 4, default=None),
            "auto_rate": data.get("autoRate") is True,
            "dues_mode": str(data.get("duesMode") or "per_sqm"),
            "occupancy_type": str(data.get("occupancy") or "Owner"),
            "status": str(data.get("status") or "Vacant"),
        }
        if values["dues_mode"] not in ("per_sqm", "manual"):
            errors["duesMode"] = "Choose per sqm or manual."
        if values["occupancy_type"] not in ("Owner", "Tenant"):
            errors["occupancy"] = "Choose Owner or Tenant."
        if values["status"] not in ("Occupied", "Vacant"):
            errors["status"] = "Choose Occupied or Vacant."
        try:
            values["manual"] = legacy.round_money(parse_decimal(data.get("manualMonthlyDues"), label="Manual monthly dues", places=2,
                                                                minimum=Decimal("0"), maximum=Decimal("10000000"),
                                                                allow_empty=True, default=Decimal("0")))
        except InputError as exc:
            errors["manualMonthlyDues"] = exc.message
        uid = existing.id if existing is not None else None
        values["parking"] = asset_choice(data, "parkingUnitId", "PARKING", errors, uid)
        values["storage"] = asset_choice(data, "storageUnitId", "STORAGE", errors, uid)
        return values

    def apply_unit(u, v, creating):
        u.floor, u.unit_type = v["floor"], v["unit_type"]
        u.area_sqm = float(v["area_sqm"] or 0)
        # Same as the classic form: an empty rate means "the rate for this type".
        u.unit_rate_per_sqm = float(v["rate"] or legacy.rate_for_type(v["unit_type"]))
        u.occupancy_type, u.status = v["occupancy_type"], v["status"]
        u.include_parking, u.include_storage = v["parking"] is not None, v["storage"] is not None
        u.assigned_parking_unit_id = v["parking"].id if v["parking"] else None
        u.assigned_storage_unit_id = v["storage"].id if v["storage"] else None
        u.auto_rate, u.dues_mode, u.manual_monthly_dues = v["auto_rate"], v["dues_mode"], v["manual"]
        if u.auto_rate and (creating or u.dues_mode == "per_sqm"):
            u.unit_rate_per_sqm = legacy.rate_for_type(u.unit_type)

    def snapshot(u):
        return {"floor": u.floor, "type": u.unit_type, "areaSqm": float(u.area_sqm or 0), "ratePerSqm": float(u.unit_rate_per_sqm or 0),
                "autoRate": bool(u.auto_rate), "duesMode": u.dues_mode, "manualMonthlyDues": money(u.manual_monthly_dues),
                "occupancy": u.occupancy_type, "status": u.status, "parkingUnitId": u.assigned_parking_unit_id if u.include_parking else None,
                "storageUnitId": u.assigned_storage_unit_id if u.include_storage else None}

    def person_fields(data, errors, kind, existing=None):
        name_max, contact_max, email_max = (200, 100, 200) if kind == "Owner" else (200, 80, 160)
        v = {
            "name": text(data, "name", f"{kind} name", errors, name_max, required=True),
            "contact_no": text(data, "contactNo", "Contact number", errors, contact_max),
            "email": text(data, "email", "Email", errors, email_max),
            "move_in": a_date(data, "moveIn", "Move-in date", errors),
            "move_out": a_date(data, "moveOut", "Move-out date", errors),
            "notes": text(data, "notes", "Notes", errors, 500 if kind == "Tenant" else 2000),
            "receive_soa_email": data.get("receiveSoaEmail") is True,
            "include_in_soa": data.get("includeInSoa") is not False,
            "representative": data.get("representative") is True,
            "status": str(data.get("status") or (existing.status if existing is not None else "Current")),
        }
        if v["email"] and not EMAIL_RE.match(v["email"]):
            errors["email"] = "Enter a valid email address."
        if v["receive_soa_email"] and not v["email"]:
            errors["email"] = "Enter an email address to send SOAs to."
        if v["status"] not in ("Current", "Past"):
            errors["status"] = "Choose Current or Past."
        if v["move_in"] and v["move_out"] and v["move_out"] < v["move_in"]:
            errors["moveOut"] = "Move-out can't be before move-in."
        return v

    # ---------------------------------------------------------------- units: create / edit
    @bp.post("")
    @permission_required("units")
    def create_unit():
        data = request.get_json(silent=True) or {}
        errors = {}
        unit_no = text(data, "unitNo", "Unit number", errors, 40, required=True)
        if unit_no and Unit.query.filter(func.lower(Unit.unit_no) == unit_no.lower()).first():
            errors["unitNo"] = "That unit number already exists."
        v = unit_fields(data, errors)
        owner_data, tenant_data = data.get("owner") or None, data.get("tenant") or None
        ov = tv = None
        if isinstance(owner_data, dict) and str(owner_data.get("name") or "").strip():
            oerr = {}
            ov = person_fields(owner_data, oerr, "Owner")
            errors.update({f"owner.{k}": m for k, m in oerr.items()})
        if isinstance(tenant_data, dict) and str(tenant_data.get("name") or "").strip():
            terr = {}
            tv = person_fields(tenant_data, terr, "Tenant")
            errors.update({f"tenant.{k}": m for k, m in terr.items()})
        if errors:
            return field_errors(errors)
        u = Unit(unit_no=unit_no, active=True)
        apply_unit(u, v, creating=True)
        db.session.add(u)
        db.session.flush()
        if ov:
            db.session.add(Owner(unit_id=u.id, owner_name=ov["name"], contact_no=ov["contact_no"], email=ov["email"],
                                 move_in=ov["move_in"], status="Current", receive_soa_email=ov["receive_soa_email"],
                                 include_in_soa=ov["include_in_soa"]))
        if tv:
            db.session.add(Tenant(unit_id=u.id, tenant_name=tv["name"], contact_no=tv["contact_no"], email=tv["email"],
                                  move_in=tv["move_in"], status="Current", receive_soa_email=tv["receive_soa_email"],
                                  include_in_soa=tv["include_in_soa"], representative=True))
        legacy.audit(f"Created unit {unit_no}", entity_type="unit", entity_id=u.id, commit=False,
                     details={"after": snapshot(u), "owner": ov["name"] if ov else None, "tenant": tv["name"] if tv else None})
        db.session.commit()
        return jsonify({"unit": detail(u)}), 201

    @bp.put("/<int:uid>")
    @permission_required("edit_unit")
    def update_unit(uid):
        u = get_unit(uid)
        if not u:
            return json_error(404, "Unit not found.")
        data = request.get_json(silent=True) or {}
        errors = {}
        v = unit_fields(data, errors, existing=u)
        if errors:
            return field_errors(errors)
        before = snapshot(u)
        apply_unit(u, v, creating=False)
        after = snapshot(u)
        if before == after:
            db.session.rollback()
            return jsonify({"unit": detail(u), "changed": False})
        changed = {k: {"before": before[k], "after": after[k]} for k in after if before[k] != after[k]}
        legacy.audit(f"Updated unit {u.unit_no}: {', '.join(changed)}", entity_type="unit", entity_id=u.id, commit=False, details=changed)
        db.session.commit()
        return jsonify({"unit": detail(u), "changed": True})

    # ---------------------------------------------------------------- owners / tenants
    def person_snapshot(p, kind):
        s = person(p, kind)
        return {k: s[k] for k in ("name", "contactNo", "email", "moveIn", "moveOut", "status", "receiveSoaEmail", "includeInSoa", "representative", "notes")}

    @bp.post("/<int:uid>/owners")
    @permission_required("add_owner")
    def add_owner(uid):
        u = get_unit(uid)
        if not u:
            return json_error(404, "Unit not found.")
        errors = {}
        v = person_fields(request.get_json(silent=True) or {}, errors, "Owner")
        if errors:
            return field_errors(errors)
        o = Owner(unit_id=uid, owner_name=v["name"], contact_no=v["contact_no"], email=v["email"], move_in=v["move_in"],
                  move_out=v["move_out"], status="Past" if v["move_out"] else "Current", notes=v["notes"],
                  receive_soa_email=v["receive_soa_email"], include_in_soa=v["include_in_soa"])
        db.session.add(o)
        db.session.flush()
        legacy.audit(f"Added owner {o.owner_name} to unit {u.unit_no}", entity_type="owner", entity_id=o.id, commit=False,
                     details={"after": person_snapshot(o, "Owner")})
        db.session.commit()
        # createdPersonId: lets the form offer the resident portal account for exactly this record.
        return jsonify({"unit": detail(u), "createdPersonId": o.id}), 201

    @bp.put("/<int:uid>/owners/<int:oid>")
    @permission_required("edit_owner")
    def edit_owner(uid, oid):
        u, o = get_unit(uid), db.session.get(Owner, oid)
        if not u or not o or o.unit_id != uid:
            return json_error(404, "Owner not found.")
        errors = {}
        v = person_fields(request.get_json(silent=True) or {}, errors, "Owner", existing=o)
        if errors:
            return field_errors(errors)
        before = person_snapshot(o, "Owner")
        o.owner_name, o.contact_no, o.email, o.notes = v["name"], v["contact_no"], v["email"], v["notes"]
        o.move_in, o.move_out, o.status = v["move_in"], v["move_out"], v["status"]
        o.receive_soa_email, o.include_in_soa = v["receive_soa_email"], v["include_in_soa"]
        if o.status == "Past" and not o.move_out:
            o.move_out = date.today()
        if o.status == "Current":
            o.move_out = None
        after = person_snapshot(o, "Owner")
        if before != after:
            changed = {k: {"before": before[k], "after": after[k]} for k in after if before[k] != after[k]}
            legacy.audit(f"Updated owner {o.owner_name} in unit {u.unit_no}: {', '.join(changed)}", entity_type="owner",
                         entity_id=o.id, commit=False, details=changed)
        db.session.commit()
        return jsonify({"unit": detail(u)})

    def clear_representative(uid, keep_id=None):
        q = Tenant.query.filter(Tenant.unit_id == uid, Tenant.representative.is_(True))
        if keep_id:
            q = q.filter(Tenant.id != keep_id)
        q.update({"representative": False}, synchronize_session=False)

    @bp.post("/<int:uid>/tenants")
    @permission_required("add_tenant")
    def add_tenant(uid):
        u = get_unit(uid)
        if not u:
            return json_error(404, "Unit not found.")
        errors = {}
        v = person_fields(request.get_json(silent=True) or {}, errors, "Tenant")
        if errors:
            return field_errors(errors)
        status = "Past" if v["move_out"] else "Current"
        rep = v["representative"] and status == "Current"
        if rep:
            clear_representative(uid)
        t = Tenant(unit_id=uid, tenant_name=v["name"], contact_no=v["contact_no"], email=v["email"], move_in=v["move_in"],
                   move_out=v["move_out"], notes=v["notes"], status=status, representative=rep,
                   receive_soa_email=v["receive_soa_email"], include_in_soa=v["include_in_soa"])
        db.session.add(t)
        if status == "Current":
            u.status = "Occupied"   # classic rule
        db.session.flush()
        legacy.audit(f"Added tenant {t.tenant_name} to unit {u.unit_no}", entity_type="tenant", entity_id=t.id, commit=False,
                     details={"after": person_snapshot(t, "Tenant")})
        db.session.commit()
        return jsonify({"unit": detail(u), "createdPersonId": t.id}), 201

    @bp.put("/<int:uid>/tenants/<int:tid>")
    @permission_required("edit_tenant")
    def edit_tenant(uid, tid):
        u, t = get_unit(uid), db.session.get(Tenant, tid)
        if not u or not t or t.unit_id != uid:
            return json_error(404, "Tenant not found.")
        errors = {}
        v = person_fields(request.get_json(silent=True) or {}, errors, "Tenant", existing=t)
        if errors:
            return field_errors(errors)
        before = person_snapshot(t, "Tenant")
        t.tenant_name, t.contact_no, t.email, t.notes = v["name"], v["contact_no"], v["email"], v["notes"]
        t.move_in, t.move_out, t.status = v["move_in"], v["move_out"], v["status"]
        t.receive_soa_email, t.include_in_soa = v["receive_soa_email"], v["include_in_soa"]
        t.representative = v["representative"] and t.status == "Current"   # a past tenant is never the representative
        if t.representative:
            clear_representative(uid, keep_id=t.id)
        if t.status == "Past" and not t.move_out:
            t.move_out = date.today()
        if t.status == "Current":
            t.move_out = None
        after = person_snapshot(t, "Tenant")
        if before != after:
            changed = {k: {"before": before[k], "after": after[k]} for k in after if before[k] != after[k]}
            legacy.audit(f"Updated tenant {t.tenant_name} in unit {u.unit_no}: {', '.join(changed)}", entity_type="tenant",
                         entity_id=t.id, commit=False, details=changed)
        db.session.commit()
        return jsonify({"unit": detail(u)})

    return bp
