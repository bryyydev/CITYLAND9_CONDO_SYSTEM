"""Phase B1: issued bills are frozen (Q1) and storage is charged from a cut-off month (D13)."""
from decimal import Decimal

import pytest

from conftest import PASSWORD, login, api


def figures(m, bill_id):
    with m.app.test_request_context("/"):
        bill = m.db.session.get(m.Billing, bill_id)
        calc = m._bill_calc(bill)
        return {k: calc[k] for k in ("condo", "parking", "storage", "total")}


def set_setting(m, key, value):
    with m.app.app_context():
        row = m.Setting.query.filter_by(key=key).first() or m.Setting(key=key)
        old = row.value
        row.value = value
        m.db.session.add(row); m.db.session.commit()
        return old


@pytest.fixture
def test502(app_module):
    with app_module.app.app_context():
        unit = app_module.Unit.query.filter_by(unit_no="TEST-502").first()
        bill = app_module.Billing.query.filter_by(unit_id=unit.id).first()
        return {"unit": unit.id, "bill": bill.id, "month": bill.billing_month, "storage": Decimal(str(bill.storage_dues))}


def test_rate_change_does_not_change_an_issued_bill(app_module, test502):
    m = app_module
    before = figures(m, test502["bill"])
    old = set_setting(m, "one_bed_rate_per_sqm", "999")
    with m.app.app_context():
        unit = m.db.session.get(m.Unit, test502["unit"]); old_rate = unit.unit_rate_per_sqm
        unit.unit_rate_per_sqm = 999; m.db.session.commit()
    try:
        assert figures(m, test502["bill"]) == before
    finally:
        set_setting(m, "one_bed_rate_per_sqm", old)
        with m.app.app_context():
            m.db.session.get(m.Unit, test502["unit"]).unit_rate_per_sqm = old_rate; m.db.session.commit()


def test_new_bills_use_the_current_rate(app_module, test502):
    m = app_module
    admin, _ = login(m, "superadmin", "Test-Admin-Pass-1")
    assert api(admin).post("/api/billing/generate", json={"month": "2031-02"}).status_code == 200
    with m.app.app_context():
        unit = m.db.session.get(m.Unit, test502["unit"])
        bill = m.Billing.query.filter_by(unit_id=unit.id, billing_month="2031-02").first()
        assert bill is not None and float(bill.assessment) == pytest.approx(m.unit_dues(unit))


def test_recalculate_reprices_from_current_rates_and_is_audited(app_module, test502):
    m = app_module
    with m.app.app_context():
        unit = m.db.session.get(m.Unit, test502["unit"]); old_area = unit.area_sqm
        unit.area_sqm = (old_area or 0) + 10; m.db.session.commit()
    try:
        staff = api(login(m, "test_staff", PASSWORD)[0])
        assert staff.post(f"/api/billing/{test502['bill']}/recalculate").status_code == 403   # refused
        assert figures(m, test502["bill"])["condo"] != Decimal(str(round((old_area + 10) * 75, 2)))
        accounting = api(login(m, "test_accounting", PASSWORD)[0])
        resp = accounting.post(f"/api/billing/{test502['bill']}/recalculate")
        assert resp.status_code == 200 and resp.get_json()["changed"] is True
        with m.app.app_context():
            unit = m.db.session.get(m.Unit, test502["unit"])
            assert float(m.db.session.get(m.Billing, test502["bill"]).assessment) == pytest.approx(m.unit_dues(unit))
            assert m.AuditLog.query.filter(m.AuditLog.action.like("Recalculated SOA for unit TEST-502%")).count() == 1
    finally:
        with m.app.app_context():
            m.db.session.get(m.Unit, test502["unit"]).area_sqm = old_area; m.db.session.commit()
        m.app.test_client()  # fresh request context for the next test


def test_hand_edited_soa_cannot_be_recalculated(app_module, test502):
    m = app_module
    with m.app.app_context():
        bill = m.db.session.get(m.Billing, test502["bill"]); bill.soa_manual_override = True; m.db.session.commit()
    try:
        admin = api(login(m, "test_admin", PASSWORD)[0])
        resp = admin.post(f"/api/billing/{test502['bill']}/recalculate")
        assert resp.status_code == 409 and "Use Edit SOA" in resp.get_json()["error"]["message"]
    finally:
        with m.app.app_context():
            m.db.session.get(m.Billing, test502["bill"]).soa_manual_override = False; m.db.session.commit()


def test_storage_is_added_only_from_the_cutoff_month(app_module, test502):
    m = app_module
    assert test502["storage"] > 0, "TEST-502 has storage in the seed data"
    old = set_setting(m, "storage_in_total_from", "9999-12")
    try:
        without = figures(m, test502["bill"])
        assert without["storage"] == 0
        set_setting(m, "storage_in_total_from", test502["month"])
        with_storage = figures(m, test502["bill"])
        assert with_storage["storage"] == test502["storage"]
        assert with_storage["total"] == without["total"] + test502["storage"]
        # The SOA (Billing API, same JSON as the resident portal) tells which case applies
        admin = api(login(m, "superadmin", "Test-Admin-Pass-1")[0])
        assert admin.get(f"/api/billing/{test502['bill']}").get_json()["bill"]["charges"]["storageIncluded"] is True
        set_setting(m, "storage_in_total_from", "9999-12")
        assert admin.get(f"/api/billing/{test502['bill']}").get_json()["bill"]["charges"]["storageIncluded"] is False
    finally:
        set_setting(m, "storage_in_total_from", old or "9999-12")


def test_rates_and_rules_validates_the_storage_month(app_module):
    m = app_module
    old = set_setting(m, "storage_in_total_from", "2031-05")
    try:
        client, _ = login(m, "superadmin", "Test-Admin-Pass-1")
        client.environ_base["HTTP_X_CSRFTOKEN"] = client.get("/api/auth/csrf").get_json()["csrfToken"]
        # Rates & Rules moved to the React app; the API applies the same rule (and saves nothing on error).
        resp = client.put("/api/admin/rates", json={"storageInTotalFrom": "May 2031"})
        assert resp.status_code == 400 and "2026-10" in resp.get_json()["error"]["fields"]["storageInTotalFrom"]
        with m.app.app_context():
            assert m.Setting.query.filter_by(key="storage_in_total_from").first().value == "2031-05"
        assert client.put("/api/admin/rates", json={"storageInTotalFrom": "2031-07"}).status_code == 200
        with m.app.app_context():
            assert m.Setting.query.filter_by(key="storage_in_total_from").first().value == "2031-07"
    finally:
        set_setting(m, "storage_in_total_from", old or "9999-12")
