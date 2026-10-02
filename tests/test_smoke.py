"""Smoke tests: every page opens, every role lands somewhere valid, no redirect loops.

Run from the project folder:
    python -m pytest -q
"""
import io
import re

import pytest

from conftest import PASSWORD, ROLES, login


def static_get_pages(app_module):
    """All legacy GET pages that take no URL parameters (except login/logout/static).

    The JSON API and the React app route have their own tests (test_api_auth.py).
    """
    return sorted(
        r.rule for r in app_module.app.url_map.iter_rules()
        if "GET" in r.methods and not r.arguments and r.endpoint not in ("static", "login", "logout")
        and not r.endpoint.startswith(("api_auth.", "react_app."))
    )


def follow(client, url, max_hops=5):
    """Follow redirects manually so a redirect loop fails the test instead of hanging."""
    for _ in range(max_hops):
        resp = client.get(url)
        if resp.status_code != 302:
            return resp, url
        url = resp.headers["Location"]
    pytest.fail(f"redirect loop starting from {url}")


def test_superadmin_every_page_opens(app_module, superadmin):
    failures = []
    for url in static_get_pages(app_module):
        resp, _ = follow(superadmin, url)
        if resp.status_code != 200:
            failures.append((url, resp.status_code))
    assert not failures, failures


def test_detail_pages_open(app_module, superadmin):
    with app_module.app.app_context():
        unit = app_module.Unit.query.filter(app_module.Unit.unit_no.like("TEST-%")).first()
        bill = app_module.Billing.query.first()
        reading = app_module.WaterReading.query.first()
    for url in (f"/unit/{unit.id}", f"/billing/{bill.id}", f"/billing/{bill.id}/qr", f"/water/{reading.id}/edit"):
        assert superadmin.get(url).status_code == 200, url


@pytest.mark.parametrize("role", ROLES)
def test_role_lands_on_accessible_page(app_module, role):
    client, landing = login(app_module, f"test_{role}", PASSWORD)
    resp, final = follow(client, landing)
    assert resp.status_code == 200, f"{role} landed on {final} with {resp.status_code}"


@pytest.mark.parametrize("role", ROLES)
def test_role_never_loops_on_forbidden_pages(app_module, role):
    client, _ = login(app_module, f"test_{role}", PASSWORD)
    for url in static_get_pages(app_module):
        resp, final = follow(client, url)
        assert resp.status_code in (200, 403), f"{role}: {url} -> {final} ({resp.status_code})"


@pytest.mark.parametrize("role", ["super_admin"] + ROLES)
def test_sidebar_links_are_accessible(app_module, role):
    """Every link the sidebar shows a role must actually open for that role."""
    if role == "super_admin":
        client, landing = login(app_module, "superadmin", "Test-Admin-Pass-1")
    else:
        client, landing = login(app_module, f"test_{role}", PASSWORD)
    assert landing == "/app/", f"{role} should land in the new app, not {landing}"
    # Old module pages keep the old sidebar until they are rebuilt; Change Password opens for every role.
    html = client.get("/change-password").get_data(as_text=True)
    sidebar = html.split("<aside", 1)[1].split("</aside>", 1)[0]
    links = set(re.findall(r'href="(/[^"#]*)"', sidebar))
    assert links, f"no sidebar links rendered for {role}"
    for url in links:
        resp = client.get(url)
        moved = resp.status_code == 302 and resp.headers["Location"].startswith("/app/")  # retired page -> new app
        assert resp.status_code == 200 or moved, f"{role}: sidebar link {url} returned {resp.status_code}"


def test_excel_export_import_roundtrip(app_module, superadmin):
    export = superadmin.get("/database/export.xlsx")
    assert export.status_code == 200 and export.data[:2] == b"PK"  # xlsx is a zip file
    resp = superadmin.post(
        "/database/import",
        data={"file": (io.BytesIO(export.data), "db.xlsx"), "excel_file": (io.BytesIO(export.data), "db.xlsx")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "import completed successfully" in resp.get_data(as_text=True)


def test_reports_export(superadmin):
    resp = superadmin.get("/reports/export.xlsx")
    assert resp.status_code == 200 and resp.data[:2] == b"PK"


def test_bad_login_rejected(app_module):
    client = app_module.app.test_client()
    resp = client.post("/login", data={"username": "superadmin", "password": "wrong"})
    # Not signed in; sent to the new login page with the reason.
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/app/login?notice=Invalid+username+or+password."
    assert client.get("/api/auth/me").status_code == 401


def test_anonymous_redirected_to_login(app_module):
    client = app_module.app.test_client()
    for url in ("/", "/dashboard", "/billing", "/portal"):
        resp = client.get(url)
        assert resp.status_code == 302 and "/login" in resp.headers["Location"], url
