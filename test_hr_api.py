"""HR & payroll API (/api/hr): permissions, employees, attendance, overtime, leave, payroll (preview,
generate, duplicates, status flow, statutory edits), loans, 13th month, reports, Payroll Rules and
the retired classic pages."""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from conftest import PASSWORD, SA_PASSWORD, SA_USERNAME, api, login

D = Decimal


@pytest.fixture
def mgr(app_module):
    return api(login(app_module, "test_manager", PASSWORD)[0])


@pytest.fixture
def emp(app_module):
    """An employee with ₱26,000 monthly salary (₱1,000/day, ₱125/hour with the default rules)."""
    m = app_module
    with m.app.app_context():
        e = m.Employee(employee_no="HR-T-001", full_name="Hana Tester", monthly_salary=D("26000"), employment_status="Active",
                       date_hired=date(2024, 1, 8))
        m.db.session.add(e); m.db.session.commit()
        eid = e.id
    yield eid
    with m.app.app_context():
        for model in (m.EmployeeLeave, m.EmployeeOvertime, m.EmployeeAttendance, m.EmployeeHRLoan):
            model.query.filter_by(employee_id=eid).delete()
        for p in m.EmployeePayroll.query.filter_by(employee_id=eid).all():
            m.EmployeePayrollStatutory.query.filter_by(payroll_id=p.id).delete()
            m.db.session.delete(p)
        m.Employee.query.filter_by(id=eid).delete()
        m.db.session.commit()


def token(n):
    return f"hr-test-form-token-{n:04d}"


# ------------------------------------------------------------------ permissions
@pytest.mark.parametrize("url", ["/api/hr/employees", "/api/hr/leave", "/api/hr/overtime", "/api/hr/payroll", "/api/hr/loans",
                                 "/api/hr/settings", "/api/hr/thirteenth-month", "/api/hr/payroll-reports"])
def test_hr_is_manager_and_superadmin_only(app_module, url):
    for role, status in (("manager", 200), ("admin", 403), ("staff", 403), ("accounting", 403), ("resident", 403)):
        assert api(login(app_module, f"test_{role}", PASSWORD)[0]).get(url).status_code == status, (url, role)
    assert api(login(app_module, SA_USERNAME, SA_PASSWORD)[0]).get(url).status_code == 200
    assert app_module.app.test_client().get(url).status_code == 401


def test_staff_enter_attendance_only(app_module, emp):
    staff = api(login(app_module, "test_staff", PASSWORD)[0])
    assert staff.get("/api/hr/attendance").status_code == 200
    resp = staff.put("/api/hr/attendance", json={"date": date.today().isoformat(), "records": [{"employeeId": emp, "status": "PRESENT", "timeIn": "08:00"}]})
    assert resp.status_code == 200
    assert staff.get("/api/hr/payroll").status_code == 403 and staff.post("/api/hr/overtime", json={}).status_code == 403
    assert api(login(app_module, "test_accounting", PASSWORD)[0]).get("/api/hr/attendance").status_code == 403


def test_mutations_need_csrf(app_module):
    client = login(app_module, "test_manager", PASSWORD)[0]
    assert client.post("/api/hr/employees", json={"employeeNo": "X", "fullName": "Y"}).status_code == 403


# ------------------------------------------------------------------ employees
def test_employee_add_edit_and_delete_rules(app_module, mgr):
    m = app_module
    bad = mgr.post("/api/hr/employees", json={"employeeNo": "", "fullName": "", "status": "Retired", "email": "x", "monthlySalary": "-1"})
    assert bad.status_code == 400 and {"employeeNo", "fullName", "status", "email", "monthlySalary"} <= set(bad.get_json()["error"]["fields"])
    e = mgr.post("/api/hr/employees", json={"employeeNo": "HR-T-900", "fullName": "Ed Employee", "position": "Guard", "monthlySalary": "18,500"}).get_json()["employee"]
    assert e["monthlySalary"] == "18500.00" and e["status"] == "Active" and e["hasRecords"] is False
    dup = mgr.post("/api/hr/employees", json={"employeeNo": "hr-t-900", "fullName": "Other"})
    assert dup.status_code == 400 and "employeeNo" in dup.get_json()["error"]["fields"]
    edited = mgr.put(f"/api/hr/employees/{e['id']}", json={**e, "position": "Senior Guard", "monthlySalary": "19000"}).get_json()["employee"]
    assert edited["position"] == "Senior Guard" and edited["monthlySalary"] == "19000.00"
    with m.app.app_context():
        assert m.AuditLog.query.filter(m.AuditLog.action.like("Updated employee HR-T-900%")).count() == 1
        m.db.session.add(m.EmployeeAttendance(employee_id=e["id"], attendance_date=date(2026, 1, 5), status="PRESENT"))
        m.db.session.commit()
    blocked = mgr.delete(f"/api/hr/employees/{e['id']}")
    assert blocked.status_code == 409 and "Separated" in blocked.get_json()["error"]["message"]
    with m.app.app_context():
        m.EmployeeAttendance.query.filter_by(employee_id=e["id"]).delete(); m.db.session.commit()
    assert mgr.delete(f"/api/hr/employees/{e['id']}").status_code == 204


# ------------------------------------------------------------------ attendance
def test_attendance_save_validate_and_history(app_module, mgr, emp):
    today = date.today()
    future = mgr.put("/api/hr/attendance", json={"date": (today + timedelta(days=1)).isoformat(), "records": [{"employeeId": emp, "status": "PRESENT"}]})
    assert future.status_code == 400
    backwards = mgr.put("/api/hr/attendance", json={"date": today.isoformat(), "records": [{"employeeId": emp, "status": "PRESENT", "timeIn": "17:00", "timeOut": "08:00"}]})
    assert backwards.status_code == 400
    day = (today - timedelta(days=1)).isoformat()
    assert mgr.put("/api/hr/attendance", json={"date": day, "records": [{"employeeId": emp, "status": "late", "timeIn": "08:20", "timeOut": "17:00"}]}).status_code == 200
    rec = next(e for e in mgr.get(f"/api/hr/attendance?date={day}").get_json()["employees"] if e["id"] == emp)["record"]
    assert rec["status"] == "LATE" and rec["lateMinutes"] == 15                       # 20 minutes minus the 5-minute grace
    mgr.put("/api/hr/attendance", json={"date": day, "records": [{"employeeId": emp, "status": "ABSENT", "timeIn": "08:00"}]})
    rec = next(e for e in mgr.get(f"/api/hr/attendance?date={day}").get_json()["employees"] if e["id"] == emp)["record"]
    assert rec["status"] == "ABSENT" and rec["timeIn"] is None                        # no times on an absence
    hist = mgr.get(f"/api/hr/attendance/history/{emp}?from={day}&to={day}").get_json()
    assert hist["counts"] == {"ABSENT": 1}
    mgr.put("/api/hr/attendance", json={"date": day, "records": [{"employeeId": emp, "status": ""}]})   # cleared
    assert next(e for e in mgr.get(f"/api/hr/attendance?date={day}").get_json()["employees"] if e["id"] == emp)["record"] is None


# ------------------------------------------------------------------ overtime & leave
def test_overtime_amount_and_single_decision(mgr, emp):
    o = mgr.post("/api/hr/overtime", json={"employeeId": emp, "date": "2026-09-10", "hours": "2", "multiplier": "1.25", "reason": "Pump repair"}).get_json()["request"]
    assert o["amount"] == "312.50" and o["status"] == "Pending"                      # 125/hour x 2 x 1.25
    assert mgr.post(f"/api/hr/overtime/{o['id']}/decision", json={"status": "Approved"}).get_json()["request"]["status"] == "Approved"
    assert mgr.post(f"/api/hr/overtime/{o['id']}/decision", json={"status": "Rejected"}).status_code == 409


def test_leave_days_and_overlap(mgr, emp):
    r = mgr.post("/api/hr/leave", json={"employeeId": emp, "from": "2026-12-01", "to": "2026-12-03", "leaveType": "SICK"}).get_json()["request"]
    assert r["days"] == 3
    overlap = mgr.post("/api/hr/leave", json={"employeeId": emp, "from": "2026-12-03", "to": "2026-12-04", "leaveType": "VACATION"})
    assert overlap.status_code == 400 and "already has leave" in overlap.get_json()["error"]["message"]
    mgr.post(f"/api/hr/leave/{r['id']}/decision", json={"status": "Rejected"})
    assert mgr.post("/api/hr/leave", json={"employeeId": emp, "from": "2026-12-03", "to": "2026-12-04", "leaveType": "VACATION"}).status_code == 201
    assert mgr.post("/api/hr/leave", json={"employeeId": emp, "from": "2026-12-10", "to": "2026-12-09", "leaveType": "SICK"}).status_code == 400


# ------------------------------------------------------------------ payroll
def test_payroll_preview_generate_duplicate_and_status_flow(app_module, mgr, emp):
    m = app_module
    with m.app.app_context():
        m.db.session.add(m.EmployeeAttendance(employee_id=emp, attendance_date=date(2026, 8, 4), status="ABSENT"))
        m.db.session.add(m.EmployeeOvertime(employee_id=emp, ot_date=date(2026, 8, 6), hours=2, rate_multiplier=1.25, amount=312.5, status="APPROVED"))
        m.db.session.add(m.EmployeeOvertime(employee_id=emp, ot_date=date(2026, 8, 7), hours=2, rate_multiplier=1.25, amount=312.5, status="PENDING"))
        m.db.session.add(m.EmployeeHRLoan(employee_id=emp, loan_type="SSS", original_amount=3000, balance=3000, monthly_deduction=500, status="ACTIVE"))
        m.db.session.commit()
    form = {"employeeId": emp, "from": "2026-08-01", "to": "2026-08-31", "allowances": "1000", "deductions": "0"}
    preview = mgr.post("/api/hr/payroll/preview", json=form).get_json()["preview"]
    assert preview["absences"] == "1000.00" and preview["overtime"] == "312.50" and preview["loanDeductions"] == "500.00"   # approved OT only
    created = mgr.post("/api/hr/payroll", json={**form, "formToken": token(1)})
    assert created.status_code == 201
    p = created.get_json()["payroll"]
    assert p["netPay"] == preview["statutory"]["netPay"] and p["status"] == "Draft" and p["nextStatus"] == "Final"
    assert p["statutory"]["grossPay"] == "27312.50"
    with m.app.app_context():
        assert float(m.EmployeeHRLoan.query.filter_by(employee_id=emp).first().balance) == 2500
    # H1: an overlapping second payroll is refused (it paid twice and deducted the loan twice).
    again = mgr.post("/api/hr/payroll", json={**form, "from": "2026-08-15", "to": "2026-09-14", "formToken": token(2)})
    assert again.status_code == 409 and "already has a payroll" in again.get_json()["error"]["message"]
    reused = mgr.post("/api/hr/payroll", json={**form, "from": "2026-10-01", "to": "2026-10-15", "formToken": token(1)})
    assert reused.status_code == 409                                                   # same form token: one payroll only
    # H2: Draft -> Final -> Paid, forward only.
    assert mgr.post(f"/api/hr/payroll/{p['id']}/status", json={"status": "PAID"}).status_code == 409
    assert mgr.post(f"/api/hr/payroll/{p['id']}/status", json={"status": "FINAL"}).get_json()["payroll"]["status"] == "Final"
    # H3: editing the statutory amounts recomputes the totals and the net pay.
    st = mgr.get(f"/api/hr/payroll/{p['id']}").get_json()["payroll"]["statutory"]
    raised = D(st["withholdingTax"]) + D("100")
    edited = mgr.put(f"/api/hr/payroll/{p['id']}/statutory", json={**st, "withholdingTax": str(raised)}).get_json()["payroll"]
    assert D(edited["netPay"]) == D(p["netPay"]) - D("100") and D(edited["statutory"]["netPay"]) == D(edited["netPay"])
    assert mgr.post(f"/api/hr/payroll/{p['id']}/status", json={"status": "PAID"}).get_json()["payroll"]["status"] == "Paid"
    locked = mgr.put(f"/api/hr/payroll/{p['id']}/statutory", json=st)
    assert locked.status_code == 409 and "already paid" in locked.get_json()["error"]["message"]
    listed = mgr.get("/api/hr/payroll?from=2026-08-01&to=2026-08-31").get_json()
    assert any(r["id"] == p["id"] for r in listed["payroll"]) and next(e for e in listed["employees"] if e["id"] == emp)["hasPayroll"]
    report = mgr.get("/api/hr/payroll-reports?year=2026").get_json()
    assert any(r["id"] == p["id"] for r in report["rows"]) and D(report["totals"]["net"]) >= D(edited["netPay"])


def test_calculator_uses_the_saved_rules(app_module, mgr):
    m = app_module
    base = mgr.post("/api/hr/payroll/preview", json={"basic": "30000"}).get_json()["statutory"]
    assert base["sssEmployee"] == "1500.00"                                            # 5% of 30,000
    assert mgr.put("/api/hr/settings", json={"values": {"hr_sss_employee_rate": "4.5"}}).status_code == 200
    try:
        assert mgr.post("/api/hr/payroll/preview", json={"basic": "30000"}).get_json()["statutory"]["sssEmployee"] == "1350.00"
    finally:
        mgr.put("/api/hr/settings", json={"values": {"hr_sss_employee_rate": "5"}})


def test_payroll_rules_validation(app_module, mgr):
    rules = {r["key"]: r for r in mgr.get("/api/hr/settings").get_json()["settings"]}
    assert rules["hr_working_days"]["value"] == "26" and rules["hr_bir_rate6"]["group"] == "BIR withholding"
    assert mgr.put("/api/hr/settings", json={"values": {"hr_sss_employee_rate": "150"}}).status_code == 400
    assert mgr.put("/api/hr/settings", json={"values": {"hr_working_days": "0"}}).status_code == 400
    assert mgr.put("/api/hr/settings", json={"values": {"bogus": "1"}}).status_code == 400
    order = mgr.put("/api/hr/settings", json={"values": {"hr_bir_bracket2": "10000"}})       # below the exempt limit
    assert order.status_code == 400 and "increase" in order.get_json()["error"]["message"]
    assert mgr.put("/api/hr/settings", json={"values": {"hr_philhealth_min_base": "200000"}}).status_code == 400


def test_loans(app_module, mgr, emp):
    assert mgr.post("/api/hr/loans", json={"employeeId": emp, "loanType": "SSS", "originalAmount": "1000", "balance": "2000", "monthlyDeduction": "100"}).status_code == 400
    x = mgr.post("/api/hr/loans", json={"employeeId": emp, "loanType": "COMPANY", "originalAmount": "6000", "monthlyDeduction": "1000"}).get_json()["loan"]
    assert x["balance"] == "6000.00" and x["status"] == "ACTIVE"
    held = mgr.put(f"/api/hr/loans/{x['id']}", json={"status": "HOLD"}).get_json()["loan"]
    assert held["status"] == "HOLD"
    preview = mgr.post("/api/hr/payroll/preview", json={"employeeId": emp, "from": "2026-07-01", "to": "2026-07-31"}).get_json()["preview"]
    assert preview["loanDeductions"] == "0.00"                                          # loans on hold aren't deducted


# ------------------------------------------------------------------ classic pages
def test_classic_hr_pages_redirect(app_module):
    manager = login(app_module, "test_manager", PASSWORD)[0]
    assert manager.get("/employees").headers["Location"] == "/app/hr/employees"
    assert manager.get("/employees/payroll").headers["Location"] == "/app/hr/payroll"
    assert manager.get("/employees/payroll/5/print").headers["Location"] == "/app/hr/payroll?payslip=5"
    assert manager.get("/employees/hr-settings").headers["Location"] == "/app/hr/tax-rules"
    assert manager.get("/employees/overtime").headers["Location"] == "/app/hr/attendance?tab=overtime"
    staff = login(app_module, "test_staff", PASSWORD)[0]
    assert staff.get("/employees/attendance").headers["Location"] == "/app/staff/attendance-entry"
    m = app_module
    with m.app.app_context():
        before = m.Employee.query.count()
    resp = manager.post("/employees", data={"employee_no": "OLD-TAB", "full_name": "Old Tab"})
    assert resp.status_code == 303 and "moved=form" in resp.headers["Location"]
    with m.app.app_context():
        assert m.Employee.query.count() == before
