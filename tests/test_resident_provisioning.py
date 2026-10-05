"""Automatic resident portal account provisioning (activation codes, one account per person,
multi-unit access). Isolated SQLite test database, dummy residents only."""
import secrets
from datetime import datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from conftest import PASSWORD, SA_PASSWORD, SA_USERNAME, api, login

BASE = "/api/admin/resident-accounts"
NEW_PW = "My-Own-Portal-Pass-77"


def token():
    return secrets.token_urlsafe(24)


@pytest.fixture(autouse=True)
def fresh_throttle(app_module):
    from app.core import security
    security.login_throttle.clear()
    yield
    security.login_throttle.clear()


@pytest.fixture
def sa(app_module):
    return api(login(app_module, SA_USERNAME, SA_PASSWORD)[0])


@pytest.fixture
def world(app_module):
    """Two residential units and a parking unit with dummy owners/tenants; cleaned up afterwards."""
    m = app_module
    with m.app.app_context():
        a = m.Unit(unit_no="PROV-A", unit_type="1 BEDROOM", area_sqm=40, active=True, status="Occupied")
        b = m.Unit(unit_no="PROV-B", unit_type="2 BEDROOM", area_sqm=60, active=True, status="Occupied")
        park = m.Unit(unit_no="PROV-P", unit_type="PARKING", area_sqm=12, active=True)
        m.db.session.add_all([a, b, park]); m.db.session.flush()
        people = {
            "ownerA": m.Owner(unit_id=a.id, owner_name="Rosa Provision", status="Current", email="rosa@example.test", contact_no="0917 111 2222"),
            "tenantA": m.Tenant(unit_id=a.id, tenant_name="Tito Tenant", status="Current"),
            "ownerB": m.Owner(unit_id=b.id, owner_name="Rosa Provision", status="Current", email="rosa@example.test", contact_no="09171112222"),
            "otherB": m.Tenant(unit_id=b.id, tenant_name="Different Person", status="Current", email="rosa@example.test"),
            "pastB": m.Tenant(unit_id=b.id, tenant_name="Old Tenant", status="Past"),
            "parkOwner": m.Owner(unit_id=park.id, owner_name="Parking Owner", status="Current"),
        }
        m.db.session.add_all(people.values()); m.db.session.commit()
        ids = {"A": a.id, "B": b.id, "P": park.id, **{k: v.id for k, v in people.items()}}
    yield ids
    with m.app.app_context():
        units = [ids["A"], ids["B"], ids["P"]]
        link_users = {l.user_id for l in m.ResidentUnitLink.query.filter(m.ResidentUnitLink.unit_id.in_(units)).all()}
        prof_users = {p.user_id for p in m.ResidentProfile.query.filter(m.ResidentProfile.unit_id.in_(units)).all()}
        for uid in link_users | prof_users:
            user = m.db.session.get(m.User, uid)
            if user:
                if user.resident_profile:
                    m.db.session.delete(user.resident_profile)
                m.db.session.delete(user)
        m.db.session.flush()
        m.Tenant.query.filter(m.Tenant.unit_id.in_(units)).delete(synchronize_session=False)
        m.Owner.query.filter(m.Owner.unit_id.in_(units)).delete(synchronize_session=False)
        m.Unit.query.filter(m.Unit.id.in_(units)).delete(synchronize_session=False)
        m.db.session.commit()


def provision(sa, person_type, person_id, mode="auto", **extra):
    return sa.post(f"{BASE}/provision", json={"personType": person_type, "personId": person_id, "mode": mode, "formToken": token(), **extra})


def resident_client(m):
    c = m.app.test_client()
    c.environ_base["HTTP_X_CSRFTOKEN"] = c.get("/api/auth/csrf").get_json()["csrfToken"]
    return c


def sign_in(m, username, password):
    c = resident_client(m)
    resp = c.post("/api/auth/login", json={"username": username, "password": password})
    if resp.status_code == 200:
        c.environ_base["HTTP_X_CSRFTOKEN"] = resp.get_json()["csrfToken"]
    return c, resp


def activate(m, username, code, new=NEW_PW):
    c, resp = sign_in(m, username, code)
    assert resp.status_code == 200, resp.get_json()
    assert c.post("/api/auth/password", json={"currentPassword": code, "newPassword": new}).status_code == 204
    return c


# ------------------------------------------------------------------ generation
def test_account_is_generated_without_any_resident_signup(app_module, sa, world):
    m = app_module
    resp = provision(sa, "Owner", world["ownerA"])
    assert resp.status_code == 201 and resp.headers["Cache-Control"] == "no-store"
    body = resp.get_json()
    cred, acc = body["credentials"], body["account"]
    assert cred["username"] == "rosa.provision" and len(cred["activationCode"]) == 14 and cred["expiresAt"].endswith("Z")
    assert acc["status"] == "pending" and acc["mustChangePassword"] and [l["unitNo"] for l in acc["links"]] == ["PROV-A"]
    with m.app.app_context():
        user = m.User.query.filter_by(username=cred["username"]).one()
        assert user.role == "resident" and user.password_hash != cred["activationCode"] and user.password_hash.startswith(("scrypt:", "pbkdf2:"))
        expiry_days = (user.temp_password_expires_at - datetime.utcnow()).days
        assert 6 <= expiry_days <= 7                                                   # default 7 days
        audits = " ".join((a.action or "") + (a.details or "") for a in m.AuditLog.query.all())
        assert cred["activationCode"] not in audits and "Provisioned resident portal account rosa.provision" in audits
    listing = sa.get(BASE).get_data(as_text=True) + sa.get(f"{BASE}/people").get_data(as_text=True)
    assert cred["activationCode"] not in listing                                       # shown only once
    person = next(p for p in sa.get(f"{BASE}/people?q=PROV-A").get_json()["people"] if p["personId"] == world["ownerA"] and p["personType"] == "Owner")
    assert person["state"] == "pending" and person["account"]["username"] == "rosa.provision"


def test_activation_flow_and_pending_accounts_have_no_portal_access(app_module, sa, world):
    m = app_module
    cred = provision(sa, "Owner", world["ownerA"]).get_json()["credentials"]
    c, resp = sign_in(m, cred["username"], cred["activationCode"].lower())          # any case accepted
    assert resp.status_code == 200 and resp.get_json()["user"]["mustChangePassword"] and resp.get_json()["user"]["activationPending"]
    blocked = c.get(f"/api/resident/units/{world['A']}/summary")
    assert blocked.status_code == 403 and blocked.get_json()["error"]["code"] == "password_change_required"
    assert c.post("/api/auth/password", json={"currentPassword": cred["activationCode"], "newPassword": "short"}).status_code == 400
    assert c.post("/api/auth/password", json={"currentPassword": cred["activationCode"], "newPassword": NEW_PW}).status_code == 204
    assert c.get(f"/api/resident/units/{world['A']}/summary").status_code == 200
    me = c.get("/api/auth/me").get_json()["user"]
    assert me["unitId"] == world["A"] and not me["activationPending"] and [u["unitNo"] for u in me["units"]] == ["PROV-A"]
    assert sign_in(m, cred["username"], cred["activationCode"])[1].status_code == 401    # code consumed
    assert sign_in(m, cred["username"], NEW_PW)[1].status_code == 200
    with m.app.app_context():
        user = m.User.query.filter_by(username=cred["username"]).one()
        assert user.temp_password_expires_at is None and not user.must_change_password
        assert m.AuditLog.query.filter(m.AuditLog.action == f"Activated resident portal account {cred['username']}").count() == 1
    assert sa.get(BASE).get_json()["accounts"] and next(a for a in sa.get(BASE).get_json()["accounts"] if a["username"] == cred["username"])["status"] == "active"


def test_expired_invalid_and_replaced_codes(app_module, sa, world):
    m = app_module
    first = provision(sa, "Tenant", world["tenantA"]).get_json()
    cred, uid = first["credentials"], first["account"]["id"]
    assert sign_in(m, cred["username"], "WRONG-CODE-0000")[1].status_code == 401
    # Replaced: the old code stops working, the new one works, open sessions end.
    early, _ = sign_in(m, cred["username"], cred["activationCode"])
    again = sa.post(f"{BASE}/{uid}/activation-code", json={"formToken": token()})
    assert again.status_code == 200
    new = again.get_json()["credentials"]
    assert new["activationCode"] != cred["activationCode"]
    assert early.get("/api/auth/me").status_code == 401
    assert sign_in(m, cred["username"], cred["activationCode"])[1].status_code == 401
    assert sign_in(m, cred["username"], new["activationCode"])[1].status_code == 200
    # Expired: refused at sign-in and for the password change.
    c, _ = sign_in(m, cred["username"], new["activationCode"])
    with m.app.app_context():
        user = m.db.session.get(m.User, uid)
        user.temp_password_expires_at = datetime.utcnow() - timedelta(minutes=1)
        m.db.session.commit()
    expired = sign_in(m, cred["username"], new["activationCode"])[1]
    assert expired.status_code == 403 and "expired" in expired.get_json()["error"]["message"]
    assert c.post("/api/auth/password", json={"currentPassword": new["activationCode"], "newPassword": NEW_PW}).status_code == 403
    row = next(a for a in sa.get(BASE).get_json()["accounts"] if a["id"] == uid)
    assert row["status"] == "pending" and row["activationExpired"] is True


def test_activation_attempts_are_rate_limited(app_module, sa, world):
    m = app_module
    cred = provision(sa, "Owner", world["ownerA"]).get_json()["credentials"]
    for _ in range(5):
        assert sign_in(m, cred["username"], "AAAA-BBBB-CCCC")[1].status_code == 401
    assert sign_in(m, cred["username"], cred["activationCode"])[1].status_code == 429   # even the right code, until the window ends


# ------------------------------------------------------------------ identity
def test_same_person_is_flagged_and_reused_only_when_confirmed(app_module, sa, world):
    m = app_module
    rosa = provision(sa, "Owner", world["ownerA"]).get_json()
    review = provision(sa, "Owner", world["ownerB"])
    assert review.status_code == 409 and review.get_json()["error"]["needsReview"]
    cand = review.get_json()["error"]["candidates"]
    assert cand[0]["username"] == "rosa.provision" and set(cand[0]["reasons"]) == {"same name", "same email", "same phone number"}
    with m.app.app_context():
        assert m.User.query.filter(m.User.username.like("rosa.provision%")).count() == 1      # nothing created yet
    linked = provision(sa, "Owner", world["ownerB"], mode="link", linkUserId=rosa["account"]["id"])
    assert linked.status_code == 200 and linked.get_json()["credentials"] is None and linked.get_json()["linked"]
    assert sorted(l["unitNo"] for l in linked.get_json()["account"]["links"]) == ["PROV-A", "PROV-B"]
    c = activate(m, rosa["credentials"]["username"], rosa["credentials"]["activationCode"])
    assert sorted(u["unitNo"] for u in c.get("/api/auth/me").get_json()["user"]["units"]) == ["PROV-A", "PROV-B"]
    assert c.get(f"/api/resident/units/{world['A']}/summary").status_code == 200
    assert c.get(f"/api/resident/units/{world['B']}/summary").status_code == 200


def test_shared_email_alone_never_merges_people(app_module, sa, world):
    rosa = provision(sa, "Owner", world["ownerA"]).get_json()
    review = provision(sa, "Tenant", world["otherB"])
    assert review.status_code == 409 and review.get_json()["error"]["candidates"][0]["reasons"] == ["same email"]
    separate = provision(sa, "Tenant", world["otherB"], mode="new")
    assert separate.status_code == 201
    assert separate.get_json()["credentials"]["username"] == "different.person" != rosa["credentials"]["username"]


def test_owner_and_tenant_of_the_same_unit_get_separate_accounts(app_module, sa, world):
    owner = provision(sa, "Owner", world["ownerA"]).get_json()["credentials"]["username"]
    tenant = provision(sa, "Tenant", world["tenantA"]).get_json()["credentials"]["username"]
    assert owner != tenant


# ------------------------------------------------------------------ duplicates / concurrency
def test_duplicate_requests_create_one_account(app_module, sa, world):
    m = app_module
    form = {"personType": "Owner", "personId": world["ownerA"], "mode": "auto", "formToken": token()}
    assert sa.post(f"{BASE}/provision", json=form).status_code == 201
    dup = sa.post(f"{BASE}/provision", json=form)                                     # double click / retry
    assert dup.status_code == 409
    again = provision(sa, "Owner", world["ownerA"])                                    # a second, separate request
    assert again.status_code == 409 and "already has a portal account" in again.get_json()["error"]["message"]
    with m.app.app_context():
        assert m.ResidentUnitLink.query.filter_by(person_type="Owner", person_id=world["ownerA"], active=True).count() == 1
        # The database itself refuses a second active link for the same owner/tenant record.
        uid = m.ResidentUnitLink.query.filter_by(person_type="Owner", person_id=world["ownerA"]).one().user_id
        m.db.session.add(m.ResidentUnitLink(user_id=uid, unit_id=world["A"], person_type="Owner", person_id=world["ownerA"],
                                            active=True, active_key=f"Owner:{world['ownerA']}"))
        with pytest.raises(IntegrityError):
            m.db.session.flush()
        m.db.session.rollback()


def test_failure_mid_way_leaves_nothing_behind(app_module, sa, world, monkeypatch):
    m = app_module
    real_audit = m.audit

    def broken(*a, **k):
        if "Provisioned" in (a[0] if a else ""):
            raise RuntimeError("disk full")
        return real_audit(*a, **k)
    monkeypatch.setattr(m, "audit", broken)
    with m.app.app_context():
        before = (m.User.query.count(), m.ResidentProfile.query.count(), m.ResidentUnitLink.query.count())
    with pytest.raises(RuntimeError):
        sa.post(f"{BASE}/provision", json={"personType": "Owner", "personId": world["ownerA"], "mode": "auto", "formToken": token()})
    with m.app.app_context():
        assert (m.User.query.count(), m.ResidentProfile.query.count(), m.ResidentUnitLink.query.count()) == before


# ------------------------------------------------------------------ eligibility / access changes
def test_ineligible_people_get_no_account(app_module, sa, world):
    past = provision(sa, "Tenant", world["pastB"])
    assert past.status_code == 409 and "not a current tenant" in past.get_json()["error"]["message"]
    park = provision(sa, "Owner", world["parkOwner"])
    assert park.status_code == 409 and "Parking and storage" in park.get_json()["error"]["message"]
    assert provision(sa, "Owner", 999999).status_code == 404


def test_moving_out_of_one_unit_keeps_the_others(app_module, sa, world):
    m = app_module
    rosa = provision(sa, "Owner", world["ownerA"]).get_json()
    provision(sa, "Owner", world["ownerB"], mode="link", linkUserId=rosa["account"]["id"])
    c = activate(m, rosa["credentials"]["username"], rosa["credentials"]["activationCode"])
    with m.app.app_context():
        m.db.session.get(m.Owner, world["ownerA"]).status = "Past"                   # sold unit A
        m.db.session.commit()
    assert c.get(f"/api/resident/units/{world['A']}/summary").status_code == 403
    assert c.get(f"/api/resident/units/{world['B']}/summary").status_code == 200
    me = c.get("/api/auth/me").get_json()["user"]
    assert me["unitId"] == world["B"] and [u["unitNo"] for u in me["units"]] == ["PROV-B"]
    # The administrator ends the remaining link: no unit left, no access.
    link_b = next(l for l in sa.get(BASE).get_json()["accounts"] if l["id"] == rosa["account"]["id"])["links"]
    link_id = next(l["id"] for l in link_b if l["unitNo"] == "PROV-B")
    assert sa.post(f"{BASE}/{rosa['account']['id']}/links/{link_id}/end", json={"reason": "Sold unit B"}).status_code == 200
    assert c.get(f"/api/resident/units/{world['B']}/summary").status_code == 403


def test_disabled_accounts_cannot_sign_in(app_module, sa, world):
    m = app_module
    rosa = provision(sa, "Owner", world["ownerA"]).get_json()
    activate(m, rosa["credentials"]["username"], rosa["credentials"]["activationCode"])
    assert sa.patch(f"{BASE}/{rosa['account']['id']}", json={"active": False, "reason": "test"}).status_code == 200
    assert sign_in(m, rosa["credentials"]["username"], NEW_PW)[1].status_code == 401
    assert sa.post(f"{BASE}/{rosa['account']['id']}/activation-code", json={"formToken": token()}).status_code == 409


# ------------------------------------------------------------------ security
def test_only_the_provisioning_permission_may_provision(app_module, world):
    m = app_module
    for role in ("admin", "manager", "staff", "accounting"):
        client = api(login(m, f"test_{role}", PASSWORD)[0])
        assert client.post(f"{BASE}/provision", json={"personType": "Owner", "personId": world["ownerA"], "formToken": token()}).status_code == 403, role
        assert client.get(f"{BASE}/people").status_code == 403
    assert m.app.test_client().post(f"{BASE}/provision", json={}).status_code in (401, 403)


def test_client_cannot_choose_the_role_and_csrf_is_required(app_module, sa, world):
    m = app_module
    bad = sa.post(f"{BASE}/provision", json={"personType": "Owner", "personId": world["ownerA"], "role": "super_admin", "formToken": token()})
    assert bad.status_code == 400
    raw = login(m, SA_USERNAME, SA_PASSWORD)[0]                                        # no CSRF header
    assert raw.post(f"{BASE}/provision", json={"personType": "Owner", "personId": world["ownerA"], "formToken": token()}).status_code == 403
    with m.app.app_context():
        assert m.ResidentUnitLink.query.filter_by(person_id=world["ownerA"], person_type="Owner").count() == 0


def test_residents_stay_isolated(app_module, sa, world):
    m = app_module
    owner = provision(sa, "Owner", world["ownerA"]).get_json()["credentials"]
    other = provision(sa, "Tenant", world["otherB"], mode="new").get_json()["credentials"]
    c = activate(m, other["username"], other["activationCode"])
    assert c.get(f"/api/resident/units/{world['A']}/summary").status_code == 403
    assert c.get(f"{BASE}/people").status_code == 403
    assert owner["username"] not in c.get(f"/api/resident/units/{world['B']}/profile").get_data(as_text=True)


def test_existing_typed_password_accounts_still_work(app_module, sa, world):
    """Accounts made the old way (typed temporary password, no expiry) keep working."""
    m = app_module
    resp = sa.post(BASE, json={"username": "legacy.resident", "password": "Typed-Temp-Pass-1", "unitId": world["A"],
                               "personType": "Tenant", "personId": world["tenantA"]})
    assert resp.status_code == 201 and resp.get_json()["account"]["activationExpiresAt"] is None
    c = activate(m, "legacy.resident", "Typed-Temp-Pass-1")
    assert c.get(f"/api/resident/units/{world['A']}/summary").status_code == 200
    assert provision(sa, "Tenant", world["tenantA"]).status_code == 409                 # already has one
