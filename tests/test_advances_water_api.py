"""Advance Payments (/api/advances) and Water Readings (/api/water): permissions, validation,
persistence, receipts, bill synchronisation, closed periods, CSRF and the retired classic pages."""
import secrets
from datetime import date
from decimal import Decimal

import pytest

from conftest import PASSWORD, SA_PASSWORD, SA_USERNAME, api, login


def token():
    return secrets.token_urlsafe(24)


@pytest.fixture
def sa(app_module):
    return api(login(app_module, SA_USERNAME, SA_PASSWORD)[0])


def unit_id(m, no):
    with m.app.app_context():
        return m.Unit.query.filter_by(unit_no=no).first().id


@pytest.fixture
def cleanup(app_module):
    """Remove water readings (by month) and advances (by remark) a test created, with their receipts."""
    months, remarks = [], []
    yield months, remarks
    m = app_module
    with m.app.app_context():
        for r in m.WaterReading.query.filter(m.WaterReading.reading_month.in_(months)).all():
            for a in m.ReceiptAllocation.query.filter_by(water_reading_id=r.id).all():
                m.db.session.delete(a.receipt)
            m.db.session.delete(r)
        m.Billing.query.filter(m.Billing.billing_month.in_(months)).delete(synchronize_session=False)
        for adv in m.AdvancePayment.query.filter(m.AdvancePayment.remarks.in_(remarks)).all():
            m.AdvanceApplication.query.filter_by(advance_payment_id=adv.id).delete()
            for a in m.ReceiptAllocation.query.filter_by(advance_payment_id=adv.id).all():
                m.db.session.delete(a.receipt)
            m.db.session.delete(adv)
        m.db.session.commit()


# ------------------------------------------------------------------ permissions
def test_permissions(app_module):
    for role, adv, water in (("admin", 200, 200), ("accounting", 200, 403), ("manager", 403, 403), ("staff", 403, 403), ("resident", 403, 403)):
        client = api(login(app_module, f"test_{role}", PASSWORD)[0])
        assert client.get("/api/advances").status_code == adv, role
        assert client.get("/api/water").status_code == water, role
    anon = app_module.app.test_client()
    assert anon.get("/api/advances").status_code == 401 and anon.get("/api/water").status_code == 401


# ------------------------------------------------------------------ advance payments
def test_advance_is_recorded_receipted_and_applied(app_module, sa, cleanup):
    months, remarks = cleanup
    m = app_module
    uid = unit_id(m, "TEST-504")
    with m.app.app_context():                 # a bill in the first covered month: the advance is applied to it at once
        m.db.session.add(m.Billing(unit_id=uid, billing_month="2038-01", assessment=Decimal("1000"), parking_dues=0, storage_dues=0, water=0,
                                   other=0, penalty=0, adjustment=0, previous_balance=0, amount_paid=0, due_date=date(2038, 1, 8), status="Unpaid"))
        m.db.session.commit()
    months.append("2038-01"); remarks.append("pytest adv api")
    body = {"unitId": uid, "amount": "3000", "startMonth": "2038-01", "months": 3, "method": "ONLINE", "reference": "GCASH-77",
            "date": "2037-12-20", "remarks": "pytest adv api", "formToken": token()}
    r = sa.post("/api/advances", json=body)
    assert r.status_code == 201, r.get_json()
    a = r.get_json()["advance"]
    assert r.get_json()["receiptNo"].startswith("OR-2037-") and a["monthlyAmount"] == "1000.00" and a["endMonth"] == "2038-03"
    assert a["applied"] == "1000.00" and a["remaining"] == "2000.00" and a["payerName"] == "Ana Garcia"
    listed = sa.get(f"/api/advances?unit={uid}&status=open").get_json()
    assert any(x["id"] == a["id"] for x in listed["advances"])
    assert sa.post("/api/advances", json={**body, "formToken": body["formToken"]}).status_code == 409      # same form twice


def test_advance_validation(app_module, sa):
    errs = sa.post("/api/advances", json={"unitId": unit_id(app_module, "P-TEST-01"), "amount": "abc", "startMonth": "soon", "months": 0,
                                          "method": "CHECK", "formToken": token()}).get_json()["error"]["fields"]
    assert "amount" in errs
    errs = sa.post("/api/advances", json={"unitId": unit_id(app_module, "P-TEST-01"), "amount": "100", "startMonth": "2038-05", "months": 1,
                                          "method": "CASH", "formToken": token()}).get_json()["error"]["fields"]
    assert "unitId" in errs                                                                      # parking units can't prepay dues
    errs = sa.post("/api/advances", json={"unitId": unit_id(app_module, "TEST-501"), "amount": "100", "startMonth": "2038-05", "months": 1,
                                          "method": "CHECK", "formToken": token()}).get_json()["error"]["fields"]
    assert "reference" in errs
    assert sa.post("/api/advances", json={"unitId": unit_id(app_module, "TEST-501"), "amount": "100", "startMonth": "2038-05",
                                          "months": 1, "method": "CASH", "formToken": "x"}).status_code == 400
    opts = sa.get("/api/advances/options").get_json()["units"]
    assert {u["unitNo"] for u in opts} == {"TEST-501", "TEST-502", "TEST-503", "TEST-504"}


# ------------------------------------------------------------------ water readings
def test_water_reading_save_correct_and_bill_sync(app_module, sa, cleanup):
    months, _ = cleanup
    m = app_module
    uid = unit_id(m, "TEST-503")
    months.extend(["2038-06", "2038-07"])
    assert sa.post("/api/water", json={"unitId": uid, "month": "2038-06", "previous": "100", "current": "110", "rate": "50"}).status_code == 201
    view = sa.get("/api/water?month=2038-07").get_json()
    todo = next(x for x in view["missing"] if x["unitId"] == uid)
    assert todo["previous"] == 110 and todo["previousMonth"] == "2038-06"                    # carried from last month
    assert sa.get(f"/api/water/previous?unit={uid}&month=2038-07").get_json()["previous"] == 110
    with m.app.app_context():                  # a bill exists for July: saving the reading updates its water
        m.db.session.add(m.Billing(unit_id=uid, billing_month="2038-07", assessment=Decimal("500"), parking_dues=0, storage_dues=0, water=0,
                                   other=0, penalty=0, adjustment=0, previous_balance=0, amount_paid=0, due_date=date(2038, 7, 8), status="Unpaid"))
        m.db.session.commit()
    r = sa.post("/api/water", json={"unitId": uid, "month": "2038-07", "current": "125"})    # previous filled in (110), default rate
    assert r.status_code == 201 and r.get_json()["billUpdated"] is True
    reading = r.get_json()["reading"]
    assert reading["previous"] == 110 and reading["usage"] == 15 and reading["amount"] == "750.00"
    assert reading["rate"] == "50" and view["defaultRate"] == "50"         # plain numbers, never "5E+1"
    fixed = sa.put(f"/api/water/{reading['id']}", json={"previous": "110", "current": "120", "rate": "50"}).get_json()
    assert fixed["created"] is False and fixed["reading"]["amount"] == "500.00"
    with m.app.app_context():
        bill = m.Billing.query.filter_by(unit_id=uid, billing_month="2038-07").first()
        assert str(bill.water) in ("500.00", "500")
    assert "current" in sa.post("/api/water", json={"unitId": uid, "month": "2038-07", "previous": "130", "current": "120"}).get_json()["error"]["fields"]


def test_water_payment_capped_at_balance_and_closed_period(app_module, sa, cleanup):
    months, _ = cleanup
    m = app_module
    uid = unit_id(m, "TEST-501")
    months.append("2038-09")
    rid = sa.post("/api/water", json={"unitId": uid, "month": "2038-09", "previous": "0", "current": "4", "rate": "50"}).get_json()["reading"]["id"]
    paid = sa.post(f"/api/water/{rid}/payments", json={"amount": "500", "method": "CASH", "type": "FULL", "date": "2038-09-10", "formToken": token()})
    assert paid.status_code == 201 and paid.get_json()["applied"] == "200.00" and paid.get_json()["reading"]["status"] == "Paid"
    again = sa.post(f"/api/water/{rid}/payments", json={"amount": "1", "method": "CASH", "date": "2038-09-11", "formToken": token()})
    assert again.status_code == 400 and "no remaining balance" in again.get_json()["error"]["message"]
    with m.app.app_context():
        row = m.Setting.query.filter_by(key="books_closed_through").first() or m.Setting(key="books_closed_through")
        row.value = "2038-09"; m.db.session.add(row)
        m.db.session.add(m.Billing(unit_id=uid, billing_month="2038-09", assessment=Decimal("500"), parking_dues=0, storage_dues=0, water=0,
                                   other=0, penalty=0, adjustment=0, previous_balance=0, amount_paid=0, due_date=date(2038, 9, 8), status="Unpaid"))
        m.db.session.commit()
    try:
        r = sa.put(f"/api/water/{rid}", json={"previous": "0", "current": "9", "rate": "50"})
        assert r.status_code == 400 and "closed" in r.get_json()["error"]["message"]
    finally:
        with m.app.app_context():
            m.Setting.query.filter_by(key="books_closed_through").first().value = ""; m.db.session.commit()


def test_csrf_and_classic_pages(app_module, sa):
    no_csrf = api(login(app_module, SA_USERNAME, SA_PASSWORD)[0])
    no_csrf.environ_base.pop("HTTP_X_CSRFTOKEN")
    assert no_csrf.post("/api/water", json={}).status_code == 403
    assert no_csrf.post("/api/advances", json={}).status_code == 403
    classic, _ = login(app_module, SA_USERNAME, SA_PASSWORD)
    assert classic.get("/water?month=2026-09").headers["Location"] == "/app/superadmin/water-readings?month=2026-09"
    assert classic.get("/billing/advance").headers["Location"] == "/app/superadmin/advances"
    with app_module.app.app_context():
        before = app_module.WaterReading.query.count()
    resp = classic.post("/water", data={"unit_id": unit_id(app_module, "TEST-501"), "month": "2039-01", "current_reading": "5"})
    assert resp.status_code == 303 and "moved=form" in resp.headers["Location"]
    with app_module.app.app_context():
        assert app_module.WaterReading.query.count() == before
