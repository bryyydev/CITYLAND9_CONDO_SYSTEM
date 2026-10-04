"""Stage 2 regressions: configured due day and month ends, read-only billing view, strict money
input, duplicate submissions, receipt numbering, voids/reversals, period closing, audit atomicity.
(Concurrency under real row locks is verified on MariaDB: database/tools/mariadb_integration.py.)"""
import json
import secrets
from datetime import date
from decimal import Decimal

import pytest

from conftest import login, api

TEST_UNIT = "TEST-504"


@pytest.fixture
def m(app_module):
    return app_module


@pytest.fixture
def bill_factory(m):
    """Create stand-alone bills in far-future months for one TEST unit; clean up afterwards."""
    made = []

    def make(month, condo="1000.00"):
        with m.app.app_context():
            unit = m.Unit.query.filter_by(unit_no=TEST_UNIT).first()
            b = m.Billing(unit_id=unit.id, billing_month=month, assessment=Decimal(condo), parking_dues=0, storage_dues=0, water=0,
                          other=0, penalty=0, adjustment=0, previous_balance=0, amount_paid=0, due_date=date(int(month[:4]), int(month[5:]), 8),
                          status="Unpaid")
            m.db.session.add(b); m.db.session.commit()
            made.append(b.id)
            return b.id
    yield make
    with m.app.app_context():
        for bid in made:
            b = m.db.session.get(m.Billing, bid)
            if not b:
                continue
            for p in list(b.payments):
                for a in m.ReceiptAllocation.query.filter_by(payment_id=p.id).all():
                    r = a.receipt
                    m.db.session.delete(r)
                m.db.session.delete(p)
            m.db.session.delete(b)
        m.AdvancePayment.query.filter(m.AdvancePayment.remarks.like("%pytest%")).delete(synchronize_session=False)
        m.db.session.commit()


def pay(client, bid, amount, *, token=None, **extra):
    """Record a payment through Billing's API (as the React app does): a fresh one-time token per form."""
    api(client)
    body = {"amount": amount, "method": extra.pop("payment_method", "CASH"), "type": extra.pop("payment_type", "FULL"),
            "date": extra.pop("payment_date", "2032-01-20"), "reference": extra.pop("reference", ""), "remarks": "pytest",
            "formToken": secrets.token_urlsafe(24) if token is None else token}
    return client.post(f"/api/billing/{bid}/payments", json=body)


def void(client, rid, reason):
    """Void through Payments & ORs' API (as the React app does), with a fresh one-time form token."""
    return api(client).post(f"/api/receipts/{rid}/void", json={"reason": reason, "formToken": secrets.token_urlsafe(24)})


def state(m, bid):
    with m.app.app_context():
        b = m.db.session.get(m.Billing, bid)
        receipts = m.Receipt.query.join(m.ReceiptAllocation).join(m.Payment, m.ReceiptAllocation.payment_id == m.Payment.id).filter(m.Payment.billing_id == bid).all()
        return money(b.amount_paid), [r.receipt_no for r in receipts]


def money(v):
    return Decimal(str(v or 0)).quantize(Decimal("0.01"))


# ------------------------------------------------------------------ due dates
@pytest.mark.parametrize("due_day,month,expected", [("31", "2033-02", "2033-02-28"), ("31", "2032-02", "2032-02-29"),
                                                    ("31", "2033-04", "2033-04-30"), ("15", "2033-03", "2033-03-15"), ("1", "2033-03", "2033-03-01")])
def test_due_date_uses_configured_day_with_month_end(m, due_day, month, expected):
    with m.app.app_context():
        row = m.Setting.query.filter_by(key="due_day").first()
        old = row.value
        row.value = due_day; m.db.session.commit()
        try:
            assert m.due_date_for(month).isoformat() == expected
        finally:
            row.value = old; m.db.session.commit()


def test_invalid_due_day_is_rejected_by_settings(m, superadmin):
    superadmin.post("/settings", data={"due_day": "32"})
    superadmin.post("/settings", data={"due_day": "abc"})
    with m.app.app_context():
        assert m.due_day_setting() in range(1, 32) and m.setting("due_day") not in ("32", "abc")


def test_viewing_billing_never_changes_existing_bills(m, superadmin, bill_factory):
    bid = bill_factory("2032-03")
    with m.app.app_context():
        b = m.db.session.get(m.Billing, bid)
        b.due_date = date(2032, 3, 20); b.water = Decimal("123.45"); m.db.session.commit()
    superadmin.get("/billing?month=2032-03")
    with m.app.app_context():
        b = m.db.session.get(m.Billing, bid)
        assert b.due_date == date(2032, 3, 20) and money(b.water) == Decimal("123.45")


# ------------------------------------------------------------------ money input
@pytest.mark.parametrize("amount", ["nan", "NaN", "inf", "-Infinity", "1e5", "-5", "0", "abc", "12.345", "999999999999", ""])
def test_invalid_payment_amounts_are_rejected_without_changes(m, superadmin, bill_factory, amount):
    bid = bill_factory("2032-04")
    resp = pay(superadmin, bid, amount)
    assert resp.status_code == 400 and resp.get_json()["error"]["fields"]["amount"]
    assert state(m, bid) == (Decimal("0.00"), [])


def test_valid_amount_formats(m, superadmin, bill_factory):
    bid = bill_factory("2032-05", condo="2000.00")
    pay(superadmin, bid, "₱1,234.50")
    assert state(m, bid)[0] == Decimal("1234.50")


@pytest.mark.parametrize("field,value", [("current", "nan"), ("current", "inf"), ("rate", "-1"), ("previous", "1e3"), ("current", "5")])
def test_invalid_water_readings_are_rejected(m, superadmin, field, value):
    with m.app.app_context():
        unit = m.Unit.query.filter_by(unit_no=TEST_UNIT).first()
        before = m.WaterReading.query.filter_by(unit_id=unit.id, reading_month="2032-06").count()
    data = {"unitId": unit.id, "month": "2032-06", "previous": "10", "current": "20", "rate": "50", field: value}
    resp = api(superadmin).post("/api/water", json=data)            # Water Readings API (React)
    assert resp.status_code == 400 and resp.get_json()["error"]["fields"]
    with m.app.app_context():
        assert m.WaterReading.query.filter_by(unit_id=unit.id, reading_month="2032-06").count() == before


def test_money_parser_rounding_and_limits():
    from app.core.numbers import InputError, parse_amount, round_money
    assert round_money(Decimal("0.005")) == Decimal("0.01") and round_money("2.675") == Decimal("2.68")
    for bad in ("NaN", "sNaN", "Infinity", "1e2", "0x10", "1.2.3"):
        with pytest.raises(InputError):
            parse_amount(bad)
    assert parse_amount("0", allow_zero=True) == Decimal("0.00")
    assert parse_amount("-10.50", allow_negative=True) == Decimal("-10.50")


# ------------------------------------------------------------------ duplicate submissions
def test_same_form_submitted_twice_records_one_payment(m, superadmin, bill_factory):
    bid = bill_factory("2032-07")
    token = secrets.token_urlsafe(24)
    pay(superadmin, bid, "300", token=token)
    resp = pay(superadmin, bid, "300", token=token)
    paid, receipts = state(m, bid)
    assert paid == Decimal("300.00") and len(receipts) == 1
    assert resp.status_code == 409 and resp.get_json()["error"]["duplicate"] is True


def test_payment_form_without_token_is_refused(m, superadmin, bill_factory):
    bid = bill_factory("2032-08")
    resp = pay(superadmin, bid, "100", token="")
    assert resp.status_code == 400 and "formToken" in resp.get_json()["error"]["fields"]
    assert state(m, bid) == (Decimal("0.00"), [])
    # The classic payment form is retired: posting it changes nothing.
    superadmin.post(f"/billing/{bid}/pay", data={"amount": "100", "payment_method": "CASH", "payment_type": "FULL",
                                                 "payment_date": "2032-01-20"})
    assert state(m, bid) == (Decimal("0.00"), [])


def test_payment_tokens_are_one_time(m, superadmin, bill_factory):
    """The React payment form sends a fresh token; the same token can't record a second payment."""
    bid = bill_factory("2032-12")
    token = secrets.token_urlsafe(24)
    assert pay(superadmin, bid, "50", token=token).status_code == 201
    assert pay(superadmin, bid, "50", token=token).status_code == 409
    assert pay(superadmin, bid, "50").status_code == 201                     # a new form: a new token
    assert state(m, bid)[0] == Decimal("100.00")


# ------------------------------------------------------------------ receipt numbering
def test_receipt_numbers_are_consecutive_and_counter_backed(m, superadmin, bill_factory):
    bid = bill_factory("2032-09", condo="5000.00")
    for _ in range(3):
        pay(superadmin, bid, "100", payment_date="2039-01-02")
    with m.app.app_context():
        seqs = sorted(r.receipt_seq for r in m.Receipt.query.filter_by(receipt_year=2039).all())
        assert seqs == [1, 2, 3]
        assert m.db.session.get(m.ReceiptCounter, 2039).last_seq == 3


def test_counter_continues_after_existing_receipts(m):
    """A year whose receipts predate the counter (backfilled data) continues after the highest number."""
    with m.app.app_context():
        unit = m.Unit.query.filter_by(unit_no=TEST_UNIT).first()
        m.db.session.add(m.Receipt(receipt_year=2040, receipt_seq=41, receipt_no="OR-2040-000041", unit_id=unit.id,
                                   received_date=date(2040, 1, 1), amount=Decimal("1.00"), payment_method="CASH", source="backfill"))
        m.db.session.commit()
        try:
            assert m.next_receipt_number(2040) == 42
            m.db.session.rollback()
        finally:
            m.Receipt.query.filter_by(receipt_year=2040).delete(); m.ReceiptCounter.query.filter_by(year=2040).delete()
            m.db.session.commit()


# ------------------------------------------------------------------ voids / reversals
def receipt_for_bill(m, bid):
    with m.app.app_context():
        r = m.Receipt.query.join(m.ReceiptAllocation).join(m.Payment, m.ReceiptAllocation.payment_id == m.Payment.id).filter(m.Payment.billing_id == bid).first()
        return r.id, r.receipt_no


def test_void_reverses_payment_and_keeps_history(m, superadmin, bill_factory):
    bid = bill_factory("2032-10")
    pay(superadmin, bid, "400")
    rid, number = receipt_for_bill(m, bid)
    assert void(superadmin, rid, "short").status_code == 400                                   # reason too short
    assert state(m, bid)[0] == Decimal("400.00")
    assert void(superadmin, rid, "Recorded on the wrong unit by mistake").status_code == 200
    with m.app.app_context():
        r = m.db.session.get(m.Receipt, rid)
        b = m.db.session.get(m.Billing, bid)
        assert r.voided_at and r.receipt_no == number and r.void_reason.startswith("Recorded on")
        assert money(b.amount_paid) == 0 and b.status in ("Unpaid", "Overdue")
        assert all(p.reversed_at for p in b.payments) and len(b.payments) == 1           # original row kept
        entry = m.AuditLog.query.filter_by(entity_type="receipt", entity_id=rid).order_by(m.AuditLog.id.desc()).first()
        assert entry.reason.startswith("Recorded on") and json.loads(entry.details)["reversed"][0]["amount_paid"]["before"] == "400.00"
    # Already void: refused, nothing changes.
    assert void(superadmin, rid, "Second attempt at voiding it").status_code == 409
    assert state(m, bid)[0] == 0
    detail = superadmin.get(f"/api/receipts/{rid}").get_json()
    assert detail["receipt"]["voided"] is True and detail["voidedBy"] == "superadmin" and detail["canVoid"] is False


def test_void_requires_permission(m, superadmin, bill_factory):
    bid = bill_factory("2032-11")
    pay(superadmin, bid, "200")
    rid, _ = receipt_for_bill(m, bid)
    admin, _ = login(m, "test_admin", "Test-Pass-123")                                       # Admin may record, not void
    assert void(admin, rid, "Admin trying to void a receipt").status_code == 403
    with m.app.app_context():
        assert m.db.session.get(m.Receipt, rid).voided_at is None


def test_void_refused_when_advance_already_applied(m, superadmin, bill_factory):
    later = bill_factory("2033-01")
    with m.app.app_context():
        unit_id = m.Unit.query.filter_by(unit_no=TEST_UNIT).first().id
    # A 1-month advance starting 2033-01 is applied to that bill right away.
    assert api(superadmin).post("/api/advances", json={"unitId": unit_id, "amount": "600", "startMonth": "2033-01", "months": 1, "method": "CASH",
                                                      "date": "2032-12-20", "remarks": "pytest advance",
                                                      "formToken": secrets.token_urlsafe(24)}).status_code == 201
    with m.app.app_context():
        adv = m.AdvancePayment.query.filter(m.AdvancePayment.remarks == "pytest advance").order_by(m.AdvancePayment.id.desc()).first()
        applied = sum(Decimal(str(a.amount)) for a in m.AdvanceApplication.query.filter_by(advance_payment_id=adv.id))
        rid = m.ReceiptAllocation.query.filter_by(advance_payment_id=adv.id).first().receipt_id
        assert applied == Decimal("600.00")
    resp = void(superadmin, rid, "Trying to void after the advance was used")
    assert resp.status_code == 409 and "already applied" in resp.get_json()["error"]["message"]
    with m.app.app_context():
        assert m.db.session.get(m.Receipt, rid).voided_at is None                            # refused
        assert m.db.session.get(m.AdvancePayment, adv.id).reversed_at is None
        for row in m.AdvanceApplication.query.filter_by(billing_id=later).all():
            m.db.session.delete(row)
        m.db.session.delete(m.db.session.get(m.Receipt, rid))
        m.db.session.commit()


def test_voided_receipts_not_counted_in_ledger(m, superadmin, bill_factory):
    bid = bill_factory("2033-02")
    pay(superadmin, bid, "250", payment_date="2041-03-03")
    rid, _ = receipt_for_bill(m, bid)
    ledger = api(superadmin).get("/api/receipts?from=2041-03-01&to=2041-03-31").get_json()
    assert ledger["collected"] == "250.00" and ledger["total"] == 1
    assert void(superadmin, rid, "Duplicate entry for the same cheque").status_code == 200
    ledger = superadmin.get("/api/receipts?from=2041-03-01&to=2041-03-31").get_json()
    assert ledger["collected"] == "0.00" and ledger["voidCount"] == 1 and ledger["receipts"][0]["voided"] is True   # listed, not counted


# ------------------------------------------------------------------ period closing
def test_closed_period_blocks_payments_and_voids(m, superadmin, bill_factory):
    bid = bill_factory("2033-03")
    pay(superadmin, bid, "100", payment_date="2042-05-10")
    rid, _ = receipt_for_bill(m, bid)
    with m.app.app_context():
        row = m.Setting.query.filter_by(key="books_closed_through").first() or m.Setting(key="books_closed_through")
        row.value = "2042-05"; m.db.session.add(row); m.db.session.commit()
    try:
        pay(superadmin, bid, "100", payment_date="2042-05-20")
        resp = void(superadmin, rid, "Voiding in a closed month")
        assert resp.status_code == 400 and "closed" in resp.get_json()["error"]["message"]
        assert state(m, bid)[0] == Decimal("100.00")
        with m.app.app_context():
            assert m.db.session.get(m.Receipt, rid).voided_at is None
        pay(superadmin, bid, "50", payment_date="2042-06-01")                                   # open month: fine
        assert state(m, bid)[0] == Decimal("150.00")
    finally:
        with m.app.app_context():
            m.Setting.query.filter_by(key="books_closed_through").first().value = ""; m.db.session.commit()


# ------------------------------------------------------------------ audit atomicity
def test_payment_and_audit_are_saved_together(m, superadmin, bill_factory, monkeypatch):
    bid = bill_factory("2033-04")

    def broken_audit(*args, **kwargs):
        raise RuntimeError("audit store unavailable")
    monkeypatch.setattr(m, "audit", broken_audit)
    with pytest.raises(RuntimeError):
        pay(superadmin, bid, "100")
    assert state(m, bid) == (Decimal("0.00"), [])                                            # no payment without its audit


def test_payment_audit_has_structure(m, superadmin, bill_factory):
    bid = bill_factory("2033-05")
    pay(superadmin, bid, "1100")
    rid, number = receipt_for_bill(m, bid)
    with m.app.app_context():
        entry = m.AuditLog.query.filter_by(entity_type="receipt", entity_id=rid).order_by(m.AuditLog.id.desc()).first()
        details = json.loads(entry.details)
        assert entry.username == "superadmin" and number in entry.action
        assert details["received"] == "1100.00"
        assert Decimal(details["applied_to_bill"]) + Decimal(details["excess_to_advance"]) == Decimal("1100.00")
        assert details["amount_paid"]["after"] == details["applied_to_bill"] and details["status"]["before"]


# ------------------------------------------------------------------ Decimal pricing
def test_half_centavo_rounds_up(m):
    """64.5 sqm x 90.05 = 5808.225: float round() gave 5808.22; the documented rule gives 5808.23."""
    class U:
        unit_rate_per_sqm, area_sqm, unit_type = 90.05, 64.5, "2 BEDROOM"
    with m.app.test_request_context():
        assert m.unit_dues(U()) == Decimal("5808.23")
        assert round(64.5 * 90.05, 2) == 5808.22            # the old behaviour this replaces


def test_compare_dues_tool_runs_read_only(m):
    import subprocess, sys, os
    from conftest import _ROOT
    with m.app.app_context():
        before = m.Billing.query.count()
    r = subprocess.run([sys.executable, os.path.join("database", "tools", "compare_dues.py")], cwd=_ROOT,
                       env={**os.environ}, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert "difference(s)" in r.stdout
    with m.app.app_context():
        assert m.Billing.query.count() == before


def test_startup_keeps_issued_due_dates(m, bill_factory):
    """init_db() used to reset every bill's due date to the 8th on each start."""
    bid = bill_factory("2033-06")
    with m.app.app_context():
        m.db.session.get(m.Billing, bid).due_date = date(2033, 6, 25); m.db.session.commit()
    m.init_db()
    with m.app.app_context():
        assert m.db.session.get(m.Billing, bid).due_date == date(2033, 6, 25)
