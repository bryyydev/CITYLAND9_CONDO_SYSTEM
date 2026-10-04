"""Billing & SOA API (/api/billing): permissions, list/preview/generate, read-only SOA, Edit SOA,
email, CSRF and the retired classic Billing screens. Payments, receipts, recalculation and closed
periods are covered by test_payment_integrity, test_phase_b1_billing and test_phase_b2_receipts."""
import json
import secrets
from datetime import date
from decimal import Decimal

import pytest

from conftest import PASSWORD, SA_PASSWORD, SA_USERNAME, api, login

BASE = "/api/billing"


@pytest.fixture
def sa(app_module):
    return api(login(app_module, SA_USERNAME, SA_PASSWORD)[0])


@pytest.fixture
def bill(app_module):
    """A stand-alone bill for TEST-504 in a far-future month; removed afterwards."""
    m = app_module
    with m.app.app_context():
        unit = m.Unit.query.filter_by(unit_no="TEST-504").first()
        b = m.Billing(unit_id=unit.id, billing_month="2036-06", assessment=Decimal("2000"), parking_dues=0, storage_dues=0, water=0,
                      other=0, penalty=0, adjustment=0, previous_balance=0, amount_paid=0, due_date=date(2036, 6, 8), status="Unpaid")
        m.db.session.add(b); m.db.session.commit()
        bid = b.id
    yield bid
    with m.app.app_context():
        b = m.db.session.get(m.Billing, bid)
        if b:
            for p in list(b.payments):
                for a in m.ReceiptAllocation.query.filter_by(payment_id=p.id).all():
                    m.db.session.delete(a.receipt)
                m.db.session.delete(p)
            m.db.session.delete(b); m.db.session.commit()


def test_permissions(app_module, bill):
    for role, can_read in (("admin", True), ("accounting", True), ("manager", False), ("staff", False), ("resident", False)):
        client = api(login(app_module, f"test_{role}", PASSWORD)[0])
        assert client.get(f"{BASE}?month=2036-06").status_code == (200 if can_read else 403), role
        assert client.get(f"{BASE}/{bill}").status_code == (200 if can_read else 403), role
        if not can_read:
            assert client.post(f"{BASE}/{bill}/payments", json={"amount": "1", "formToken": secrets.token_urlsafe(24)}).status_code == 403
            assert client.put(f"{BASE}/{bill}/soa", json={}).status_code == 403
            assert client.post(f"{BASE}/generate", json={"month": "2036-07"}).status_code == 403
    assert app_module.app.test_client().get(BASE).status_code == 401


def test_list_and_detail(app_module, sa, bill):
    rows = sa.get(f"{BASE}?month=2036-06").get_json()["bills"]
    row = next(r for r in rows if r["id"] == bill)
    # (the total also carries TEST-504's earlier unpaid months, as on every SOA)
    assert row["unitNo"] == "TEST-504" and row["payerName"] == "Ana Garcia" and Decimal(row["total"]) >= Decimal("2000")
    d = sa.get(f"{BASE}/{bill}").get_json()["bill"]
    assert d["charges"]["condoDues"] == "2000.00" and d["stored"]["condoDues"] == "2000.00" and d["qrUrl"] == f"/billing/{bill}/qr"
    assert d["contact"]["name"] == "Ana Garcia" and isinstance(d["emailRecipients"], list)
    assert sa.get(f"{BASE}?month=2036-13").status_code == 400 and sa.get(f"{BASE}/999999").status_code == 404


def test_reading_an_soa_changes_nothing(app_module, sa, bill):
    """The classic SOA page re-saved the due date, water, penalty and status on every view; the API doesn't."""
    m = app_module
    with m.app.app_context():
        b = m.db.session.get(m.Billing, bill)
        b.due_date = date(2036, 6, 20); b.status = "Weird"; m.db.session.commit()
    sa.get(f"{BASE}/{bill}"); sa.get(f"{BASE}?month=2036-06")
    with m.app.app_context():
        b = m.db.session.get(m.Billing, bill)
        assert b.due_date == date(2036, 6, 20) and b.status == "Weird"


def test_preview_and_generate(app_module, sa):
    p = sa.get(f"{BASE}/preview?month=2036-09").get_json()
    assert p["residentialUnits"] >= 4 and p["toCreate"] == p["residentialUnits"] - p["alreadyBilled"] and p["dueDate"] == "2036-09-08"
    r = sa.post(f"{BASE}/generate", json={"month": "2036-09"}).get_json()
    assert r["created"] == p["toCreate"] and r["skipped"] == p["alreadyBilled"]
    again = sa.post(f"{BASE}/generate", json={"month": "2036-09"}).get_json()
    assert again["created"] == 0 and again["skipped"] == p["residentialUnits"]
    assert sa.post(f"{BASE}/generate", json={"month": "Sept"}).status_code == 400
    m = app_module
    with m.app.app_context():
        m.Billing.query.filter_by(billing_month="2036-09").delete(); m.db.session.commit()


def test_generate_refused_in_a_closed_period(app_module, sa):
    m = app_module
    with m.app.app_context():
        row = m.Setting.query.filter_by(key="books_closed_through").first() or m.Setting(key="books_closed_through")
        row.value = "2036-10"; m.db.session.add(row); m.db.session.commit()
    try:
        assert sa.get(f"{BASE}/preview?month=2036-10").get_json()["closed"] is True
        resp = sa.post(f"{BASE}/generate", json={"month": "2036-10"})
        assert resp.status_code == 400 and "closed" in resp.get_json()["error"]["message"]
        with m.app.app_context():
            assert m.Billing.query.filter_by(billing_month="2036-10").count() == 0
    finally:
        with m.app.app_context():
            m.Setting.query.filter_by(key="books_closed_through").first().value = ""; m.db.session.commit()


def test_edit_soa_needs_a_reason_and_is_audited(app_module, sa, bill):
    body = {"condoDues": "1800", "parking": "0", "storage": "0", "water": "150.50", "other": "100", "adjustment": "-50",
            "penalty": "0", "previousBalance": "0", "dueDate": "2036-06-15"}
    errs = sa.put(f"{BASE}/{bill}/soa", json={**body, "water": "abc"}).get_json()["error"]["fields"]
    assert {"water", "note"} <= set(errs)
    d = sa.put(f"{BASE}/{bill}/soa", json={**body, "note": "Water meter misread; corrected per inspection"}).get_json()["bill"]
    # 1800 + 150.50 + other 100 - adjustment 50 (other charges and the adjustment count since 2026-10-04)
    assert d["manualOverride"] is True and d["total"] == "2000.50" and d["dueDate"] == "2036-06-15"
    assert d["charges"]["other"] == "100.00" and d["charges"]["adjustment"] == "-50.00" and d["stored"]["adjustment"] == "-50.00"
    assert d["note"].startswith("Water meter misread")
    m = app_module
    with m.app.app_context():
        log = m.AuditLog.query.filter_by(entity_type="billing", entity_id=bill).order_by(m.AuditLog.id.desc()).first()
        assert log.reason.startswith("Water meter misread") and "assessment" in json.loads(log.details)
    # hand-corrected: can't be re-priced from rates
    assert sa.post(f"{BASE}/{bill}/recalculate").status_code == 409


def test_payment_result_and_detail(app_module, sa, bill):
    r = sa.post(f"{BASE}/{bill}/payments", json={"amount": "2500", "method": "CHECK", "reference": "", "date": "2036-06-02",
                                                 "formToken": secrets.token_urlsafe(24)})
    assert r.status_code == 400 and "reference" in r.get_json()["error"]["fields"]            # check needs a reference
    balance = Decimal(sa.get(f"{BASE}/{bill}").get_json()["bill"]["balance"])           # includes earlier unpaid months
    r = sa.post(f"{BASE}/{bill}/payments", json={"amount": str(balance + 500), "method": "CHECK", "reference": "CHK-77", "date": "2036-06-02",
                                                 "formToken": secrets.token_urlsafe(24)}).get_json()
    assert r["receiptNo"].startswith("OR-2036-") and Decimal(r["appliedToBill"]) == balance and r["excessToAdvance"] == "500.00"
    assert r["status"] == "Paid" and r["bill"]["payments"][0]["receiptNo"] == r["receiptNo"]
    m = app_module
    with m.app.app_context():   # remove the excess advance and everything that points at it
        for adv in m.AdvancePayment.query.filter(m.AdvancePayment.reference == "CHK-77").all():
            m.AdvanceApplication.query.filter_by(advance_payment_id=adv.id).delete(synchronize_session=False)
            m.ReceiptAllocation.query.filter_by(advance_payment_id=adv.id).delete(synchronize_session=False)
            m.db.session.delete(adv)
        m.db.session.commit()


def test_email_needs_smtp_and_recipients(app_module, sa, bill):
    overview = sa.get(f"{BASE}/email?month=2036-06").get_json()
    assert overview["smtpConfigured"] is False and any(r["billId"] == bill for r in overview["rows"])
    resp = sa.post(f"{BASE}/email/send", json={"month": "2036-06", "billIds": [bill]})
    assert resp.status_code in (409,) and "SMTP" in resp.get_json()["error"]["message"]
    assert sa.post(f"{BASE}/email/send", json={"month": "2036-13"}).status_code == 400


def test_csrf_required(sa, bill):
    sa.environ_base.pop("HTTP_X_CSRFTOKEN")
    assert sa.post(f"{BASE}/{bill}/payments", json={"amount": "1", "formToken": secrets.token_urlsafe(24)}).status_code == 403


def test_classic_billing_urls_redirect(app_module, bill):
    sa_classic, _ = login(app_module, SA_USERNAME, SA_PASSWORD)
    assert sa_classic.get("/billing?month=2036-06").headers["Location"] == "/app/superadmin/billing?month=2036-06"
    assert sa_classic.get(f"/billing/{bill}").headers["Location"] == f"/app/superadmin/billing?bill={bill}"
    for url in (f"/billing/{bill}/edit-soa", f"/billing/{bill}/recalculate", f"/billing/{bill}/email", "/billing/email/send"):
        resp = sa_classic.post(url, data={"assessment": "1"})
        assert resp.status_code == 303 and "moved=form" in resp.headers["Location"], url
    acc, _ = login(app_module, "test_accounting", PASSWORD)
    assert acc.get("/billing").headers["Location"] == "/app/accounting/billing"
    with app_module.app.app_context():
        assert str(app_module.db.session.get(app_module.Billing, bill).assessment) in ("2000", "2000.00")


def test_other_charges_and_adjustment_count_in_the_total(app_module, sa, bill):
    """Fixed 2026-10-04: Billing.other and Billing.adjustment were stored but never counted."""
    m = app_module
    before = Decimal(sa.get(f"{BASE}/{bill}").get_json()["bill"]["total"])
    with m.app.app_context():
        b = m.db.session.get(m.Billing, bill)
        b.other, b.adjustment = Decimal("250.00"), Decimal("-100.00"); m.db.session.commit()
    d = sa.get(f"{BASE}/{bill}").get_json()["bill"]
    assert Decimal(d["total"]) == before + Decimal("150.00")
    assert Decimal(d["charges"]["currentCharges"]) == Decimal("2150.00")
    with m.app.test_request_context("/"):
        assert m.bill_total(m.db.session.get(m.Billing, bill)) == Decimal(d["total"])     # one formula everywhere
