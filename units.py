"""/api/units — Units Directory: units, owners, tenants, parking and storage.

    GET  /api/units?q=&kind=&floor=&status=&past=&page=&perPage=   list (kind: residential | COMMERCIAL | DRYING AREA | PARKING | STORAGE;
                                                             past=1: the search also finds past owners/tenants)
    GET  /api/units/options                                  parking/storage units for assignment, unit types, floors
    POST /api/units                                          create a unit (optionally with a first owner and tenant)
    GET  /api/units/bulk-template                            empty workbook for bulk creation of new units
    POST /api/units/bulk-import/check                       validate all rows without writing
    POST /api/units/bulk-import                              revalidate and create all rows atomically
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
from datetime import date, datetime
from decimal import Decimal

from flask import Blueprint, jsonify, request
from sqlalchemy import func, or_, true
from sqlalchemy.orm import selectinload

from ..core.numbers import InputError, parse_decimal
from ..utils.auth import json_error, permission_required, protect_api_blueprint

UNIT_TYPES = ("STUDIO TYPE", "1 BEDROOM", "2 BEDROOM", "3 BEDROOM", "COMMERCIAL", "DRYING AREA", "PARKING", "STORAGE")
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
            "chargePayers": {
                "condo": u.condo_charge_payer or "Owner",
                "water": u.water_charge_payer or "Owner",
                "parking": u.parking_charge_payer or "Owner",
                "storage": u.storage_charge_payer or "Owner",
                "other": u.other_charge_payer or "Owner",
            },
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
        if kind not in ("residential", "COMMERCIAL", "DRYING AREA") + ASSETS:
            return json_error(400, "kind must be residential, COMMERCIAL, DRYING AREA, PARKING or STORAGE.")
        base = Unit.query.filter(Unit.active == true())
        counts = {
            "residential": base.filter(or_(Unit.unit_type.is_(None),
                                           Unit.unit_type.notin_(ASSETS + ("COMMERCIAL", "DRYING AREA")))).count(),
            "COMMERCIAL": base.filter(Unit.unit_type == "COMMERCIAL").count(),
            "DRYING AREA": base.filter(Unit.unit_type == "DRYING AREA").count(),
            "PARKING": base.filter(Unit.unit_type == "PARKING").count(),
            "STORAGE": base.filter(Unit.unit_type == "STORAGE").count(),
        }
        query = base.options(selectinload(Unit.owners), selectinload(Unit.tenants),
                             selectinload(Unit.assigned_parking_unit), selectinload(Unit.assigned_storage_unit))
        if kind == "COMMERCIAL" or kind == "DRYING AREA" or kind in ASSETS:
            query = query.filter(Unit.unit_type == kind)
        else:
            query = query.filter(or_(Unit.unit_type.is_(None),
                                    Unit.unit_type.notin_(ASSETS + ("COMMERCIAL", "DRYING AREA"))))
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

    @bp.delete("/<int:uid>")
    @permission_required("delete_unit")
    def delete_unit(uid):
        u = get_unit(uid)
        if not u:
            return json_error(404, "Unit not found.")
        if not u.active:
            return json_error(409, "This unit is already removed from the active directory.")
        for other in Unit.query.filter_by(assigned_parking_unit_id=u.id).all():
            other.assigned_parking_unit_id = None
            other.include_parking = False
        for other in Unit.query.filter_by(assigned_storage_unit_id=u.id).all():
            other.assigned_storage_unit_id = None
            other.include_storage = False
        u.active = False
        legacy.audit(f"Removed unit {u.unit_no} from the active directory", entity_type="unit", entity_id=u.id, commit=False)
        db.session.commit()
        return "", 204

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
        other = Unit.query.filter(field == asset.id, flag == true(), Unit.id != (unit_id or 0)).first()
        if other:
            errors[key] = f"{asset.unit_no} is already assigned to unit {other.unit_no}."
            return None
        return asset

    def unit_fields(data, errors, existing=None, default_residential_type=False):
        """Validated unit values (the classic form's fields)."""
        utype = str(data.get("type") or "").strip()
        if not utype and default_residential_type:
            utype = "STUDIO TYPE"
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
        payer_defaults = {
            "condo": existing.condo_charge_payer if existing is not None else values["occupancy_type"],
            "water": existing.water_charge_payer if existing is not None else values["occupancy_type"],
            "parking": existing.parking_charge_payer if existing is not None else values["occupancy_type"],
            "storage": existing.storage_charge_payer if existing is not None else values["occupancy_type"],
            "other": existing.other_charge_payer if existing is not None else values["occupancy_type"],
        }
        values["charge_payers"] = {}
        for key, default in payer_defaults.items():
            payer = str((data.get("chargePayers") or {}).get(key, default) or "").strip()
            if payer not in ("Owner", "Tenant"):
                errors[f"chargePayers.{key}"] = "Choose Owner or Tenant."
                payer = default
            values["charge_payers"][key] = payer
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
        u.condo_charge_payer = v["charge_payers"]["condo"]
        u.water_charge_payer = v["charge_payers"]["water"]
        u.parking_charge_payer = v["charge_payers"]["parking"]
        u.storage_charge_payer = v["charge_payers"]["storage"]
        u.other_charge_payer = v["charge_payers"]["other"]
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
                "storageUnitId": u.assigned_storage_unit_id if u.include_storage else None,
                "chargePayers": {"condo": u.condo_charge_payer, "water": u.water_charge_payer,
                                 "parking": u.parking_charge_payer, "storage": u.storage_charge_payer,
                                 "other": u.other_charge_payer}}

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

    # ---------------------------------------------------------------- guarded bulk creation
    def bulk_cell_text(value):
        if value is None:
            return ""
        if isinstance(value, (date, datetime)):
            return value.isoformat()[:10]
        return str(value).strip()

    def bulk_yes_no(value, default, row_errors, field):
        if value in (None, ""):
            return default
        if isinstance(value, bool):
            return value
        text_value = str(value).strip().lower()
        if text_value in ("yes", "true", "1"):
            return True
        if text_value in ("no", "false", "0"):
            return False
        row_errors.append(f"{field} must be Yes or No.")
        return default

    def inspect_bulk_upload():
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return None, (json_error(400, "Choose a CITYLAND9 bulk unit workbook."), 400)
        if not upload.filename.lower().endswith(".xlsx"):
            return None, (json_error(400, "Choose an Excel .xlsx file created from the bulk unit template."), 400)
        try:
            from ..services.unit_bulk import UNIT_BULK_MAX_ROWS, read_units_rows
            rows = read_units_rows(upload.stream, max_rows=UNIT_BULK_MAX_ROWS)
        except (ValueError, OSError, ImportError) as exc:
            return None, (json_error(400, str(exc)), 400)
        except Exception:
            legacy.app.logger.exception("Bulk unit workbook could not be read")
            return None, (json_error(400, "The workbook is not a valid Excel .xlsx file."), 400)

        existing_unit_numbers = {
            str(unit_no).strip().casefold()
            for (unit_no,) in Unit.query.with_entities(Unit.unit_no).all()
            if unit_no
        }
        seen, prepared = set(), []
        for row_number, row in rows:
            row_errors = []
            unit_no = bulk_cell_text(row.get("unit_no"))
            category = bulk_cell_text(row.get("category")).upper()
            residential_type = bulk_cell_text(row.get("residential_type")).upper()
            commercial_type = bulk_cell_text(row.get("commercial_type")).upper()
            if category == "RESIDENTIAL":
                unit_type = residential_type or "STUDIO TYPE"
                floor = bulk_cell_text(row.get("floor"))
            elif category == "COMMERCIAL":
                unit_type = "COMMERCIAL"
                floor = commercial_type
                if commercial_type and commercial_type not in ("2F", "UG", "LG"):
                    row_errors.append("Commercial type must be 2F, UG, or LG.")
            elif category in ("DRYING AREA", "STORAGE", "PARKING"):
                unit_type = category
                floor = bulk_cell_text(row.get("floor"))
            else:
                unit_type = ""
                floor = bulk_cell_text(row.get("floor"))
            key = unit_no.casefold()
            if not unit_no:
                row_errors.append("Enter a unit number.")
            elif key in seen or key in existing_unit_numbers:
                prepared.append({
                    "row": row_number,
                    "unitNo": unit_no,
                    "type": unit_type,
                    "areaSqm": bulk_cell_text(row.get("area_sqm")),
                    "ownerName": bulk_cell_text(row.get("owner_name")),
                    "tenantName": bulk_cell_text(row.get("tenant_name")),
                    "errors": [],
                    "skipped": True,
                })
                seen.add(key)
                continue
            elif len(unit_no) > 40:
                row_errors.append("Unit number can't exceed 40 characters.")
            if not category:
                row_errors.append("Choose a unit category.")
            elif category not in ("RESIDENTIAL", "COMMERCIAL", "DRYING AREA", "STORAGE", "PARKING"):
                row_errors.append("Choose a supported unit category.")
            if key:
                seen.add(key)
            if category == "RESIDENTIAL" and unit_type not in UNIT_TYPES:
                row_errors.append("Choose a supported Residential type.")
            if category in ("COMMERCIAL", "DRYING AREA", "STORAGE", "PARKING") and unit_type not in UNIT_TYPES:
                row_errors.append("Choose a supported unit category.")

            area_raw = row.get("area_sqm")
            if area_raw in (None, ""):
                row_errors.append("Area (sqm) is required.")
            auto_rate = bulk_yes_no(row.get("auto_rate"), True, row_errors, "auto_rate")
            parking_no = bulk_cell_text(row.get("parking_unit_no"))
            storage_no = bulk_cell_text(row.get("storage_unit_no"))
            parking = Unit.query.filter(func.lower(Unit.unit_no) == parking_no.lower(), Unit.unit_type == "PARKING", Unit.active == true()).first() if parking_no else None
            storage = Unit.query.filter(func.lower(Unit.unit_no) == storage_no.lower(), Unit.unit_type == "STORAGE", Unit.active == true()).first() if storage_no else None
            if unit_type in ASSETS and (parking_no or storage_no):
                row_errors.append("Parking/storage assignments can only be entered on a residential or commercial unit.")
            if parking_no and parking is None:
                row_errors.append("Parking reference must match an existing active PARKING unit.")
            if storage_no and storage is None:
                row_errors.append("Storage reference must match an existing active STORAGE unit.")

            values = {
                "floor": floor,
                "type": unit_type,
                "areaSqm": "" if area_raw is None else str(area_raw),
                "ratePerSqm": "" if row.get("rate_per_sqm") in (None, "") else str(row.get("rate_per_sqm")),
                "autoRate": auto_rate,
                "duesMode": bulk_cell_text(row.get("dues_mode")) or "per_sqm",
                "manualMonthlyDues": "" if row.get("manual_monthly_dues") is None else str(row.get("manual_monthly_dues")),
                "occupancy": bulk_cell_text(row.get("occupancy")) or "Owner",
                "status": bulk_cell_text(row.get("status")) or "Vacant",
                "chargePayers": {
                    key: bulk_cell_text(row.get(f"{key}_payer")) or bulk_cell_text(row.get("occupancy")) or "Owner"
                    for key in ("condo", "water", "parking", "storage", "other")
                },
                "parkingUnitId": parking.id if parking else None,
                "storageUnitId": storage.id if storage else None,
            }
            unit_errors = {}
            validated_unit = unit_fields(values, unit_errors)
            row_errors.extend(unit_errors.values())
            try:
                if validated_unit["area_sqm"] is None or validated_unit["area_sqm"] <= 0:
                    row_errors.append("Area (sqm) must be greater than zero.")
            except (TypeError, KeyError):
                pass

            owner_data = None
            tenant_data = None
            for kind, prefix, name_field in (("Owner", "owner", "owner_name"), ("Tenant", "tenant", "tenant_name")):
                name = bulk_cell_text(row.get(name_field))
                fields = ("contact_no", "email", "move_in")
                extras = [bulk_cell_text(row.get(f"{prefix}_{field}")) for field in fields]
                if not name and any(extras):
                    row_errors.append(f"{kind} details require a {kind.lower()} name.")
                if name:
                    person_input = {
                        "name": name,
                        "contactNo": extras[0],
                        "email": extras[1],
                        "moveIn": extras[2],
                        "moveOut": "",
                        "notes": "",
                        "receiveSoaEmail": False,
                        "includeInSoa": True,
                        "representative": kind == "Tenant",
                        "status": "Current",
                    }
                    person_errors = {}
                    validated_person = person_fields(person_input, person_errors, kind)
                    row_errors.extend(f"{kind}: {message}" for message in person_errors.values())
                    if kind == "Owner":
                        owner_data = validated_person
                    else:
                        tenant_data = validated_person

            prepared.append({
                "row": row_number,
                "unitNo": unit_no,
                "type": unit_type,
                "areaSqm": bulk_cell_text(area_raw),
                "ownerName": bulk_cell_text(row.get("owner_name")),
                "tenantName": bulk_cell_text(row.get("tenant_name")),
                "errors": row_errors,
                "skipped": False,
                "unitValues": validated_unit,
                "ownerValues": owner_data,
                "tenantValues": tenant_data,
            })
        return prepared, None

    def bulk_preview(prepared):
        return {
            "total": len(prepared),
            "valid": sum(not item["errors"] and not item["skipped"] for item in prepared),
            "invalid": sum(bool(item["errors"]) for item in prepared),
            "skipped": sum(item["skipped"] for item in prepared),
            "rows": [{key: item[key] for key in ("row", "unitNo", "type", "areaSqm", "ownerName", "tenantName", "errors", "skipped")}
                     for item in prepared],
        }

    @bp.get("/bulk-template")
    @permission_required("units")
    def bulk_unit_template():
        from flask import send_file
        from ..services.unit_bulk import build_units_template
        workbook = build_units_template()
        return send_file(workbook, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                         as_attachment=True, download_name="CITYLAND9_Bulk_Units_Template.xlsx")

    @bp.post("/bulk-import/check")
    @permission_required("units")
    def check_bulk_units():
        prepared, error = inspect_bulk_upload()
        if error:
            return error
        return jsonify(bulk_preview(prepared))

    @bp.post("/bulk-import")
    @permission_required("units")
    def create_bulk_units():
        prepared, error = inspect_bulk_upload()
        if error:
            return error
        preview = bulk_preview(prepared)
        if preview["invalid"]:
            return jsonify({"error": {"message": "Fix all row issues before creating any units.", "preview": preview}}), 409

        created = []
        try:
            for item in prepared:
                if item["skipped"]:
                    continue
                unit = Unit(unit_no=item["unitNo"], active=True)
                apply_unit(unit, item["unitValues"], creating=True)
                db.session.add(unit)
                db.session.flush()
                owner = item["ownerValues"]
                tenant = item["tenantValues"]
                if owner:
                    db.session.add(Owner(
                        unit_id=unit.id, owner_name=owner["name"], contact_no=owner["contact_no"], email=owner["email"],
                        move_in=owner["move_in"], status="Current", receive_soa_email=owner["receive_soa_email"],
                        include_in_soa=owner["include_in_soa"], notes=owner["notes"],
                    ))
                if tenant:
                    db.session.add(Tenant(
                        unit_id=unit.id, tenant_name=tenant["name"], contact_no=tenant["contact_no"], email=tenant["email"],
                        move_in=tenant["move_in"], status="Current", receive_soa_email=tenant["receive_soa_email"],
                        include_in_soa=tenant["include_in_soa"], representative=True, notes=tenant["notes"],
                    ))
                legacy.audit(f"Created unit {item['unitNo']} by bulk upload", entity_type="unit", entity_id=unit.id,
                             commit=False, details={"source": "bulk_unit_upload", "owner": owner["name"] if owner else None,
                                                    "tenant": tenant["name"] if tenant else None})
                created.append(item["unitNo"])
            db.session.commit()
        except Exception:
            db.session.rollback()
            legacy.app.logger.exception("Bulk unit creation failed; transaction rolled back")
            return json_error(500, "The bulk unit upload failed; no units were created. Check the server log.")
        return jsonify({"created": len(created), "skipped": preview["skipped"], "unitNumbers": created}), 201

    # ---------------------------------------------------------------- units: create / edit
    @bp.post("")
    @permission_required("units")
    def create_unit():
        data = request.get_json(silent=True) or {}
        errors = {}
        unit_no = text(data, "unitNo", "Unit number", errors, 40, required=True)
        if unit_no and Unit.query.filter(func.lower(Unit.unit_no) == unit_no.lower()).first():
            errors["unitNo"] = "That unit number already exists."
        v = unit_fields(data, errors, default_residential_type=True)
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
        q = Tenant.query.filter(Tenant.unit_id == uid, Tenant.representative == true())
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
