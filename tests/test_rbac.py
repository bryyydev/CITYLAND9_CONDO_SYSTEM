"""Role-based access control: the permission matrix is enforced on every legacy page,
on the REST API, and residents can only ever reach their own unit (bug D2 / IDOR)."""
import re

import pytest

from conftest import api, PASSWORD, ROLES, login

from app.core.permissions import ANY_SIGNED_IN, PERMISSIONS, PUBLIC_ENDPOINTS, can
from app.core.roles import ALL_ROLES

PUBLIC = {"static", "login", "logout", "index"} | PUBLIC_ENDPOINTS


def legacy_endpoints(app_module):
    return {r.endpoint for r in app_module.app.url_map.iter_rules()
            if not r.endpoint.startswith(("api_", "react_app."))} - PUBLIC


def client_for(app_module, role):
    if role == "super_admin":
        return login(app_module, "superadmin", "Test-Admin-Pass-1")[0]
    return login(app_module, f"test_{role}", PASSWORD)[0]


def api_client(app_module, role):
    client = client_for(app_module, role)
    token = client.get("/api/auth/csrf").get_json()["csrfToken"]
    return client, token


def units(app_module):
    with app_module.app.app_context():
        own = app_module.User.query.filter_by(username="test_resident").first().resident_profile.unit_id
        other = app_module.Unit.query.filter(app_module.Unit.unit_no == "TEST-502").first().id
        other_bill = app_module.Billing.query.filter_by(unit_id=other).first().id
    return own, other, other_bill


# ---- the matrix covers everything --------------------------------------------------------
def test_every_legacy_page_has_a_matrix_entry(app_module):
    missing = sorted(e for e in legacy_endpoints(app_module) if e not in PERMISSIONS and e not in ANY_SIGNED_IN)
    assert not missing, f"endpoints with no permission entry (they would be denied to everyone): {missing}"


def test_matrix_only_uses_known_roles():
    assert all(roles <= set(ALL_ROLES) for roles in PERMISSIONS.values())


@pytest.mark.parametrize("role", ["super_admin"] + ROLES)
def test_legacy_pages_follow_the_matrix(app_module, role):
    client = client_for(app_module, role)
    rules = [r for r in app_module.app.url_map.iter_rules()
             if "GET" in r.methods and not r.arguments and r.endpoint in legacy_endpoints(app_module)]
    wrong = []
    for rule in rules:
        resp = client.get(rule.rule)
        allowed = can(role, rule.endpoint)
        # Retired old pages (e.g. the resident portal) forward to the new app instead of rendering.
        moved = resp.status_code == 302 and resp.headers["Location"].startswith("/app/")
        ok = (resp.status_code == 200 or moved) if allowed else resp.status_code in (302, 403)
        if not ok:
            wrong.append((rule.rule, "expected allowed" if allowed else "expected denied", resp.status_code))
    assert not wrong, wrong


# ---- specific requirements -----------------------------------------------------------------
def test_d2_resident_cannot_open_any_unit_page(app_module):
    own, other, _ = units(app_module)
    client = client_for(app_module, "resident")
    for uid in (other, own):  # the admin unit page is not part of the resident portal at all
        resp = client.get(f"/unit/{uid}")
        assert resp.status_code == 302 and "/unit/" not in resp.headers["Location"] and "/units" not in resp.headers["Location"]
        assert api(client).get(f"/api/units/{uid}").status_code == 403
    assert api(client_for(app_module, "admin")).get(f"/api/units/{other}").status_code == 200


def test_d3_admin_can_edit_units_again(app_module):
    _, other, _ = units(app_module)
    client = api(client_for(app_module, "admin"))
    resp = client.put(f"/api/units/{other}", json={"floor": "5", "type": "1 BEDROOM", "areaSqm": "50",
                                                   "status": "Occupied", "occupancy": "Tenant"})
    assert resp.status_code == 200 and resp.get_json()["unit"]["floor"] == "5"


def test_accounting_has_billing_read_write_but_not_soa_email(app_module):
    _, other, other_bill = units(app_module)
    client = api(client_for(app_module, "accounting"))
    assert client.get("/api/billing").status_code == 200
    assert client.get(f"/api/billing/{other_bill}").status_code == 200
    me = client.get("/api/auth/me").get_json()["user"]["permissions"]
    assert "edit_soa" in me and "email_bill" not in me and "send_billing_emails" not in me   # buttons follow the matrix
    assert client.post(f"/api/billing/{other_bill}/email").status_code == 403
    assert client.get("/api/billing/email").status_code == 403
    assert client.get("/api/advances").status_code == 200                               # Advance Payments (React)


def test_staff_cannot_see_financial_reports_or_audit_logs(app_module):
    client = client_for(app_module, "staff")
    for url in ("/reports", "/reports/export.xlsx", "/audit"):
        assert client.get(url).status_code == 302, url
    assert client.get("/api/certificates").status_code == 200
    assert client.get("/api/certificates/options").status_code == 200


def test_legacy_sidebar_shows_only_permitted_links(app_module):
    for role in ["super_admin"] + ROLES:
        client = client_for(app_module, role)
        home = client.get("/change-password").get_data(as_text=True)
        sidebar = home.split("<aside", 1)[1].split("</aside>", 1)[0]
        for href in set(re.findall(r'href="(/[^"#]*)"', sidebar)):
            resp = client.get(href)
            moved = resp.status_code == 302 and resp.headers["Location"].startswith("/app/")  # retired page -> new app
            assert resp.status_code == 200 or moved, (role, href)


# ---- REST API: 401 / 403 JSON --------------------------------------------------------------------
def test_api_returns_401_when_signed_out_and_403_json_for_wrong_role(app_module):
    own, _, _ = units(app_module)
    resp = app_module.app.test_client().get(f"/api/resident/units/{own}/summary")
    assert resp.status_code == 401 and resp.is_json
    client, _ = api_client(app_module, "staff")
    resp = client.get(f"/api/resident/units/{own}/summary")
    assert resp.status_code == 403 and resp.get_json()["error"]["status"] == 403


# ---- Resident portal API and IDOR ----------------------------------------------------------------
def test_resident_sees_own_summary_and_soa(app_module):
    own, _, _ = units(app_module)
    client, _ = api_client(app_module, "resident")
    summary = client.get(f"/api/resident/units/{own}/summary").get_json()
    assert summary["unit"]["id"] == own and summary["residentName"] == "Test Resident"
    statements = client.get(f"/api/resident/units/{own}/soa").get_json()["statements"]
    assert statements, "the seeded unit has bills"
    detail = client.get(f"/api/resident/units/{own}/soa/{statements[0]['id']}").get_json()["statement"]
    assert set(detail["charges"]) >= {"condoDues", "parking", "storage", "water", "penalty", "previousBalance"}
    # Same figures as the legacy billing engine
    with app_module.app.test_request_context("/"):
        bill = app_module.db.session.get(app_module.Billing, statements[0]["id"])
        assert detail["total"] == str(app_module.bill_total(bill).quantize(app_module.Decimal("0.01")))
    assert summary["outstandingBalance"] == statements[0]["balance"]


def test_idor_resident_cannot_read_another_units_data(app_module):
    own, other, other_bill = units(app_module)
    client, token = api_client(app_module, "resident")
    for path in (f"/summary", "/soa", f"/soa/{other_bill}", "/maintenance"):
        resp = client.get(f"/api/resident/units/{other}{path}")
        assert resp.status_code == 403, path
    # Another unit's bill id under the resident's OWN unit path is not found either.
    assert client.get(f"/api/resident/units/{own}/soa/{other_bill}").status_code == 404
    resp = client.post(f"/api/resident/units/{other}/maintenance", json={"title": "x", "description": "y"},
                       headers={"X-CSRFToken": token})
    assert resp.status_code == 403


def test_resident_files_maintenance_request_for_own_unit(app_module):
    own, _, _ = units(app_module)
    client, token = api_client(app_module, "resident")
    body = {"title": "Leaking faucet", "description": "Kitchen sink drips", "category": "Plumbing", "priority": "Normal"}
    assert client.post(f"/api/resident/units/{own}/maintenance", json=body).status_code == 403   # no CSRF token
    assert client.post(f"/api/resident/units/{own}/maintenance", json={**body, "priority": "ASAP"},
                       headers={"X-CSRFToken": token}).status_code == 400
    resp = client.post(f"/api/resident/units/{own}/maintenance", json=body, headers={"X-CSRFToken": token})
    assert resp.status_code == 201 and resp.get_json()["ticket"]["ticketNo"].startswith("MT-")
    titles = [t["title"] for t in client.get(f"/api/resident/units/{own}/maintenance").get_json()["tickets"]]
    assert "Leaking faucet" in titles


def test_admin_may_open_any_unit_in_the_resident_api(app_module):
    _, other, _ = units(app_module)
    client, _ = api_client(app_module, "admin")
    assert client.get(f"/api/resident/units/{other}/summary").status_code == 200


# ---- role integrity ---------------------------------------------------------------------------------
def test_unknown_role_cannot_be_saved(app_module):
    with pytest.raises(ValueError):
        app_module.User(username="x", password_hash="x", role="cashier")
    # Accounts are created through /api/admin/users only (the classic form was retired); it refuses unknown roles.
    client = client_for(app_module, "super_admin")
    token = client.get("/api/auth/csrf").get_json()["csrfToken"]
    resp = client.post("/api/admin/users", json={"username": "bad_role_user", "password": "Long-enough-Pass-1", "role": "cashier"},
                       headers={"X-CSRFToken": token})
    assert resp.status_code == 400 and resp.get_json()["error"]["fields"]["role"]
    with app_module.app.app_context():
        assert app_module.User.query.filter_by(username="bad_role_user").first() is None
