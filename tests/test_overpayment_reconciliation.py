"""Overpayment reconciliation (CITYLAND9_SOA_VERIFICATION.md, "Excess not allocated both to bill and
same-month advance"). A payment above the bill balance used to be split into a bill payment AND an
advance that was immediately applied to the SAME bill, so the excess was counted twice: the bill went
to a hidden negative balance (shown as ₱0.00 / Paid) and the credit was used up.

Now each peso is allocated exactly once: the excess becomes an advance for the NEXT billing month,
which is applied when that month's bill exists (at once, if it already exists)."""
import secrets
from datetime import date
from decimal import Decimal

import pytest

D = Decimal


def put_setting(m, key, value):
    row = m.Setting.query.filter_by(key=key).first()
    if row:
        row.value = value
    else:
        m.db.session.add(m.Setting(key=key, value=value))


@pytest.fixture
def unit(app_module):
    """A fresh 1-bedroom unit (dues 3,000/month, no penalty); other units inactive meanwhile."""
    m = app_module
    with m.app.app_context():
        u = m.Unit(unit_no="OVERPAY-1", unit_type="1 BEDROOM", area_sqm=40, active=True)
        m.db.session.add(u)
        m.db.session.commit()
        others = [x.id for x in m.Unit.query.filter(m.Unit.id != u.id, m.Unit.active.is_(True)).all()]
        m.Unit.query.filter(m.Unit.id.in_(others)).update({"active": False}, synchronize_session=False)
        saved = getattr(m.Setting.query.filter_by(key="penalty_rate").first(), "value", None)
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
        if saved is None:
            m.Setting.query.filter_by(key="penalty_rate").delete()
        else:
            put_setting(m, "penalty_rate", saved)
        m.db.session.commit()


def bill(m, uid, month):
    m.reset_financial_caches()
    return m.Billing.query.filter_by(unit_id=uid, billing_month=month).one()


def pay(m, bill_id, amount, when):
    return m.record_bill_payment(bill_id, amount=D(amount), payment_method="CASH", payment_type="FULL", reference="", remarks="",
                                 payment_date=when, form_token=secrets.token_urlsafe(24))


def advance_applied(m, bill_id):
    return sum((m.money(a.amount) for a in m.AdvanceApplication.query.filter_by(billing_id=bill_id).all()), D("0"))


def test_excess_is_counted_once_and_carried_to_next_month(app_module, unit):
    m = app_module
    with m.app.test_request_context():
        m.generate_bills("2030-01")
        jan = bill(m, unit, "2030-01")
        assert m.bill_balance(jan) == D("3000.00")
        result = pay(m, jan.id, "3500", date(2030, 1, 5))
        # Amount received / applied to the bill / remaining advance stay distinct.
        assert (result["applied"], result["excess"]) == (D("3000.00"), D("500.00"))
        receipt = result["receipt"]
        assert m.money(receipt.amount) == D("3500.00")
        assert sum((m.money(a.amount) for a in receipt.allocations), D("0")) == D("3500.00")

        jan = bill(m, unit, "2030-01")
        calc = m._bill_calc(jan)
        # The defect: ₱500 of the excess was ALSO applied to January (total 2,500, raw balance -500).
        assert advance_applied(m, jan.id) == D("0.00")
        assert calc["total"] == D("3000.00") and m.money(jan.amount_paid) == D("3000.00")
        assert calc["balance"] == D("0.00")                      # raw balance, not clamped
        adv = m.AdvancePayment.query.filter_by(unit_id=unit).one()
        assert adv.start_month == "2030-02" and m.advance_balance(adv) == D("500.00")   # credit kept for February

        # February's bill uses the credit once.
        m.generate_bills("2030-02")
        feb = bill(m, unit, "2030-02")
        assert advance_applied(m, feb.id) == D("500.00") and m._bill_calc(feb)["total"] == D("2500.00")
        assert m.advance_balance(m.AdvancePayment.query.filter_by(unit_id=unit).one()) == D("0.00")
        pay(m, feb.id, "2500", date(2030, 2, 5))
        feb = bill(m, unit, "2030-02")
        assert m._bill_calc(feb)["balance"] == D("0.00")
        # Ledger: cash received (6,000) == charges (6,000); nothing double counted.
        received = sum((m.money(r.amount) for r in m.Receipt.query.filter_by(unit_id=unit).all()), D("0"))
        charged = sum((m._bill_calc(b)["charge"] + advance_applied(m, b.id) for b in m.Billing.query.filter_by(unit_id=unit).all()), D("0"))
        assert received == charged == D("6000.00")


def test_excess_applies_at_once_when_next_month_is_already_billed(app_module, unit):
    m = app_module
    with m.app.test_request_context():
        m.generate_bills("2030-01")
        m.generate_bills("2030-02")
        jan = bill(m, unit, "2030-01")
        pay(m, jan.id, "3400", date(2030, 1, 5))
        assert advance_applied(m, bill(m, unit, "2030-01").id) == D("0.00")
        feb = bill(m, unit, "2030-02")
        assert advance_applied(m, feb.id) == D("400.00")
        # February was billed 3,000 with January's 0 previous balance; 400 of the excess covers part of it.
        assert m._bill_calc(feb)["balance"] == D("2600.00")


def test_payment_on_a_paid_bill_becomes_next_month_credit(app_module, unit):
    m = app_module
    with m.app.test_request_context():
        m.generate_bills("2030-01")
        jan = bill(m, unit, "2030-01")
        pay(m, jan.id, "3000", date(2030, 1, 5))
        result = pay(m, jan.id, "1000", date(2030, 1, 20))
        assert (result["applied"], result["excess"]) == (D("0.00"), D("1000.00"))
        jan = bill(m, unit, "2030-01")
        assert advance_applied(m, jan.id) == D("0.00") and m._bill_calc(jan)["balance"] == D("0.00")
        assert sum((m.advance_balance(a) for a in m.AdvancePayment.query.filter_by(unit_id=unit).all()), D("0")) == D("1000.00")


def test_a_scheduled_advance_never_pays_more_than_a_bill_still_owes(app_module, unit):
    """A prepaid advance recorded after the month was already paid in cash is not applied on top of
    the cash payment (it stays as remaining credit for later months instead)."""
    m = app_module
    with m.app.test_request_context():
        m.generate_bills("2030-01")
        jan = bill(m, unit, "2030-01")
        pay(m, jan.id, "3000", date(2030, 1, 5))
        m.record_advance_payment(unit_id=unit, amount=D("6000"), start_month="2030-01", months=2, method="CASH", reference="",
                                 remarks="prepaid", payment_date=date(2030, 1, 10), form_token=secrets.token_urlsafe(24))
        jan = bill(m, unit, "2030-01")
        assert advance_applied(m, jan.id) == D("0.00") and m._bill_calc(jan)["balance"] == D("0.00")
        adv = m.AdvancePayment.query.filter_by(unit_id=unit, remarks="prepaid").one()
        assert m.advance_balance(adv) == D("6000.00")


def test_detector_finds_old_double_allocations_and_changes_nothing(app_module, unit):
    """What the old code wrote (excess advance starting in the same month, applied to the same paid
    bill) is reported by the read-only detector; nothing is modified."""
    import importlib.util
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "database", "tools", "detect_overpayment_allocations.py")
    spec = importlib.util.spec_from_file_location("detect_overpayment_allocations", path)
    detector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(detector)
    m = app_module
    with m.app.test_request_context():
        m.generate_bills("2030-01")
        jan = bill(m, unit, "2030-01")
        pay(m, jan.id, "3000", date(2030, 1, 5))
        adv = m.AdvancePayment(unit_id=unit, payment_date=date(2030, 1, 5), amount=D("500"), start_month="2030-01", coverage_months=1,
                               monthly_amount=D("500"), payment_method="CASH", remarks=f"{detector.MARK}2030-01.")
        m.db.session.add(adv); m.db.session.flush()
        m.db.session.add(m.AdvanceApplication(advance_payment_id=adv.id, billing_id=jan.id, billing_month="2030-01", amount=D("500")))
        m.db.session.commit()
        counts = (m.AdvanceApplication.query.count(), m.AdvancePayment.query.count(), m.Payment.query.count())
        rows = [r for r in detector.find_affected(m) if r["bill_id"] == jan.id]
        assert len(rows) == 1 and rows[0]["kind"] == "A" and rows[0]["applied"] == "500.00" and rows[0]["raw_balance"] == "-500.00"
        assert (m.AdvanceApplication.query.count(), m.AdvancePayment.query.count(), m.Payment.query.count()) == counts
        assert m.money(m.AdvanceApplication.query.filter_by(billing_id=jan.id).one().amount) == D("500.00")   # not rewritten
        # Re-running the allocator does not rewrite the recorded allocation either.
        m.allocate_advances_for_month("2030-01", unit)
        m.db.session.commit()
        assert m.money(m.AdvanceApplication.query.filter_by(billing_id=jan.id).one().amount) == D("500.00")
