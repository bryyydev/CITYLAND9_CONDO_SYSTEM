"""Stage 1 security regressions: CSRF on classic forms, POST-only logout, session validation and
revocation, expiry, sign-in throttling, password policy, forced password change, no default
administrator, secret-key checks, encrypted SMTP password, development-server guard."""
import os
import subprocess
import sys
import time

import pytest
from werkzeug.security import generate_password_hash

from conftest import _ROOT, PASSWORD, SA_PASSWORD, SA_USERNAME, login

PY = sys.executable


@pytest.fixture(autouse=True)
def _fresh_throttle(app_module):
    from app.core import security
    security.login_throttle.clear()
    yield
    security.login_throttle.clear()


def api_login(app_module, username, password, client=None):
    client = client or app_module.app.test_client()
    token = client.get("/api/auth/csrf").get_json()["csrfToken"]
    resp = client.post("/api/auth/login", json={"username": username, "password": password}, headers={"X-CSRFToken": token})
    if resp.status_code == 200:
        client.environ_base["HTTP_X_CSRFTOKEN"] = resp.get_json()["csrfToken"]
    return client, resp


@pytest.fixture
def temp_account(app_module):
    """A staff account with a known password, removed afterwards."""
    m = app_module
    with m.app.app_context():
        u = m.User(username="sec_staff", password_hash=generate_password_hash("Sec-Staff-Pass-1"), role="staff", active=True)
        m.db.session.add(u); m.db.session.commit()
        uid = u.id
    yield uid
    with m.app.app_context():
        u = m.db.session.get(m.User, uid)
        if u:
            m.db.session.delete(u); m.db.session.commit()


def count(app_module, model, **filters):
    with app_module.app.app_context():
        return model.query.filter_by(**filters).count()


# ------------------------------------------------------------------ CSRF (classic screens)

def _api_csrf(client):
    """Send the session's API CSRF token with the client's requests (the React app does the same)."""
    client.environ_base["HTTP_X_CSRFTOKEN"] = client.get("/api/auth/csrf").get_json()["csrfToken"]
    return client

def test_classic_forms_get_a_csrf_token(superadmin):
    html = superadmin.get("/change-password").get_data(as_text=True)
    assert html.count('name="csrf_token"') >= html.lower().count('method="post"') >= 1


@pytest.mark.parametrize("token", [None, "wrong-token"])
def test_classic_post_without_valid_token_is_rejected_and_changes_nothing(app_module, superadmin, token):
    superadmin.auto_csrf = False
    before = count(app_module, app_module.Expense)
    data = {"expense_date": "2026-10-01", "category": "Supplies", "description": "csrf probe", "amount": "10"}
    if token:
        data["csrf_token"] = token
    resp = superadmin.post("/expenses", data=data)
    assert resp.status_code == 400 and "Form expired" in resp.get_data(as_text=True)
    assert count(app_module, app_module.Expense) == before


def test_classic_post_with_form_token_works(app_module, superadmin):
    # The token is accepted: the form is processed (and refuses the wrong current password) instead
    # of failing with "Form expired". Nothing is changed.
    superadmin.auto_csrf = False
    token = superadmin.csrf_token()
    resp = superadmin.post("/change-password", data={"current_password": "not-it", "new_password": "Whatever-Pass-91",
                                                     "confirm_password": "Whatever-Pass-91", "csrf_token": token})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/change-password")


def test_classic_page_moved_to_react_changes_nothing(app_module, superadmin):
    before = count(app_module, app_module.Expense)
    resp = superadmin.post("/expenses", data={"expense_date": "2026-10-01", "category": "Supplies", "description": "old tab",
                                              "amount": "10"})
    assert resp.status_code == 303 and "moved=form" in resp.headers["Location"]
    assert count(app_module, app_module.Expense) == before


def test_legacy_login_post_requires_csrf(app_module):
    client = app_module.app.test_client()
    client.auto_csrf = False
    assert client.post("/login", data={"username": SA_USERNAME, "password": SA_PASSWORD}).status_code == 400
    assert client.get("/api/auth/me").status_code == 401


def test_logout_requires_post(app_module, superadmin):
    assert superadmin.get("/logout").status_code == 302
    assert superadmin.get("/api/auth/me").status_code == 200       # GET did not sign out
    superadmin.auto_csrf = False
    assert superadmin.post("/logout").status_code == 400            # POST without token refused
    assert superadmin.get("/api/auth/me").status_code == 200
    superadmin.auto_csrf = True
    superadmin.post("/logout")
    assert superadmin.get("/api/auth/me").status_code == 401


def test_api_mutation_without_csrf_is_rejected(app_module, temp_account):
    client, _ = api_login(app_module, "sec_staff", "Sec-Staff-Pass-1")
    client.environ_base.pop("HTTP_X_CSRFTOKEN")
    resp = client.post("/api/auth/password", json={"currentPassword": "Sec-Staff-Pass-1", "newPassword": "Another-Pass-77"})
    assert resp.status_code == 403 and "CSRF" in resp.get_json()["error"]["message"]


# ------------------------------------------------------------------ session validation / revocation
def test_deactivated_account_loses_its_open_sessions(app_module, temp_account):
    api, _ = api_login(app_module, "sec_staff", "Sec-Staff-Pass-1")
    classic, _ = login(app_module, "sec_staff", "Sec-Staff-Pass-1")
    assert api.get("/api/auth/me").status_code == 200
    assert classic.get("/expenses").headers["Location"] == "/app/staff/expenses"   # signed in: sent on to the React page
    with app_module.app.app_context():
        app_module.db.session.get(app_module.User, temp_account).active = False
        app_module.db.session.commit()
    assert api.get("/api/auth/me").status_code == 401
    resp = classic.get("/expenses")
    assert resp.status_code == 302 and "login" in resp.headers["Location"]


def test_admin_reset_ends_sessions_and_forces_new_password(app_module, temp_account):
    victim, _ = api_login(app_module, "sec_staff", "Sec-Staff-Pass-1")
    admin, _ = api_login(app_module, SA_USERNAME, SA_PASSWORD)
    assert admin.post(f"/api/admin/users/{temp_account}/password", json={"newPassword": "Temp-Reset-Pass-9"}).status_code == 204
    assert victim.get("/api/auth/me").status_code == 401                  # old session ended
    fresh, resp = api_login(app_module, "sec_staff", "Temp-Reset-Pass-9")
    assert resp.get_json()["user"]["mustChangePassword"] is True
    # Until the password is changed only the auth endpoints work.
    blocked = fresh.get("/api/resident/notices")
    assert blocked.status_code == 403 and blocked.get_json()["error"]["code"] == "password_change_required"
    assert fresh.get("/expenses").headers["Location"].endswith("/app/")
    assert fresh.post("/api/auth/password", json={"currentPassword": "Temp-Reset-Pass-9", "newPassword": "My-Own-Pass-55"}).status_code == 204
    me = fresh.get("/api/auth/me").get_json()["user"]
    assert me["mustChangePassword"] is False
    assert fresh.get("/expenses").headers["Location"] == "/app/staff/expenses"


def test_own_password_change_keeps_this_session_and_ends_others(app_module, temp_account):
    here, _ = api_login(app_module, "sec_staff", "Sec-Staff-Pass-1")
    elsewhere, _ = api_login(app_module, "sec_staff", "Sec-Staff-Pass-1")
    assert here.post("/api/auth/password", json={"currentPassword": "Sec-Staff-Pass-1", "newPassword": "Changed-Pass-42"}).status_code == 204
    assert here.get("/api/auth/me").status_code == 200
    assert elsewhere.get("/api/auth/me").status_code == 401


@pytest.mark.parametrize("field,age", [("seen", 61 * 60 * 24), ("iat", 13 * 3600)])
def test_idle_and_absolute_expiry(app_module, temp_account, field, age):
    client, _ = api_login(app_module, "sec_staff", "Sec-Staff-Pass-1")
    with client.session_transaction() as sess:
        sess[field] = time.time() - age
        if field == "iat":
            sess["seen"] = time.time()
    assert client.get("/api/auth/me").status_code == 401


def test_forged_session_version_is_refused(app_module, temp_account):
    client, _ = api_login(app_module, "sec_staff", "Sec-Staff-Pass-1")
    with client.session_transaction() as sess:
        sess["sv"] = sess["sv"] - 1
    assert client.get("/api/auth/me").status_code == 401


# ------------------------------------------------------------------ sign-in throttling and generic errors
def test_login_errors_are_generic(app_module, temp_account):
    _, unknown = api_login(app_module, "no_such_user", "Whatever-Pass-1")
    _, wrong = api_login(app_module, "sec_staff", "Wrong-Pass-123")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.get_json()["error"]["message"] == wrong.get_json()["error"]["message"] == "Invalid username or password."


def test_login_is_throttled_after_repeated_failures(app_module, temp_account):
    for _ in range(5):
        assert api_login(app_module, "sec_staff", "Wrong-Pass-123")[1].status_code == 401
    _, locked = api_login(app_module, "sec_staff", "Sec-Staff-Pass-1")          # correct password, still locked
    assert locked.status_code == 429 and int(locked.headers["Retry-After"]) > 0
    # Other accounts from the same client are not locked by this account's failures.
    assert api_login(app_module, SA_USERNAME, SA_PASSWORD)[1].status_code == 200


def test_throttle_is_bounded():
    from app.core.security import LoginThrottle
    t = LoginThrottle(per_account=3, max_keys=50)
    for i in range(500):
        t.failure(f"user{i}", "10.0.0.9")
    assert len(t._failures) <= 50


# ------------------------------------------------------------------ password policy everywhere
@pytest.mark.parametrize("password", ["short", "password123", "aaaaaaaaaaaa", "sec_staff_2026x"])
def test_password_policy_on_create_reset_and_change(app_module, temp_account, password):
    admin, _ = api_login(app_module, SA_USERNAME, SA_PASSWORD)
    assert admin.post("/api/admin/users", json={"username": "sec_staff_2026", "password": password.replace("sec_staff", "sec_staff_2026"), "role": "staff"}).status_code == 400
    assert admin.post(f"/api/admin/users/{temp_account}/password", json={"newPassword": password}).status_code == 400
    me, _ = api_login(app_module, "sec_staff", "Sec-Staff-Pass-1")
    assert me.post("/api/auth/password", json={"currentPassword": "Sec-Staff-Pass-1", "newPassword": password}).status_code == 400


def test_classic_user_creation_uses_the_policy(app_module, superadmin):
    superadmin.post("/users", data={"username": "weak_one", "password": "12345678", "role": "staff"})
    assert count(app_module, app_module.User, username="weak_one") == 0


def test_old_default_password_must_be_changed(app_module):
    m = app_module
    with m.app.app_context():
        u = m.User(username="old_default", password_hash=generate_password_hash("admin123"), role="admin", active=True)
        m.db.session.add(u); m.db.session.commit()
        uid = u.id
    try:
        _, resp = api_login(m, "old_default", "admin123")
        assert resp.get_json()["user"]["mustChangePassword"] is True
    finally:
        with m.app.app_context():
            m.db.session.delete(m.db.session.get(m.User, uid)); m.db.session.commit()


# ------------------------------------------------------------------ configuration
def run_py(code, **env):
    full = {k: v for k, v in os.environ.items() if k not in ("SECRET_KEY", "APP_ENV")}
    full.update(env)
    return subprocess.run([PY, "-c", code], cwd=_ROOT, env=full, capture_output=True, text=True, timeout=120)


@pytest.mark.parametrize("key", ["", "change-me", "cityland9-v10-change-this-secret", "short-key-123"])
def test_production_refuses_weak_secret_key(key):
    r = run_py("import sys; sys.path.insert(0,'backend'); import legacy_app", SECRET_KEY=key, DATABASE_URL="sqlite://")
    assert r.returncode != 0 and "SECRET_KEY" in r.stderr
    if key:
        assert key not in r.stderr  # the value itself is never echoed


def test_development_mode_must_be_explicit():
    from app.core import security
    assert security.check_secret_key("x" * 40 + "abcdefghij") and security.environment() == "production"
    r = run_py("import sys; sys.path.insert(0,'backend'); import legacy_app; print('ok')", SECRET_KEY="", APP_ENV="development",
               DATABASE_URL="sqlite://")
    assert r.returncode == 0 and "ok" in r.stdout


def test_fresh_database_gets_no_default_admin(tmp_path):
    db = "sqlite:///" + str(tmp_path / "fresh.db").replace("\\", "/")
    r = run_py("import sys; sys.path.insert(0,'backend'); import legacy_app as m; m.init_db();"
               "\nwith m.app.app_context(): print('users', m.User.query.count())",
               SECRET_KEY=os.environ["SECRET_KEY"], DATABASE_URL=db)
    assert r.returncode == 0, r.stderr
    assert "users 0" in r.stdout and "admin123" not in r.stdout + r.stderr
    assert "create_admin.py" in r.stdout


def test_debugger_refused_on_network_address(tmp_path):
    db = "sqlite:///" + str(tmp_path / "dev.db").replace("\\", "/")
    full = {k: v for k, v in os.environ.items() if k not in ("SECRET_KEY",)}
    full.update(APP_ENV="development", FLASK_DEBUG="1", FLASK_HOST="0.0.0.0", DATABASE_URL=db, DB_ENGINE="sqlite")
    r = subprocess.run([PY, os.path.join("backend", "run.py")], cwd=_ROOT, env=full, capture_output=True, text=True, timeout=120)
    assert r.returncode != 0 and "Refusing to start the Flask debugger" in r.stderr


# ------------------------------------------------------------------ SMTP password
def test_smtp_password_is_encrypted_and_never_rendered(app_module, superadmin):
    # Rates & Rules (incl. SMTP) moved to the React app: /api/admin/rates. Same rules as before.
    m = app_module
    api = _api_csrf(superadmin)
    assert api.put("/api/admin/rates", json={"smtpHost": "mail.lan", "smtpSender": "soa@cityland9.ph",
                                             "smtpPassword": "Mail-Secret-991"}).status_code == 200
    with m.app.app_context():
        stored = m.Setting.query.filter_by(key="smtp_password").first().value
        assert stored.startswith("enc:v1:") and "Mail-Secret-991" not in stored
        assert m.get_smtp_password() == "Mail-Secret-991"
    body = api.get("/api/admin/rates").get_data(as_text=True)
    assert "Mail-Secret-991" not in body and stored not in body and '"smtpPasswordSet":true' in body.replace(" ", "")
    api.put("/api/admin/rates", json={"smtpHost": "mail.lan", "smtpPassword": ""})          # blank keeps it
    with m.app.app_context():
        assert m.get_smtp_password() == "Mail-Secret-991"
    api.put("/api/admin/rates", json={"smtpHost": "mail.lan", "smtpPasswordClear": True})   # explicit removal
    with m.app.app_context():
        assert m.get_smtp_password() == ""


# ------------------------------------------------------------------ headers
def test_security_headers_and_no_store(superadmin):
    resp = superadmin.get("/api/auth/me")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert resp.headers["Cache-Control"] == "no-store"


# ------------------------------------------------------------------ console administrator tool
def test_create_admin_console_tool(app_module, monkeypatch):
    import getpass
    import importlib
    sys.path.insert(0, os.path.join(_ROOT, "database"))
    answers = iter(["Console-Admin-77", "Console-Admin-77"])
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(answers))
    monkeypatch.setattr("builtins.input", lambda prompt="": "console_admin")
    monkeypatch.setattr(sys, "argv", ["create_admin.py"])
    monkeypatch.setattr(sys, "stdin", type("Console", (), {"isatty": staticmethod(lambda: True)})())
    tool = importlib.import_module("create_admin")
    tool.main()
    m = app_module
    with m.app.app_context():
        u = m.User.query.filter_by(username="console_admin").first()
        assert u and u.role == "super_admin" and not u.must_change_password
        assert m.AuditLog.query.filter(m.AuditLog.action.like("%console_admin created%")).count() == 1
        m.db.session.delete(u); m.db.session.commit()


# ------------------------------------------------------------------ health checks and limits
def test_health_and_readiness(app_module):
    client = app_module.app.test_client()
    assert client.get("/healthz").get_json() == {"status": "ok"}
    resp = client.get("/readyz")
    data = resp.get_json()
    assert resp.status_code == 200 and data["checks"]["database"]["ok"] is True
    assert "backup" in data["checks"] and "disk" in data["checks"]
    assert "password" not in resp.get_data(as_text=True).lower()


def test_upload_size_limit(app_module, superadmin):
    import io
    big = io.BytesIO(b"0" * (app_module.app.config["MAX_CONTENT_LENGTH"] + 1))
    resp = _api_csrf(superadmin).post("/api/admin/system/import", data={"file": (big, "huge.xlsx")}, content_type="multipart/form-data")
    assert resp.status_code == 413


def test_non_workbook_upload_is_rejected(app_module, superadmin):
    import io
    with app_module.app.app_context():
        units_before = app_module.Unit.query.count()
    resp = _api_csrf(superadmin).post("/api/admin/system/import", data={"file": (io.BytesIO(b"not a zip"), "fake.xlsx")},
                                      content_type="multipart/form-data")
    assert resp.status_code == 400 and "not a valid Excel" in resp.get_json()["error"]["message"]
    with app_module.app.app_context():
        assert app_module.Unit.query.count() == units_before


def test_import_row_limit(app_module, superadmin, monkeypatch):
    import io
    from openpyxl import Workbook
    monkeypatch.setattr(app_module, "IMPORT_MAX_ROWS", 3)
    wb = Workbook(); ws = wb.active; ws.title = "Expenses"
    ws.append(["expense_date", "category", "description", "amount"])
    for i in range(5):
        ws.append(["2033-01-01", "Supplies", f"row {i}", 1])
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    with app_module.app.app_context():
        before = app_module.Expense.query.count()
    resp = _api_csrf(superadmin).post("/api/admin/system/import", data={"file": (buf, "rows.xlsx")}, content_type="multipart/form-data")
    with app_module.app.app_context():
        assert app_module.Expense.query.count() == before
    assert resp.status_code == 400 and "more than 3 rows" in resp.get_json()["error"]["message"]
