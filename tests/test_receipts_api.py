"""Payments & ORs API (/api/receipts): ledger filters and totals, permissions, CSRF, one void per form,
and the retired classic receipt pages. Void rules (reason, reversal, closed period, applied advance)
are covered by test_payment_integrity."""
import secrets
from datetime import date
from decimal import Decimal

import pytest

from conftest import PASSWORD, SA_PASSWORD, SA_USERNAME, api, login

BASE = "/api/receipts"


@pytest.fixture
def sa(app_module):
    return api(login(app_module, SA_USERNAME, SA_PASSWORD)[0])


@pytest.fixture
def paid_bill(app_module, sa):
    """A bill for TEST-504 in 2037-03 paid with two receipts (cash 300, check 200); removed afterwards."""
    m = app_module
    with m.app.app_context():
        unit = m.Unit.query.filter_by(unit_no="TEST-504").first()
        b = m.Billing(unit_id=unit.id, billing_month="2037-03", assessment=Decimal("1000"), parking_dues=0, storage_dues=0, water=0,
                      other=0, penalty=0, adjustment=0, previous_balance=0, amount_paid=0, due_date=date(2037, 3, 8), status="Unpaid")
        m.db.session.add(b); m.db.session.commit()
        bid = b.id
    for amount, method, ref in (("300", "CASH", ""), ("200", "CHECK", "CHK-RCPT-9")):
        r = sa.post(f"/api/billing/{bid}/payments", json={"amount": amount, "method": method, "reference": ref, "date": "2047-03-10",
                                                          "type": "PARTIAL", "formToken": secrets.token_urlsafe(24)})
        assert r.status_code == 201, r.get_json()
    yield bid
    with m.app.app_context():
        b = m.db.session.get(m.Billing, bid)
        for p in list(b.payments):
            for a in m.ReceiptAllocation.query.filter_by(payment_id=p.id).all():
                m.db.session.delete(a.receipt)
            m.db.session.delete(p)
        m.db.session.delete(b); m.db.session.commit()


def test_ledger_totals_and_filters(sa, paid_bill):
    data = sa.get(f"{BASE}?from=2047-03-01&to=2047-03-31").get_json()
    assert data["total"] == 2 and data["collected"] == "500.00"
    assert data["byMethod"] == {"CASH": "300.00", "CHECK": "200.00", "ONLINE": "0.00"}
    r = data["receipts"][0]
    assert r["unitNo"] == "TEST-504" and r["items"][0]["kind"] == "bill" and r["receivedBy"] == SA_USERNAME
    assert [x["method"] for x in sa.get(f"{BASE}?from=2047-03-01&to=2047-03-31&method=CHECK").get_json()["receipts"]] == ["CHECK"]
    assert sa.get(f"{BASE}?from=2047-03-01&to=2047-03-31&q=CHK-RCPT").get_json()["total"] == 1
    assert sa.get(f"{BASE}?from=2047-03-31&to=2047-03-01").get_json()["total"] == 2          # reversed range is swapped
    assert sa.get(f"{BASE}?from=soon").status_code == 400 and sa.get(f"{BASE}?method=GCASH").status_code == 400


def test_permissions(app_module, sa, paid_bill):
    rid = sa.get(f"{BASE}?from=2047-03-01&to=2047-03-31").get_json()["receipts"][0]["id"]
    admin = api(login(app_module, "test_admin", PASSWORD)[0])
    assert admin.get(BASE).status_code == 200
    detail = admin.get(f"{BASE}/{rid}").get_json()
    assert detail["canVoid"] is False                                                        # Admin may record, not void
    assert admin.post(f"{BASE}/{rid}/void", json={"reason": "Admin is not allowed to void", "formToken": secrets.token_urlsafe(24)}).status_code == 403
    for role in ("manager", "staff", "resident"):
        client = api(login(app_module, f"test_{role}", PASSWORD)[0])
        assert client.get(BASE).status_code == 403 and client.get(f"{BASE}/{rid}").status_code == 403, role
    assert app_module.app.test_client().get(BASE).status_code == 401


def test_one_void_per_form_and_csrf(app_module, sa, paid_bill):
    rid = sa.get(f"{BASE}?from=2047-03-01&to=2047-03-31&method=CASH").get_json()["receipts"][0]["id"]
    token = secrets.token_urlsafe(24)
    no_csrf = app_module.app.test_client()
    assert no_csrf.post(f"{BASE}/{rid}/void", json={"reason": "No CSRF token sent here", "formToken": token}).status_code in (401, 403)
    assert sa.post(f"{BASE}/{rid}/void", json={"reason": "Entered twice by the cashier", "formToken": "short"}).status_code == 400
    first = sa.post(f"{BASE}/{rid}/void", json={"reason": "Entered twice by the cashier", "formToken": token})
    assert first.status_code == 200 and first.get_json()["receipt"]["voided"] is True
    again = sa.post(f"{BASE}/{rid}/void", json={"reason": "Entered twice by the cashier", "formToken": token})
    assert again.status_code == 409
    data = sa.get(f"{BASE}?from=2047-03-01&to=2047-03-31").get_json()
    assert data["collected"] == "200.00" and data["voidCount"] == 1
    assert [r["id"] for r in sa.get(f"{BASE}?from=2047-03-01&to=2047-03-31&status=void").get_json()["receipts"]] == [rid]
    bill = sa.get(f"/api/billing/{paid_bill}").get_json()["bill"]
    assert bill["amountPaid"] == "200.00"                                                   # the void reversed the payment


def test_classic_receipt_urls_redirect(app_module, sa, paid_bill):
    rid = sa.get(f"{BASE}?from=2047-03-01&to=2047-03-31").get_json()["receipts"][0]["id"]
    classic, _ = login(app_module, SA_USERNAME, SA_PASSWORD)
    assert classic.get("/receipts").headers["Location"] == "/app/superadmin/payments"
    assert classic.get(f"/receipts/{rid}").headers["Location"] == f"/app/superadmin/payments?receipt={rid}"
    resp = classic.post(f"/receipts/{rid}/void", data={"reason": "Old tab trying to void this"})
    assert resp.status_code == 303 and "moved=form" in resp.headers["Location"]
    assert sa.get(f"{BASE}/{rid}").get_json()["receipt"]["voided"] is False                 # nothing changed
    acc, _ = login(app_module, "test_accounting", PASSWORD)
    assert acc.get("/receipts").headers["Location"] == "/app/accounting/payments"
