"""Rates & Rules API (/api/admin/rates): Superadmin only, validation, persistence, write-only SMTP
password, audit, CSRF, and issued bills untouched by a rate change."""
import pytest

from conftest import PASSWORD, SA_PASSWORD, SA_USERNAME

BASE = "/api/admin/rates"


def api_client(app_module, username, password):
    client = app_module.app.test_client()
    token = client.get("/api/auth/csrf").get_json()["csrfToken"]
    resp = client.post("/api/auth/login", json={"username": username, "password": password}, headers={"X-CSRFToken": token})
    assert resp.status_code == 200, resp.get_json()
    client.environ_base["HTTP_X_CSRFTOKEN"] = client.get("/api/auth/csrf").get_json()["csrfToken"]
    return client


@pytest.fixture
def sa(app_module):
    return api_client(app_module, SA_USERNAME, SA_PASSWORD)


@pytest.fixture(autouse=True)
def restore_settings(app_module):
    """Every test leaves the settings exactly as it found them."""
    m = app_module
    with m.app.app_context():
        saved = {s.key: s.value for s in m.Setting.query.all()}
    yield
    with m.app.app_context():
        for s in m.Setting.query.all():
            if s.key not in saved:
                m.db.session.delete(s)
            else:
                s.value = saved[s.key]
        m.db.session.commit()


def test_only_superadmin(app_module):
    for role in ("admin", "manager", "staff", "accounting", "resident"):
        client = api_client(app_module, f"test_{role}", PASSWORD)
        assert client.get(BASE).status_code == 403, role
        assert client.put(BASE, json={"waterRate": "1"}).status_code == 403, role
    assert app_module.app.test_client().get(BASE).status_code == 401


def test_get_never_returns_the_smtp_password(app_module, sa):
    with app_module.app.app_context():
        app_module.set_smtp_password("S3cret-Mail-Pass")
        app_module.db.session.commit()
    rates = sa.get(BASE).get_json()["rates"]
    assert rates["smtpPasswordSet"] is True and "smtpPassword" not in rates
    assert "S3cret-Mail-Pass" not in str(rates)
    assert rates["dueDay"] == 8 and set(rates["ratesPerSqm"]) == {"residential", "commercial", "dryingArea"}


def test_validation_saves_nothing(app_module, sa):
    before = sa.get(BASE).get_json()["rates"]
    resp = sa.put(BASE, json={"waterRate": "abc", "penaltyRate": "150", "ratesPerSqm": {"residential": "-1"},
                              "smtpPort": "99999", "smtpSender": "nope", "onlinePaymentUrl": "ftp://x",
                              "storageInTotalFrom": "2026-13", "booksClosedThrough": "soon",
                              "onlinePaymentInstructions": "x" * 300, "corporationName": " ",
                              "penaltyIncludes": {"water": "yes"}, "vatRate": "101",
                              "vatIncludes": {"condo": "yes"}, "address": "New address"})
    assert resp.status_code == 400
    fields = resp.get_json()["error"]["fields"]
    assert {"waterRate", "penaltyRate", "ratesPerSqm.residential", "smtpPort", "smtpSender", "onlinePaymentUrl",
            "storageInTotalFrom", "booksClosedThrough", "onlinePaymentInstructions", "corporationName",
            "penaltyIncludes.water", "vatRate", "vatIncludes.condo"} <= set(fields)
    assert sa.get(BASE).get_json()["rates"] == before          # "address" was valid but nothing was saved


def test_save_persists_and_is_audited(app_module, sa):
    resp = sa.put(BASE, json={"waterRate": "55.25", "ratesPerSqm": {"residential": "60", "commercial": "140.50", "dryingArea": "22.75"}, "penaltyIncludes": {"parking": True},
                              "vatRate": "12", "vatIncludes": {"condo": True, "storage": True},
                              "booksClosedThrough": "2026-08", "smtpPort": "465", "onlinePaymentUrl": "https://pay.example/cl9"})
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["rates"]["waterRate"] == "55.25" and body["rates"]["ratesPerSqm"]["residential"] == "60"
    assert body["rates"]["ratesPerSqm"]["commercial"] == "140.5"
    assert body["rates"]["ratesPerSqm"]["dryingArea"] == "22.75"
    assert body["rates"]["penaltyIncludes"]["parking"] is True and body["rates"]["booksClosedThrough"] == "2026-08"
    assert body["rates"]["vatRate"] == "12" and body["rates"]["vatIncludes"]["condo"] is True
    assert body["rates"]["vatIncludes"]["storage"] is True and body["rates"]["vatIncludes"]["water"] is False
    with app_module.app.app_context():
        assert app_module.setting("water_rate") == "55.25"
        assert app_module.setting("studio_rate_per_sqm") == "60"
        assert app_module.rate_for_type("STUDIO TYPE") == 60
        assert app_module.rate_for_type("1 BEDROOM") == 60
        assert app_module.rate_for_type("2 BEDROOM") == 60
        assert app_module.rate_for_type("3 BEDROOM") == 60
        assert app_module.setting("commercial_rate_per_sqm") == "140.5"
        assert app_module.rate_for_type("COMMERCIAL") == 140.5
        assert app_module.setting("drying_area_rate_per_sqm") == "22.75"
        assert app_module.rate_for_type("DRYING AREA") == 22.75
        assert app_module.books_closed_through() == "2026-08"
        actions = [a.action for a in app_module.AuditLog.query.order_by(app_module.AuditLog.id.desc()).limit(2).all()]
    assert actions[0].startswith("Updated Rates & Rules") and "water_rate" in actions[0]
    assert actions[1] == "Books closed through changed: none -> 2026-08"
    # Saving the same values again changes nothing and writes no audit entry.
    with app_module.app.app_context():
        count = app_module.AuditLog.query.count()
    assert sa.put(BASE, json={"waterRate": "55.25"}).get_json()["changed"] == []
    with app_module.app.app_context():
        assert app_module.AuditLog.query.count() == count
    assert sa.put(BASE, json={"penaltyIncludes": {"parking": False}}).status_code == 200   # back to the default


def test_vat_is_added_to_soa_without_changing_the_stored_bill(app_module, sa):
    from decimal import Decimal
    from app.services.soa import soa_detail

    m = app_module
    with m.app.app_context():
        bill = m.Billing.query.filter(m.Billing.assessment > 0).first()
        assert bill is not None
        bill_id = bill.id
        calc = m._bill_calc(bill)
        before = (m.money(bill.assessment), m.money(bill.water), m.money(bill.amount_paid),
                  m.money(calc["total"]), m.money(calc["current"]))

    resp = sa.put(BASE, json={"vatRate": "12", "vatIncludes": {"condo": True}})
    assert resp.status_code == 200
    with m.app.app_context():
        bill = m.db.session.get(m.Billing, bill_id)
        statement = soa_detail(m, bill)
        vat = (Decimal(before[0]) * Decimal("0.12")).quantize(Decimal("0.01"))
        assert Decimal(statement["charges"]["vat"]) == vat
        assert Decimal(statement["total"]) == Decimal(before[3]) + vat
        assert Decimal(statement["balance"]) == Decimal(str(m._bill_calc(bill)["balance"])) + vat
        assert Decimal(statement["charges"]["currentCharges"]) == Decimal(before[4]) + Decimal(statement["charges"]["vat"])
        assert statement["charges"]["vatEligible"] == ["Condo dues"]
        assert (m.money(bill.assessment), m.money(bill.water), m.money(bill.amount_paid),
                m.money(m._bill_calc(bill)["total"]), m.money(m._bill_calc(bill)["current"])) == before


def test_water_can_never_be_penalty_eligible(app_module, sa):
    """Confirmed rule (2026-10-06): water and unrelated charges never increase the penalty base."""
    resp = sa.put(BASE, json={"penaltyIncludes": {"water": True}})
    assert resp.status_code == 400 and "penaltyIncludes.water" in resp.get_json()["error"]["fields"]
    with app_module.app.app_context():
        assert app_module.setting("penalty_include_water", "0") != "1"
        # Even a stored "1" (e.g. from before this rule) is ignored by the calculator.
        assert "water" not in app_module.current_penalty_rules()[1]


def test_smtp_password_replace_and_remove(app_module, sa):
    assert sa.put(BASE, json={"smtpPassword": "New-Mail-Pass-1"}).status_code == 200
    with app_module.app.app_context():
        assert app_module.get_smtp_password() == "New-Mail-Pass-1"
        stored = app_module.Setting.query.filter_by(key="smtp_password").first().value
        assert stored and "New-Mail-Pass-1" not in stored            # encrypted at rest
        log = app_module.AuditLog.query.order_by(app_module.AuditLog.id.desc()).first()
        assert "New-Mail-Pass-1" not in (log.details or "") and "replaced" in (log.details or "")
    assert sa.put(BASE, json={"smtpPassword": ""}).get_json()["rates"]["smtpPasswordSet"] is True   # empty keeps it
    assert sa.put(BASE, json={"smtpPasswordClear": True}).get_json()["rates"]["smtpPasswordSet"] is False


def test_rate_change_does_not_touch_issued_bills(app_module, sa):
    m = app_module
    cols = ("assessment", "parking_dues", "storage_dues", "water", "other", "penalty", "adjustment",
            "previous_balance", "amount_paid", "status", "due_date")
    with m.app.app_context():
        before = {b.id: tuple(getattr(b, c) for c in cols) for b in m.Billing.query.all()}
        payments = m.Payment.query.count()
    assert before, "the seed data should contain issued bills"
    assert sa.put(BASE, json={"ratesPerSqm": {"studio": "99", "oneBed": "99", "commercial": "99"}, "waterRate": "99", "penaltyRate": "20"}).status_code == 200
    with m.app.app_context():
        assert {b.id: tuple(getattr(b, c) for c in cols) for b in m.Billing.query.all()} == before
        assert m.Payment.query.count() == payments


def test_requires_csrf(sa):
    sa.environ_base.pop("HTTP_X_CSRFTOKEN")
    assert sa.put(BASE, json={"waterRate": "1"}).status_code in (400, 403)
