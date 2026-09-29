"""Phase B2: every payment gets an official receipt with an automatic, gap-free OR number."""
import re
from decimal import Decimal

import pytest

from conftest import PASSWORD, login

MONTH = "2032-03"


@pytest.fixture(scope="module")
def bill(app_module):
    """A fresh unpaid bill for TEST-503."""
    m = app_module
    admin, _ = login(m, "superadmin", "admin123")
    admin.post("/billing", data={"month": MONTH})
    with m.app.app_context():
        unit = m.Unit.query.filter_by(unit_no="TEST-503").first()
        b = m.Billing.query.filter_by(unit_id=unit.id, billing_month=MONTH).first()
        return {"id": b.id, "unit": unit.id}


def pay(client, bill_id, amount, date="2032-03-05", method="CASH", ref=""):
    return client.post(f"/billing/{bill_id}/pay", data={"amount": amount, "payment_date": date, "payment_method": method,
                                                         "payment_type": "PARTIAL", "reference": ref}, follow_redirects=True)


def receipts_for(m, unit_id):
    with m.app.app_context():
        return [(r.receipt_no, Decimal(str(r.amount)), sorted((a.kind, Decimal(str(a.amount))) for a in r.allocations))
                for r in m.Receipt.query.filter_by(unit_id=unit_id).order_by(m.Receipt.id)]


def test_bill_payment_gets_an_official_receipt(app_module, bill):
    m = app_module
    client, _ = login(m, "test_accounting", PASSWORD)
    page = pay(client, bill["id"], "100").get_data(as_text=True)
    orn = re.search(r"official receipt (OR-2032-\d{6})", page)
    assert orn, "the confirmation shows the OR number"
    no, amount, parts = receipts_for(m, bill["unit"])[-1]
    assert no == orn.group(1) and amount == Decimal("100.00") and parts == [("bill", Decimal("100.00"))]
    # shown on the SOA payment history
    assert orn.group(1) in client.get(f"/billing/{bill['id']}").get_data(as_text=True)


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
    client, _ = login(m, "test_admin", PASSWORD)
    with m.app.app_context():
        unit_id = m.Unit.query.filter_by(unit_no="TEST-504").first().id
    for date in ("2033-01-10", "2033-01-11"):
        client.post("/billing/advance", data={"unit_id": unit_id, "amount": "300", "start_month": "2033-02", "coverage_months": "1",
                                              "payment_method": "CASH", "payment_date": date})
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
    client, _ = login(m, "test_admin", PASSWORD)
    resp = client.post(f"/water/{rid}/paid", data={"amount": "1", "payment_method": "CASH", "payment_type": "PARTIAL",
                                                   "paid_date": "2032-04-02"}, follow_redirects=True)
    assert "official receipt OR-2032-" in resp.get_data(as_text=True)
    assert receipts_for(m, unit_id)[-1][2] == [("water", Decimal("1.00"))]


def test_receipts_page_follows_the_permission_matrix(app_module):
    m = app_module
    accounting, _ = login(m, "test_accounting", PASSWORD)
    page = accounting.get("/receipts?start=2032-01-01&end=2033-12-31").get_data(as_text=True)
    assert "OR-2032-" in page and "OR-2033-" in page
    with m.app.app_context():
        rid = m.Receipt.query.first().id
    assert "OFFICIAL RECEIPT" in accounting.get(f"/receipts/{rid}").get_data(as_text=True)
    staff, _ = login(m, "test_staff", PASSWORD)
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
