"""Shared fixtures for the CityLand 9 smoke tests.

The tests run against a throwaway SQLite database in a temporary folder, so the
real cityland_condo_web.db (or SQL Server, if .env points there) is never touched.
"""
import os
import sys
import tempfile
import warnings

import secrets

import pytest
from flask.testing import FlaskClient

# Must be set BEFORE legacy_app is imported, because it reads DATABASE_URL at import time.
_TMP_DIR = tempfile.mkdtemp(prefix="cityland9_tests_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP_DIR, "test.db").replace("\\", "/")
# Production rules apply in tests too: a strong, random secret key (never a real one).
os.environ["SECRET_KEY"] = __import__("secrets").token_hex(32)
os.environ.pop("APP_ENV", None)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "backend"))
warnings.filterwarnings("ignore", message=".*Decimal objects natively.*")

import legacy_app as cityland  # noqa: E402
import seed_test_data  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

ROLES = ["admin", "manager", "staff", "accounting", "resident"]
PASSWORD = "Test-Pass-123"
# No default administrator exists any more; the tests create their own Superadmin.
SA_USERNAME, SA_PASSWORD = "superadmin", "Test-Admin-Pass-1"


class CsrfClient(FlaskClient):
    """Test client that sends the session's CSRF token with classic (non-API) POSTs, exactly
    as the real pages do (the server injects it into every form). Set auto_csrf = False to
    test that requests WITHOUT a token are rejected."""
    auto_csrf = True

    def open(self, *args, **kwargs):
        method = (kwargs.get("method") or "GET").upper()
        path = args[0] if args and isinstance(args[0], str) else kwargs.get("path", "/")
        if self.auto_csrf and method in ("POST", "PUT", "PATCH", "DELETE") and not str(path).startswith("/api/"):
            headers = dict(kwargs.get("headers") or {})
            headers.setdefault("X-CSRFToken", self.csrf_token())
            kwargs["headers"] = headers
            # Like a freshly rendered form: a new one-time form token (payment routes require one).
            data = kwargs.get("data")
            if isinstance(data, dict) and "form_token" not in data:
                kwargs["data"] = {**data, "form_token": secrets.token_urlsafe(24)}
        return super().open(*args, **kwargs)

    def csrf_token(self):
        with self.session_transaction() as sess:
            if "csrf_token" not in sess:
                sess["csrf_token"] = secrets.token_urlsafe(32)
            return sess["csrf_token"]


@pytest.fixture(scope="session")
def app_module():
    cityland.app.config["TESTING"] = True
    cityland.app.test_client_class = CsrfClient
    # Excel-import backups are written to the project root; keep them in the temp folder instead.
    cityland.BASE_DIR = _TMP_DIR
    cityland.init_db()
    seed_test_data.add_if_missing()
    with cityland.app.app_context():
        if not cityland.User.query.filter_by(username=SA_USERNAME).first():
            cityland.db.session.add(cityland.User(username=SA_USERNAME, password_hash=generate_password_hash(SA_PASSWORD),
                                                  role="super_admin", active=True))
        unit_id = cityland.Unit.query.filter(cityland.Unit.unit_no.like("TEST-%")).first().id
        for role in ROLES:
            if cityland.User.query.filter_by(username=f"test_{role}").first():
                continue
            user = cityland.User(username=f"test_{role}", password_hash=generate_password_hash(PASSWORD), role=role, active=True)
            cityland.db.session.add(user)
            cityland.db.session.flush()
            if role == "resident":
                cityland.db.session.add(cityland.ResidentProfile(user_id=user.id, unit_id=unit_id, person_type="Owner", display_name="Test Resident"))
        cityland.db.session.commit()
    return cityland


def login(app_module, username, password):
    client = app_module.app.test_client()
    resp = client.post("/login", data={"username": username, "password": password})
    assert resp.status_code == 302, f"login failed for {username}"
    return client, resp.headers["Location"]


@pytest.fixture
def superadmin(app_module):
    client, _ = login(app_module, SA_USERNAME, SA_PASSWORD)
    return client
