"""/api/auth: session login for the React frontend, sharing the legacy session."""
import pytest

from conftest import PASSWORD, ROLES


def csrf(client):
    return client.get("/api/auth/csrf").get_json()["csrfToken"]


def api_login(client, username, password):
    token = csrf(client)
    return client.post("/api/auth/login", json={"username": username, "password": password},
                       headers={"X-CSRFToken": token})


def test_login_requires_csrf_token(app_module):
    client = app_module.app.test_client()
    resp = client.post("/api/auth/login", json={"username": "superadmin", "password": "Test-Admin-Pass-1"})
    assert resp.status_code == 403
    assert "CSRF" in resp.get_json()["error"]["message"]


def test_security_headers_restrict_cross_origin_resource_embedding(app_module):
    response = app_module.app.test_client().get("/api/auth/csrf")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert response.headers["Cross-Origin-Resource-Policy"] == "same-origin"
    assert response.headers["X-Permitted-Cross-Domain-Policies"] == "none"


def test_wrong_password_is_rejected(app_module):
    resp = api_login(app_module.app.test_client(), "superadmin", "wrong")
    assert resp.status_code == 401
    assert resp.get_json()["error"]["message"] == "Invalid username or password."


def test_missing_fields_rejected(app_module):
    resp = api_login(app_module.app.test_client(), "", "")
    assert resp.status_code == 400


def test_inactive_account_cannot_sign_in(app_module):
    with app_module.app.app_context():
        user = app_module.User.query.filter_by(username="test_staff").first()
        user.active = False
        app_module.db.session.commit()
    try:
        assert api_login(app_module.app.test_client(), "test_staff", PASSWORD).status_code == 401
    finally:
        with app_module.app.app_context():
            app_module.User.query.filter_by(username="test_staff").first().active = True
            app_module.db.session.commit()


def test_superadmin_login_me_and_shared_legacy_session(app_module):
    client = app_module.app.test_client()
    resp = api_login(client, "superadmin", "Test-Admin-Pass-1")
    assert resp.status_code == 200
    user = resp.get_json()["user"]
    assert user == {**user, "username": "superadmin", "role": "super_admin", "home": "dashboard", "portal": "superadmin", "unitId": None}
    assert {"dashboard", "billing", "users", "settings", "resident_users", "audit_logs", "employee_payroll"} <= set(user["permissions"])
    assert client.get("/api/auth/me").get_json()["user"]["username"] == "superadmin"
    # Same Flask session: legacy pages open without a second login.
    assert client.get("/dashboard").status_code == 200


@pytest.mark.parametrize("role, home, portal, allowed, denied", [
    ("admin", "dashboard", "admin", {"units", "billing", "water", "reports", "move_certificate"},
     {"users", "resident_users", "settings", "audit_logs", "employees", "employee_payroll"}),
    ("manager", "employees", "hr", {"employees", "employee_payroll", "employee_hr_settings", "announcements"},
     {"billing", "dashboard", "settings", "documents", "reports"}),
    ("staff", "move_certificate", "staff", {"move_certificate", "gate_pass", "expenses", "employee_attendance", "maintenance"},
     {"reports", "audit_logs", "billing", "employee_leave", "employee_payroll"}),
    ("accounting", "reports", "accounting", {"reports", "audit_logs", "billing", "mark_bill_paid", "edit_soa", "advance_payments"},
     {"units", "employees", "move_certificate", "users", "settings", "email_bill"}),
    ("resident", "resident_portal", "resident", {"resident_portal", "maintenance", "api_resident"},
     {"units", "unit_detail", "billing", "reports", "documents", "audit_logs"}),
])
def test_permissions_follow_the_role_matrix(app_module, role, home, portal, allowed, denied):
    user = api_login(app_module.app.test_client(), f"test_{role}", PASSWORD).get_json()["user"]
    assert user["role"] == role and user["home"] == home and user["portal"] == portal
    assert (user["unitId"] is not None) == (role == "resident")
    assert allowed <= set(user["permissions"])
    assert not denied & set(user["permissions"])


def test_logout_ends_both_sessions(app_module):
    client = app_module.app.test_client()
    token = api_login(client, "superadmin", "Test-Admin-Pass-1").get_json()["csrfToken"]
    assert client.post("/api/auth/logout", headers={"X-CSRFToken": token}).status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/dashboard").status_code == 302  # legacy page now redirects to login


def test_login_and_logout_are_audited_like_legacy(app_module):
    client = app_module.app.test_client()
    token = api_login(client, "test_accounting", PASSWORD).get_json()["csrfToken"]
    client.post("/api/auth/logout", headers={"X-CSRFToken": token})
    with app_module.app.app_context():
        rows = app_module.AuditLog.query.filter_by(username="test_accounting").order_by(app_module.AuditLog.id.desc()).limit(2).all()
        assert [r.action for r in rows] == ["Logout", "Login"]


def test_resident_can_change_username_and_other_sessions_end(app_module):
    first = app_module.app.test_client()
    second = app_module.app.test_client()
    assert api_login(first, "test_resident", PASSWORD).status_code == 200
    assert api_login(second, "test_resident", PASSWORD).status_code == 200
    token = csrf(first)

    try:
        response = first.post("/api/auth/username", json={
            "currentPassword": PASSWORD, "newUsername": "resident.new",
        }, headers={"X-CSRFToken": token})

        assert response.status_code == 200
        assert response.get_json()["user"]["username"] == "resident.new"
        assert first.get("/api/auth/me").get_json()["user"]["username"] == "resident.new"
        assert second.get("/api/auth/me").status_code == 401
        with app_module.app.app_context():
            user = app_module.User.query.filter_by(username="resident.new").one()
            assert user.role == "resident"
            row = app_module.AuditLog.query.filter_by(username="resident.new", action="Changed resident username").one()
            assert '"before": "test_resident"' in row.details
            assert '"after": "resident.new"' in row.details
    finally:
        with app_module.app.app_context():
            user = app_module.User.query.filter_by(username="resident.new").first()
            if user:
                user.username = "test_resident"
                app_module.db.session.commit()


@pytest.mark.parametrize("new_username", ["ab", "bad name", "bad/name", "x" * 81])
def test_resident_username_change_rejects_invalid_names(app_module, new_username):
    client = app_module.app.test_client()
    api_login(client, "test_resident", PASSWORD)
    response = client.post("/api/auth/username", json={
        "currentPassword": PASSWORD, "newUsername": new_username,
    }, headers={"X-CSRFToken": csrf(client)})
    assert response.status_code == 400
    assert "3–80" in response.get_json()["error"]["message"]


def test_resident_username_change_rejects_taken_name_and_wrong_password(app_module):
    client = app_module.app.test_client()
    api_login(client, "test_resident", PASSWORD)
    headers = {"X-CSRFToken": csrf(client)}

    wrong_password = client.post("/api/auth/username", json={
        "currentPassword": "wrong", "newUsername": "resident.new",
    }, headers=headers)
    assert wrong_password.status_code == 400
    assert "Current password" in wrong_password.get_json()["error"]["message"]

    taken = client.post("/api/auth/username", json={
        "currentPassword": PASSWORD, "newUsername": "test_staff",
    }, headers=headers)
    assert taken.status_code == 409
    assert "already taken" in taken.get_json()["error"]["message"]


def test_nonresident_cannot_use_resident_username_change(app_module):
    client = app_module.app.test_client()
    api_login(client, "test_staff", PASSWORD)
    response = client.post("/api/auth/username", json={
        "currentPassword": PASSWORD, "newUsername": "staff.new",
    }, headers={"X-CSRFToken": csrf(client)})
    assert response.status_code == 403


def test_me_requires_login_and_unknown_api_is_json_404(app_module):
    client = app_module.app.test_client()
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401 and resp.is_json
    resp = client.get("/api/does-not-exist")
    assert resp.status_code == 404 and resp.is_json


def test_react_route_answers(app_module):
    resp = app_module.app.test_client().get("/app/billing")
    # 200 when frontend/dist exists, otherwise a clear 503 message; never a crash or legacy page.
    assert resp.status_code in (200, 503)
