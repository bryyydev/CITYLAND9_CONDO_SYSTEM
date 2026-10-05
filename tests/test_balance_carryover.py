"""Bug B1 (fixed 2026-10-05): balances carried over between months.

Each bill's total includes the unit's earlier unpaid balance ("previous"). The previous balance used to
be the SUM of the earlier bills' full balances, so arrears were counted again every month (3 unpaid
months of 3,000 showed 12,000), and a payment on the latest bill never cleared the older bills, which
came back on the next SOA. Now: one running balance per unit, and payments settle the oldest charges
first (bill balances, statuses and the penalty base)."""
import secrets
from datetime import date
from decimal import Decimal

import pytest

D = Decimal
MONTHS = ("2025-01", "2025-02", "2025-03", "2025-04")


def put_setting(m, key, value):
    row = m.Setting.query.filter_by(key=key).first()
    if row:
        row.value = value
    else:
        m.db.session.add(m.Setting(key=key, value=value))


@pytest.fixture
def unit(app_module):
    """A fresh 1-bedroom unit (dues 3,000/month); the other units are inactive during the test so
    bill generation only bills this one. Everything it creates is removed afterwards."""
    m = app_module
    with m.app.app_context():
        u = m.Unit(unit_no="CARRY-1", unit_type="1 BEDROOM", area_sqm=40, active=True)
        m.db.session.add(u)
        m.db.session.commit()
        others = [x.id for x in m.Unit.query.filter(m.Unit.id != u.id, m.Unit.active.is_(True)).all()]
        m.Unit.query.filter(m.Unit.id.in_(others)).update({"active": False}, synchronize_session=False)
        saved = {k: getattr(m.Setting.query.filter_by(key=k).first(), "value", None) for k in ("penalty_rate", "penalty_include_condo")}
        put_setting(m, "penalty_rate", "0")
        m.db.session.commit()
        uid = u.id
    yield uid
    with m.app.app_context():
        for r in m.Receipt.query.filter_by(unit_id=uid).all():
            m.db.session.delete(r)
        m.db.session.flush()
        bill_ids = [b.id for b in m.Billing.query.filter_by(unit_id=uid).all()]
        m.Payment.query.filter(m.Payment.billing_id.in_(bill_ids)).delete(synchronize_session=False)
        adv_ids = [a.id for a in m.AdvancePayment.query.filter_by(unit_id=uid).all()]
        m.AdvanceApplication.query.filter(m.AdvanceApplication.advance_payment_id.in_(adv_ids)).delete(synchronize_session=False)
        m.AdvancePayment.query.filter_by(unit_id=uid).delete()
        m.Billing.query.filter_by(unit_id=uid).delete()
        m.Unit.query.filter_by(id=uid).delete()
        m.Unit.query.filter(m.Unit.id.in_(others)).update({"active": True}, synchronize_session=False)
        for k, v in saved.items():
            if v is None:
                m.Setting.query.filter_by(key=k).delete()
            else:
                put_setting(m, k, v)
        m.db.session.commit()


def bills(m, uid):
    m.reset_financial_caches()
    return {b.billing_month: b for b in m.Billing.query.filter_by(unit_id=uid).order_by(m.Billing.billing_month).all()}


def calc(m, uid, month):
    return m._bill_calc(bills(m, uid)[month])


def pay(m, bill_id, amount, when=date(2025, 4, 20)):
    return m.record_bill_payment(bill_id, amount=D(amount), payment_method="CASH", payment_type="FULL", reference="", remarks="",
                                 payment_date=when, form_token=secrets.token_urlsafe(24))


def generate(m, *months):
    for month in months:
        m.generate_bills(month)


def test_unpaid_months_are_counted_once(app_module, unit):
    m = app_module
    with m.app.test_request_context():
        generate(m, *MONTHS[:3])
        figures = [(calc(m, unit, mo)["previous"], calc(m, unit, mo)["total"]) for mo in MONTHS[:3]]
        assert figures == [(D("0"), D("3000")), (D("3000"), D("6000")), (D("6000"), D("9000"))]   # was 9,000 / 12,000
        b = bills(m, unit)
        assert [m.bill_unpaid_part(b[mo]) for mo in MONTHS[:3]] == [D("3000")] * 3
        assert m.previous_outstanding(unit, "2025-04") == D("9000")                               # was 18,000
        assert m.overdue_months_for_unit(unit) == ["2025-03", "2025-02", "2025-01"]


def test_paying_the_latest_soa_clears_the_older_bills(app_module, unit):
    m = app_module
    with m.app.test_request_context():
        generate(m, "2025-01", "2025-02")
        result = pay(m, bills(m, unit)["2025-02"].id, "6000")
        assert result["applied"] == D("6000") and result["excess"] == D("0")
        b = bills(m, unit)
        assert m.bill_balance(b["2025-01"]) == 0 and m.bill_status(b["2025-01"]) == "Paid"         # settled by the later payment
        assert m.bill_status(b["2025-02"]) == "Paid"
        assert m.overdue_months_for_unit(unit) == []
        generate(m, "2025-03")
        mar = calc(m, unit, "2025-03")
        assert (mar["previous"], mar["total"]) == (D("0"), D("3000"))                               # January no longer comes back
        assert bills(m, unit)["2025-03"].previous_balance == 0                                       # stored figure too


def test_the_old_bill_cannot_be_paid_twice(app_module, unit):
    m = app_module
    with m.app.test_request_context():
        generate(m, "2025-01", "2025-02")
        pay(m, bills(m, unit)["2025-02"].id, "6000")
        jan = bills(m, unit)["2025-01"]
        result = pay(m, jan.id, "3000")
        # Nothing is owed on January any more: the money is kept as an advance, January's amount paid is unchanged.
        assert result["applied"] == D("0") and result["excess"] == D("3000")
        assert m.money(bills(m, unit)["2025-01"].amount_paid) == D("0")


def test_a_partial_payment_settles_the_oldest_month_first(app_module, unit):
    m = app_module
    with m.app.test_request_context():
        generate(m, *MONTHS[:3])
        pay(m, bills(m, unit)["2025-03"].id, "4000")
        b = bills(m, unit)
        assert [m.bill_status(b[mo]) for mo in MONTHS[:3]] == ["Paid", "Partially Paid", "Partially Paid"]
        assert [m.bill_unpaid_part(b[mo]) for mo in MONTHS[:3]] == [D("0"), D("2000"), D("3000")]
        assert m.bill_balance(b["2025-03"]) == D("5000")
        generate(m, "2025-04")
        assert calc(m, unit, "2025-04")["previous"] == D("5000")


def test_paying_an_older_bill_directly_reduces_the_next_soa(app_module, unit):
    m = app_module
    with m.app.test_request_context():
        generate(m, "2025-01", "2025-02")
        pay(m, bills(m, unit)["2025-01"].id, "3000")
        feb = calc(m, unit, "2025-02")
        assert (feb["previous"], feb["total"], feb["balance"]) == (D("0"), D("3000"), D("3000"))


def test_paid_dues_stop_earning_penalty(app_module, unit):
    m = app_module
    with m.app.test_request_context():
        put_setting(m, "penalty_rate", "10")
        put_setting(m, "penalty_include_condo", "1")
        m.db.session.commit()
        generate(m, "2025-01", "2025-02")
        assert calc(m, unit, "2025-02")["penalty"] == D("300.00")                  # 10% of January's unpaid dues
        pay(m, bills(m, unit)["2025-02"].id, str(calc(m, unit, "2025-02")["total"]))
        generate(m, "2025-03")
        mar = calc(m, unit, "2025-03")
        assert mar["penalty_base"] == D("0") and mar["penalty"] == D("0")          # was 300.00 forever (January stayed "unpaid")
        assert m.selected_penalty_base_for_unit(unit, "2025-03") == D("0")


def test_voiding_the_receipt_brings_the_balances_back(app_module, unit):
    m = app_module
    with m.app.test_request_context():
        generate(m, "2025-01", "2025-02")
        receipt = pay(m, bills(m, unit)["2025-02"].id, "6000")["receipt"]
        m.void_receipt_record(receipt.id, "Bounced check, test of bug B1", secrets.token_urlsafe(24))
        b = bills(m, unit)
        assert m.bill_balance(b["2025-01"]) == D("3000") and m.bill_balance(b["2025-02"]) == D("6000")
