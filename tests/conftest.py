"""Shared fixtures for the CityLand 9 smoke tests.

The tests run against a throwaway SQLite database in a temporary folder, so the
real cityland_condo_web.db (or SQL Server, if .env points there) is never touched.
"""
import os
import sys
import tempfile
import warnings

import pytest

# Must be set BEFORE legacy_app is imported, because it reads DATABASE_URL at import time.
_TMP_DIR = tempfile.mkdtemp(prefix="cityland9_tests_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP_DIR, "test.db").replace("\\", "/")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "backend"))
warnings.filterwarnings("ignore", message=".*Decimal objects natively.*")

import legacy_app as cityland  # noqa: E402
import seed_test_data  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

ROLES = ["admin", "manager", "staff", "accounting", "resident"]
PASSWORD = "Test-Pass-123"


@pytest.fixture(scope="session")
def app_module():
    cityland.app.config["TESTING"] = True
    # Excel-import backups are written to the project root; keep them in the temp folder instead.
    cityland.BASE_DIR = _TMP_DIR
    cityland.init_db()
    seed_test_data.add_if_missing()
    with cityland.app.app_context():
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
    client, _ = login(app_module, "superadmin", "admin123")
    return client
