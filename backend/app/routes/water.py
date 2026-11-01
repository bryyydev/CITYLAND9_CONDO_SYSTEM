"""/api/water — Water Readings: monthly meter readings, corrections and water payments.

    GET  /api/water?month=YYYY-MM              the month's readings, units still to read, unpaid water (any month)
    GET  /api/water/previous?unit=&month=      the reading to start from (last month's current reading)
    POST /api/water                            save a unit's reading for a month (creates or corrects it)
    PUT  /api/water/<id>                       correct an existing reading
    POST /api/water/commercial                 save one shared reading per commercial type and month
    PUT  /api/water/commercial/<id>            correct a shared commercial reading
    POST /api/water/<id>/payments              record a water payment and issue the official receipt

Permission keys are the classic screen's (Superadmin and Admin): water, water_previous, edit_water,
mark_water_paid. The rules are the classic ones, in one place (legacy.save_water_reading,
legacy.record_water_payment): current >= previous, rate = the reading's own rate or the Rates &
Rules water rate, one reading per unit and month, an already-generated bill gets the new water
amount (refused when that month's books are closed), payments apply at most the balance, one per
form token, receipt + audit saved together. Commercial readings are separate shared meters: the
total usage charge is split over active units of that type by area and included in their SOAs.
"""
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import Blueprint, jsonify, request, send_file
from sqlalchemy import func, true

from ..core.numbers import InputError, parse_amount, parse_decimal
from ..services.soa import money
from ..utils.auth import json_error, permission_required, protect_api_blueprint

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def field_errors(errors, status=400):
    response = jsonify({"error": {"status": status, "message": next(iter(errors.values())), "fields": errors}})
    response.status_code = status
    return response


def rate_text(value):
    """A rate as typed: 50, 52.5 (str(Decimal(50).normalize()) would give "5E+1")."""
    return format(Decimal(str(value or 0)).normalize(), "f")


def num(value):
    """A reading as a plain number for JSON (3 decimals at most, no float noise)."""
    return float(Decimal(str(value or 0)).quantize(Decimal("0.001")).normalize())


def safe_decimal(value, default=Decimal("0")):
    if value in (None, ""):
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def make_water_blueprint(legacy):
    db, W, CW, Unit, Billing = legacy.db, legacy.WaterReading, legacy.CommercialWaterReading, legacy.Unit, legacy.Billing
    commercial_types = ("2F", "UG", "LG")
    bp = protect_api_blueprint(Blueprint("api_water", __name__, url_prefix="/api/water"))

    def payer(unit):
        c = legacy.current_contact_for_unit(unit)
        return (getattr(c, "tenant_name", None) or getattr(c, "owner_name", None) or "") if c else ""

    def row(r):
        total = money(r.bill_amount)
        paid = money(r.paid_amount)
        balance = max(Decimal(total) - Decimal(paid), Decimal("0"))
        if hasattr(Billing, "water_reading_month"):
            bill = Billing.query.filter_by(unit_id=r.unit_id, water_reading_month=r.reading_month).order_by(Billing.billing_month.asc()).first()
        else:
            bill = Billing.query.filter_by(unit_id=r.unit_id, billing_month=r.reading_month).first()
        return {
            "id": r.id, "unitId": r.unit_id, "unitNo": r.unit.unit_no, "payerName": payer(r.unit), "month": r.reading_month,
            "chargePayer": r.charge_payer or r.unit.water_charge_payer or "Owner",
            "payerRequired": (legacy.party_ledger_enabled(r.reading_month)
                              or legacy.party_ledger_enabled(date.today().isoformat()[:7])),
            "previous": num(r.previous_reading), "current": num(r.current_reading), "usage": num(r.usage),
            "rate": rate_text(r.rate), "amount": total, "paidAmount": paid, "balance": money(balance),
            "status": "Paid" if balance <= 0 and Decimal(total) > 0 else "Partially Paid" if Decimal(paid) > 0 else "Unpaid",
            "readingDate": r.reading_date.isoformat() if r.reading_date else None,
            "readingFrom": r.reading_period_start.isoformat() if r.reading_period_start else None,
            "readingTo": r.reading_period_end.isoformat() if r.reading_period_end else None,
            "paidDate": r.paid_date.isoformat() if r.paid_date else None,
            "billId": bill.id if bill else None,
            "receipts": [legacy.receipt_no_for("water", r.id)] if legacy.receipt_no_for("water", r.id) else [],
        }

    def residential():
        return Unit.query.filter(Unit.active == true(), Unit.unit_type.notin_(("PARKING", "STORAGE", "COMMERCIAL"))).order_by(Unit.unit_no).all()

    def commercial_row(reading):
        units = Unit.query.filter_by(active=True, unit_type="COMMERCIAL", floor=reading.commercial_type).all()
        total_area = sum((Decimal(str(unit.area_sqm or 0)) for unit in units), Decimal("0"))
        return {
            "id": reading.id, "commercialType": reading.commercial_type, "month": reading.reading_month,
            "previous": num(reading.previous_reading), "current": num(reading.current_reading), "usage": num(reading.usage),
            "rate": rate_text(reading.rate), "amount": money(reading.bill_amount),
            "readingDate": reading.reading_date.isoformat() if reading.reading_date else None,
            "readingFrom": reading.reading_period_start.isoformat() if reading.reading_period_start else None,
            "readingTo": reading.reading_period_end.isoformat() if reading.reading_period_end else None,
            "unitCount": len(units), "totalArea": num(total_area),
        }

    @bp.get("")
    @permission_required("water")
    def month_view():
        month = request.args.get("month", "").strip() or date.today().strftime("%Y-%m")
        if not MONTH_RE.match(month):
            return json_error(400, "month must look like 2026-10.")
        readings = W.query.filter_by(reading_month=month).join(Unit).filter(Unit.unit_type != "COMMERCIAL").order_by(Unit.unit_no).all()
        commercial_readings = CW.query.filter_by(reading_month=month).order_by(CW.commercial_type).all()
        commercial_done = {r.commercial_type for r in commercial_readings}
        commercial_units = {floor: Unit.query.filter_by(active=True, unit_type="COMMERCIAL", floor=floor).all()
                           for floor in commercial_types}
        commercial_missing = []
        for floor, units in commercial_units.items():
            if not units or floor in commercial_done:
                continue
            prev = legacy.previous_commercial_water_reading(floor, month)
            commercial_missing.append({"commercialType": floor, "previous": num(prev.current_reading) if prev else 0,
                                       "previousMonth": prev.reading_month if prev else None,
                                       "unitCount": len(units), "totalArea": num(sum((Decimal(str(u.area_sqm or 0)) for u in units), Decimal("0")))})
        done = {r.unit_id for r in readings}
        missing = []
        for u in residential():
            if u.id in done:
                continue
            prev = legacy.previous_water_reading(u.id, month)
            missing.append({"unitId": u.id, "unitNo": u.unit_no, "payerName": payer(u),
                            "previous": num(prev.current_reading) if prev else 0, "previousMonth": prev.reading_month if prev else None})
        unpaid = [row(r) for r in W.query.join(Unit).filter(Unit.unit_type != "COMMERCIAL").order_by(W.reading_month.desc(), Unit.unit_no).limit(2000).all()
                  if Decimal(money(r.bill_amount)) - Decimal(money(r.paid_amount)) > 0]
        rows = [row(r) for r in readings]
        commercial_rows = [commercial_row(r) for r in commercial_readings]
        commercial_usage = sum((r["usage"] for r in commercial_rows), 0)
        commercial_amount = sum((Decimal(r["amount"]) for r in commercial_rows), Decimal("0"))
        return jsonify({
            "month": month, "readings": rows, "missing": missing, "unpaid": unpaid[:500],
            "commercialReadings": commercial_rows, "commercialMissing": commercial_missing,
            "commercialTypes": [{"commercialType": floor, "unitCount": len(units),
                                 "totalArea": num(sum((Decimal(str(u.area_sqm or 0)) for u in units), Decimal("0")))}
                                for floor, units in commercial_units.items() if units],
            "defaultRate": rate_text(legacy.setting_float("water_rate", 50)),
            "autoCompute": legacy.setting("water_auto_compute", "1") == "1",
            "totals": {"read": len(rows) + len(commercial_rows), "toRead": len(missing) + len(commercial_missing),
                       "usage": num(sum(r["usage"] for r in rows) + commercial_usage),
                       "amount": money(sum((Decimal(r["amount"]) for r in rows), Decimal("0")) + commercial_amount),
                       "unpaid": money(sum((Decimal(r["balance"]) for r in unpaid), Decimal("0")))} ,
        })

    def inspect_bulk_upload():
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return None, (json_error(400, "Choose a CITYLAND9 bulk water-reading workbook."), 400)
        if not upload.filename.lower().endswith(".xlsx"):
            return None, (json_error(400, "Choose an Excel .xlsx file created from the bulk water-reading template."), 400)
        try:
            from ..services.water_bulk import cell_text, read_water_rows
            rows = read_water_rows(upload.stream)
        except (ValueError, OSError, ImportError) as exc:
            return None, (json_error(400, str(exc)), 400)
        except Exception:
            legacy.app.logger.exception("Bulk water-reading workbook could not be read")
            return None, (json_error(400, "The workbook is not a valid Excel .xlsx file."), 400)

        units = {
            str(unit.unit_no).strip().casefold(): unit
            for unit in Unit.query.filter(Unit.active == true()).all()
            if unit.unit_no
        }
        commercial_units_by_type = {kind: [] for kind in commercial_types}
        for unit in Unit.query.filter_by(active=True, unit_type="COMMERCIAL").all():
            if unit.floor in commercial_units_by_type:
                commercial_units_by_type[unit.floor].append(unit)
        seen, prepared = set(), []
        for sheet, row_number, source in rows:
            errors = []
            unit_no = cell_text(source.get("unit_no"))
            commercial_type = cell_text(source.get("commercial_type")).upper()
            month = cell_text(source.get("reading_month"))
            def parse_date(value, field):
                if isinstance(value, datetime):
                    return value.date()
                if isinstance(value, date):
                    return value
                date_text = cell_text(value)
                if not date_text:
                    return None
                try:
                    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_text):
                        raise ValueError
                    return date.fromisoformat(date_text)
                except ValueError:
                    errors.append(f"{field} must be a valid date in YYYY-MM-DD format.")
                    return None

            reading_from = parse_date(source.get("reading_from"), "reading_from")
            reading_to = parse_date(source.get("reading_to"), "reading_to")
            reading_date = parse_date(source.get("reading_date"), "reading_date")
            if reading_from is None and reading_to is None and reading_date is not None:
                reading_to = reading_date
            elif reading_from is None or reading_to is None:
                errors.append("Enter both reading_from and reading_to.")
            elif reading_from > reading_to:
                errors.append("reading_from must be on or before reading_to.")
            if not MONTH_RE.fullmatch(month):
                errors.append("reading_month must look like YYYY-MM.")

            identity = None
            unit = None
            if sheet == "WaterReadings":
                unit = units.get(unit_no.casefold())
                if not unit_no:
                    errors.append("Enter a unit number.")
                elif unit is None:
                    errors.append("Unit number doesn't match an active unit in CITYLAND9.")
                elif unit.unit_type in ("PARKING", "STORAGE", "COMMERCIAL"):
                    errors.append("Choose an active residential unit; commercial readings belong on the commercial sheet.")
                identity = ("unit", unit.id) if unit else ("unit_no", unit_no.casefold())
            else:
                if commercial_type not in ("2F", "UG", "LG"):
                    errors.append("commercial_type must be 2F, UG, or LG.")
                identity = ("commercial", commercial_type)
                commercial_units = commercial_units_by_type.get(commercial_type, [])
                if commercial_type in ("2F", "UG", "LG") and not commercial_units:
                    errors.append(f"There are no active commercial units for {commercial_type}.")
                elif commercial_units and sum((Decimal(str(u.area_sqm or 0)) for u in commercial_units), Decimal("0")) <= 0:
                    errors.append(f"Active commercial units for {commercial_type} need a total area greater than zero.")

            key = (identity, month)
            if identity and MONTH_RE.fullmatch(month):
                if key in seen:
                    errors.append("This unit or commercial type appears more than once for the same month.")
                seen.add(key)
                if sheet == "WaterReadings" and unit:
                    if W.query.filter_by(unit_id=unit.id, reading_month=month).first():
                        errors.append("A reading already exists for this unit and month; bulk upload never overwrites existing readings.")
                elif sheet == "CommercialWaterReadings" and commercial_type in ("2F", "UG", "LG"):
                    if CW.query.filter_by(commercial_type=commercial_type, reading_month=month).first():
                        errors.append("A reading already exists for this commercial type and month; bulk upload never overwrites existing readings.")

            if MONTH_RE.fullmatch(month):
                try:
                    legacy.check_open_period(month, "Water readings")
                except InputError as exc:
                    errors.append(exc.message)

            try:
                current = parse_decimal(source.get("current_reading"), label="Current reading", places=3,
                                        minimum=Decimal("0"), maximum=Decimal("99999999"))
            except InputError as exc:
                current = None
                errors.append(exc.message)
            try:
                previous = parse_decimal(source.get("previous_reading"), label="Previous reading", places=3,
                                         minimum=Decimal("0"), maximum=Decimal("99999999"), allow_empty=True)
            except InputError as exc:
                previous = None
                errors.append(exc.message)
            try:
                rate = parse_decimal(source.get("rate"), label="Water rate", places=4,
                                     minimum=Decimal("0"), maximum=Decimal("100000"), allow_empty=True)
            except InputError as exc:
                rate = None
                errors.append(exc.message)

            prepared.append({
                "sheet": sheet, "row": row_number, "unitNo": unit_no, "commercialType": commercial_type,
                "month": month, "readingDate": reading_date.isoformat() if reading_date else "",
                "readingFrom": reading_from.isoformat() if reading_from else "",
                "readingTo": reading_to.isoformat() if reading_to else "",
                "previous": previous, "current": current, "rate": rate, "identity": identity,
                "errors": errors,
            })

        imported_history = {}
        for item in prepared:
            if item["identity"] and MONTH_RE.fullmatch(item["month"]) and item["current"] is not None:
                imported_history.setdefault(item["identity"], []).append(item)
        for item in prepared:
            previous = item["previous"]
            if previous is None and item["identity"] and MONTH_RE.fullmatch(item["month"]):
                candidates = [prior for prior in imported_history.get(item["identity"], [])
                              if prior["month"] < item["month"] and prior["current"] is not None]
                latest_import = max(candidates, key=lambda prior: (prior["month"], prior["row"])) if candidates else None
                if item["sheet"] == "WaterReadings" and item["identity"][0] == "unit":
                    prior = legacy.previous_water_reading(item["identity"][1], item["month"])
                elif item["sheet"] == "CommercialWaterReadings":
                    prior = legacy.previous_commercial_water_reading(item["commercialType"], item["month"])
                else:
                    prior = None
                if latest_import and prior and prior.reading_month > latest_import["month"]:
                    previous = safe_decimal(prior.current_reading)
                elif latest_import:
                    previous = latest_import["current"]
                else:
                    previous = safe_decimal(getattr(prior, "current_reading", None)) if prior else Decimal("0")
                item["previous"] = previous
            if item["previous"] is not None and item["current"] is not None and item["current"] < item["previous"]:
                item["errors"].append("Current reading can't be lower than the previous reading.")
            if item["sheet"] == "CommercialWaterReadings" and item["rate"] is None:
                item["rate"] = Decimal(str(legacy.setting_float("water_rate", 50)))
            elif item["sheet"] == "WaterReadings" and item["rate"] is None:
                item["rate"] = Decimal(str(legacy.setting_float("water_rate", 50)))

        return prepared, None

    def bulk_preview(prepared):
        valid = sum(not item["errors"] for item in prepared)
        return {
            "total": len(prepared), "valid": valid, "invalid": len(prepared) - valid,
            "rows": [{
                "sheet": item["sheet"], "row": item["row"], "unitNo": item["unitNo"],
                "commercialType": item["commercialType"], "month": item["month"],
                "previous": str(item["previous"]) if item["previous"] is not None else "",
                "current": str(item["current"]) if item["current"] is not None else "",
                "readingDate": item["readingDate"], "errors": item["errors"],
                "readingFrom": item["readingFrom"], "readingTo": item["readingTo"],
            } for item in prepared],
        }

    @bp.get("/bulk-template")
    @permission_required("water")
    def bulk_water_template():
        from ..services.water_bulk import build_water_template
        return send_file(build_water_template(),
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                         as_attachment=True, download_name="CITYLAND9_Bulk_Water_Readings_Template.xlsx")

    @bp.post("/bulk-import/check")
    @permission_required("water")
    def check_bulk_water_readings():
        prepared, error = inspect_bulk_upload()
        if error:
            return error
        return jsonify(bulk_preview(prepared))

    @bp.post("/bulk-import")
    @permission_required("water")
    def create_bulk_water_readings():
        prepared, error = inspect_bulk_upload()
        if error:
            return error
        preview = bulk_preview(prepared)
        if preview["invalid"]:
            return jsonify({"error": {"message": "Fix all row issues before saving any water readings.", "preview": preview}}), 409
        created = []
        try:
            for item in sorted(prepared, key=lambda row: (row["month"], row["sheet"], row["row"])):
                if item["sheet"] == "WaterReadings":
                    reading, _, _ = legacy.save_water_reading(
                        unit_id=item["identity"][1], month=item["month"], previous=item["previous"],
                        current=item["current"], rate=item["rate"],
                        reading_date=date.fromisoformat(item["readingTo"] or item["readingDate"]),
                        period_start=date.fromisoformat(item["readingFrom"]) if item["readingFrom"] else None,
                        period_end=date.fromisoformat(item["readingTo"]) if item["readingTo"] else None,
                        commit=False,
                    )
                    label = reading.unit.unit_no
                else:
                    reading, _, _ = legacy.save_commercial_water_reading(
                        commercial_type=item["commercialType"], month=item["month"], previous=item["previous"],
                        current=item["current"], rate=item["rate"],
                        reading_date=date.fromisoformat(item["readingTo"] or item["readingDate"]),
                        period_start=date.fromisoformat(item["readingFrom"]) if item["readingFrom"] else None,
                        period_end=date.fromisoformat(item["readingTo"]) if item["readingTo"] else None,
                        commit=False,
                    )
                    label = item["commercialType"]
                created.append({"label": label, "month": item["month"]})
            db.session.commit()
        except InputError as exc:
            db.session.rollback()
            return json_error(409, exc.message)
        except Exception:
            db.session.rollback()
            legacy.app.logger.exception("Bulk water-reading upload failed; transaction rolled back")
            return json_error(500, "The bulk water-reading upload failed; no readings were saved. Check the server log.")
        return jsonify({"created": len(created), "readings": created}), 201

    @bp.get("/previous")
    @permission_required("water_previous")
    def previous():
        unit = request.args.get("unit", type=int)
        month = request.args.get("month", "").strip()
        if not unit or not MONTH_RE.match(month):
            return json_error(400, "unit and month are required.")
        prev = legacy.previous_water_reading(unit, month)
        return jsonify({"previous": num(prev.current_reading) if prev else 0, "found": bool(prev),
                        "sourceMonth": prev.reading_month if prev else None})

    def reading_values(data, errors):
        def dec(key, label, places, required, maximum):
            try:
                return parse_decimal(data.get(key), label=label, places=places, minimum=Decimal("0"), maximum=Decimal(maximum),
                                     allow_empty=not required, default=None)
            except InputError as exc:
                errors[key] = exc.message
                return None
        current = dec("current", "Current reading", 3, True, "99999999")
        previous = dec("previous", "Previous reading", 3, False, "99999999")
        rate = dec("rate", "Water rate", 4, False, "100000")
        def parse_date(key):
            raw = str(data.get(key) or "").strip()
            if not raw:
                return None
            try:
                parsed = date.fromisoformat(raw)
                if parsed.isoformat() != raw:
                    raise ValueError
                return parsed
            except ValueError:
                errors[key] = "Choose a valid date."
                return None

        period_start = parse_date("readingFrom")
        period_end = parse_date("readingTo")
        reading_date = parse_date("readingDate")
        if period_start is not None or period_end is not None:
            if period_start is None:
                errors["readingFrom"] = "Choose the start of the reading period."
            if period_end is None:
                errors["readingTo"] = "Choose the end of the reading period."
            if period_start and period_end and period_start > period_end:
                errors["readingFrom"] = "The start date must be on or before the end date."
        return previous, current, rate, reading_date, period_start, period_end

    def save(unit_id, month, data):
        errors = {}
        previous, current, rate, reading_date, period_start, period_end = reading_values(data, errors)
        if errors:
            return field_errors(errors)
        if previous is None:
            prev = legacy.previous_water_reading(unit_id, month) if unit_id else None
            previous = safe_decimal(prev.current_reading) if prev else Decimal("0")
        reading, created, bill_updated = legacy.save_water_reading(unit_id=unit_id, month=month, previous=previous, current=current,
                                                                   rate=rate, reading_date=reading_date, period_start=period_start, period_end=period_end)
        return jsonify({"reading": row(reading), "created": created, "billUpdated": bill_updated}), (201 if created else 200)

    @bp.post("")
    @permission_required("water")
    def create_or_correct():
        data = request.get_json(silent=True) or {}
        unit_id = data.get("unitId") if isinstance(data.get("unitId"), int) else None
        return save(unit_id, str(data.get("month") or ""), data)

    @bp.put("/<int:rid>")
    @permission_required("edit_water")
    def correct(rid):
        r = db.session.get(W, rid)
        if not r:
            return json_error(404, "Water reading not found.")
        return save(r.unit_id, r.reading_month, request.get_json(silent=True) or {})

    def save_commercial(commercial_type, month, data):
        errors = {}
        previous, current, rate, reading_date, period_start, period_end = reading_values(data, errors)
        if commercial_type not in commercial_types:
            errors["commercialType"] = "Choose a valid commercial type."
        if not MONTH_RE.match(month or ""):
            errors["month"] = "Choose the reading month."
        if errors:
            return field_errors(errors)
        if previous is None:
            prev = legacy.previous_commercial_water_reading(commercial_type, month)
            previous = safe_decimal(prev.current_reading) if prev else Decimal("0")
        reading, created, bill_updated = legacy.save_commercial_water_reading(
            commercial_type=commercial_type, month=month, previous=previous, current=current,
            rate=rate, reading_date=reading_date, period_start=period_start, period_end=period_end,
        )
        return jsonify({"reading": commercial_row(reading), "created": created, "billUpdated": bill_updated}), (201 if created else 200)

    @bp.post("/commercial")
    @permission_required("water")
    def create_commercial():
        data = request.get_json(silent=True) or {}
        return save_commercial(str(data.get("commercialType") or ""), str(data.get("month") or ""), data)

    @bp.put("/commercial/<int:rid>")
    @permission_required("edit_water")
    def correct_commercial(rid):
        reading = db.session.get(CW, rid)
        if not reading:
            return json_error(404, "Commercial water reading not found.")
        data = request.get_json(silent=True) or {}
        return save_commercial(reading.commercial_type, reading.reading_month, data)

    @bp.post("/<int:rid>/payments")
    @permission_required("mark_water_paid")
    @legacy.retry_on_deadlock
    def pay(rid):
        data = request.get_json(silent=True) or {}
        errors = {}
        try:
            amount = parse_amount(data.get("amount"), label="Amount")
        except InputError as exc:
            errors["amount"] = exc.message
            amount = None
        raw_date = str(data.get("date") or "").strip()
        paid_date = date.today()
        if raw_date:
            try:
                paid_date = date.fromisoformat(raw_date)
            except ValueError:
                errors["date"] = "Choose the payment date."
        token = str(data.get("formToken") or "")
        if not TOKEN_RE.match(token):
            errors["formToken"] = "This form is out of date. Reload and try again."
        if errors:
            return field_errors(errors)
        result = legacy.record_water_payment(rid, amount=amount, payment_method=data.get("method"), payment_type=data.get("type"),
                                             reference=data.get("reference"), paid_date=paid_date, form_token=token,
                                             payer_type=data.get("payerType"))
        if result is None:
            return json_error(404, "Water reading not found.")
        reading, receipt, applied = result
        return jsonify({"reading": row(reading), "receiptNo": receipt.receipt_no, "receiptId": receipt.id, "applied": applied}), 201

    return bp
