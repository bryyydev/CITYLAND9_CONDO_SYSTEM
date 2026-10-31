"""Schema Phase A: database-level uniqueness and checks, automatic who/when tracking,
unused tables removed. These run on SQLite; the same constraints were verified on
MariaDB by the migration rehearsal (database/db_migrate.py)."""
import pytest
from sqlalchemy.exc import IntegrityError

from conftest import api, PASSWORD, login


def insert_fails(app_module, obj):
    with app_module.app.app_context():
        app_module.db.session.add(obj)
        with pytest.raises(IntegrityError):
            app_module.db.session.commit()
        app_module.db.session.rollback()


def a_bill(app_module):
    with app_module.app.app_context():
        b = app_module.Billing.query.first()
        return b.unit_id, b.billing_month


def test_second_bill_for_same_unit_and_month_is_rejected(app_module):
    unit_id, month = a_bill(app_module)
    insert_fails(app_module, app_module.Billing(unit_id=unit_id, billing_month=month))


def test_invalid_month_is_rejected(app_module):
    unit_id, _ = a_bill(app_module)
    insert_fails(app_module, app_module.Billing(unit_id=unit_id, billing_month="2026-13"))
    insert_fails(app_module, app_module.WaterReading(unit_id=unit_id, reading_month="Sept 26"))


def test_negative_payment_is_rejected(app_module):
    with app_module.app.app_context():
        bill_id = app_module.Billing.query.first().id
    insert_fails(app_module, app_module.Payment(billing_id=bill_id, amount=-1))


def test_leave_cannot_end_before_it_starts(app_module):
    from datetime import date
    with app_module.app.app_context():
        emp = app_module.Employee(employee_no="PA-1", full_name="Phase A")
        app_module.db.session.add(emp); app_module.db.session.commit(); emp_id = emp.id
    insert_fails(app_module, app_module.EmployeeLeave(employee_id=emp_id, leave_type="VACATION",
                                                      start_date=date(2026, 10, 5), end_date=date(2026, 10, 1)))


def test_changes_record_who_and_when(app_module):
    with app_module.app.app_context():
        unit = app_module.Unit.query.filter_by(unit_no="TEST-504").first()
        uid, old_area = unit.id, unit.area_sqm
    client = api(login(app_module, "test_admin", PASSWORD)[0])
    assert client.put(f"/api/units/{uid}", json={"floor": "5", "type": "3 BEDROOM", "areaSqm": str((old_area or 0) + 1),
                                                 "status": "Occupied", "occupancy": "Tenant"}).status_code == 200
    with app_module.app.app_context():
        unit = app_module.db.session.get(app_module.Unit, uid)
        assert unit.updated_by == "test_admin"
        assert unit.updated_at is not None


def test_unused_tables_are_gone(app_module):
    names = set(app_module.db.metadata.tables)
    assert "employee_payslip_item" not in names and "employee_holiday" not in names


def test_rates_and_rules_no_longer_writes_hr_copies(app_module):
    client, _ = login(app_module, "superadmin", "Test-Admin-Pass-1")
    client.post("/settings", data={"corporation_name": "CITYLAND 9 CONDOMINIUM CORPORATION"})
    with app_module.app.app_context():
        assert app_module.Setting.query.filter(app_module.Setting.key.like("hr_%")).count() == 0
