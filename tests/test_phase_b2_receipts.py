"""Phase B2: every payment gets an official receipt with an automatic, gap-free OR number."""
import re
import secrets
from decimal import Decimal

import pytest

from conftest import PASSWORD, login, api

MONTH = "2032-03"


@pytest.fixture(scope="module")
def bill(app_module):
    """A fresh unpaid bill for TEST-503."""
    m = app_module
    admin, _ = login(m, "superadmin", "Test-Admin-Pass-1")
    api(admin).post("/api/billing/generate", json={"month": MONTH})
    with m.app.app_context():
        unit = m.Unit.query.filter_by(unit_no="TEST-503").first()
        b = m.Billing.query.filter_by(unit_id=unit.id, billing_month=MONTH).first()
        return {"id": b.id, "unit": unit.id}


def pay(client, bill_id, amount, date="2032-03-05", method="CASH", ref=""):
    """Billing's payment API (as the React app calls it), with a fresh one-time form token."""
    return api(client).post(f"/api/billing/{bill_id}/payments", json={"amount": amount, "date": date, "method": method, "type": "PARTIAL",
                                                                      "reference": ref, "formToken": secrets.token_urlsafe(24)})


def receipts_for(m, unit_id):
    with m.app.app_context():
        return [(r.receipt_no, Decimal(str(r.amount)), sorted((a.kind, Decimal(str(a.amount))) for a in r.allocations))
                for r in m.Receipt.query.filter_by(unit_id=unit_id).order_by(m.Receipt.id)]


def test_bill_payment_gets_an_official_receipt(app_module, bill):
    m = app_module
    client, _ = login(m, "test_accounting", PASSWORD)
    result = pay(client, bill["id"], "100").get_json()
    orn = re.fullmatch(r"OR-2032-\d{6}", result["receiptNo"])
    assert orn, "the confirmation shows the OR number"
    no, amount, parts = receipts_for(m, bill["unit"])[-1]
    assert no == result["receiptNo"] and amount == Decimal("100.00") and parts == [("bill", Decimal("100.00"))]
    # shown on the SOA payment history
    payments = client.get(f"/api/billing/{bill['id']}").get_json()["bill"]["payments"]
    assert result["receiptNo"] in [p["receiptNo"] for p in payments]


def test_overpayment_is_one_receipt_for_the_bill_and_the_advance(app_module, bill):
    m = app_module
    with m.app.test_request_context("/"):
        balance = m.bill_balance(m.db.session.get(m.Billing, bill["id"]))
    client, _ = login(m, "test_accounting", PASSWORD)
    pay(client, bill["id"], str(balance + Decimal("500")), ref="BDO-1", method="ONLINE")
    no, amount, parts = receipts_for(m, bill["unit"])[-1]
    assert amount == balance + Decimal("500")
    assert parts == sorted([("bill", balance), ("advance", Decimal("500.00"))])


def test_numbers_are_sequential_and_restart_each_year(app_module, bill):
    m = app_module
    client = api(login(m, "test_admin", PASSWORD)[0])
    with m.app.app_context():
        unit_id = m.Unit.query.filter_by(unit_no="TEST-504").first().id
    for date in ("2033-01-10", "2033-01-11"):
        r = client.post("/api/advances", json={"unitId": unit_id, "amount": "300", "startMonth": "2033-02", "months": 1,
                                               "method": "CASH", "date": date, "formToken": secrets.token_urlsafe(24)})
        assert r.status_code == 201, r.get_json()
    with m.app.app_context():
        nos = [r.receipt_no for r in m.Receipt.query.filter_by(receipt_year=2033).order_by(m.Receipt.receipt_seq)]
    assert nos[:2] == ["OR-2033-000001", "OR-2033-000002"]


def test_water_payment_gets_a_receipt(app_module):
    m = app_module
    with m.app.app_context():
        reading = m.WaterReading.query.filter(m.WaterReading.paid.is_(False)).first()
        rid, unit_id = reading.id, reading.unit_id
        balance = Decimal(str(reading.bill_amount)) - Decimal(str(reading.paid_amount or 0))
    assert balance > 0
    client = api(login(m, "test_admin", PASSWORD)[0])
    resp = client.post(f"/api/water/{rid}/payments", json={"amount": "1", "method": "CASH", "type": "PARTIAL", "date": "2032-04-02",
                                                         "formToken": secrets.token_urlsafe(24)})
    assert resp.status_code == 201 and resp.get_json()["receiptNo"].startswith("OR-2032-")
    assert receipts_for(m, unit_id)[-1][2] == [("water", Decimal("1.00"))]


def test_receipts_page_follows_the_permission_matrix(app_module):
    m = app_module
    accounting = api(login(m, "test_accounting", PASSWORD)[0])
    numbers = [r["receiptNo"] for r in accounting.get("/api/receipts?from=2032-01-01&to=2033-12-31").get_json()["receipts"]]
    assert any(n.startswith("OR-2032-") for n in numbers) and any(n.startswith("OR-2033-") for n in numbers)
    with m.app.app_context():
        rid = m.Receipt.query.first().id
    assert accounting.get(f"/api/receipts/{rid}").get_json()["receipt"]["receiptNo"].startswith("OR-")
    staff = api(login(m, "test_staff", PASSWORD)[0])
    assert staff.get("/api/receipts").status_code == 403 and staff.get(f"/api/receipts/{rid}").status_code == 403
    assert staff.get("/receipts").status_code == 302


def test_receipts_reconcile_with_recorded_payments(app_module):
    m = app_module
    with m.app.app_context():
        receipted = {"bill": set(), "advance": set(), "water": set()}
        for a in m.ReceiptAllocation.query.all():
            receipted[a.kind].add(a.payment_id or a.advance_payment_id or a.water_reading_id)
        issued = sum((Decimal(str(r.amount)) for r in m.Receipt.query.all()), Decimal("0"))
        allocated = sum((Decimal(str(a.amount)) for a in m.ReceiptAllocation.query.all()), Decimal("0"))
        assert issued == allocated
        # every payment recorded through the screens since B2 has a receipt
        new_payments = m.Payment.query.join(m.Billing).filter(m.Billing.billing_month == MONTH).all()
        assert new_payments and all(p.id in receipted["bill"] for p in new_payments)
