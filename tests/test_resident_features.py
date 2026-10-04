"""Resident Portal features: payment history & receipts, water usage, gate pass / move
requests (with staff approval on the legacy Gate Pass page) and profile / password.

Every unit-scoped route must refuse another unit's data (IDOR, bug D2).
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from werkzeug.security import generate_password_hash

from conftest import api, login

PW = "Owner-Pass-1"


@pytest.fixture
def owner(app_module):
    """A resident account linked to the Current owner of TEST-504, signed in on the API."""
    m = app_module
    with m.app.app_context():
        unit = m.Unit.query.filter_by(unit_no="TEST-504").first()
        person = m.Owner.query.filter_by(unit_id=unit.id, status="Current").first()
        saved = (person.contact_no, person.email)
        user = m.User(username="ana_owner", password_hash=generate_password_hash(PW), role="resident", active=True)
        m.db.session.add(user); m.db.session.flush()
        m.db.session.add(m.ResidentProfile(user_id=user.id, unit_id=unit.id, person_type="Owner",
                                           person_id=person.id, display_name="Ana Garcia"))
        other = m.Unit.query.filter_by(unit_no="TEST-503").first().id
        m.db.session.commit()
        ids = {"unit": unit.id, "other": other, "user": user.id, "owner": person.id}
    client = m.app.test_client()
    token = client.get("/api/auth/csrf").get_json()["csrfToken"]
    assert client.post("/api/auth/login", json={"username": "ana_owner", "password": PW},
                       headers={"X-CSRFToken": token}).status_code == 200
    client.environ_base["HTTP_X_CSRFTOKEN"] = client.get("/api/auth/csrf").get_json()["csrfToken"]
    yield client, ids
    with m.app.app_context():
        m.GatePass.query.filter_by(unit_id=ids["unit"]).delete()
        person = m.db.session.get(m.Owner, ids["owner"])
        person.contact_no, person.email = saved
        user = m.db.session.get(m.User, ids["user"])
        m.db.session.delete(user.resident_profile)
        m.db.session.delete(user)
        m.db.session.commit()


def test_other_units_are_refused_on_every_new_route(owner):
    client, ids = owner
    for path in ("receipts", "receipts/1", "water", "gate-passes", "profile"):
        assert client.get(f"/api/resident/units/{ids['other']}/{path}").status_code == 403, path
    assert client.post(f"/api/resident/units/{ids['other']}/gate-passes", json={}).status_code == 403
    assert client.put(f"/api/resident/units/{ids['other']}/profile", json={}).status_code == 403


def test_payment_history_lists_the_units_receipts(app_module, owner):
    client, ids = owner
    m = app_module
    with m.app.app_context():
        bill = m.Billing.query.filter_by(unit_id=ids["unit"]).first()
        pay = m.Payment(billing_id=bill.id, amount=Decimal("1500.00"), payment_date=date(2026, 9, 20), payment_method="GCASH")
        m.db.session.add(pay)
        receipt = m.issue_receipt(ids["unit"], date(2026, 9, 20), "GCASH", "REF-1", "", [("bill", pay, Decimal("1500.00"))],
                                  received_by="test")
        m.db.session.commit()
        rid, pid, month = receipt.id, pay.id, bill.billing_month
    try:
        data = client.get(f"/api/resident/units/{ids['unit']}/receipts").get_json()
        row = next(r for r in data["receipts"] if r["id"] == rid)
        assert row["amount"] == "1500.00" and row["method"] == "GCASH"
        assert row["items"][0]["kind"] == "bill" and row["items"][0]["month"] == month
        detail = client.get(f"/api/resident/units/{ids['unit']}/receipts/{rid}").get_json()
        assert detail["receipt"]["receiptNo"].startswith("OR-2026-")
        assert detail["unit"]["unitNo"] == "TEST-504"
    finally:
        with m.app.app_context():
            m.db.session.delete(m.db.session.get(m.Receipt, rid))
            m.db.session.delete(m.db.session.get(m.Payment, pid))
            m.db.session.commit()


def test_receipt_of_another_unit_is_not_found(app_module, owner):
    client, ids = owner
    m = app_module
    with m.app.app_context():
        bill = m.Billing.query.filter_by(unit_id=ids["other"]).first()
        pay = m.Payment(billing_id=bill.id, amount=Decimal("10.00"), payment_date=date(2026, 9, 1))
        m.db.session.add(pay)
        rid = m.issue_receipt(ids["other"], date(2026, 9, 1), "CASH", "", "", [("bill", pay, Decimal("10.00"))]).id
        m.db.session.commit()
        pid = pay.id
    try:
        # Own unit in the URL, another unit's receipt id: must not leak.
        assert client.get(f"/api/resident/units/{ids['unit']}/receipts/{rid}").status_code == 404
    finally:
        with m.app.app_context():
            m.db.session.delete(m.db.session.get(m.Receipt, rid))
            m.db.session.delete(m.db.session.get(m.Payment, pid))
            m.db.session.commit()


def test_water_usage(owner):
    client, ids = owner
    readings = client.get(f"/api/resident/units/{ids['unit']}/water").get_json()["readings"]
    sep = next(r for r in readings if r["month"] == "2026-09")
    assert sep["usage"] == 8.0 and sep["amount"] == "400.00"


def test_gate_pass_request_approval_flow(app_module, owner):
    client, ids = owner
    base = f"/api/resident/units/{ids['unit']}/gate-passes"
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    assert client.post(base, json={"type": "Party", "date": tomorrow, "name": "X", "purpose": "Y"}).status_code == 400
    assert client.post(base, json={"type": "Move-in", "date": "2000-01-01", "name": "X", "purpose": "Y"}).status_code == 400
    assert client.post(base, json={"type": "Move-in", "date": tomorrow, "name": "", "purpose": "Y"}).status_code == 400

    resp = client.post(base, json={"type": "Move-in", "date": tomorrow, "name": "ABC Movers", "purpose": "Sofa and boxes"})
    assert resp.status_code == 201
    first = resp.get_json()["pass"]
    assert first["status"] == "Requested"
    second = client.post(base, json={"type": "Visitor", "date": tomorrow, "name": "Lola", "purpose": "Visit"}).get_json()["pass"]

    # Staff see the request on the Gate Passes page (API) and approve / reject it.
    staff = api(login(app_module, "test_staff", "Test-Pass-123")[0])
    listed = {p["id"]: p for p in staff.get("/api/gate-passes").get_json()["passes"]}
    assert listed[first["id"]]["name"] == "ABC Movers" and listed[first["id"]]["source"] == "resident"
    assert staff.post(f"/api/gate-passes/{first['id']}/review", json={"decision": "approve"}).status_code == 200
    # Rejecting without a reason is refused; with a reason it goes through.
    assert staff.post(f"/api/gate-passes/{second['id']}/review", json={"decision": "reject"}).status_code == 400
    assert client.get(base).get_json()["passes"][0]["status"] == "Requested"
    assert staff.post(f"/api/gate-passes/{second['id']}/review", json={"decision": "reject", "note": "Guest list full"}).status_code == 200
    # Already handled: can't be reviewed again.
    assert staff.post(f"/api/gate-passes/{first['id']}/review", json={"decision": "reject", "note": "x"}).status_code == 409

    passes = {p["id"]: p for p in client.get(base).get_json()["passes"]}
    assert passes[first["id"]]["status"] == "Issued"
    assert passes[second["id"]]["status"] == "Rejected" and passes[second["id"]]["reviewNote"] == "Guest list full"
    # A handled request can no longer be cancelled.
    assert client.post(f"{base}/{first['id']}/cancel").status_code == 409


def test_resident_can_cancel_a_pending_request_but_not_review_it(app_module, owner):
    client, ids = owner
    base = f"/api/resident/units/{ids['unit']}/gate-passes"
    p = client.post(base, json={"type": "Delivery", "date": date.today().isoformat(), "name": "LBC", "purpose": "Parcel"}).get_json()["pass"]
    legacy_resident, _ = login(app_module, "ana_owner", PW)
    assert api(legacy_resident).post(f"/api/gate-passes/{p['id']}/review", json={"decision": "approve"}).status_code == 403
    assert client.get(base).get_json()["passes"][0]["status"] == "Requested"
    assert client.post(f"{base}/{p['id']}/cancel").get_json()["pass"]["status"] == "Cancelled"


def test_profile_update_changes_the_linked_owner_record(app_module, owner):
    client, ids = owner
    url = f"/api/resident/units/{ids['unit']}/profile"
    profile = client.get(url).get_json()["profile"]
    assert profile["canEdit"] and profile["name"] == "Ana Garcia"
    assert client.put(url, json={"contactNo": "0917 123 4567", "email": "not-an-email"}).status_code == 400
    assert client.put(url, json={"contactNo": "call me", "email": ""}).status_code == 400
    resp = client.put(url, json={"contactNo": "0917 123 4567", "email": "ana.new@example.com"})
    assert resp.status_code == 200
    with app_module.app.app_context():
        person = app_module.db.session.get(app_module.Owner, ids["owner"])
        assert (person.contact_no, person.email) == ("0917 123 4567", "ana.new@example.com")


def test_unlinked_resident_cannot_edit_contact(app_module):
    client, _ = login(app_module, "test_resident", "Test-Pass-123")
    client.environ_base["HTTP_X_CSRFTOKEN"] = client.get("/api/auth/csrf").get_json()["csrfToken"]
    unit_id = client.get("/api/auth/me").get_json()["user"]["unitId"]
    assert client.get(f"/api/resident/units/{unit_id}/profile").get_json()["profile"]["canEdit"] is False
    assert client.put(f"/api/resident/units/{unit_id}/profile", json={"contactNo": "1"}).status_code == 409


def test_change_password(app_module, owner):
    client, _ = owner
    url = "/api/auth/password"
    assert client.post(url, json={"currentPassword": "wrong", "newPassword": "New-Pass-99"}).status_code == 400
    assert client.post(url, json={"currentPassword": PW, "newPassword": "short"}).status_code == 400
    assert client.post(url, json={"currentPassword": PW, "newPassword": PW}).status_code == 400
    assert client.post(url, json={"currentPassword": PW, "newPassword": "New-Pass-99"}).status_code == 204
    fresh = app_module.app.test_client()
    assert fresh.post("/login", data={"username": "ana_owner", "password": "New-Pass-99"}).status_code == 302
