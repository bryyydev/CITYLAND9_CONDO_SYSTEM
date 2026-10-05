"""/api/hr — HR & payroll (replaces the classic /employees... screens).

    Employees      GET/POST /api/hr/employees, PUT/DELETE /api/hr/employees/<id>
    Attendance     GET /api/hr/attendance?date=  PUT /api/hr/attendance  GET /api/hr/attendance/history/<id>?from&to
    Overtime       GET/POST /api/hr/overtime     POST /api/hr/overtime/<id>/decision
    Leave          GET/POST /api/hr/leave        POST /api/hr/leave/<id>/decision
    Payroll        GET /api/hr/payroll?from&to   POST /api/hr/payroll   POST /api/hr/payroll/preview
                   GET /api/hr/payroll/<id>      PUT /api/hr/payroll/<id>/statutory   POST /api/hr/payroll/<id>/status
    13th month     GET /api/hr/thirteenth-month?year=
    Reports        GET /api/hr/payroll-reports?year=
    Loans          GET/POST /api/hr/loans        PUT /api/hr/loans/<id>
    Payroll Rules  GET/PUT /api/hr/settings

Permission keys are the classic ones (backend/app/core/permissions.py): HR is Superadmin + Manager;
attendance entry also Staff. The payroll computation is the classic one (legacy _v1039_calc_payroll /
_v1040_calc with the saved Payroll Rules). Changes from the classic screens (H1-H5 in
docs/module-migration-checklist.md): a second payroll for the same employee and period is refused;
payroll status moves Draft -> Final -> Paid and a Paid payroll can't be edited; editing the
statutory amounts recomputes the totals and the net pay; leave and overtime are filed as Pending and
decided once, overlapping leave is refused; an employee with records can't be deleted. Every change
is audited.
"""
import re
from datetime import date, datetime, time
from decimal import Decimal

from flask import Blueprint, jsonify, request

from ..core.numbers import InputError, parse_amount, parse_decimal
from ..services.soa import money
from ..utils.auth import json_error, permission_required, protect_api_blueprint, signed_in_user

EMPLOYMENT_STATUSES = ("Active", "On Leave", "Inactive", "Separated")
ATTENDANCE_STATUSES = ("PRESENT", "LATE", "UNDERTIME", "LATE/UNDERTIME", "ABSENT", "LEAVE", "REST DAY")
NO_TIMES = ("ABSENT", "LEAVE", "REST DAY")
LEAVE_TYPES = ("VACATION", "SICK", "EMERGENCY", "SERVICE INCENTIVE", "MATERNITY", "PATERNITY", "OTHER")
LOAN_TYPES = ("SSS", "PAG-IBIG", "COMPANY", "OTHER")
LOAN_STATUSES = ("ACTIVE", "HOLD", "PAID")
PAYROLL_FLOW = {"DRAFT": "FINAL", "FINAL": "PAID"}
STATUTORY_INPUTS = ("sss_employee", "sss_employer", "sss_ec_employer", "philhealth_employee", "philhealth_employer",
                    "pagibig_employee", "pagibig_employer", "withholding_tax", "thirteenth_month")
CAMEL = {"sss_employee": "sssEmployee", "sss_employer": "sssEmployer", "sss_ec_employer": "sssEcEmployer",
         "philhealth_employee": "philhealthEmployee", "philhealth_employer": "philhealthEmployer",
         "pagibig_employee": "pagibigEmployee", "pagibig_employer": "pagibigEmployer", "withholding_tax": "withholdingTax",
         "thirteenth_month": "thirteenthMonth", "gross_pay": "grossPay", "total_employee_deductions": "totalEmployeeDeductions",
         "total_employer_cost": "totalEmployerCost", "net_pay_after_statutory": "netPay"}
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
# Payroll Rules: key -> (label, group, unit, min, max)
SETTINGS = {
    "hr_working_days": ("Working days per month", "Attendance", "days", 1, 31),
    "hr_work_hours_per_day": ("Work hours per day", "Attendance", "hours", 1, 24),
    "hr_grace_minutes": ("Grace period for late", "Attendance", "minutes", 0, 120),
    "hr_sss_employee_rate": ("Employee share", "SSS", "%", 0, 100),
    "hr_sss_employer_rate": ("Employer share", "SSS", "%", 0, 100),
    "hr_sss_max_msc": ("Maximum monthly salary credit", "SSS", "₱", 0, 1_000_000),
    "hr_sss_ec_threshold": ("EC: salary credit up to", "SSS", "₱", 0, 1_000_000),
    "hr_sss_ec_low": ("EC (employer) up to the threshold", "SSS", "₱", 0, 10_000),
    "hr_sss_ec_high": ("EC (employer) above the threshold", "SSS", "₱", 0, 10_000),
    "hr_philhealth_rate": ("Premium rate", "PhilHealth", "%", 0, 100),
    "hr_philhealth_min_base": ("Salary floor", "PhilHealth", "₱", 0, 1_000_000),
    "hr_philhealth_max_base": ("Salary ceiling", "PhilHealth", "₱", 0, 10_000_000),
    "hr_philhealth_employee_share": ("Employee share of the premium", "PhilHealth", "%", 0, 100),
    "hr_pagibig_max_base": ("Maximum fund salary", "Pag-IBIG", "₱", 0, 1_000_000),
    "hr_pagibig_low_rate": ("Employee rate (salary ≤ ₱1,500)", "Pag-IBIG", "%", 0, 100),
    "hr_pagibig_high_rate": ("Employee rate (above ₱1,500)", "Pag-IBIG", "%", 0, 100),
    "hr_pagibig_employer_rate": ("Employer rate", "Pag-IBIG", "%", 0, 100),
    "hr_bir_exempt_threshold": ("Tax-exempt up to (monthly)", "BIR withholding", "₱", 0, 10_000_000),
    "hr_bir_bracket2": ("15% bracket up to", "BIR withholding", "₱", 0, 10_000_000),
    "hr_bir_bracket3": ("20% bracket up to", "BIR withholding", "₱", 0, 10_000_000),
    "hr_bir_bracket4": ("25% bracket up to", "BIR withholding", "₱", 0, 10_000_000),
    "hr_bir_bracket5": ("30% bracket up to", "BIR withholding", "₱", 0, 100_000_000),
    "hr_bir_rate2": ("Rate, 2nd bracket", "BIR withholding", "%", 0, 100),
    "hr_bir_rate3": ("Rate, 3rd bracket", "BIR withholding", "%", 0, 100),
    "hr_bir_rate4": ("Rate, 4th bracket", "BIR withholding", "%", 0, 100),
    "hr_bir_rate5": ("Rate, 5th bracket", "BIR withholding", "%", 0, 100),
    "hr_bir_rate6": ("Rate, top bracket", "BIR withholding", "%", 0, 100),
    "hr_13th_month_ceiling": ("Tax-exempt 13th month & benefits", "13th month", "₱", 0, 10_000_000),
}


def field_errors(errors, status=400):
    response = jsonify({"error": {"status": status, "message": next(iter(errors.values())), "fields": errors}})
    response.status_code = status
    return response


def iso(v):
    return v.isoformat() if v else None


def hhmm(t):
    return t.strftime("%H:%M") if t else None


def title(s):
    return (s or "").title()


def make_hr_blueprint(legacy):
    db = legacy.db
    Employee, Attendance, Payroll, Statutory = legacy.Employee, legacy.EmployeeAttendance, legacy.EmployeePayroll, legacy.EmployeePayrollStatutory
    Leave, Overtime, Loan, HRSetting = legacy.EmployeeLeave, legacy.EmployeeOvertime, legacy.EmployeeHRLoan, legacy.EmployeeHRSetting
    f = legacy._v1039_money
    bp = protect_api_blueprint(Blueprint("api_hr", __name__, url_prefix="/api/hr"))

    def body():
        return request.get_json(silent=True) or {}

    def text(data, key, label, errors, max_len, required=False):
        value = str(data.get(key) or "").strip()
        if required and not value:
            errors[key] = f"{label} is required."
        elif len(value) > max_len:
            errors[key] = f"{label} can't be longer than {max_len} characters."
        return value

    def a_date(data, key, label, errors, required=False):
        raw = str(data.get(key) or "").strip()
        if not raw:
            if required:
                errors[key] = f"{label} is required."
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError:
            errors[key] = f"{label} must be a date."
            return None

    def amount(data, key, label, errors, allow_zero=True, default="0"):
        try:
            return parse_amount(data.get(key) if data.get(key) not in (None, "") else default, label=label, allow_zero=allow_zero)
        except InputError as exc:
            errors[key] = exc.message
            return None

    def employee_or_error(data, errors, key="employeeId"):
        eid = data.get(key)
        emp = db.session.get(Employee, eid) if isinstance(eid, int) else None
        if not emp:
            errors[key] = "Choose the employee."
        return emp

    def names():
        return {e.id: e.full_name for e in Employee.query.all()}

    def hr_setting(key):
        return legacy._v1041_hr_float(key, legacy.HR_SETTING_DEFAULTS[key])

    def late_and_undertime(row):
        """Minutes late (after 8:00 + grace) and undertime (before 17:00), as payroll counts them."""
        status = (row.status or "").upper()
        late = under = 0
        if row.time_in and status in ("LATE", "LATE/UNDERTIME", "PRESENT"):
            late = max(row.time_in.hour * 60 + row.time_in.minute - 8 * 60 - int(hr_setting("hr_grace_minutes")), 0)
        if row.time_out and status in ("UNDERTIME", "LATE/UNDERTIME"):
            under = max(17 * 60 - (row.time_out.hour * 60 + row.time_out.minute), 0)
        return late, under

    # ================================================================ employees
    def employee_row(e, with_records=False):
        row = {"id": e.id, "employeeNo": e.employee_no, "fullName": e.full_name, "position": e.position or "",
               "department": e.department or "", "status": e.employment_status or "Active", "contactNo": e.contact_no or "",
               "email": e.email or "", "dateHired": iso(e.date_hired), "monthlySalary": money(e.monthly_salary), "notes": e.notes or ""}
        if with_records:
            row["hasRecords"] = has_records(e.id)
        return row

    def has_records(eid):
        return any(m.query.filter_by(employee_id=eid).first() for m in (Attendance, Payroll, Leave, Overtime, Loan))

    def employee_values(data, errors, current=None):
        v = {"full_name": text(data, "fullName", "Full name", errors, 200, required=True),
             "position": text(data, "position", "Position", errors, 120),
             "department": text(data, "department", "Department", errors, 120),
             "employment_status": str(data.get("status") or "Active"),
             "contact_no": text(data, "contactNo", "Contact number", errors, 80),
             "email": text(data, "email", "Email", errors, 160),
             "date_hired": a_date(data, "dateHired", "Date hired", errors),
             "monthly_salary": amount(data, "monthlySalary", "Monthly salary", errors),
             "notes": text(data, "notes", "Notes", errors, 500)}
        if v["employment_status"] not in EMPLOYMENT_STATUSES:
            errors["status"] = "Choose the employment status."
        if v["email"] and "@" not in v["email"]:
            errors["email"] = "Enter a valid email address."
        if v["date_hired"] and v["date_hired"] > date.today():
            errors["dateHired"] = "The hire date can't be in the future."
        return v

    @bp.get("/employees")
    @permission_required("employees")
    def employees():
        rows = Employee.query.order_by(Employee.full_name).all()
        return jsonify({"employees": [employee_row(e, with_records=True) for e in rows], "statuses": list(EMPLOYMENT_STATUSES)})

    @bp.post("/employees")
    @permission_required("employees")
    def add_employee():
        data, errors = body(), {}
        number = text(data, "employeeNo", "Employee No.", errors, 50, required=True)
        values = employee_values(data, errors)
        if number and Employee.query.filter(db.func.lower(Employee.employee_no) == number.lower()).first():
            errors["employeeNo"] = "That employee number is already used."
        if errors:
            return field_errors(errors)
        e = Employee(employee_no=number, **values)
        db.session.add(e)
        db.session.flush()
        legacy.audit(f"Added employee {number} ({e.full_name})", entity_type="employee", entity_id=e.id, commit=False)
        db.session.commit()
        return jsonify({"employee": employee_row(e, with_records=True)}), 201

    @bp.put("/employees/<int:eid>")
    @permission_required("employee_edit")
    def edit_employee(eid):
        e = db.session.get(Employee, eid)
        if not e:
            return json_error(404, "Employee not found.")
        errors = {}
        values = employee_values(body(), errors, e)
        if errors:
            return field_errors(errors)
        changed = {}
        for k, v in values.items():
            before = getattr(e, k)
            if (money(before) if k == "monthly_salary" else before) != (money(v) if k == "monthly_salary" else v):
                changed[k] = {"before": str(before) if before is not None else None, "after": str(v) if v is not None else None}
                setattr(e, k, v)
        if changed:
            legacy.audit(f"Updated employee {e.employee_no}: {', '.join(changed)}", entity_type="employee", entity_id=e.id, details=changed, commit=False)
        db.session.commit()
        return jsonify({"employee": employee_row(e, with_records=True)})

    @bp.delete("/employees/<int:eid>")
    @permission_required("delete_employee")
    def delete_employee(eid):
        e = db.session.get(Employee, eid)
        if not e:
            return json_error(404, "Employee not found.")
        if has_records(eid):
            return json_error(409, f"{e.full_name} has attendance, payroll, leave, overtime or loan records, which must be kept. "
                                   "Set the status to Separated instead.")
        legacy.audit(f"Deleted employee {e.employee_no} ({e.full_name})", entity_type="employee", entity_id=e.id, commit=False)
        db.session.delete(e)
        db.session.commit()
        return "", 204

    # ================================================================ attendance
    def attendance_row(r, name):
        late, under = late_and_undertime(r)
        return {"id": r.id, "employeeId": r.employee_id, "employeeName": name, "date": iso(r.attendance_date),
                "timeIn": hhmm(r.time_in), "timeOut": hhmm(r.time_out), "status": r.status or "PRESENT",
                "remarks": r.remarks or "", "lateMinutes": late, "undertimeMinutes": under}

    @bp.get("/attendance")
    @permission_required("employee_attendance")
    def attendance():
        raw = request.args.get("date", "").strip()
        try:
            day = date.fromisoformat(raw) if raw else date.today()
        except ValueError:
            return json_error(400, "date must look like 2026-10-05.")
        records = {r.employee_id: r for r in Attendance.query.filter_by(attendance_date=day).all()}
        staff = [e for e in Employee.query.order_by(Employee.full_name).all()
                 if e.employment_status not in ("Inactive", "Separated") or e.id in records]
        return jsonify({"date": day.isoformat(), "statuses": list(ATTENDANCE_STATUSES),
                        "employees": [{"id": e.id, "fullName": e.full_name, "employeeNo": e.employee_no, "status": e.employment_status or "Active",
                                       "record": attendance_row(records[e.id], e.full_name) if e.id in records else None} for e in staff],
                        "schedule": {"start": "08:00", "end": "17:00", "graceMinutes": int(hr_setting("hr_grace_minutes"))}})

    @bp.put("/attendance")
    @permission_required("employee_attendance")
    def save_attendance():
        data = body()
        errors = {}
        day = a_date(data, "date", "Date", errors, required=True)
        if day and day > date.today():
            errors["date"] = "Attendance can't be entered for a future date."
        items = data.get("records")
        if not isinstance(items, list) or not items:
            errors["records"] = "Enter at least one time record."
        if errors:
            return field_errors(errors)
        saved = cleared = 0
        problems = {}
        for i, item in enumerate(items):
            emp = db.session.get(Employee, item.get("employeeId")) if isinstance(item, dict) and isinstance(item.get("employeeId"), int) else None
            if not emp:
                problems[f"records.{i}"] = "Unknown employee."
                continue
            row = Attendance.query.filter_by(employee_id=emp.id, attendance_date=day).first()
            status = str(item.get("status") or "").upper()
            if not status:                      # cleared: remove the day's record
                if row:
                    db.session.delete(row)
                    cleared += 1
                continue
            if status not in ATTENDANCE_STATUSES:
                problems[f"records.{i}"] = f"{emp.full_name}: choose a valid status."
                continue
            tin, tout = str(item.get("timeIn") or ""), str(item.get("timeOut") or "")
            if status in NO_TIMES:
                tin = tout = ""
            if (tin and not TIME_RE.match(tin)) or (tout and not TIME_RE.match(tout)):
                problems[f"records.{i}"] = f"{emp.full_name}: times must look like 08:00."
                continue
            if tin and tout and tout <= tin:
                problems[f"records.{i}"] = f"{emp.full_name}: time out must be after time in."
                continue
            remarks = str(item.get("remarks") or "").strip()[:255]
            if not row:
                row = Attendance(employee_id=emp.id, attendance_date=day)
                db.session.add(row)
            row.status = status
            row.time_in = datetime.strptime(tin, "%H:%M").time() if tin else None
            row.time_out = datetime.strptime(tout, "%H:%M").time() if tout else None
            row.remarks = remarks or None
            saved += 1
        if problems:
            db.session.rollback()
            return field_errors(problems)
        legacy.audit(f"Saved attendance for {day.isoformat()}: {saved} record(s){f', {cleared} cleared' if cleared else ''}",
                     entity_type="attendance", commit=False)
        db.session.commit()
        return jsonify({"saved": saved, "cleared": cleared})

    @bp.get("/attendance/history/<int:eid>")
    @permission_required("employee_attendance_history")
    def attendance_history(eid):
        e = db.session.get(Employee, eid)
        if not e:
            return json_error(404, "Employee not found.")
        today = date.today()
        try:
            start = date.fromisoformat(request.args.get("from") or today.replace(day=1).isoformat())
            end = date.fromisoformat(request.args.get("to") or today.isoformat())
        except ValueError:
            return json_error(400, "Dates must look like 2026-10-05.")
        if end < start:
            start, end = end, start
        rows = Attendance.query.filter(Attendance.employee_id == eid, Attendance.attendance_date >= start,
                                       Attendance.attendance_date <= end).order_by(Attendance.attendance_date.desc()).all()
        counts = {}
        for r in rows:
            counts[r.status or "PRESENT"] = counts.get(r.status or "PRESENT", 0) + 1
        out = [attendance_row(r, e.full_name) for r in rows]
        return jsonify({"employee": employee_row(e), "from": start.isoformat(), "to": end.isoformat(), "records": out, "counts": counts,
                        "lateMinutes": sum(r["lateMinutes"] for r in out), "undertimeMinutes": sum(r["undertimeMinutes"] for r in out)})

    # ================================================================ overtime & leave
    def overtime_row(o, nm):
        return {"id": o.id, "employeeId": o.employee_id, "employeeName": nm.get(o.employee_id, f"Employee #{o.employee_id}"),
                "date": iso(o.ot_date), "hours": float(o.hours or 0), "multiplier": float(o.rate_multiplier or 1.25),
                "amount": money(o.amount), "status": title(o.status), "reason": o.reason or "", "decidedBy": o.approved_by or ""}

    def leave_row(r, nm):
        return {"id": r.id, "employeeId": r.employee_id, "employeeName": nm.get(r.employee_id, f"Employee #{r.employee_id}"),
                "leaveType": r.leave_type, "from": iso(r.start_date), "to": iso(r.end_date), "days": float(r.days or 0),
                "status": title(r.status), "reason": r.reason or "", "decidedBy": r.approved_by or ""}

    def decide(model, rid, kind, row_fn):
        decision = str(body().get("status") or "").upper()
        if decision not in ("APPROVED", "REJECTED"):
            return field_errors({"status": "Approve or reject the request."})
        row = db.session.query(model).filter_by(id=rid).with_for_update().first()
        if not row:
            return json_error(404, f"{kind.capitalize()} request not found.")
        if (row.status or "PENDING") != "PENDING":
            return json_error(409, f"This {kind} request was already {row.status.lower()}.")
        row.status = decision
        row.approved_by = signed_in_user().username
        legacy.audit(f"{decision.title()} {kind} request #{row.id} for employee {row.employee_id}", entity_type=kind, entity_id=row.id, commit=False)
        db.session.commit()
        return jsonify({"request": row_fn(row, names())})

    @bp.get("/overtime")
    @permission_required("employee_overtime")
    def overtime():
        nm = names()
        return jsonify({"requests": [overtime_row(o, nm) for o in Overtime.query.order_by(Overtime.ot_date.desc(), Overtime.id.desc()).limit(1000)],
                        "multipliers": [{"value": 1.25, "label": "Ordinary day"}, {"value": 1.30, "label": "Rest / special day"},
                                        {"value": 2.60, "label": "Regular holiday"}, {"value": 3.38, "label": "Rest day + regular holiday"}]})

    @bp.post("/overtime")
    @permission_required("employee_overtime")
    def file_overtime():
        data, errors = body(), {}
        emp = employee_or_error(data, errors)
        when = a_date(data, "date", "Date", errors, required=True)
        try:
            hours = parse_decimal(data.get("hours"), label="Hours", places=2, minimum=Decimal("0.25"), maximum=Decimal("24"))
        except InputError as exc:
            errors["hours"] = exc.message
            hours = None
        try:
            mult = parse_decimal(data.get("multiplier") or "1.25", label="Rate multiplier", places=3, minimum=Decimal("1"), maximum=Decimal("5"))
        except InputError as exc:
            errors["multiplier"] = exc.message
            mult = None
        reason = text(data, "reason", "Reason", errors, 500)
        if errors:
            return field_errors(errors)
        hourly = f(emp.monthly_salary) / hr_setting("hr_working_days") / hr_setting("hr_work_hours_per_day")
        o = Overtime(employee_id=emp.id, ot_date=when, hours=hours, rate_multiplier=mult, amount=round(hourly * float(hours) * float(mult), 2),
                     status="PENDING", reason=reason)
        db.session.add(o)
        db.session.flush()
        legacy.audit(f"Filed overtime for employee {emp.employee_no}: {hours}h on {when.isoformat()}", entity_type="overtime", entity_id=o.id, commit=False)
        db.session.commit()
        return jsonify({"request": overtime_row(o, names())}), 201

    @bp.post("/overtime/<int:oid>/decision")
    @permission_required("employee_overtime_status")
    def decide_overtime(oid):
        return decide(Overtime, oid, "overtime", overtime_row)

    @bp.get("/leave")
    @permission_required("employee_leave")
    def leave():
        nm = names()
        return jsonify({"requests": [leave_row(r, nm) for r in Leave.query.order_by(Leave.start_date.desc(), Leave.id.desc()).limit(1000)],
                        "types": list(LEAVE_TYPES)})

    @bp.post("/leave")
    @permission_required("employee_leave")
    def file_leave():
        data, errors = body(), {}
        emp = employee_or_error(data, errors)
        start = a_date(data, "from", "Start date", errors, required=True)
        end = a_date(data, "to", "End date", errors, required=True)
        kind = str(data.get("leaveType") or "")
        if kind not in LEAVE_TYPES:
            errors["leaveType"] = "Choose the leave type."
        reason = text(data, "reason", "Reason", errors, 500)
        if start and end and end < start:
            errors["to"] = "The leave ends before it starts."
        if start and end and (end - start).days > 365:
            errors["to"] = "A leave request can't be longer than a year."
        if not errors and Leave.query.filter(Leave.employee_id == emp.id, Leave.status != "REJECTED", Leave.start_date <= end,
                                             Leave.end_date >= start).first():
            errors["from"] = f"{emp.full_name} already has leave filed for part of these dates."
        if errors:
            return field_errors(errors)
        r = Leave(employee_id=emp.id, leave_type=kind, start_date=start, end_date=end, days=legacy._v1041_days(start, end), status="PENDING", reason=reason)
        db.session.add(r)
        db.session.flush()
        legacy.audit(f"Filed {kind.lower()} leave for employee {emp.employee_no}: {start.isoformat()} to {end.isoformat()}",
                     entity_type="leave", entity_id=r.id, commit=False)
        db.session.commit()
        return jsonify({"request": leave_row(r, names())}), 201

    @bp.post("/leave/<int:lid>/decision")
    @permission_required("employee_leave_status")
    def decide_leave(lid):
        return decide(Leave, lid, "leave", leave_row)

    # ================================================================ payroll
    def statutory_of(p):
        row = Statutory.query.filter_by(payroll_id=p.id).first()
        if row:
            return {CAMEL[k]: money(getattr(row, k)) for k in CAMEL}
        gross = f(p.basic_salary) + f(p.overtime_pay) + f(p.allowances)
        return {CAMEL[k]: money(v) for k, v in legacy._v1040_calc(gross, p.basic_salary, p.deductions, p.absences, p.late_undertime).items()}

    def payroll_row(p, nm, detail=False):
        row = {"id": p.id, "employeeId": p.employee_id, "employeeName": nm.get(p.employee_id, f"Employee #{p.employee_id}"),
               "periodStart": iso(p.period_start), "periodEnd": iso(p.period_end), "basic": money(p.basic_salary),
               "overtime": money(p.overtime_pay), "allowances": money(p.allowances), "deductions": money(p.deductions),
               "absences": money(p.absences), "lateUndertime": money(p.late_undertime),
               "gross": money(f(p.basic_salary) + f(p.overtime_pay) + f(p.allowances)), "netPay": money(p.net_pay),
               "status": title(p.status), "remarks": p.remarks or "", "nextStatus": title(PAYROLL_FLOW.get(p.status)) or None}
        if detail:
            e = db.session.get(Employee, p.employee_id)
            row["employee"] = employee_row(e) if e else None
            row["statutory"] = statutory_of(p)
            row["editable"] = p.status != "PAID"
        return row

    def period_args():
        today = date.today()
        try:
            start = date.fromisoformat(request.args.get("from") or today.replace(day=1).isoformat())
            end = date.fromisoformat(request.args.get("to") or today.isoformat())
        except ValueError:
            raise InputError("from", "Dates must look like 2026-10-05.")
        return (start, end) if start <= end else (end, start)

    def loans_due(eid):
        return sum(min(f(x.monthly_deduction), f(x.balance)) for x in Loan.query.filter_by(employee_id=eid, status="ACTIVE").all())

    @bp.get("/payroll")
    @permission_required("employee_payroll")
    def payroll():
        start, end = period_args()
        nm = names()
        rows = Payroll.query.filter(Payroll.period_start <= end, Payroll.period_end >= start).order_by(Payroll.period_start.desc(), Payroll.id.desc()).all()
        done = {p.employee_id for p in rows}
        staff = [e for e in Employee.query.order_by(Employee.full_name).all() if e.employment_status not in ("Inactive", "Separated")]
        return jsonify({"from": start.isoformat(), "to": end.isoformat(), "payroll": [payroll_row(p, nm) for p in rows],
                        "employees": [{"id": e.id, "fullName": e.full_name, "employeeNo": e.employee_no, "monthlySalary": money(e.monthly_salary),
                                       "hasPayroll": e.id in done} for e in staff]})

    def preview_for(emp, start, end, basic, overtime, allowances, deductions):
        approved = sum(f(x.amount) for x in Overtime.query.filter(Overtime.employee_id == emp.id, Overtime.ot_date >= start,
                                                                   Overtime.ot_date <= end, Overtime.status == "APPROVED").all())
        ot = approved if overtime is None else float(overtime)
        basic_v, absence, late, _ = legacy._v1039_calc_payroll(emp, start, end, None if basic is None else float(basic), ot, float(allowances), float(deductions))
        loans = loans_due(emp.id)
        other = float(deductions) + loans
        gross = basic_v + ot + float(allowances)
        sc = legacy._v1040_calc(gross, basic_v, other, absence, late)
        return {"basic": money(basic_v), "overtime": money(ot), "approvedOvertime": money(approved), "allowances": money(allowances),
                "deductions": money(deductions), "loanDeductions": money(loans), "absences": money(absence), "lateUndertime": money(late),
                "statutory": {CAMEL[k]: money(v) for k, v in sc.items()}}

    def payroll_inputs(data, errors):
        emp = employee_or_error(data, errors)
        start = a_date(data, "from", "Period start", errors, required=True)
        end = a_date(data, "to", "Period end", errors, required=True)
        basic = None if data.get("basic") in (None, "") else amount(data, "basic", "Basic salary", errors)
        overtime = None if data.get("overtime") in (None, "") else amount(data, "overtime", "Overtime pay", errors)
        allowances = amount(data, "allowances", "Allowances", errors)
        deductions = amount(data, "deductions", "Other deductions", errors)
        return emp, start, end, basic, overtime, allowances, deductions

    @bp.post("/payroll/preview")
    @permission_required("employee_payroll")
    def payroll_preview():
        """What generating would save (nothing is saved). Without an employee: the statutory
        computation of the given monthly amounts with the saved Payroll Rules (calculator)."""
        data, errors = body(), {}
        if data.get("employeeId") in (None, "", 0):
            gross_parts = {k: amount(data, k, label, errors) for k, label in (("basic", "Basic salary"), ("overtime", "Overtime pay"),
                                                                                ("allowances", "Allowances"), ("deductions", "Other deductions"),
                                                                                ("lateUndertime", "Late / undertime"))}
            if errors:
                return field_errors(errors)
            gross = float(gross_parts["basic"] + gross_parts["overtime"] + gross_parts["allowances"])
            sc = legacy._v1040_calc(gross, float(gross_parts["basic"]), float(gross_parts["deductions"]), 0, float(gross_parts["lateUndertime"]))
            return jsonify({"statutory": {CAMEL[k]: money(v) for k, v in sc.items()}})
        emp, start, end, basic, overtime, allowances, deductions = payroll_inputs(data, errors)
        if errors:
            return field_errors(errors)
        if end < start:
            return field_errors({"to": "The period ends before it starts."})
        return jsonify({"preview": preview_for(emp, start, end, basic, overtime, allowances, deductions)})

    @bp.post("/payroll")
    @permission_required("employee_payroll")
    def generate():
        data, errors = body(), {}
        emp, start, end, basic, overtime, allowances, deductions = payroll_inputs(data, errors)
        status = str(data.get("status") or "DRAFT").upper()
        remarks = text(data, "remarks", "Remarks", errors, 255)
        if errors:
            return field_errors(errors)
        try:
            p = legacy.generate_payroll(employee_id=emp.id, start=start, end=end, basic=basic, overtime=overtime, allowances=allowances,
                                        deductions=deductions, status=status, remarks=remarks, form_token=data.get("formToken"))
        except legacy.PayrollExists as exc:
            return json_error(409, str(exc))
        except (InputError, legacy.DuplicateSubmission):
            raise                 # 400 with the field / 409 "already submitted" (global handlers)
        except Exception as exc:  # D6: nothing was saved
            return json_error(500, f"Payroll was NOT saved: the statutory computation failed ({exc}). Nothing was changed, including loan balances.")
        return jsonify({"payroll": payroll_row(p, names(), detail=True)}), 201

    @bp.get("/payroll/<int:pid>")
    @permission_required("employee_payroll_print")
    def payslip(pid):
        p = db.session.get(Payroll, pid)
        if not p:
            return json_error(404, "Payroll not found.")
        return jsonify({"payroll": payroll_row(p, names(), detail=True), "corporation": legacy.setting("corporation_name", "CITYLAND 9 CONDOMINIUM CORPORATION"),
                        "address": legacy.setting("address", "")})

    @bp.put("/payroll/<int:pid>/statutory")
    @permission_required("employee_payroll_statutory")
    def edit_statutory(pid):
        p = db.session.query(Payroll).filter_by(id=pid).with_for_update().first()
        if not p:
            return json_error(404, "Payroll not found.")
        if p.status == "PAID":
            return json_error(409, "This payroll is already paid; its amounts can't be changed.")
        data, errors = body(), {}
        values = {k: amount(data, CAMEL[k], CAMEL[k], errors) for k in STATUTORY_INPUTS}
        if errors:
            return field_errors(errors)
        row = Statutory.query.filter_by(payroll_id=p.id).first() or Statutory(payroll_id=p.id)
        db.session.add(row)
        before = {k: money(getattr(row, k)) for k in STATUTORY_INPUTS}
        for k, v in values.items():
            setattr(row, k, v)
        # Totals follow the amounts (H3: the classic screen saved typed totals that didn't match).
        gross = Decimal(str(f(p.basic_salary) + f(p.overtime_pay) + f(p.allowances))).quantize(Decimal("0.01"))
        other = Decimal(str(f(p.deductions) + f(p.absences) + f(p.late_undertime))).quantize(Decimal("0.01"))
        employee_ded = values["sss_employee"] + values["philhealth_employee"] + values["pagibig_employee"] + values["withholding_tax"] + other
        row.gross_pay = gross
        row.total_employee_deductions = employee_ded
        row.total_employer_cost = values["sss_employer"] + values["sss_ec_employer"] + values["philhealth_employer"] + values["pagibig_employer"]
        row.net_pay_after_statutory = gross - employee_ded
        p.net_pay = row.net_pay_after_statutory
        changed = {CAMEL[k]: {"before": before[k], "after": money(values[k])} for k in STATUTORY_INPUTS if before[k] != money(values[k])}
        legacy.audit(f"Edited statutory amounts of payroll #{p.id}", entity_type="payroll", entity_id=p.id, details=changed, commit=False)
        db.session.commit()
        return jsonify({"payroll": payroll_row(p, names(), detail=True)})

    @bp.post("/payroll/<int:pid>/status")
    @permission_required("employee_payroll")
    def advance_status(pid):
        p = db.session.query(Payroll).filter_by(id=pid).with_for_update().first()
        if not p:
            return json_error(404, "Payroll not found.")
        wanted = str(body().get("status") or "").upper()
        if PAYROLL_FLOW.get(p.status) != wanted:
            return json_error(409, f"A {p.status.lower()} payroll can only move to {PAYROLL_FLOW[p.status].lower()}." if p.status in PAYROLL_FLOW
                              else "This payroll is already paid.")
        before, p.status = p.status, wanted
        legacy.audit(f"Payroll #{p.id} {before.lower()} -> {wanted.lower()}", entity_type="payroll", entity_id=p.id, commit=False)
        db.session.commit()
        return jsonify({"payroll": payroll_row(p, names(), detail=True)})

    def year_arg():
        try:
            year = int(request.args.get("year") or date.today().year)
        except ValueError:
            raise InputError("year", "year must be a number.")
        if not 2000 <= year <= 2100:
            raise InputError("year", "year must be between 2000 and 2100.")
        return year

    @bp.get("/thirteenth-month")
    @permission_required("employee_13th_month_full")
    def thirteenth():
        year = year_arg()
        ceiling = round(hr_setting("hr_13th_month_ceiling"), 2)
        rows = []
        for e in Employee.query.order_by(Employee.full_name).all():
            ps = Payroll.query.filter(Payroll.employee_id == e.id, db.extract("year", Payroll.period_end) == year).all()
            basic = sum(f(p.basic_salary) for p in ps)
            if not ps and e.employment_status in ("Inactive", "Separated"):
                continue
            amount_ = round(basic / 12, 2)
            exempt = min(amount_, ceiling)
            rows.append({"employeeId": e.id, "employeeName": e.full_name, "employeeNo": e.employee_no, "payrolls": len(ps),
                         "basicTotal": money(basic), "thirteenth": money(amount_), "exempt": money(exempt), "taxable": money(amount_ - exempt)})
        return jsonify({"year": year, "ceiling": money(ceiling), "rows": rows,
                        "total": money(sum(Decimal(r["thirteenth"]) for r in rows)), "taxable": money(sum(Decimal(r["taxable"]) for r in rows))})

    @bp.get("/payroll-reports")
    @permission_required("employee_payroll_reports")
    def payroll_reports():
        year = year_arg()
        nm = names()
        rows = Payroll.query.filter(db.extract("year", Payroll.period_end) == year).order_by(Payroll.period_end, Payroll.employee_id).all()
        stats = {s.payroll_id: s for s in Statutory.query.filter(Statutory.payroll_id.in_([p.id for p in rows])).all()} if rows else {}
        totals = dict.fromkeys(("basic", "overtime", "allowances", "net", "sssEmployee", "sssEmployer", "philhealthEmployee",
                                "philhealthEmployer", "pagibigEmployee", "pagibigEmployer", "withholdingTax"), Decimal("0"))
        for p in rows:
            totals["basic"] += Decimal(str(f(p.basic_salary))); totals["overtime"] += Decimal(str(f(p.overtime_pay)))
            totals["allowances"] += Decimal(str(f(p.allowances))); totals["net"] += Decimal(str(f(p.net_pay)))
            s = stats.get(p.id)
            if s:
                for key, col in (("sssEmployee", "sss_employee"), ("sssEmployer", "sss_employer"), ("philhealthEmployee", "philhealth_employee"),
                                 ("philhealthEmployer", "philhealth_employer"), ("pagibigEmployee", "pagibig_employee"),
                                 ("pagibigEmployer", "pagibig_employer"), ("withholdingTax", "withholding_tax")):
                    totals[key] += Decimal(str(f(getattr(s, col))))
        return jsonify({"year": year, "rows": [payroll_row(p, nm) for p in rows], "totals": {k: money(v) for k, v in totals.items()}})

    # ================================================================ loans
    def loan_row(x, nm):
        return {"id": x.id, "employeeId": x.employee_id, "employeeName": nm.get(x.employee_id, f"Employee #{x.employee_id}"),
                "loanType": x.loan_type, "referenceNo": x.reference_no or "", "originalAmount": money(x.original_amount),
                "balance": money(x.balance), "monthlyDeduction": money(x.monthly_deduction), "status": x.status or "ACTIVE", "notes": x.notes or ""}

    @bp.get("/loans")
    @permission_required("employee_hr_loans")
    def loans():
        nm = names()
        return jsonify({"loans": [loan_row(x, nm) for x in Loan.query.order_by(Loan.id.desc()).all()], "types": list(LOAN_TYPES),
                        "statuses": list(LOAN_STATUSES)})

    @bp.post("/loans")
    @permission_required("employee_hr_loans")
    def add_loan():
        data, errors = body(), {}
        emp = employee_or_error(data, errors)
        kind = str(data.get("loanType") or "")
        if kind not in LOAN_TYPES:
            errors["loanType"] = "Choose the loan type."
        original = amount(data, "originalAmount", "Loan amount", errors, allow_zero=False, default="")
        balance = amount(data, "balance", "Balance", errors, default=str(original) if original is not None else "0")
        monthly = amount(data, "monthlyDeduction", "Monthly deduction", errors, allow_zero=False, default="")
        ref = text(data, "referenceNo", "Reference no.", errors, 80)
        notes = text(data, "notes", "Notes", errors, 300)
        if original is not None and balance is not None and balance > original:
            errors["balance"] = "The balance can't be more than the loan amount."
        if errors:
            return field_errors(errors)
        x = Loan(employee_id=emp.id, loan_type=kind, reference_no=ref, original_amount=original, balance=balance,
                 monthly_deduction=monthly, status="ACTIVE" if balance > 0 else "PAID", notes=notes)
        db.session.add(x)
        db.session.flush()
        legacy.audit(f"Added {kind} loan for employee {emp.employee_no}: {original} at {monthly}/month", entity_type="loan", entity_id=x.id, commit=False)
        db.session.commit()
        return jsonify({"loan": loan_row(x, names())}), 201

    @bp.put("/loans/<int:lid>")
    @permission_required("employee_hr_loans")
    def edit_loan(lid):
        x = db.session.get(Loan, lid)
        if not x:
            return json_error(404, "Loan not found.")
        data, errors = body(), {}
        status = str(data.get("status") or x.status)
        if status not in LOAN_STATUSES:
            errors["status"] = "Choose Active, On hold or Paid."
        monthly = amount(data, "monthlyDeduction", "Monthly deduction", errors, allow_zero=False, default=str(x.monthly_deduction))
        notes = text(data, "notes", "Notes", errors, 300)
        if status == "ACTIVE" and f(x.balance) <= 0:
            errors["status"] = "This loan has no balance left."
        if errors:
            return field_errors(errors)
        before = {"status": x.status, "monthly": money(x.monthly_deduction)}
        x.status, x.monthly_deduction, x.notes = status, monthly, notes
        legacy.audit(f"Updated loan #{x.id}: status {before['status']} -> {status}, monthly {before['monthly']} -> {money(monthly)}",
                     entity_type="loan", entity_id=x.id, commit=False)
        db.session.commit()
        return jsonify({"loan": loan_row(x, names())})

    # ================================================================ payroll rules
    def settings_out():
        return [{"key": k, "label": label, "group": group, "unit": unit, "value": legacy._v1041_hr_setting(k, legacy.HR_SETTING_DEFAULTS[k]),
                 "default": legacy.HR_SETTING_DEFAULTS[k]} for k, (label, group, unit, _lo, _hi) in SETTINGS.items()]

    @bp.get("/settings")
    @permission_required("employee_hr_settings")
    def settings():
        return jsonify({"settings": settings_out()})

    @bp.put("/settings")
    @permission_required("employee_hr_settings")
    def save_settings():
        values = body().get("values")
        if not isinstance(values, dict):
            return field_errors({"values": "Send the rules to save."})
        errors, clean = {}, {}
        for key, raw in values.items():
            if key not in SETTINGS:
                errors[key] = "Unknown rule."
                continue
            label, _g, _u, lo, hi = SETTINGS[key]
            try:
                clean[key] = parse_decimal(raw, label=label, places=4, minimum=Decimal(str(lo)), maximum=Decimal(str(hi)))
            except InputError as exc:
                errors[key] = exc.message
        merged = {k: Decimal(legacy._v1041_hr_setting(k, legacy.HR_SETTING_DEFAULTS[k])) for k in SETTINGS} | clean
        brackets = [merged[k] for k in ("hr_bir_exempt_threshold", "hr_bir_bracket2", "hr_bir_bracket3", "hr_bir_bracket4", "hr_bir_bracket5")]
        if not errors and any(b >= c for b, c in zip(brackets, brackets[1:])):
            errors["hr_bir_bracket2"] = "BIR brackets must increase: exempt limit < 15% < 20% < 25% < 30% bracket."
        if not errors and merged["hr_philhealth_min_base"] > merged["hr_philhealth_max_base"]:
            errors["hr_philhealth_min_base"] = "The PhilHealth floor can't be above the ceiling."
        if errors:
            return field_errors(errors)
        changed = {}
        for key, v in clean.items():
            text_v = format(v.normalize(), "f")
            row = HRSetting.query.filter_by(key=key).first() or HRSetting(key=key)
            if row.value != text_v:
                changed[key] = {"before": row.value, "after": text_v}
                row.value = text_v
                db.session.add(row)
        if changed:
            legacy.audit(f"Updated Payroll Rules: {', '.join(SETTINGS[k][0] for k in changed)}", entity_type="hr_settings", details=changed, commit=False)
        db.session.commit()
        return jsonify({"settings": settings_out(), "changed": len(changed)})

    return bp
