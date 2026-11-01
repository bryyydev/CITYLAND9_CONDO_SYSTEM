"""SOA PDF download (server-generated, A4) for the office and residents, and the SOA email.

Checks real PDF output: content type, attachment file name, %PDF signature, text extracted with
pypdf (same figures as the SOA API), page count / repeated table headers for a long statement, the
payment QR only when a payment link is configured, and every access rule."""
import io
from copy import deepcopy
import secrets
import ssl
from datetime import date
from decimal import Decimal

import pytest
from pypdf import PdfReader
from werkzeug.security import generate_password_hash

from conftest import PASSWORD, SA_PASSWORD, SA_USERNAME, api, login

PW = "Pdf-Resident-Pass-1"


def pdf_text(data):
    reader = PdfReader(io.BytesIO(data))
    return reader, "\n".join(p.extract_text() for p in reader.pages)


def peso(value):
    return f"₱{Decimal(value):,.2f}"


@pytest.fixture
def sa(app_module):
    return api(login(app_module, SA_USERNAME, SA_PASSWORD)[0])


def a_bill(m, unit_no):
    with m.app.app_context():
        u = m.Unit.query.filter_by(unit_no=unit_no).first()
        return m.Billing.query.filter_by(unit_id=u.id).order_by(m.Billing.billing_month.desc()).first().id, u.id


@pytest.fixture
def resident(app_module):
    """A resident account for the Current owner of TEST-504."""
    m = app_module
    with m.app.app_context():
        u = m.Unit.query.filter_by(unit_no="TEST-504").first()
        owner = m.Owner.query.filter_by(unit_id=u.id, status="Current").first()
        user = m.User(username="pdf_resident", password_hash=generate_password_hash(PW), role="resident", active=True)
        m.db.session.add(user); m.db.session.flush()
        m.db.session.add(m.ResidentProfile(user_id=user.id, unit_id=u.id, person_type="Owner", person_id=owner.id, display_name="PDF Resident"))
        m.db.session.commit()
        uid = user.id
    yield api(login(m, "pdf_resident", PW)[0])
    with m.app.app_context():
        user = m.db.session.get(m.User, uid)
        m.db.session.delete(user.resident_profile); m.db.session.delete(user); m.db.session.commit()


def test_office_pdf_is_a_real_a4_pdf_with_the_api_figures(app_module, sa):
    bid, _ = a_bill(app_module, "TEST-502")
    resp = sa.get(f"/api/billing/{bid}/soa.pdf")
    assert resp.status_code == 200 and resp.headers["Content-Type"] == "application/pdf"
    detail = sa.get(f"/api/billing/{bid}").get_json()["bill"]
    assert resp.headers["Content-Disposition"] == f'attachment; filename="SOA_TEST-502_{detail["billingMonth"]}.pdf"'
    assert resp.data[:5] == b"%PDF-" and resp.headers.get("Cache-Control") == "no-store"
    reader, text = pdf_text(resp.data)
    box = reader.pages[0].mediabox
    assert (round(float(box.width)), round(float(box.height))) == (595, 842)          # A4 portrait
    for expected in (detail["statementNo"], "TEST-502", "BILLING INVOICE", "BILLED TO", "CURRENT PERIOD CHARGES",
                     peso(detail["total"]), peso(detail["charges"]["condoDues"]), peso(abs(Decimal(detail["balance"])))):
        assert expected in text, expected
    for excluded in ("PAYMENTS RECEIVED", "No payments recorded for this statement yet.", "HOW TO PAY",
                     "Quote statement no.", "Scan to pay online", "Payments received:"):
        assert excluded not in text, excluded
    overdue_total = Decimal(detail["charges"]["previousBalance"]) + Decimal(detail["charges"]["penalty"])
    assert ("OVERDUE BALANCES" in text) is (overdue_total != 0)
    assert ("Total Overdue Balance & Penalties" in text) is (overdue_total != 0)
    for expected in (
        "REMINDERS & PAYMENT POLICIES",
        "Administration Office",
        "on or before every 8th of the month",
        "4% penalty per month",
        "Issue your check in favor of",
        "encourage exact amount to expedite O.R. issuance",
        "02-8559-9048",
        "not be liable for cash/check payments given to the guards",
        "Any and all disconnection notice(s)",
    ):
        assert expected in text
    assert "Any and all disconnection notice(s) received prior to this Statement of Account shall remain valid" in " ".join(reader.pages[-1].extract_text().split())
    assert "Page 1 of" in text
    inline = sa.get(f"/api/billing/{bid}/soa.pdf?inline=1")
    assert inline.status_code == 200 and inline.data[:5] == b"%PDF-"
    assert inline.headers["Content-Disposition"].startswith("inline;")
    assert pdf_text(inline.data)[1] == text


def test_zero_overdue_balance_is_omitted_but_payment_policies_are_included(app_module, sa):
    bid, _ = a_bill(app_module, "TEST-502")
    from app.services.soa import soa_detail
    from app.services.soa_pdf import build_soa_pdf

    with app_module.app.app_context():
        bill = app_module.db.session.get(app_module.Billing, bid)
        statement = deepcopy(soa_detail(app_module, bill))
    statement["charges"]["previousBalance"] = "0.00"
    statement["charges"]["penalty"] = "0.00"
    statement["previousUnpaid"] = []
    _, text = pdf_text(build_soa_pdf(statement))

    assert "OVERDUE BALANCES" not in text
    assert "Total Overdue Balance & Penalties" not in text
    assert "REMINDERS & PAYMENT POLICIES" in text
    assert "Administration Office" in text
    assert "Any and all disconnection notice(s) received prior to this Statement of Account shall remain valid" in " ".join(text.split())


def test_reminders_stay_complete_together_when_soa_spans_pages(app_module, sa):
    bid, _ = a_bill(app_module, "TEST-502")
    from app.services.soa import soa_detail
    from app.services.soa_pdf import build_soa_pdf

    with app_module.app.app_context():
        bill = app_module.db.session.get(app_module.Billing, bid)
        statement = deepcopy(soa_detail(app_module, bill))
    statement["previousUnpaid"] = [
        {
            "month": "2025-01",
            "statementNo": f"PRIOR-{index:03d}",
            "dueDate": "2025-02-08",
            "balance": "100.00",
        }
        for index in range(60)
    ]
    statement["charges"]["previousBalance"] = "6000.00"

    reader, text = pdf_text(build_soa_pdf(statement))
    pages = [page.extract_text() for page in reader.pages]
    assert len(pages) > 1
    reminder_pages = [page for page in pages if "REMINDERS & PAYMENT POLICIES" in page]
    assert len(reminder_pages) == 1
    reminder_text = reminder_pages[0]
    assert all(
        expected in reminder_text
        for expected in (
            "Administration Office",
            "on or before every 8th of the month",
            "4% penalty per month",
            "Issue your check in favor of",
            "encourage exact amount to expedite O.R. issuance",
            "02-8559-9048",
            "not be liable for cash/check payments given to the guards",
            "Any and all disconnection notice(s)",
            "are fully settled.",
        )
    )
    assert "REMINDERS & PAYMENT POLICIES" in text


def test_soa_hides_excluded_contacts_and_labels_latest_water_reading_month(app_module, sa):
    m = app_module
    with m.app.app_context():
        unit = m.Unit(unit_no="SOA-MONTH", unit_type="STUDIO TYPE", area_sqm=30, active=True)
        m.db.session.add(unit)
        m.db.session.flush()
        hidden_owner = m.Owner(unit_id=unit.id, owner_name="Hidden Owner", status="Current", include_in_soa=False)
        visible_owner = m.Owner(unit_id=unit.id, owner_name="Visible Owner", status="Current", include_in_soa=True)
        hidden_tenant = m.Tenant(unit_id=unit.id, tenant_name="Hidden Tenant", status="Current", representative=True,
                                 include_in_soa=False, email="hidden@example.test")
        visible_tenant = m.Tenant(unit_id=unit.id, tenant_name="Visible Tenant", status="Current", representative=False,
                                  include_in_soa=True, email="visible@example.test")
        reading = m.WaterReading(unit_id=unit.id, reading_month="2026-10", previous_reading=100, current_reading=150,
                     rate=35, reading_date=date(2026, 10, 24), paid=False)
        bill = m.Billing(unit_id=unit.id, billing_month="2026-11", assessment=0, parking_dues=0, storage_dues=0,
                 water=1750, other=0, penalty=0, adjustment=0, previous_balance=0, amount_paid=0,
                 due_date=date(2026, 11, 8), status="Unpaid")
        m.db.session.add_all([hidden_owner, visible_owner, hidden_tenant, visible_tenant, reading, bill])
        m.db.session.commit()
        unit_id, bill_id = unit.id, bill.id

    try:
        statement = sa.get(f"/api/billing/{bill_id}").get_json()["bill"]
        assert statement["billingMonth"] == "2026-11"
        assert statement["statementNo"] == f"202611-{bill_id:05d}"
        assert statement["payerName"] == "Visible Tenant" and statement["contact"]["name"] == "Visible Tenant"
        assert statement["account"] == {"unitNo": "SOA-MONTH", "unitType": "STUDIO TYPE", "floor": "",
                                         "billTo": "Visible Tenant", "ownerName": "Visible Owner", "tenantName": "Visible Tenant"}
        assert statement["charges"]["waterReadingMonth"] == "2026-10"
        assert statement["charges"]["waterReadingFrom"] == "2026-09-16"
        assert statement["charges"]["waterReadingTo"] == "2026-10-15"
        assert statement["charges"]["waterUsage"] == 50 and statement["charges"]["waterRate"] == "35.00"
        assert statement["charges"]["water"] == "1750.00"

        response = sa.get(f"/api/billing/{bill_id}/soa.pdf")
        _, text = pdf_text(response.data)
        assert "November 2026" in text and "Water Consumption" in text
        assert "Water reading: September 16, 2026 to October 15, 2026" in " ".join(text.split())
        assert "50 m" in text and "₱35.00/m" in text and "₱1,750.00" in text
        assert "Visible Owner" in text and "Visible Tenant" in text
        assert "Hidden Owner" not in text and "Hidden Tenant" not in text
    finally:
        with m.app.app_context():
            m.Billing.query.filter_by(id=bill_id).delete()
            m.WaterReading.query.filter_by(unit_id=unit_id, reading_month="2026-10").delete()
            m.Tenant.query.filter_by(unit_id=unit_id).delete()
            m.Owner.query.filter_by(unit_id=unit_id).delete()
            m.Unit.query.filter_by(id=unit_id).delete()
            m.db.session.commit()


def test_pdf_identifies_prior_unpaid_statements_by_number_and_due_date(app_module):
    m = app_module
    with m.app.app_context():
        unit = m.Unit(unit_no="SOA-ARREARS-DETAIL", unit_type="STUDIO TYPE", area_sqm=30, active=True)
        m.db.session.add(unit)
        m.db.session.flush()
        prior = m.Billing(
            unit_id=unit.id, billing_month="2026-08", assessment=3500, parking_dues=0,
            storage_dues=0, water=0, other=0, penalty=0, adjustment=0,
            previous_balance=0, amount_paid=0, due_date=date(2026, 11, 8), status="Unpaid",
        )
        current = m.Billing(
            unit_id=unit.id, billing_month="2026-10", assessment=0, parking_dues=0,
            storage_dues=0, water=0, other=0, penalty=0, adjustment=0,
            previous_balance=0, amount_paid=0, due_date=date(2026, 11, 8), status="Unpaid",
        )
        m.db.session.add_all([prior, current])
        m.db.session.commit()
        from app.services.soa import soa_detail
        from app.services.soa_pdf import build_soa_pdf
        statement = soa_detail(m, current)
        pdf = build_soa_pdf(statement)
        unit_id, current_id = unit.id, current.id

    try:
        assert statement["previousUnpaid"] == [{
            "id": prior.id,
            "statementNo": f"202608-{prior.id:05d}",
            "month": "2026-08",
            "dueDate": "2026-11-08",
            "balance": "3500.00",
        }]
        _, text = pdf_text(pdf)
        assert "Prior SOA balance" in text
        assert statement["previousUnpaid"][0]["statementNo"] in text
        assert "Nov 8, 2026" in text
        assert peso("3500.00") in text
    finally:
        with m.app.app_context():
            m.Billing.query.filter(m.Billing.id.in_([prior.id, current_id])).delete(synchronize_session=False)
            m.Unit.query.filter_by(id=unit_id).delete()
            m.db.session.commit()


def test_resident_pdf_matches_the_resident_statement(app_module, resident):
    bid, unit_id = a_bill(app_module, "TEST-504")
    resp = resident.get(f"/api/resident/units/{unit_id}/soa/{bid}/pdf")
    assert resp.status_code == 200 and resp.headers["Content-Type"] == "application/pdf"
    stmt = resident.get(f"/api/resident/units/{unit_id}/soa/{bid}").get_json()["statement"]
    _, text = pdf_text(resp.data)
    assert stmt["statementNo"] in text and peso(stmt["total"]) in text
    assert "Admin Office" in text and "staff" not in text.lower()                        # resident-facing wording only


def test_pdf_access_rules(app_module, resident):
    m = app_module
    own_bill, own_unit = a_bill(m, "TEST-504")
    other_bill, other_unit = a_bill(m, "TEST-502")
    assert resident.get(f"/api/resident/units/{own_unit}/soa/{other_bill}/pdf").status_code == 404   # bill of another unit
    assert resident.get(f"/api/resident/units/{other_unit}/soa/{other_bill}/pdf").status_code == 403
    assert resident.get(f"/api/billing/{own_bill}/soa.pdf").status_code == 403                        # office route
    anon = m.app.test_client()
    for url in (f"/api/billing/{own_bill}/soa.pdf", f"/api/resident/units/{own_unit}/soa/{own_bill}/pdf"):
        resp = anon.get(url)
        assert resp.status_code == 401 and resp.headers["Content-Type"].startswith("application/json")
    staff = api(login(m, "test_staff", PASSWORD)[0])
    assert staff.get(f"/api/billing/{own_bill}/soa.pdf").status_code == 403
    assert api(login(m, "test_accounting", PASSWORD)[0]).get(f"/api/billing/{own_bill}/soa.pdf").status_code == 200
    missing = api(login(m, SA_USERNAME, SA_PASSWORD)[0]).get("/api/billing/999999/soa.pdf")
    assert missing.status_code == 404 and missing.get_json()["error"]["message"] == "Statement not found."


def test_generation_failure_is_an_error_not_a_file(app_module, sa, monkeypatch):
    from app.services import soa_pdf

    def broken(*a, **k):
        raise RuntimeError("font missing")
    monkeypatch.setattr(soa_pdf, "build_soa_pdf", broken)
    bid, _ = a_bill(app_module, "TEST-502")
    resp = sa.get(f"/api/billing/{bid}/soa.pdf")
    assert resp.status_code == 500 and resp.headers["Content-Type"].startswith("application/json")
    assert "could not be generated" in resp.get_json()["error"]["message"]


@pytest.fixture
def long_statement(app_module):
    """A synthetic unit whose statement has 70 partial payments (forces several pages)."""
    m = app_module
    with m.app.test_request_context():
        u = m.Unit(unit_no="PDF-LONG", unit_type="3 BEDROOM", area_sqm=180, active=True)
        m.db.session.add(u); m.db.session.commit()
        others = [x.id for x in m.Unit.query.filter(m.Unit.id != u.id, m.Unit.active.is_(True)).all()]
        m.Unit.query.filter(m.Unit.id.in_(others)).update({"active": False}, synchronize_session=False)
        m.db.session.commit()
        m.generate_bills("2029-03")
        bill = m.Billing.query.filter_by(unit_id=u.id).one()
        for i in range(70):
            m.record_bill_payment(bill.id, amount=Decimal("100"), payment_method="CASH", payment_type="PARTIAL", reference=f"LONG-{i:03d}",
                                  remarks="", payment_date=date(2029, 3, 1 + i % 27), form_token=secrets.token_urlsafe(24), payer_type="Owner")
        ids = (u.id, bill.id, others)
    yield ids
    with m.app.app_context():
        uid, bid, others = ids
        for r in m.Receipt.query.filter_by(unit_id=uid).all():
            m.db.session.delete(r)
        m.db.session.flush()
        m.Payment.query.filter_by(billing_id=bid).delete()
        m.Billing.query.filter_by(unit_id=uid).delete()
        m.Unit.query.filter_by(id=uid).delete()
        m.Unit.query.filter(m.Unit.id.in_(others)).update({"active": True}, synchronize_session=False)
        m.db.session.commit()


def test_long_payment_history_is_omitted_from_soa(app_module, sa, long_statement):
    _, bid, _ = long_statement
    resp = sa.get(f"/api/billing/{bid}/soa.pdf")
    reader, text = pdf_text(resp.data)
    pages = [p.extract_text() for p in reader.pages]
    assert len(pages) == 1
    assert all(f"LONG-{i:03d}" not in text for i in range(70))
    assert "PAYMENTS RECEIVED" not in text
    assert "HOW TO PAY" not in text
    assert "Page 1 of 1" in pages[-1]
    assert all(p.strip() for p in pages)                                                   # no blank page


def test_soa_does_not_include_payment_sections_or_qr(app_module, sa):
    m = app_module
    bid, _ = a_bill(m, "TEST-502")
    with m.app.app_context():
        row = m.Setting.query.filter_by(key="online_payment_url").first()
        saved = row.value if row else None
    try:
        sa.put("/api/admin/rates", json={"onlinePaymentUrl": ""})
        no_qr = PdfReader(io.BytesIO(sa.get(f"/api/billing/{bid}/soa.pdf").data))
        assert sum(len(p.images) for p in no_qr.pages) == 0
        sa.put("/api/admin/rates", json={"onlinePaymentUrl": "https://pay.example/cl9"})
        with_qr = sa.get(f"/api/billing/{bid}/soa.pdf")
        reader, text = pdf_text(with_qr.data)
        assert sum(len(p.images) for p in reader.pages) == 0
        assert all(section not in text for section in ("PAYMENTS RECEIVED", "HOW TO PAY", "Scan to pay online"))
        before = sa.get(f"/api/billing/{bid}").get_json()["bill"]
        sa.get(f"/billing/{bid}/qr")                                                           # opening the QR...
        after = sa.get(f"/api/billing/{bid}").get_json()["bill"]
        assert (after["status"], after["amountPaid"]) == (before["status"], before["amountPaid"])   # ...never marks it paid
    finally:
        sa.put("/api/admin/rates", json={"onlinePaymentUrl": saved or ""})


def test_soa_email_uses_the_statement_figures_and_attaches_the_pdf(app_module, monkeypatch):
    m = app_module
    sent = []
    tls_contexts = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def ehlo(self): pass
        def starttls(self, context=None): tls_contexts.append(context)
        def login(self, *a): pass
        def send_message(self, msg): sent.append(msg)
    monkeypatch.setattr(m.smtplib, "SMTP", FakeSMTP)
    with m.app.test_request_context():
        for k, v in (("smtp_host", "smtp.example"), ("smtp_sender", "billing@example.com")):
            row = m.Setting.query.filter_by(key=k).first()
            if row:
                row.value = v
            else:
                m.db.session.add(m.Setting(key=k, value=v))
        m.db.session.commit()
        u = m.Unit.query.filter_by(unit_no="TEST-502").first()
        bill = m.Billing.query.filter_by(unit_id=u.id).first()
        from app.services.soa import soa_detail
        stmt = soa_detail(m, bill)
        m.send_soa_email_to_contact(bill, {"name": "Test Owner", "email": "owner@example.com"})
    msg = sent[0]
    assert len(tls_contexts) == 1
    assert tls_contexts[0].check_hostname is True and tls_contexts[0].verify_mode == ssl.CERT_REQUIRED
    html = msg.get_body(preferencelist=("html",)).get_content()
    assert stmt["statementNo"] in html and peso(stmt["total"]) in html
    attachments = list(msg.iter_attachments())
    assert len(attachments) == 1 and attachments[0].get_content_type() == "application/pdf"
    assert attachments[0].get_filename() == f"SOA_TEST-502_{bill.billing_month}.pdf"
    assert attachments[0].get_content()[:5] == b"%PDF-"
