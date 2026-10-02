"""Users & Access API (/api/admin/users): listing, filters, create, password reset, delete,
protections, permissions and CSRF. Same rules as the classic /users page."""
import json

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

from conftest import PASSWORD

BASE = "/api/admin/users"


def api_client(app_module, username, password):
    client = app_module.app.test_client()
    token = client.get("/api/auth/csrf").get_json()["csrfToken"]
    resp = client.post("/api/auth/login", json={"username": username, "password": password}, headers={"X-CSRFToken": token})
    assert resp.status_code == 200, resp.get_json()
    client.environ_base["HTTP_X_CSRFTOKEN"] = client.get("/api/auth/csrf").get_json()["csrfToken"]
    return client


@pytest.fixture
def sa(app_module):
    return api_client(app_module, "superadmin", "Test-Admin-Pass-1")


@pytest.fixture
def temp_user(app_module):
    """A throwaway staff account, removed afterwards if still there."""
    m = app_module
    with m.app.app_context():
        u = m.User(username="tmp_cashier", password_hash=generate_password_hash("Old-Pass-1"), role="staff", active=True)
        m.db.session.add(u); m.db.session.commit()
        uid = u.id
    yield uid
    with m.app.app_context():
        u = m.db.session.get(m.User, uid)
        if u:
            m.db.session.delete(u); m.db.session.commit()


def audit_has(app_module, text):
    with app_module.app.app_context():
        return app_module.AuditLog.query.filter(app_module.AuditLog.action.like(f"%{text}%")).count() > 0


def test_list_never_returns_password_hashes(sa):
    data = sa.get(BASE).get_json()
    assert data["total"] >= 6 and data["users"]
    body = str(data)
    assert not {"password", "passwordHash", "password_hash"} & set(data["users"][0]) and "pbkdf2" not in body and "scrypt" not in body
    row = next(u for u in data["users"] if u["username"] == "superadmin")
    assert row["isSelf"] and row["roleLabel"] == "Superadmin" and row["createdAt"].endswith("Z") and "." not in row["createdAt"]
    assert not row["canResetPassword"] and not row["canDelete"]          # own account, Superadmin
    assert all(r["value"] != "resident" for r in data["creatableRoles"])


def test_filters_and_pagination(sa):
    assert {u["role"] for u in sa.get(f"{BASE}?role=staff").get_json()["users"]} == {"staff"}
    assert [u["username"] for u in sa.get(f"{BASE}?q=SUPER").get_json()["users"]] == ["superadmin"]
    assert sa.get(f"{BASE}?q=nobody-like-this").get_json()["total"] == 0
    assert all(u["active"] for u in sa.get(f"{BASE}?status=active").get_json()["users"])
    page = sa.get(f"{BASE}?perPage=2&page=2").get_json()
    assert page["page"] == 2 and page["perPage"] == 2 and len(page["users"]) <= 2
    assert sa.get(f"{BASE}?role=cashier").status_code == 400
    assert sa.get(f"{BASE}?q=%25").get_json()["total"] == 0   # % is matched literally, not as a wildcard


def test_create_user_validation_and_success(app_module, sa):
    bad = sa.post(BASE, json={"username": "x", "password": "short", "role": "cashier"}).get_json()["error"]["fields"]
    assert set(bad) == {"username", "password", "role"}
    assert sa.post(BASE, json={"username": "res_user", "password": "Long-enough-1", "role": "resident"}).status_code == 400
    assert sa.post(BASE, json={"username": "SUPERADMIN", "password": "Long-enough-1", "role": "staff"}).get_json()["error"]["fields"]["username"]
    resp = sa.post(BASE, json={"username": "new_teller", "password": "Long-enough-1", "role": "accounting"})
    assert resp.status_code == 201
    user = resp.get_json()["user"]
    assert user["role"] == "accounting" and not {"password", "passwordHash", "password_hash"} & set(user)
    assert audit_has(app_module, "Created user new_teller (accounting)")
    api_client(app_module, "new_teller", "Long-enough-1")   # can sign in
    assert sa.delete(f"{BASE}/{user['id']}").status_code == 204


def test_reset_password_rules(app_module, sa, temp_user):
    m = app_module
    with m.app.app_context():
        sa_id = m.User.query.filter_by(username="superadmin").first().id
    assert sa.post(f"{BASE}/{temp_user}/password", json={"newPassword": "short"}).status_code == 400
    assert sa.post(f"{BASE}/{sa_id}/password", json={"newPassword": "Long-enough-1"}).status_code == 403
    assert sa.post(f"{BASE}/999999/password", json={"newPassword": "Long-enough-1"}).status_code == 404
    assert sa.post(f"{BASE}/{temp_user}/password", json={"newPassword": "New-Pass-99"}).status_code == 204
    with m.app.app_context():
        assert check_password_hash(m.db.session.get(m.User, temp_user).password_hash, "New-Pass-99")
    assert audit_has(m, "Reset password for user tmp_cashier")


def test_delete_protections(app_module, sa, temp_user):
    m = app_module
    with m.app.app_context():
        sa_id = m.User.query.filter_by(username="superadmin").first().id
        res_id = m.User.query.filter_by(username="test_resident").first().id
    assert sa.delete(f"{BASE}/{sa_id}").status_code == 403                      # self
    assert sa.delete(f"{BASE}/{res_id}").status_code == 409                     # resident -> Resident Accounts
    assert sa.delete(f"{BASE}/{temp_user}").status_code == 204
    assert audit_has(m, "Deleted user tmp_cashier (staff)")
    with m.app.app_context():
        assert m.db.session.get(m.User, temp_user) is None


def test_last_active_superadmin_cannot_be_deleted(app_module):
    m = app_module
    with m.app.app_context():
        second = m.User(username="sa_two", password_hash=generate_password_hash("Second-SA-1"), role="super_admin", active=True)
        m.db.session.add(second); m.db.session.commit()
        sa_id = m.User.query.filter_by(username="superadmin").first().id
    client = api_client(m, "sa_two", "Second-SA-1")
    # Deactivate the original superadmin, leaving sa_two as the only active Superadmin.
    with m.app.app_context():
        m.db.session.get(m.User, sa_id).active = False; m.db.session.commit()
    try:
        row = next(u for u in client.get(f"{BASE}?q=superadmin").get_json()["users"] if u["username"] == "superadmin")
        assert row["canDelete"]   # an inactive Superadmin may be deleted while another active one exists
        with m.app.app_context():
            two_id = m.User.query.filter_by(username="sa_two").first().id
        # sa_two is now the last active Superadmin. Only an active Superadmin can call this API,
        # so this case is reached through self-deletion, which is refused (the last-Superadmin
        # check stays in the API as a backstop).
        assert client.delete(f"{BASE}/{two_id}").status_code == 403
    finally:
        with m.app.app_context():
            m.db.session.get(m.User, sa_id).active = True
            m.db.session.delete(m.User.query.filter_by(username="sa_two").first()); m.db.session.commit()


@pytest.mark.parametrize("role", ["admin", "manager", "staff", "accounting", "resident"])
def test_other_roles_are_refused(app_module, role, temp_user):
    client = api_client(app_module, f"test_{role}", PASSWORD)
    assert client.get(BASE).status_code == 403
    assert client.post(BASE, json={"username": "hacker1", "password": "Long-enough-1", "role": "super_admin"}).status_code == 403
    assert client.post(f"{BASE}/{temp_user}/password", json={"newPassword": "Long-enough-1"}).status_code == 403
    assert client.delete(f"{BASE}/{temp_user}").status_code == 403


def test_signed_out_and_csrf(app_module, sa, temp_user):
    anon = app_module.app.test_client()
    assert anon.get(BASE).status_code == 401
    sa.environ_base.pop("HTTP_X_CSRFTOKEN")
    for resp in (sa.post(BASE, json={"username": "no_csrf", "password": "Long-enough-1", "role": "staff"}),
                 sa.post(f"{BASE}/{temp_user}/password", json={"newPassword": "Long-enough-1"}),
                 sa.delete(f"{BASE}/{temp_user}")):
        assert resp.status_code == 403 and "CSRF" in resp.get_json()["error"]["message"]
    with app_module.app.app_context():
        assert app_module.User.query.filter_by(username="no_csrf").first() is None
        assert app_module.db.session.get(app_module.User, temp_user) is not None


# ------------------------------------------------------------------ role / status changes (PATCH)
def audit_row(app_module, text):
    with app_module.app.app_context():
        a = app_module.AuditLog.query.filter(app_module.AuditLog.action.like(f"%{text}%")).order_by(app_module.AuditLog.id.desc()).first()
        return None if a is None else {"action": a.action, "entity_type": a.entity_type, "entity_id": a.entity_id,
                                       "reason": a.reason, "details": a.details, "username": a.username}


def set_known_password(app_module, user_id, password):
    with app_module.app.app_context():
        u = app_module.db.session.get(app_module.User, user_id)
        u.password_hash = generate_password_hash(password)
        app_module.db.session.commit()


def test_rows_report_temporary_passwords_and_edit_rights(app_module, sa, temp_user):
    created = sa.post(BASE, json={"username": "temp_flag", "password": "Long-enough-1", "role": "staff"}).get_json()["user"]
    try:
        assert created["mustChangePassword"] is True and created["canEdit"] is True
        me = next(u for u in sa.get(f"{BASE}?q=superadmin").get_json()["users"] if u["username"] == "superadmin")
        assert me["canEdit"] is False and "own role" in me["editBlockedReason"]
        resident = sa.get(f"{BASE}?role=resident").get_json()["users"][0]
        assert resident["canEdit"] is False and "Resident Accounts" in resident["editBlockedReason"]
    finally:
        sa.delete(f"{BASE}/{created['id']}")


def test_change_role_ends_sessions_and_is_audited(app_module, sa, temp_user):
    set_known_password(app_module, temp_user, "Cashier-Pass-1")
    victim = api_client(app_module, "tmp_cashier", "Cashier-Pass-1")
    assert victim.get("/api/auth/me").status_code == 200
    resp = sa.patch(f"{BASE}/{temp_user}", json={"role": "accounting", "reason": "Moved to Accounting"})
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["user"]["role"] == "accounting"
    assert victim.get("/api/auth/me").status_code == 401                 # old session ended
    api_client(app_module, "tmp_cashier", "Cashier-Pass-1")               # can sign in again, new role
    row = audit_row(app_module, "Updated user tmp_cashier")
    assert row["action"] == "Updated user tmp_cashier: role Staff -> Accounting"
    assert row["entity_type"] == "user" and row["entity_id"] == temp_user and row["reason"] == "Moved to Accounting"
    details = json.loads(row["details"])
    assert details == {"before": {"active": True, "role": "staff"}, "after": {"active": True, "role": "accounting"}}
    assert row["username"] == "superadmin"


def test_deactivate_blocks_sign_in_and_reactivate_restores(app_module, sa, temp_user):
    set_known_password(app_module, temp_user, "Cashier-Pass-1")
    victim = api_client(app_module, "tmp_cashier", "Cashier-Pass-1")
    assert sa.patch(f"{BASE}/{temp_user}", json={"active": False}).get_json()["user"]["active"] is False
    assert victim.get("/api/auth/me").status_code == 401
    anon = app_module.app.test_client()
    token = anon.get("/api/auth/csrf").get_json()["csrfToken"]
    assert anon.post("/api/auth/login", json={"username": "tmp_cashier", "password": "Cashier-Pass-1"},
                     headers={"X-CSRFToken": token}).status_code == 401
    assert sa.get(f"{BASE}?status=inactive&q=tmp_cashier").get_json()["total"] == 1
    assert audit_row(app_module, "Updated user tmp_cashier")["action"].endswith(": deactivated")
    assert sa.patch(f"{BASE}/{temp_user}", json={"active": True}).status_code == 200
    api_client(app_module, "tmp_cashier", "Cashier-Pass-1")
    assert audit_row(app_module, "Updated user tmp_cashier")["action"].endswith(": activated")


def test_patch_validation_and_protections(app_module, sa, temp_user):
    with app_module.app.app_context():
        sa_id = app_module.User.query.filter_by(username="superadmin").first().id
        res_id = app_module.User.query.filter_by(username="test_resident").first().id
    assert sa.patch(f"{BASE}/{sa_id}", json={"active": False}).status_code == 403          # own account
    assert sa.patch(f"{BASE}/{res_id}", json={"role": "staff"}).status_code == 409         # resident -> Resident Accounts
    assert sa.patch(f"{BASE}/999999", json={"active": False}).status_code == 404
    assert sa.patch(f"{BASE}/{temp_user}", json={"role": "resident"}).get_json()["error"]["fields"]["role"]
    assert sa.patch(f"{BASE}/{temp_user}", json={"role": "cashier"}).status_code == 400
    assert sa.patch(f"{BASE}/{temp_user}", json={"active": "no"}).get_json()["error"]["fields"]["active"]
    assert sa.patch(f"{BASE}/{temp_user}", json={"active": False, "reason": "x" * 501}).get_json()["error"]["fields"]["reason"]
    assert "Nothing to change" in sa.patch(f"{BASE}/{temp_user}", json={"role": "staff", "active": True}).get_json()["error"]["message"]
    with app_module.app.app_context():
        u = app_module.db.session.get(app_module.User, temp_user)
        assert u.role == "staff" and u.active                                              # nothing applied


def test_demoting_another_superadmin(app_module, sa):
    m = app_module
    with m.app.app_context():
        two = m.User(username="sa_three", password_hash=generate_password_hash("Third-SA-1"), role="super_admin", active=True)
        m.db.session.add(two)
        m.db.session.commit()
        two_id = two.id
    try:
        assert sa.patch(f"{BASE}/{two_id}", json={"role": "admin"}).get_json()["user"]["role"] == "admin"
        with m.app.app_context():
            assert m.User.query.filter_by(role="super_admin", active=True).count() >= 1
    finally:
        with m.app.app_context():
            m.db.session.delete(m.db.session.get(m.User, two_id))
            m.db.session.commit()


def test_mutations_write_structured_audit(app_module, sa, temp_user):
    assert sa.post(f"{BASE}/{temp_user}/password", json={"newPassword": "Fresh-Pass-77"}).status_code == 204
    row = audit_row(app_module, "Reset password for user tmp_cashier")
    assert row["entity_type"] == "user" and row["entity_id"] == temp_user
    with app_module.app.app_context():
        assert app_module.db.session.get(app_module.User, temp_user).must_change_password
    resp = sa.post(f"{BASE}/{temp_user}/password", json={"newPassword": "short"})
    assert resp.status_code == 400 and resp.get_json()["error"]["fields"]["newPassword"]
    created = sa.post(BASE, json={"username": "audit_me", "password": "Long-enough-1", "role": "manager"}).get_json()["user"]
    row = audit_row(app_module, "Created user audit_me")
    assert row["entity_id"] == created["id"] and "Long-enough" not in (row["details"] or "")
    assert sa.delete(f"{BASE}/{created['id']}").status_code == 204
    assert audit_row(app_module, "Deleted user audit_me")["entity_id"] == created["id"]


def test_failed_audit_rolls_back_the_change(app_module, sa, temp_user, monkeypatch):
    """The change and its audit entry are one transaction: if the audit row can't be saved, nothing is."""
    m = app_module
    original = m.AuditLog.__init__

    with m.app.app_context():
        taken = m.AuditLog.query.order_by(m.AuditLog.id).first().id

    def broken(self, *a, **k):
        original(self, *a, **k)
        self.id = taken             # duplicate primary key: the audit insert fails when the transaction commits
    monkeypatch.setattr(m.AuditLog, "__init__", broken)
    monkeypatch.setitem(m.app.config, "PROPAGATE_EXCEPTIONS", False)
    assert sa.patch(f"{BASE}/{temp_user}", json={"active": False}).status_code == 500
    monkeypatch.setattr(m.AuditLog, "__init__", original)
    with m.app.app_context():
        assert m.db.session.get(m.User, temp_user).active is True


@pytest.mark.parametrize("role", ["admin", "manager", "staff", "accounting", "resident"])
def test_other_roles_cannot_change_access(app_module, role, temp_user):
    client = api_client(app_module, f"test_{role}", PASSWORD)
    assert client.patch(f"{BASE}/{temp_user}", json={"role": "super_admin"}).status_code == 403
    with app_module.app.app_context():
        assert app_module.db.session.get(app_module.User, temp_user).role == "staff"


def test_patch_requires_csrf(app_module, sa, temp_user):
    sa.environ_base.pop("HTTP_X_CSRFTOKEN")
    resp = sa.patch(f"{BASE}/{temp_user}", json={"active": False})
    assert resp.status_code == 403 and "CSRF" in resp.get_json()["error"]["message"]
    with app_module.app.app_context():
        assert app_module.db.session.get(app_module.User, temp_user).active is True
