"""SOA PDF download (server-generated, A4) for the office and residents, and the SOA email.

Checks real PDF output: content type, attachment file name, %PDF signature, text extracted with
pypdf (same figures as the SOA API), page count / repeated table headers for a long statement, the
payment QR only when a payment link is configured, and every access rule."""
import io
import secrets
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
    for expected in (detail["statementNo"], "TEST-502", "STATEMENT OF ACCOUNT", "CHARGES", "HOW TO PAY",
                     peso(detail["total"]), peso(detail["charges"]["condoDues"]), peso(abs(Decimal(detail["balance"])))):
        assert expected in text, expected
    assert "Page 1 of" in text


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
                                  remarks="", payment_date=date(2029, 3, 1 + i % 27), form_token=secrets.token_urlsafe(24))
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


def test_long_statement_paginates_with_repeated_headers(app_module, sa, long_statement):
    _, bid, _ = long_statement
    resp = sa.get(f"/api/billing/{bid}/soa.pdf")
    reader, text = pdf_text(resp.data)
    pages = [p.extract_text() for p in reader.pages]
    assert len(pages) >= 2
    detail = sa.get(f"/api/billing/{bid}").get_json()["bill"]
    assert all(f"LONG-{i:03d}" in text for i in range(70))                                 # nothing cut off
    assert peso(detail["total"]) in text and peso(detail["balance"]) in text
    later = [p for p in pages[1:] if "LONG-" in p]
    assert later and all("OR NO." in p and "AMOUNT" in p for p in later)                   # table header repeated
    assert f"Page {len(pages)} of {len(pages)}" in pages[-1]
    assert all(p.strip() for p in pages)                                                   # no blank page


def test_qr_only_when_a_payment_link_is_configured(app_module, sa):
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
        assert sum(len(p.images) for p in reader.pages) == 1 and "Scan to pay online" in text
        before = sa.get(f"/api/billing/{bid}").get_json()["bill"]
        sa.get(f"/billing/{bid}/qr")                                                           # opening the QR...
        after = sa.get(f"/api/billing/{bid}").get_json()["bill"]
        assert (after["status"], after["amountPaid"]) == (before["status"], before["amountPaid"])   # ...never marks it paid
    finally:
        sa.put("/api/admin/rates", json={"onlinePaymentUrl": saved or ""})


def test_soa_email_uses_the_statement_figures_and_attaches_the_pdf(app_module, monkeypatch):
    m = app_module
    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def ehlo(self): pass
        def starttls(self): pass
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
    html = msg.get_body(preferencelist=("html",)).get_content()
    assert stmt["statementNo"] in html and peso(stmt["total"]) in html
    attachments = list(msg.iter_attachments())
    assert len(attachments) == 1 and attachments[0].get_content_type() == "application/pdf"
    assert attachments[0].get_filename() == f"SOA_TEST-502_{bill.billing_month}.pdf"
    assert attachments[0].get_content()[:5] == b"%PDF-"
