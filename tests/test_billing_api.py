"""Billing & SOA API (/api/billing): permissions, list/preview/generate, read-only SOA, Edit SOA,
email, CSRF and the retired classic Billing screens. Payments, receipts, recalculation and closed
periods are covered by test_payment_integrity, test_phase_b1_billing and test_phase_b2_receipts."""
import json
import secrets
from io import BytesIO
from datetime import date, datetime
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
        b.issued_at = datetime.utcnow()
        m.db.session.add(b); m.db.session.commit()
        bid = b.id
    yield bid
    with m.app.app_context():
        b = m.db.session.get(m.Billing, bid)
        if b:
            for p in list(b.payments):
                for a in m.ReceiptAllocation.query.filter_by(payment_id=p.id).all():
                    m.db.session.delete(a.receipt)
            m.db.session.delete(b); m.db.session.commit()


def test_permissions(app_module, bill):
    for role, can_read in (("admin", True), ("accounting", True), ("manager", False), ("staff", False), ("resident", False)):
        client = api(login(app_module, f"test_{role}", PASSWORD)[0])
        assert client.get(f"{BASE}?month=2036-06").status_code == (200 if can_read else 403), role
        assert client.get(f"{BASE}/{bill}").status_code == (200 if can_read else 403), role
        assert client.get(f"{BASE}/print-statements?month=2036-06").status_code == (200 if can_read else 403)
        assert client.get(f"{BASE}/month.pdf?month=2036-06").status_code == (200 if can_read else 403)
        if not can_read:
            assert client.post(f"{BASE}/{bill}/payments", json={"amount": "1", "formToken": secrets.token_urlsafe(24)}).status_code == 403
            assert client.put(f"{BASE}/{bill}/soa", json={}).status_code == 403
            assert client.post(f"{BASE}/generate", json={"month": "2036-07"}).status_code == 403
        assert client.post(f"{BASE}/delete-generated", json={"month": "2036-06", "billIds": [bill]}).status_code == 403
    assert app_module.app.test_client().get(BASE).status_code == 401


def test_list_and_detail(app_module, sa, bill):
    rows = sa.get(f"{BASE}?month=2036-06").get_json()["bills"]
    row = next(r for r in rows if r["id"] == bill)
    # (the total also carries TEST-504's earlier unpaid months, as on every SOA)
    assert row["unitNo"] == "TEST-504" and row["payerName"] == "Ana Garcia" and row["payerRequired"] is True and Decimal(row["total"]) >= Decimal("2000")
    d = sa.get(f"{BASE}/{bill}").get_json()["bill"]
    assert d["charges"]["condoDues"] == "2000.00" and d["stored"]["condoDues"] == "2000.00" and d["qrUrl"] == f"/billing/{bill}/qr"
    assert d["payerRequired"] is True
    assert d["contact"]["name"] == "Ana Garcia" and isinstance(d["emailRecipients"], list)
    assert sa.get(f"{BASE}?month=2036-13").status_code == 400 and sa.get(f"{BASE}/999999").status_code == 404


def test_party_balance_validates_payer_and_date(app_module, sa, bill):
    assert sa.get(f"{BASE}/{bill}/party-balance?payerType=Resident&date=2036-06-10").status_code == 400
    assert sa.get(f"{BASE}/{bill}/party-balance?payerType=Owner&date=not-a-date").status_code == 400


def test_superadmin_can_delete_an_unpaid_unallocated_generated_soa(app_module, sa, bill):
    response = sa.post(f"{BASE}/delete-generated", json={"month": "2036-06", "billIds": [bill]})
    assert response.status_code == 200
    assert response.get_json() == {"month": "2036-06", "deleted": 1, "billIds": [bill]}
    assert sa.get(f"{BASE}/{bill}").status_code == 404
    with app_module.app.app_context():
        audit = app_module.AuditLog.query.filter_by(entity_type="billing_batch").order_by(app_module.AuditLog.id.desc()).first()
        assert audit and "Deleted 1 generated SOA" in audit.action


def test_superadmin_can_delete_all_unpaid_unallocated_soas_for_a_month(app_module, sa, bill):
    response = sa.post(f"{BASE}/delete-generated", json={"month": "2036-06"})
    assert response.status_code == 200
    assert response.get_json()["deleted"] == 1
    assert bill in response.get_json()["billIds"]
    assert sa.get(f"{BASE}/{bill}").status_code == 404


def test_generated_soa_deletion_is_refused_when_a_payment_exists(app_module, sa, bill):
    m = app_module
    with m.app.app_context():
        payment = m.Payment(billing_id=bill, amount=Decimal("25.00"), payment_date=date(2036, 6, 2),
                            payment_method="CASH", payment_type="PARTIAL", reference="KEEP-TEST")
        m.db.session.add(payment)
        m.db.session.commit()
    response = sa.post(f"{BASE}/delete-generated", json={"month": "2036-06", "billIds": [bill]})
    assert response.status_code == 409
    assert "Nothing was deleted" in response.get_json()["error"]["message"]
    assert response.get_json()["error"]["blocked"][0]["reasons"] == ["has payment records"]
    assert sa.get(f"{BASE}/{bill}").status_code == 200


def test_setting_reads_are_cached_per_request(app_module):
    from sqlalchemy import event

    statements = []
    with app_module.app.app_context():
        engine = app_module.db.engine
    with app_module.app.test_request_context():
        def capture_statement(_conn, _cursor, statement, *_):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", capture_statement)
        try:
            app_module.prime_setting_cache()
            for _ in range(4):
                app_module.setting("vat_rate", "0")
                app_module.setting("corporation_name", "CITYLAND")
        finally:
            event.remove(engine, "before_cursor_execute", capture_statement)
    setting_reads = [sql for sql in statements if "from setting" in " ".join(sql.lower().split())]
    assert len(setting_reads) == 1, statements


def test_print_all_returns_combined_month_pdf(app_module, sa, bill):
    from pypdf import PdfReader

    response = sa.get(f"{BASE}/month.pdf?month=2036-06")
    assert response.status_code == 200 and response.mimetype == "application/pdf"
    assert "attachment; filename=\"SOA_2036-06_all.pdf\"" in response.headers["Content-Disposition"]
    reader = PdfReader(BytesIO(response.data))
    assert len(reader.pages) >= 1
    assert any("203606-" in (page.extract_text() or "") for page in reader.pages)
    assert sa.get(f"{BASE}/month.pdf?month=2036-07").status_code == 404


def test_print_statements_returns_month_details_in_one_response(app_module, sa, bill):
    response = sa.get(f"{BASE}/print-statements?month=2036-06")
    assert response.status_code == 200
    statements = response.get_json()["statements"]
    statement = next(row for row in statements if row["id"] == bill and row.get("payerType") == "Owner")
    assert statement["unitNo"] == "TEST-504"
    assert statement["statementNo"].startswith("203606-") and statement["statementNo"].endswith("-O")
    assert statement["charges"]["condoDues"] == "2000.00"
    assert isinstance(statement["payments"], list)
    assert any(row["id"] == bill and row.get("payerType") == "Tenant" for row in statements)
    owner_only = sa.get(f"{BASE}/print-statements?month=2036-06&payer=Owner")
    tenant_only = sa.get(f"{BASE}/print-statements?month=2036-06&payer=Tenant")
    assert owner_only.status_code == tenant_only.status_code == 200
    assert {row["payerType"] for row in owner_only.get_json()["statements"]} == {"Owner"}
    assert {row["payerType"] for row in tenant_only.get_json()["statements"]} == {"Tenant"}
    assert sa.get(f"{BASE}/print-statements?month=2036-06&payer=Resident").status_code == 400
    owner_pdf = sa.get(f"{BASE}/{bill}/soa.pdf?payer=Owner")
    tenant_pdf = sa.get(f"{BASE}/{bill}/soa.pdf?payer=Tenant")
    assert owner_pdf.status_code == tenant_pdf.status_code == 200
    assert owner_pdf.headers["Content-Disposition"].endswith("_Owner.pdf\"")
    assert tenant_pdf.headers["Content-Disposition"].endswith("_Tenant.pdf\"")
    assert sa.get(f"{BASE}/print-statements?month=2036-13").status_code == 400
    assert sa.get(f"{BASE}/print-statements?month=2036-07").get_json() == {"statements": []}


def test_print_statements_includes_zero_balance_owner_and_tenant_soas(app_module, sa):
    m = app_module
    with m.app.app_context():
        unit = m.Unit(unit_no="SOA-ZERO-2036", unit_type="STUDIO TYPE", area_sqm=0, active=True)
        m.db.session.add(unit)
        m.db.session.flush()
        bill = m.Billing(
            unit_id=unit.id, billing_month="2036-06", assessment=0, parking_dues=0, storage_dues=0, water=0,
            other=0, penalty=0, adjustment=0, previous_balance=0, amount_paid=0,
            due_date=date(2036, 6, 8), status="Paid",
        )
        bill.issued_at = datetime.utcnow()
        m.db.session.add(bill)
        m.db.session.commit()
        bill_id, unit_id = bill.id, unit.id
    try:
        for payer in ("Owner", "Tenant"):
            response = sa.get(f"{BASE}/print-statements?month=2036-06&payer={payer}")
            assert response.status_code == 200, response.get_json()
            statement = next(item for item in response.get_json()["statements"] if item["id"] == bill_id)
            assert statement["payerType"] == payer
            assert Decimal(statement["balance"]) == Decimal("0.00")
    finally:
        with m.app.app_context():
            m.db.session.delete(m.db.session.get(m.Billing, bill_id))
            m.db.session.delete(m.db.session.get(m.Unit, unit_id))
            m.db.session.commit()


def test_opening_balance_is_created_once_at_cutover(app_module):
    m = app_module
    with m.app.app_context():
        unit = m.Unit.query.filter_by(unit_no="TEST-504").first()
        cutover = m.party_ledger_cutover_month()
        m.ensure_party_opening_balance(unit.id, "2036-06")
        m.db.session.commit()
        m.ensure_party_opening_balance(unit.id, "2036-07")
        m.db.session.commit()
        rows = m.PartyOpeningBalance.query.filter_by(unit_id=unit.id, payer_type="Tenant").all()
        assert len(rows) == 1 and rows[0].as_of_month == m.month_shift(cutover, -1)


def test_unknown_post_cutover_payment_blocks_party_soa_until_audited_assignment(app_module, sa, bill):
    m = app_module
    with m.app.app_context():
        row = m.Payment(billing_id=bill, amount=Decimal("25.00"), payment_date=date(2036, 6, 2), payment_method="CASH",
                        payment_type="PARTIAL", reference="UNASSIGNED-TEST", payer_type=None)
        m.db.session.add(row)
        m.db.session.commit()
        payment_id = row.id
    try:
        assert sa.get(f"{BASE}/{bill}?payer=Owner").status_code == 409
        listing = sa.get(f"{BASE}/{bill}/party-transactions").get_json()
        payment = next(item for item in listing["transactions"] if item["kind"] == "payment" and item["id"] == payment_id)
        assert listing["ready"] is False and payment["payerType"] is None
        assigned = sa.put(f"{BASE}/{bill}/party-transactions/payment/{payment_id}", json={"payerType": "Owner"})
        assert assigned.status_code == 200 and assigned.get_json()["ready"] is True
        assert sa.get(f"{BASE}/{bill}?payer=Owner").status_code == 200
        with m.app.app_context():
            assert m.db.session.get(m.Payment, payment_id).payer_type == "Owner"
            log = m.AuditLog.query.filter_by(entity_type="payment", entity_id=payment_id).order_by(m.AuditLog.id.desc()).first()
            assert log and "Owner" in log.details
    finally:
        with m.app.app_context():
            row = m.db.session.get(m.Payment, payment_id)
            if row:
                m.db.session.delete(row)
                m.db.session.commit()


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
    assert p["assetsToCreate"] == p["assetUnits"] - p["assetsAlreadyBilled"] - len(p["assetsDeferred"])
    r = sa.post(f"{BASE}/generate", json={"month": "2036-09"}).get_json()
    assert r["created"] == p["toCreate"] + p["assetsToCreate"] and r["skipped"] == p["alreadyBilled"] + p["assetsAlreadyBilled"]
    again = sa.post(f"{BASE}/generate", json={"month": "2036-09"}).get_json()
    assert again["created"] == 0 and again["skipped"] == p["residentialUnits"] + p["assetUnits"] - len(p["assetsDeferred"])
    assert sa.post(f"{BASE}/generate", json={"month": "Sept"}).status_code == 400
    m = app_module
    with m.app.app_context():
        m.Billing.query.filter_by(billing_month="2036-09").delete(); m.db.session.commit()


def test_asset_soas_are_separate_and_do_not_double_charge(app_module, sa):
    m = app_module
    month = "2040-02"
    with m.app.app_context():
        home = m.Unit.query.filter_by(unit_no="TEST-504").first()
        home_id = home.id
        old_parking, old_storage, old_include = home.assigned_parking_unit_id, home.assigned_storage_unit_id, home.include_storage
        cutoff = m.Setting.query.filter_by(key="storage_in_total_from").first()
        old_cutoff = cutoff.value if cutoff else None
        if cutoff is None:
            cutoff = m.Setting(key="storage_in_total_from", value="2040-01")
            m.db.session.add(cutoff)
        else:
            cutoff.value = "2040-01"
        parking = m.Unit(unit_no="TEST-ASSET-PARK", unit_type="PARKING", area_sqm=10, unit_rate_per_sqm=20, active=True)
        storage = m.Unit(unit_no="TEST-ASSET-STORE", unit_type="STORAGE", area_sqm=5, dues_mode="manual",
                         manual_monthly_dues=150, active=True)
        m.db.session.add_all([parking, storage]); m.db.session.flush()
        home.assigned_parking_unit_id, home.assigned_storage_unit_id, home.include_storage = parking.id, storage.id, True
        parking_id, storage_id = parking.id, storage.id
        m.db.session.commit()
    try:
        result = sa.post(f"{BASE}/generate", json={"month": month})
        assert result.status_code == 200
        with m.app.app_context():
            home_bill = m.Billing.query.filter_by(unit_id=home_id, billing_month=month).one()
            parking_bill = m.Billing.query.filter_by(unit_id=parking_id, billing_month=month).one()
            storage_bill = m.Billing.query.filter_by(unit_id=storage_id, billing_month=month).one()
            assert home_bill.parking_dues == 0 and home_bill.storage_dues == 0
            assert parking_bill.parking_dues == 200 and parking_bill.storage_dues == 0
            assert storage_bill.storage_dues == 150 and storage_bill.parking_dues == 0
            parking_bill_id, storage_bill_id = parking_bill.id, storage_bill.id
        parking_detail = sa.get(f"{BASE}/{parking_bill_id}").get_json()["bill"]
        storage_detail = sa.get(f"{BASE}/{storage_bill_id}").get_json()["bill"]
        assert parking_detail["payerName"] == "Ana Garcia"
        assert parking_detail["charges"]["parking"] == "200.00"
        assert parking_detail["bases"]["parking"]["basis"] == "sqm"
        assert storage_detail["payerName"] == "Ana Garcia"
        assert storage_detail["charges"]["storage"] == "150.00"
        assert storage_detail["bases"]["storage"]["basis"] == "manual"

        legacy_month = "2040-03"
        with m.app.app_context():
            legacy_bill = m.Billing(
                unit_id=home_id, billing_month=legacy_month, assessment=2000, parking_dues=200,
                storage_dues=150, water=0, other=0, penalty=0, adjustment=0,
                previous_balance=0, amount_paid=0, due_date=date(2040, 3, 8), status="Unpaid",
                storage_included=True,
            )
            m.db.session.add(legacy_bill); m.db.session.commit()
        preview = sa.get(f"{BASE}/preview?month={legacy_month}").get_json()
        assert {"TEST-ASSET-PARK", "TEST-ASSET-STORE"}.issubset(set(preview["assetsDeferred"]))
        generated = sa.post(f"{BASE}/generate", json={"month": legacy_month}).get_json()
        assert {"TEST-ASSET-PARK", "TEST-ASSET-STORE"}.issubset(set(generated["deferredAssets"]))
        with m.app.app_context():
            assert m.Billing.query.filter_by(unit_id=parking_id, billing_month=legacy_month).first() is None
            assert m.Billing.query.filter_by(unit_id=storage_id, billing_month=legacy_month).first() is None
            existing_home_bill = m.Billing.query.filter_by(unit_id=home_id, billing_month=legacy_month).one()
            assert existing_home_bill.parking_dues == 200 and existing_home_bill.storage_dues == 150
    finally:
        with m.app.app_context():
            home = m.db.session.get(m.Unit, home_id)
            home.assigned_parking_unit_id, home.assigned_storage_unit_id, home.include_storage = old_parking, old_storage, old_include
            m.Billing.query.filter(m.Billing.billing_month.in_((month, "2040-03"))).delete()
            m.Unit.query.filter(m.Unit.unit_no.in_(("TEST-ASSET-PARK", "TEST-ASSET-STORE"))).delete(synchronize_session=False)
            setting = m.Setting.query.filter_by(key="storage_in_total_from").first()
            if old_cutoff is None:
                m.db.session.delete(setting)
            else:
                setting.value = old_cutoff
            m.db.session.commit()


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
            "otherItems": [
                {"description": "Key replacement", "amount": "60", "payerType": "Owner"},
                {"description": "Move-in fee", "amount": "40", "payerType": "Tenant"},
            ],
            "penalty": "0", "previousBalance": "0", "dueDate": "2036-06-15"}
    errs = sa.put(f"{BASE}/{bill}/soa", json={**body, "water": "abc"}).get_json()["error"]["fields"]
    assert {"water", "note"} <= set(errs)
    d = sa.put(f"{BASE}/{bill}/soa", json={**body, "note": "Water meter misread; corrected per inspection"}).get_json()["bill"]
    # 1800 + 150.50 + other 100 - adjustment 50 (other charges and the adjustment count since 2026-10-04)
    assert d["manualOverride"] is True and d["total"] == "2000.50" and d["dueDate"] == "2036-06-15"
    assert d["charges"]["other"] == "100.00" and d["charges"]["adjustment"] == "-50.00" and d["stored"]["adjustment"] == "-50.00"
    assert d["charges"]["otherItems"] == [
        {"description": "Key replacement", "amount": "60.00", "payerType": "Owner"},
        {"description": "Move-in fee", "amount": "40.00", "payerType": "Tenant"},
    ]
    assert d["note"].startswith("Water meter misread")
    m = app_module
    with m.app.app_context():
        log = m.AuditLog.query.filter_by(entity_type="billing", entity_id=bill).order_by(m.AuditLog.id.desc()).first()
        assert log.reason.startswith("Water meter misread") and "assessment" in json.loads(log.details)
    # hand-corrected: can't be re-priced from rates
    assert sa.post(f"{BASE}/{bill}/recalculate").status_code == 409


def test_payment_result_and_detail(app_module, sa, bill):
    r = sa.post(f"{BASE}/{bill}/payments", json={"amount": "2500", "method": "CHECK", "reference": "", "date": "2036-06-02", "payerType": "Tenant",
                                                 "formToken": secrets.token_urlsafe(24)})
    assert r.status_code == 400 and "reference" in r.get_json()["error"]["fields"]            # check needs a reference
    balance = Decimal(sa.get(f"{BASE}/{bill}?payer=Tenant").get_json()["bill"]["balance"])
    r = sa.post(f"{BASE}/{bill}/payments", json={"amount": str(balance + 500), "method": "CHECK", "reference": "CHK-77", "date": "2036-06-02", "payerType": "Tenant",
                                                 "formToken": secrets.token_urlsafe(24)}).get_json()
    assert r["receiptNo"].startswith("OR-2036-") and Decimal(r["appliedToBill"]) == balance and r["excessToAdvance"] == "500.00"
    assert r["status"] == "Partially Paid" and r["bill"]["payments"][0]["receiptNo"] == r["receiptNo"]
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
