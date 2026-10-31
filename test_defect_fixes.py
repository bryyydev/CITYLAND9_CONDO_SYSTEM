"""Fixes for the defects found during the migration (docs/module-migration-checklist.md §5):
D1 certificate crash, D5 leave/overtime status, D6 payroll all-or-nothing, D8 13th-month ceiling,
and the Units search for past owners/tenants (the old Tenants list)."""
from datetime import date
from decimal import Decimal

import pytest

from conftest import SA_PASSWORD, SA_USERNAME, api, login


@pytest.fixture
def sa(app_module):
    return login(app_module, SA_USERNAME, SA_PASSWORD)[0]


@pytest.fixture
def employee(app_module):
    m = app_module
    with m.app.app_context():
        e = m.Employee(employee_no="DEF-001", full_name="Dina Defect", monthly_salary=Decimal("30000"), date_hired=date(2025, 1, 6))
        m.db.session.add(e); m.db.session.commit()
        eid = e.id
    yield eid
    with m.app.app_context():
        for model in ("EmployeeLeave", "EmployeeOvertime"):
            getattr(m, model).query.filter_by(employee_id=eid).delete()
        for p in m.EmployeePayroll.query.filter_by(employee_id=eid).all():
            m.EmployeePayrollStatutory.query.filter_by(payroll_id=p.id).delete()
            m.db.session.delete(p)
        m.EmployeeHRLoan.query.filter_by(employee_id=eid).delete()
        m.db.session.delete(m.db.session.get(m.Employee, eid)); m.db.session.commit()


def test_d1_move_certificate_is_generated(app_module, sa):
    m = app_module
    with m.app.app_context():
        unit = m.Unit.query.filter_by(unit_no="TEST-502").first()
        tenant = m.Tenant.query.filter_by(unit_id=unit.id).first()
        uid, tid = unit.id, tenant.id
        before = m.MoveCertificate.query.count()
    resp = api(sa).post("/api/certificates", json={"unitId": uid, "moveType": "Move In", "personType": "Tenant",
                                                   "personId": tid, "certificateDate": "2026-10-04"})
    assert resp.status_code == 201                                     # was HTTP 500 (AttributeError)
    with m.app.app_context():
        assert m.MoveCertificate.query.count() == before + 1
        cert = m.MoveCertificate.query.order_by(m.MoveCertificate.id.desc()).first()
        assert cert.issued_by == SA_USERNAME and cert.certificate_no.startswith("CL9-2026-")
        m.db.session.delete(cert); m.db.session.commit()


def test_d5_status_values_are_checked_and_approver_recorded(app_module, sa, employee):
    m = app_module
    client = api(sa)
    base = {"employeeId": employee, "from": "2026-11-02", "to": "2026-11-03", "leaveType": "VACATION"}
    assert client.post("/api/hr/leave", json={**base, "leaveType": "WHATEVER"}).status_code == 400          # refused
    with m.app.app_context():
        assert m.EmployeeLeave.query.filter_by(employee_id=employee).count() == 0
    req = client.post("/api/hr/leave", json=base).get_json()["request"]
    assert req["status"] == "Pending" and not req["decidedBy"]                                             # always filed Pending
    assert client.post(f"/api/hr/leave/{req['id']}/decision", json={"status": "paid-already"}).status_code == 400
    done = client.post(f"/api/hr/leave/{req['id']}/decision", json={"status": "approved"}).get_json()["request"]
    with m.app.app_context():
        leave = m.db.session.get(m.EmployeeLeave, req["id"])
        assert leave.status == "APPROVED" and leave.approved_by == SA_USERNAME and done["decidedBy"] == SA_USERNAME
    assert client.post(f"/api/hr/leave/{req['id']}/decision", json={"status": "rejected"}).status_code == 409   # decided once
    assert client.post("/api/hr/overtime", json={"employeeId": employee, "date": "2026-11-04", "hours": "30"}).status_code == 400
    with m.app.app_context():
        assert m.EmployeeOvertime.query.filter_by(employee_id=employee).count() == 0


def test_d6_payroll_is_all_or_nothing(app_module, sa, employee, monkeypatch):
    m = app_module
    client = api(sa)
    with m.app.app_context():
        m.db.session.add(m.EmployeeHRLoan(employee_id=employee, loan_type="SSS", original_amount=5000, balance=5000,
                                          monthly_deduction=1000, status="ACTIVE"))
        m.db.session.commit()
    form = {"employeeId": employee, "from": "2026-11-01", "to": "2026-11-15", "status": "DRAFT"}

    def broken(*args, **kwargs):
        raise RuntimeError("statutory table missing")
    monkeypatch.setattr(m, "_v1040_calc", broken)
    resp = client.post("/api/hr/payroll", json={**form, "formToken": "d6-token-number-one"})
    assert resp.status_code == 500 and "Payroll was NOT saved" in resp.get_json()["error"]["message"]
    with m.app.app_context():
        assert m.EmployeePayroll.query.filter_by(employee_id=employee).count() == 0      # no half-saved payroll
        assert float(m.EmployeeHRLoan.query.filter_by(employee_id=employee).first().balance) == 5000
    monkeypatch.undo()
    assert client.post("/api/hr/payroll", json={**form, "formToken": "d6-token-number-two"}).status_code == 201
    with m.app.app_context():
        p = m.EmployeePayroll.query.filter_by(employee_id=employee).one()
        assert m.EmployeePayrollStatutory.query.filter_by(payroll_id=p.id).count() == 1
        assert float(m.EmployeeHRLoan.query.filter_by(employee_id=employee).first().balance) == 4000   # reduced together
    assert client.post("/api/hr/payroll", json={**form, "from": "2026-12-01", "to": "2026-12-15", "status": "BOGUS",
                                                "formToken": "d6-token-number-three"}).status_code == 400   # unknown status: refused
    with m.app.app_context():
        assert m.EmployeePayroll.query.filter_by(employee_id=employee).count() == 1


def test_d8_13th_month_report_uses_the_ceiling(app_module, sa, employee):
    m = app_module
    with m.app.app_context():
        m.db.session.add(m.EmployeePayroll(employee_id=employee, period_start=date(2031, 1, 1), period_end=date(2031, 12, 31),
                                           basic_salary=1200000, overtime_pay=0, allowances=0, deductions=0, absences=0,
                                           late_undertime=0, net_pay=0, status="PAID"))
        m.db.session.commit()
    data = api(sa).get("/api/hr/thirteenth-month?year=2031").get_json()
    row = next(r for r in data["rows"] if r["employeeId"] == employee)
    # 1,200,000 / 12 = 100,000 -> 90,000 tax-exempt, 10,000 taxable (default ceiling)
    assert (row["thirteenth"], row["exempt"], row["taxable"]) == ("100000.00", "90000.00", "10000.00") and data["ceiling"] == "90000.00"


def test_units_search_can_include_past_owners_and_tenants(app_module, sa):
    m = app_module
    client = api(sa)
    with m.app.app_context():
        unit = m.Unit.query.filter_by(unit_no="TEST-503").first()
        m.db.session.add(m.Tenant(unit_id=unit.id, tenant_name="Petra Pastenant", status="Past", contact_no="09179990000"))
        m.db.session.commit()
    try:
        assert client.get("/api/units?q=Pastenant").get_json()["units"] == []
        assert [u["unitNo"] for u in client.get("/api/units?q=Pastenant&past=1").get_json()["units"]] == ["TEST-503"]
        assert [u["unitNo"] for u in client.get("/api/units?q=09179990000&past=1").get_json()["units"]] == ["TEST-503"]
        assert sa.get("/tenants?q=Petra").headers["Location"] == "/app/superadmin/units?q=Petra&past=1"
    finally:
        with m.app.app_context():
            m.Tenant.query.filter_by(tenant_name="Petra Pastenant").delete(); m.db.session.commit()
