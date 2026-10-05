"""Issued statements keep what was issued (migration 0010) + the confirmed billing rules.

Confirmed 2026-10-06: condo dues = area × rate per sqm (parking/storage likewise); overdue condo dues
carry a 4% penalty; water and unrelated charges never increase the penalty base; Decimal amounts
rounded half-up to the centavo."""
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
    """39.0 sqm at ₱51.28/sqm (₱1,999.92 a month), 4% penalty on condo dues; other units inactive."""
    m = app_module
    keys = ("penalty_rate", "penalty_include_condo", "penalty_include_water", "storage_in_total_from")
    with m.app.app_context():
        u = m.Unit(unit_no="SNAP-1", unit_type="1 BEDROOM", area_sqm=39.0, unit_rate_per_sqm=51.28, active=True)
        m.db.session.add(u)
        m.db.session.commit()
        others = [x.id for x in m.Unit.query.filter(m.Unit.id != u.id, m.Unit.active.is_(True)).all()]
        m.Unit.query.filter(m.Unit.id.in_(others)).update({"active": False}, synchronize_session=False)
        saved = {k: getattr(m.Setting.query.filter_by(key=k).first(), "value", None) for k in keys}
        put_setting(m, "penalty_rate", "4")
        put_setting(m, "penalty_include_condo", "1")
        m.db.session.commit()
        uid = u.id
    yield uid
    with m.app.app_context():
        bill_ids = [b.id for b in m.Billing.query.filter_by(unit_id=uid).all()]
        m.Payment.query.filter(m.Payment.billing_id.in_(bill_ids)).delete(synchronize_session=False)
        m.Billing.query.filter_by(unit_id=uid).delete()
        m.WaterReading.query.filter_by(unit_id=uid).delete()
        m.Unit.query.filter_by(id=uid).delete()
        m.Unit.query.filter(m.Unit.id.in_(others)).update({"active": True}, synchronize_session=False)
        for k, v in saved.items():
            if v is None:
                m.Setting.query.filter_by(key=k).delete()
            else:
                put_setting(m, k, v)
        m.db.session.commit()


def bill(m, uid, month):
    m.reset_financial_caches()
    return m.Billing.query.filter_by(unit_id=uid, billing_month=month).one()


def test_area_times_rate_and_the_confirmed_4_percent(app_module, unit):
    m = app_module
    with m.app.test_request_context():
        m.generate_bills("2025-05")
        may = bill(m, unit, "2025-05")
        assert m.money(may.assessment) == D("1999.92")                        # 39.0 × 51.28
        assert (may.dues_basis, m.money(may.condo_area), D(str(may.condo_rate))) == ("sqm", D("39.00"), D("51.2800"))
        assert D(str(may.penalty_rate)) == D("4.0000") and may.penalty_basis == "condo" and may.issued_at is not None
        assert m.money(may.issued_amount) == D("1999.92")
        m.generate_bills("2025-06")                                           # May unpaid and overdue
        jun = bill(m, unit, "2025-06")
        calc = m._bill_calc(jun)
        assert calc["penalty_base"] == D("1999.92") and calc["penalty"] == D("80.00")   # 79.9968 rounded half-up


def test_water_never_increases_the_penalty_base(app_module, unit):
    m = app_module
    with m.app.test_request_context():
        put_setting(m, "penalty_include_water", "1")                          # even if stored, it is ignored
        m.db.session.commit()
        m.save_water_reading(unit_id=unit, month="2025-05", previous=D("0"), current=D("11"), rate=D("50"), reading_date=date(2025, 5, 28))
        m.generate_bills("2025-05")
        assert m._bill_calc(bill(m, unit, "2025-05"))["water"] == D("550.00")
        m.generate_bills("2025-06")
        calc = m._bill_calc(bill(m, unit, "2025-06"))
        assert calc["previous"] == D("2549.92")                              # 1,999.92 + 550 unpaid
        assert calc["penalty_base"] == D("1999.92") and calc["penalty"] == D("80.00")
        assert "water" not in (bill(m, unit, "2025-06").penalty_basis or "")


def test_settings_changes_do_not_alter_issued_statements(app_module, unit):
    m = app_module
    with m.app.test_request_context():
        put_setting(m, "storage_in_total_from", "2025-01")
        m.db.session.commit()
        m.generate_bills("2025-05")
        m.generate_bills("2025-06")
        jun = bill(m, unit, "2025-06")
        before = m._bill_calc(jun)
        assert before["penalty"] == D("80.00") and jun.storage_included is True
        put_setting(m, "penalty_rate", "10")                                  # rules change later...
        put_setting(m, "storage_in_total_from", "2030-01")
        m.db.session.commit()
        m.reset_financial_caches()
        after = m._bill_calc(bill(m, unit, "2025-06"))
        assert (after["penalty"], after["total"]) == (before["penalty"], before["total"])   # ...the issued SOA is unchanged
        assert m.storage_charged(bill(m, unit, "2025-06")) is True
        m.generate_bills("2025-07")                                           # new statements use the new rules
        jul = bill(m, unit, "2025-07")
        assert D(str(jul.penalty_rate)) == D("10.0000") and jul.storage_included is False


def test_water_row_edits_do_not_silently_restate_but_explicit_saves_do(app_module, unit):
    m = app_module
    with m.app.test_request_context():
        m.save_water_reading(unit_id=unit, month="2025-05", previous=D("0"), current=D("10"), rate=D("50"), reading_date=date(2025, 5, 28))
        m.generate_bills("2025-05")
        may = bill(m, unit, "2025-05")
        issued = m.money(may.issued_amount)
        assert m._bill_calc(may)["water"] == D("500.00") and issued == D("2499.92")
        # A direct change to the reading row (not through the audited save) no longer changes the SOA.
        r = m.WaterReading.query.filter_by(unit_id=unit, reading_month="2025-05").one()
        r.current_reading = 20
        m.db.session.commit()
        assert m._bill_calc(bill(m, unit, "2025-05"))["water"] == D("500.00")
        # The audited correction updates the statement (an explicit restatement); the issued amount is kept.
        m.save_water_reading(unit_id=unit, month="2025-05", previous=D("0"), current=D("12"), rate=D("50"), reading_date=date(2025, 5, 28))
        may = bill(m, unit, "2025-05")
        assert m._bill_calc(may)["water"] == D("600.00") and m.money(may.issued_amount) == issued
        assert m.AuditLog.query.filter(m.AuditLog.entity_type == "water_reading").count() >= 2


def test_bills_issued_before_the_snapshot_keep_following_current_rules(app_module, unit):
    """Documented limitation: no issue-time values are invented for older bills."""
    m = app_module
    with m.app.test_request_context():
        m.generate_bills("2025-05")
        m.generate_bills("2025-06")
        jun = bill(m, unit, "2025-06")
        for col in ("issued_at", "issued_amount", "penalty_rate", "penalty_basis", "storage_included"):
            setattr(jun, col, None)                                           # as on a pre-0010 bill
        m.db.session.commit()
        assert m._bill_calc(bill(m, unit, "2025-06"))["penalty"] == D("80.00")
        put_setting(m, "penalty_rate", "10")
        m.db.session.commit()
        assert m._bill_calc(bill(m, unit, "2025-06"))["penalty"] == D("199.99")   # 199.992 half-up; follows current rate


def test_rounding_is_half_up_at_the_centavo(app_module, unit):
    m = app_module
    with m.app.test_request_context():
        m.generate_bills("2025-05")
        may = bill(m, unit, "2025-05")
        may.assessment = D("1000.05")                                         # 10% of 1,000.05 = 100.005
        m.db.session.commit()
        m.generate_bills("2025-06")
        jun = bill(m, unit, "2025-06")
        jun.penalty_rate = D("10")
        m.db.session.commit()
        assert m._bill_calc(bill(m, unit, "2025-06"))["penalty"] == D("100.01")   # half-even would give 100.00


def test_default_penalty_rate_for_new_installations_is_4(app_module):
    m = app_module
    with m.app.app_context():
        row = m.Setting.query.filter_by(key="penalty_rate").first()
        saved = row.value if row else None
        if row:
            m.db.session.delete(row)
            m.db.session.commit()
        try:
            assert m.current_penalty_rules()[0] == D("4")
        finally:
            if saved is not None:
                put_setting(m, "penalty_rate", saved)
                m.db.session.commit()
