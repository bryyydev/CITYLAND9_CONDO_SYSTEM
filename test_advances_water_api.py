"""Advance Payments (/api/advances) and Water Readings (/api/water): permissions, validation,
persistence, receipts, bill synchronisation, closed periods, CSRF and the retired classic pages."""
import secrets
from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from openpyxl import Workbook
from sqlalchemy import true
from sqlalchemy.dialects import mssql

from conftest import PASSWORD, SA_PASSWORD, SA_USERNAME, api, login


def token():
    return secrets.token_urlsafe(24)


def water_workbook(unit_rows=(), commercial_rows=()):
    workbook = Workbook()
    workbook.remove(workbook.active)
    for name, headers, rows in (
        ("WaterReadings", ("unit_no", "reading_month", "previous_reading", "current_reading", "rate", "reading_date"), unit_rows),
        ("CommercialWaterReadings", ("commercial_type", "reading_month", "previous_reading", "current_reading", "rate", "reading_date"), commercial_rows),
    ):
        sheet = workbook.create_sheet(name)
        sheet.append(headers)
        for row in rows:
            sheet.append(row)
    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)
    return stream


def water_period_workbook(unit_rows=()):
    workbook = Workbook()
    workbook.remove(workbook.active)
    sheet = workbook.create_sheet("WaterReadings")
    sheet.append(("unit_no", "reading_month", "previous_reading", "current_reading", "rate", "reading_from", "reading_to"))
    for row in unit_rows:
        sheet.append(row)
    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)
    return stream


@pytest.fixture
def sa(app_module):
    return api(login(app_module, SA_USERNAME, SA_PASSWORD)[0])


def unit_id(m, no):
    with m.app.app_context():
        return m.Unit.query.filter_by(unit_no=no).first().id


@pytest.fixture
def cleanup(app_module):
    """Remove water readings (by month) and advances (by remark) a test created, with their receipts."""
    months, remarks = [], []
    yield months, remarks
    m = app_module
    with m.app.app_context():
        for r in m.WaterReading.query.filter(m.WaterReading.reading_month.in_(months)).all():
            for a in m.ReceiptAllocation.query.filter_by(water_reading_id=r.id).all():
                m.db.session.delete(a.receipt)
            m.db.session.delete(r)
        m.CommercialWaterReading.query.filter(m.CommercialWaterReading.reading_month.in_(months)).delete(synchronize_session=False)
        m.Billing.query.filter(m.Billing.billing_month.in_(months)).delete(synchronize_session=False)
        for adv in m.AdvancePayment.query.filter(m.AdvancePayment.remarks.in_(remarks)).all():
            m.AdvanceApplication.query.filter_by(advance_payment_id=adv.id).delete()
            for a in m.ReceiptAllocation.query.filter_by(advance_payment_id=adv.id).all():
                m.db.session.delete(a.receipt)
            m.db.session.delete(adv)
        m.db.session.commit()


# ------------------------------------------------------------------ permissions
def test_permissions(app_module):
    for role, adv, water in (("admin", 200, 200), ("accounting", 200, 403), ("manager", 403, 403), ("staff", 403, 403), ("resident", 403, 403)):
        client = api(login(app_module, f"test_{role}", PASSWORD)[0])
        assert client.get("/api/advances").status_code == adv, role
        assert client.get("/api/water").status_code == water, role
        assert client.get("/api/water/bulk-template").status_code == water, role
    anon = app_module.app.test_client()
    assert anon.get("/api/advances").status_code == 401 and anon.get("/api/water").status_code == 401


# ------------------------------------------------------------------ advance payments
def test_advance_is_recorded_receipted_and_applied(app_module, sa, cleanup):
    months, remarks = cleanup
    m = app_module
    uid = unit_id(m, "TEST-504")
    with m.app.app_context():                 # a bill in the first covered month: the advance is applied to it at once
        m.db.session.add(m.Billing(unit_id=uid, billing_month="2038-01", assessment=Decimal("1000"), parking_dues=0, storage_dues=0, water=0,
                                   other=0, penalty=0, adjustment=0, previous_balance=0, amount_paid=0, due_date=date(2038, 1, 8), status="Unpaid"))
        m.db.session.commit()
    months.append("2038-01"); remarks.append("pytest adv api")
    body = {"unitId": uid, "amount": "3000", "startMonth": "2038-01", "months": 3, "method": "ONLINE", "reference": "GCASH-77",
            "date": "2037-12-20", "remarks": "pytest adv api", "payerType": "Owner", "formToken": token()}
    r = sa.post("/api/advances", json=body)
    assert r.status_code == 201, r.get_json()
    a = r.get_json()["advance"]
    assert r.get_json()["receiptNo"].startswith("OR-2037-") and a["monthlyAmount"] == "1000.00" and a["endMonth"] == "2038-03"
    assert a["applied"] == "1000.00" and a["remaining"] == "2000.00" and a["payerName"] == "Ana Garcia"
    listed = sa.get(f"/api/advances?unit={uid}&status=open").get_json()
    assert any(x["id"] == a["id"] for x in listed["advances"])
    assert sa.post("/api/advances", json={**body, "formToken": body["formToken"]}).status_code == 409      # same form twice


def test_advance_validation(app_module, sa):
    errs = sa.post("/api/advances", json={"unitId": unit_id(app_module, "P-TEST-01"), "amount": "abc", "startMonth": "soon", "months": 0,
                                          "method": "CHECK", "formToken": token()}).get_json()["error"]["fields"]
    assert "amount" in errs
    errs = sa.post("/api/advances", json={"unitId": unit_id(app_module, "P-TEST-01"), "amount": "100", "startMonth": "2038-05", "months": 1,
                                          "method": "CASH", "formToken": token()}).get_json()["error"]["fields"]
    assert "unitId" in errs                                                                      # parking units can't prepay dues
    errs = sa.post("/api/advances", json={"unitId": unit_id(app_module, "TEST-501"), "amount": "100", "startMonth": "2038-05", "months": 1,
                                          "method": "CHECK", "formToken": token()}).get_json()["error"]["fields"]
    assert "reference" in errs
    assert sa.post("/api/advances", json={"unitId": unit_id(app_module, "TEST-501"), "amount": "100", "startMonth": "2038-05",
                                          "months": 1, "method": "CASH", "formToken": "x"}).status_code == 400
    opts = sa.get("/api/advances/options").get_json()["units"]
    assert {u["unitNo"] for u in opts} == {"TEST-501", "TEST-502", "TEST-503", "TEST-504"}


# ------------------------------------------------------------------ water readings
def test_bulk_water_import_saves_reading_period_range(app_module, sa, cleanup):
    months, _ = cleanup
    months.append("2042-10")
    workbook = water_period_workbook([
        ("TEST-503", "2042-10", 0, 10, 50, "2042-09-16", "2042-10-15"),
    ])
    checked = sa.post("/api/water/bulk-import/check", data={"file": (workbook, "water.xlsx")},
                      content_type="multipart/form-data")
    assert checked.status_code == 200, checked.get_json()
    preview_row = checked.get_json()["rows"][0]
    assert (preview_row["readingFrom"], preview_row["readingTo"]) == ("2042-09-16", "2042-10-15")

    saved = sa.post("/api/water/bulk-import", data={
        "file": (water_period_workbook([
            ("TEST-503", "2042-10", 0, 10, 50, "2042-09-16", "2042-10-15"),
        ]), "water.xlsx"),
    }, content_type="multipart/form-data")
    assert saved.status_code == 201, saved.get_json()
    with app_module.app.app_context():
        reading = app_module.WaterReading.query.filter_by(
            unit_id=unit_id(app_module, "TEST-503"), reading_month="2042-10"
        ).one()
        assert reading.reading_period_start == date(2042, 9, 16)
        assert reading.reading_period_end == date(2042, 10, 15)
        assert reading.reading_date == date(2042, 10, 15)


def test_bulk_water_import_handles_null_previous_reading_values(app_module, sa):
    m = app_module
    uid = unit_id(m, "TEST-503")
    with m.app.app_context():
        m.db.session.add(m.WaterReading(
            unit_id=uid, reading_month="2038-09", previous_reading=None, current_reading=None,
            rate=50, reading_date=date(2038, 9, 15),
        ))
        m.db.session.commit()
    response = sa.post("/api/water/bulk-import/check", data={
        "file": (water_workbook([("TEST-503", "2038-10", None, 120, None, "2038-10-15")]), "water.xlsx"),
    }, content_type="multipart/form-data")
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["valid"] == 1


def test_water_bulk_active_unit_filter_compiles_for_sql_server(app_module):
    statement = app_module.Unit.__table__.select().where(app_module.Unit.active == true())
    sql = str(statement.compile(dialect=mssql.dialect()))
    assert "unit.active = 1" in sql


def test_bulk_water_template_permissions_preview_and_atomic_save(app_module, sa, cleanup):
    months, _ = cleanup
    months.extend(["2040-01", "2040-02", "2041-01"])
    template = sa.get("/api/water/bulk-template")
    assert template.status_code == 200 and template.data[:2] == b"PK"
    from openpyxl import load_workbook
    workbook = load_workbook(BytesIO(template.data), read_only=True)
    assert workbook["WaterReadings"].values.__next__() == ("unit_no", "reading_month", "previous_reading", "current_reading", "rate", "reading_from", "reading_to")
    assert workbook["CommercialWaterReadings"].values.__next__() == ("commercial_type", "reading_month", "previous_reading", "current_reading", "rate", "reading_from", "reading_to")
    workbook.close()

    m = app_module
    with m.app.app_context():
        commercial = m.Unit.query.filter_by(unit_no="BULK-WATER-COMMERCIAL", unit_type="COMMERCIAL").first()
        if not commercial:
            commercial = m.Unit(unit_no="BULK-WATER-COMMERCIAL", floor="2F", unit_type="COMMERCIAL",
                                area_sqm=Decimal("40"), unit_rate_per_sqm=0, dues_mode="per_sqm",
                                status="Vacant", active=True)
            m.db.session.add(commercial)
            m.db.session.commit()
    good = water_workbook(
        [
            ("TEST-503", "2040-01", 100, 110, 50, "2040-01-15"),
            ("TEST-503", "2040-02", None, 120, None, "2040-02-15"),
        ],
        [("2F", "2040-01", 0, 5, 50, "2040-01-15")],
    )
    checked = sa.post("/api/water/bulk-import/check", data={"file": (good, "water.xlsx")}, content_type="multipart/form-data")
    assert checked.status_code == 200, checked.get_json()
    report = checked.get_json()
    assert (report["total"], report["valid"], report["invalid"]) == (3, 3, 0)
    assert report["rows"][1]["previous"] == "110.000"

    saved = sa.post("/api/water/bulk-import", data={
        "file": (water_workbook(
            [
                ("TEST-503", "2040-01", 100, 110, 50, "2040-01-15"),
                ("TEST-503", "2040-02", None, 120, None, "2040-02-15"),
            ],
            [("2F", "2040-01", 0, 5, 50, "2040-01-15")],
        ), "water.xlsx"),
    }, content_type="multipart/form-data")
    assert saved.status_code == 201, saved.get_json()
    assert saved.get_json()["created"] == 3
    with m.app.app_context():
        jan = m.WaterReading.query.filter_by(unit_id=unit_id(m, "TEST-503"), reading_month="2040-01").one()
        feb = m.WaterReading.query.filter_by(unit_id=unit_id(m, "TEST-503"), reading_month="2040-02").one()
        shared = m.CommercialWaterReading.query.filter_by(commercial_type="2F", reading_month="2040-01").one()
        assert jan.reading_date == date(2040, 1, 15) and jan.current_reading == 110
        assert feb.previous_reading == 110 and feb.current_reading == 120
        assert shared.reading_date == date(2040, 1, 15) and shared.current_reading == 5

    invalid = water_workbook(
        [
            ("TEST-503", "2041-01", 120, 125, 50, "2041-01-15"),
            ("NO-SUCH-UNIT", "2041-01", 0, 2, 50, "2041-01-15"),
        ],
    )
    blocked = sa.post("/api/water/bulk-import", data={"file": (invalid, "water.xlsx")}, content_type="multipart/form-data")
    assert blocked.status_code == 409 and blocked.get_json()["error"]["preview"]["invalid"] == 1
    with m.app.app_context():
        assert m.WaterReading.query.filter_by(unit_id=unit_id(m, "TEST-503"), reading_month="2041-01").first() is None
        m.Unit.query.filter_by(unit_no="BULK-WATER-COMMERCIAL").delete()
        m.db.session.commit()
    existing = sa.post("/api/water/bulk-import/check", data={
        "file": (water_workbook([("TEST-501", "2026-09", 20, 999, 50, "2026-09-15")]), "water.xlsx"),
    }, content_type="multipart/form-data")
    assert existing.status_code == 200
    assert existing.get_json()["invalid"] == 1
    assert "never overwrites existing readings" in existing.get_json()["rows"][0]["errors"][0]
    with m.app.app_context():
        reading = m.WaterReading.query.filter_by(unit_id=unit_id(m, "TEST-501"), reading_month="2026-09").one()
        assert reading.current_reading == 26


def test_water_reading_save_correct_and_bill_sync(app_module, sa, cleanup):
    months, _ = cleanup
    m = app_module
    uid = unit_id(m, "TEST-503")
    months.extend(["2038-06", "2038-07", "2038-08"])
    first = sa.post("/api/water", json={"unitId": uid, "month": "2038-06", "previous": "100", "current": "110", "rate": "50",
                                        "readingFrom": "2038-05-16", "readingTo": "2038-06-15"})
    assert first.status_code == 201 and first.get_json()["reading"]["readingFrom"] == "2038-05-16"
    assert first.get_json()["reading"]["readingTo"] == "2038-06-15"
    view = sa.get("/api/water?month=2038-07").get_json()
    todo = next(x for x in view["missing"] if x["unitId"] == uid)
    assert todo["previous"] == 110 and todo["previousMonth"] == "2038-06"                    # carried from last month
    assert sa.get(f"/api/water/previous?unit={uid}&month=2038-07").get_json()["previous"] == 110
    with m.app.app_context():                  # July uses June's reading
        m.db.session.add(m.Billing(unit_id=uid, billing_month="2038-07", assessment=Decimal("500"), parking_dues=0, storage_dues=0, water=500,
                                   water_reading_month="2038-06",
                                   other=0, penalty=0, adjustment=0, previous_balance=0, amount_paid=0, due_date=date(2038, 7, 8), status="Unpaid"))
        m.db.session.commit()
    r = sa.post("/api/water", json={"unitId": uid, "month": "2038-07", "current": "125",
                                    "readingFrom": "2038-06-16", "readingTo": "2038-07-15"})  # previous filled in (110), default rate
    assert r.status_code == 201 and r.get_json()["billUpdated"] is False      # July reading belongs to August
    reading = r.get_json()["reading"]
    assert reading["previous"] == 110 and reading["usage"] == 15 and reading["amount"] == "750.00"
    assert reading["rate"] == "50" and view["defaultRate"] == "50"         # plain numbers, never "5E+1"
    with m.app.app_context():
        m.db.session.add(m.Billing(unit_id=uid, billing_month="2038-08", assessment=Decimal("500"), parking_dues=0, storage_dues=0, water=750,
                                   water_reading_month="2038-07", other=0, penalty=0, adjustment=0, previous_balance=0,
                                   amount_paid=0, due_date=date(2038, 8, 8), status="Unpaid"))
        m.db.session.commit()
    fixed = sa.put(f"/api/water/{reading['id']}", json={"previous": "110", "current": "120", "rate": "50",
                                                       "readingFrom": "2038-06-16", "readingTo": "2038-07-14"}).get_json()
    assert fixed["created"] is False and fixed["reading"]["amount"] == "500.00" and fixed["billUpdated"] is True
    assert fixed["reading"]["readingTo"] == "2038-07-14"
    with m.app.app_context():
        july_bill = m.Billing.query.filter_by(unit_id=uid, billing_month="2038-07").first()
        august_bill = m.Billing.query.filter_by(unit_id=uid, billing_month="2038-08").first()
        assert str(july_bill.water) in ("500.00", "500")
        assert str(august_bill.water) in ("500.00", "500")
    assert "current" in sa.post("/api/water", json={"unitId": uid, "month": "2038-07", "previous": "130", "current": "120"}).get_json()["error"]["fields"]
    invalid_dates = sa.post("/api/water", json={"unitId": uid, "month": "2038-08", "previous": "0", "current": "1",
                                                "readingFrom": "2038-08-16", "readingTo": "2038-08-15"})
    assert "readingFrom" in invalid_dates.get_json()["error"]["fields"]


def test_shared_commercial_reading_allocates_and_syncs_soa(app_module, sa):
    m = app_module
    reading_month, billing_month = "2038-10", "2038-11"
    unit_ids = []
    try:
        with m.app.app_context():
            assert not m.Billing.query.filter_by(billing_month=billing_month).first()
            for suffix, area in (("A", 10), ("B", 20)):
                unit = m.Unit(unit_no=f"TEST-COM-{suffix}", floor="LG", unit_type="COMMERCIAL", area_sqm=area, active=True)
                m.db.session.add(unit)
                m.db.session.flush()
                unit_ids.append(unit.id)
            m.db.session.add(m.CommercialWaterReading(
                commercial_type="LG", reading_month="2038-09", previous_reading=90,
                current_reading=100, rate=50,
            ))
            m.db.session.commit()
            shares, _ = m.commercial_water_shares("LG", Decimal("0.01"))
            assert [shares[uid] for uid in unit_ids] == [Decimal("0.00"), Decimal("0.01")]
            m.generate_bills(billing_month)
            stale_bills = [m.Billing.query.filter_by(unit_id=uid, billing_month=billing_month).first() for uid in unit_ids]
            assert [bill.water_reading_month for bill in stale_bills] == ["2038-09", "2038-09"]

        individual = sa.post("/api/water", json={"unitId": unit_ids[0], "month": reading_month, "previous": "0", "current": "1"})
        assert individual.status_code == 400 and "unitId" in individual.get_json()["error"]["fields"]
        saved = sa.post("/api/water/commercial", json={
            "commercialType": "LG", "month": reading_month, "previous": "100", "current": "110", "rate": "50",
            "readingFrom": "2038-09-16", "readingTo": "2038-10-15",
        })
        assert saved.status_code == 201, saved.get_json()
        assert saved.get_json()["reading"]["amount"] == "500.00"
        assert saved.get_json()["reading"]["readingFrom"] == "2038-09-16"
        assert saved.get_json()["reading"]["readingTo"] == "2038-10-15"
        assert saved.get_json()["billUpdated"] is True
        month_view = sa.get(f"/api/water?month={reading_month}").get_json()
        assert month_view["commercialMissing"] == []
        assert not any(row["unitId"] in unit_ids for row in month_view["missing"])

        with m.app.app_context():
            bills = [m.Billing.query.filter_by(unit_id=uid, billing_month=billing_month).first() for uid in unit_ids]
            assert [m.money(bill.water) for bill in bills] == [Decimal("166.67"), Decimal("333.33")]
            assert [bill.water_reading_month for bill in bills] == [reading_month, reading_month]
            reading_id = saved.get_json()["reading"]["id"]

        corrected = sa.put(f"/api/water/commercial/{reading_id}", json={
            "previous": "100", "current": "120", "rate": "50",
            "readingFrom": "2038-09-17", "readingTo": "2038-10-16",
        })
        assert corrected.status_code == 200, corrected.get_json()
        assert corrected.get_json()["created"] is False and corrected.get_json()["billUpdated"] is True

        with m.app.app_context():
            bills = [m.Billing.query.filter_by(unit_id=uid, billing_month=billing_month).first() for uid in unit_ids]
            assert [m.money(bill.water) for bill in bills] == [Decimal("333.33"), Decimal("666.67")]
            first_bill_id = bills[0].id
        statement = sa.get(f"/api/billing/{first_bill_id}").get_json()["bill"]
        assert statement["charges"]["water"] == "333.33"
        assert statement["charges"]["waterReadingFrom"] == "2038-09-17"
        assert statement["charges"]["waterReadingTo"] == "2038-10-16"
        assert statement["charges"]["waterAllocation"] == {
            "commercialType": "LG", "unitArea": "10.00", "totalArea": "30.00",
            "sharedAmount": "1000.00", "unitAmount": "333.33",
        }
    finally:
        with m.app.app_context():
            m.Billing.query.filter_by(billing_month=billing_month).delete(synchronize_session=False)
            m.CommercialWaterReading.query.filter(
                m.CommercialWaterReading.commercial_type == "LG",
                m.CommercialWaterReading.reading_month.in_(("2038-09", reading_month)),
            ).delete(synchronize_session=False)
            if unit_ids:
                m.Unit.query.filter(m.Unit.id.in_(unit_ids)).delete(synchronize_session=False)
            m.db.session.commit()


def test_water_payment_capped_at_balance_and_closed_period(app_module, sa, cleanup):
    months, _ = cleanup
    m = app_module
    uid = unit_id(m, "TEST-501")
    months.append("2038-09")
    rid = sa.post("/api/water", json={"unitId": uid, "month": "2038-09", "previous": "0", "current": "4", "rate": "50"}).get_json()["reading"]["id"]
    with m.app.app_context():
        reading = m.db.session.get(m.WaterReading, rid)
        bill = m.Billing(
            unit_id=uid, billing_month="2038-09", assessment=0, parking_dues=0, storage_dues=0, water=200,
            water_reading_month="2038-09", other=0, penalty=0, adjustment=0, previous_balance=0,
            amount_paid=0, due_date=date(2038, 9, 8), status="Unpaid",
            condo_charge_payer="Owner", water_charge_payer=reading.charge_payer,
        )
        m.db.session.add(bill); m.db.session.commit()
        bill_id = bill.id
        before_payment = m.party_balance_as_of(uid, "Owner", date(2038, 9, 9))
    paid = sa.post(f"/api/water/{rid}/payments", json={"amount": "500", "method": "CASH", "type": "FULL", "date": "2038-09-10", "payerType": "Owner", "formToken": token()})
    assert paid.status_code == 201 and paid.get_json()["applied"] == "200.00" and paid.get_json()["reading"]["status"] == "Paid"
    with m.app.app_context():
        assert m.party_month_payments(uid, "Owner", "2038-09") == Decimal("200.00")
        assert m.party_balance_as_of(uid, "Owner", date(2038, 9, 10)) == before_payment - Decimal("200.00")
    statement = sa.get(f"/api/billing/{bill_id}?payer=Owner").get_json()["bill"]
    assert statement["charges"]["water"] == "200.00" and statement["charges"]["waterPaidSeparately"] is False
    assert any(payment["type"] == "WATER" and payment["receiptNo"] == paid.get_json()["receiptNo"] for payment in statement["payments"])
    again = sa.post(f"/api/water/{rid}/payments", json={"amount": "1", "method": "CASH", "date": "2038-09-11", "payerType": "Owner", "formToken": token()})
    assert again.status_code == 400 and "no remaining balance" in again.get_json()["error"]["message"]
    with m.app.app_context():
        row = m.Setting.query.filter_by(key="books_closed_through").first() or m.Setting(key="books_closed_through")
        row.value = "2038-09"; m.db.session.add(row)
        m.db.session.commit()
    try:
        r = sa.put(f"/api/water/{rid}", json={"previous": "0", "current": "9", "rate": "50"})
        assert r.status_code == 400 and "closed" in r.get_json()["error"]["message"]
    finally:
        with m.app.app_context():
            m.Setting.query.filter_by(key="books_closed_through").first().value = ""; m.db.session.commit()


def test_csrf_and_classic_pages(app_module, sa):
    no_csrf = api(login(app_module, SA_USERNAME, SA_PASSWORD)[0])
    no_csrf.environ_base.pop("HTTP_X_CSRFTOKEN")
    assert no_csrf.post("/api/water", json={}).status_code == 403
    assert no_csrf.post("/api/advances", json={}).status_code == 403
    classic, _ = login(app_module, SA_USERNAME, SA_PASSWORD)
    assert classic.get("/water?month=2026-09").headers["Location"] == "/app/superadmin/water-readings?month=2026-09"
    assert classic.get("/billing/advance").headers["Location"] == "/app/superadmin/advances"
    with app_module.app.app_context():
        before = app_module.WaterReading.query.count()
    resp = classic.post("/water", data={"unit_id": unit_id(app_module, "TEST-501"), "month": "2039-01", "current_reading": "5"})
    assert resp.status_code == 303 and "moved=form" in resp.headers["Location"]
    with app_module.app.app_context():
        assert app_module.WaterReading.query.count() == before
