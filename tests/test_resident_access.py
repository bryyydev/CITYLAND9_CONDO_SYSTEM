"""A resident's portal access ends automatically when they move out (gap 2).

Scenario: a renter's account is linked to their tenant record. When the admin marks
the tenant "Past" (Inactive), the renter can no longer sign in, an open session ends,
and the unit's SOA/maintenance data is refused, on the React API and the legacy pages.
"""
import pytest
from werkzeug.security import generate_password_hash

from conftest import login

PW = "Renter-Pass-1"


@pytest.fixture
def renter(app_module):
    """A resident account linked to a Current tenant of TEST-503."""
    m = app_module
    with m.app.app_context():
        unit = m.Unit.query.filter_by(unit_no="TEST-503").first()
        tenant = m.Tenant(unit_id=unit.id, tenant_name="Rita Renter", status="Current")
        m.db.session.add(tenant); m.db.session.flush()
        user = m.User(username="rita_renter", password_hash=generate_password_hash(PW), role="resident", active=True)
        m.db.session.add(user); m.db.session.flush()
        m.db.session.add(m.ResidentProfile(user_id=user.id, unit_id=unit.id, person_type="Tenant",
                                           person_id=tenant.id, display_name="Rita Renter"))
        m.db.session.commit()
        ids = {"unit": unit.id, "tenant": tenant.id, "user": user.id}
    yield ids
    with m.app.app_context():
        m.db.session.delete(m.db.session.get(m.User, ids["user"]).resident_profile)
        m.db.session.delete(m.db.session.get(m.User, ids["user"]))
        m.db.session.delete(m.db.session.get(m.Tenant, ids["tenant"]))
        m.db.session.commit()


def api_login(client, username, password):
    token = client.get("/api/auth/csrf").get_json()["csrfToken"]
    return client.post("/api/auth/login", json={"username": username, "password": password}, headers={"X-CSRFToken": token})


def set_tenant(app_module, tenant_id, **fields):
    with app_module.app.app_context():
        tenant = app_module.db.session.get(app_module.Tenant, tenant_id)
        for key, value in fields.items():
            setattr(tenant, key, value)
        app_module.db.session.commit()


def test_current_renter_has_access(app_module, renter):
    client = app_module.app.test_client()
    assert api_login(client, "rita_renter", PW).status_code == 200
    assert client.get(f"/api/resident/units/{renter['unit']}/summary").status_code == 200
    assert client.get("/portal").status_code == 200


def test_moving_out_ends_access_everywhere(app_module, renter):
    # The renter is signed in on both the React portal and the legacy portal.
    react = app_module.app.test_client()
    assert api_login(react, "rita_renter", PW).status_code == 200
    legacy = app_module.app.test_client()
    legacy.post("/login", data={"username": "rita_renter", "password": PW})
    assert legacy.get("/portal").status_code == 200

    # The admin marks the tenant Inactive ("Past") on the unit page, the normal move-out step.
    admin, _ = login(app_module, "superadmin", "admin123")
    admin.post(f"/tenant/{renter['tenant']}/status", data={"status": "Past"})

    # Open React session: data refused with the reason, and the session is ended.
    resp = react.get(f"/api/resident/units/{renter['unit']}/summary")
    assert resp.status_code == 403
    assert "no longer listed as a current tenant of unit TEST-503" in resp.get_json()["error"]["message"]
    assert react.get("/api/auth/me").status_code == 401
    # Open legacy session: signed out, reason shown on the login page.
    resp = legacy.get("/portal", follow_redirects=True)
    assert "no longer listed as a current tenant" in resp.get_data(as_text=True)
    assert legacy.get("/maintenance").status_code == 302
    assert legacy.get("/change-password").status_code == 302
    # New sign-in attempts are refused with the reason.
    resp = api_login(app_module.app.test_client(), "rita_renter", PW)
    assert resp.status_code == 403 and "no longer listed" in resp.get_json()["error"]["message"]

    # Marking the tenant Active again restores access (e.g. a mistake is corrected).
    set_tenant(app_module, renter["tenant"], status="Current")
    assert api_login(app_module.app.test_client(), "rita_renter", PW).status_code == 200


def test_tenant_moved_to_another_unit_loses_access_to_the_old_unit(app_module, renter):
    with app_module.app.app_context():
        other = app_module.Unit.query.filter_by(unit_no="TEST-504").first().id
    set_tenant(app_module, renter["tenant"], unit_id=other)
    assert api_login(app_module.app.test_client(), "rita_renter", PW).status_code == 403


def test_deactivated_resident_account_loses_access(app_module, renter):
    with app_module.app.app_context():
        app_module.db.session.get(app_module.User, renter["user"]).resident_profile.active = False
        app_module.db.session.commit()
    resp = api_login(app_module.app.test_client(), "rita_renter", PW)
    assert resp.status_code == 403 and "deactivated" in resp.get_json()["error"]["message"]


def test_owner_linked_account_follows_owner_status(app_module):
    m = app_module
    with m.app.app_context():
        unit = m.Unit.query.filter_by(unit_no="TEST-504").first()
        owner = m.Owner(unit_id=unit.id, owner_name="Olga Owner", status="Past")
        m.db.session.add(owner); m.db.session.flush()
        user = m.User(username="olga_owner", password_hash=generate_password_hash(PW), role="resident", active=True)
        m.db.session.add(user); m.db.session.flush()
        m.db.session.add(m.ResidentProfile(user_id=user.id, unit_id=unit.id, person_type="Owner", person_id=owner.id, display_name="Olga"))
        m.db.session.commit()
    resp = api_login(app_module.app.test_client(), "olga_owner", PW)
    assert resp.status_code == 403 and "current owner" in resp.get_json()["error"]["message"]


def test_accounts_not_linked_to_a_person_keep_unit_access(app_module):
    # conftest's test_resident is linked to a unit but to no owner/tenant record.
    assert api_login(app_module.app.test_client(), "test_resident", "Test-Pass-123").status_code == 200
