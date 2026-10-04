"""Resident Accounts API (/api/admin/resident-accounts) and the retired classic /resident-users page.

Superadmin only (permission key resident_users). Create links a resident login to a unit and,
optionally, a current owner/tenant; update changes link/name/status; reset sets a temporary
password. No delete. The classic page only redirects and a form from an old tab changes nothing.
"""
import pytest
from werkzeug.security import check_password_hash, generate_password_hash

from conftest import PASSWORD, SA_PASSWORD, SA_USERNAME

BASE = "/api/admin/resident-accounts"
APP_URL = "/app/superadmin/resident-accounts"
NEW_PW = "Resident-Temp-Pass-1"


def api_client(app_module, username, password):
    client = app_module.app.test_client()
    token = client.get("/api/auth/csrf").get_json()["csrfToken"]
    resp = client.post("/api/auth/login", json={"username": username, "password": password}, headers={"X-CSRFToken": token})
    assert resp.status_code == 200, resp.get_json()
    client.environ_base["HTTP_X_CSRFTOKEN"] = client.get("/api/auth/csrf").get_json()["csrfToken"]
    return client


@pytest.fixture
def sa(app_module):
    return api_client(app_module, SA_USERNAME, SA_PASSWORD)


@pytest.fixture
def unit_with_people(app_module):
    """An active unit with a current owner, a current tenant and a moved-out tenant; removed afterwards."""
    m = app_module
    created = []
    with m.app.app_context():
        unit = m.Unit(unit_no="RA-TEST-01", floor="9", unit_type="Residential", area_sqm=30, active=True)
        m.db.session.add(unit); m.db.session.flush()
        owner = m.Owner(unit_id=unit.id, owner_name="Olivia Owner", status="Current")
        tenant = m.Tenant(unit_id=unit.id, tenant_name="Tomas Tenant", status="Current")
        gone = m.Tenant(unit_id=unit.id, tenant_name="Gina Gone", status="Moved Out")
        m.db.session.add_all([owner, tenant, gone]); m.db.session.commit()
        ids = {"unit": unit.id, "owner": owner.id, "tenant": tenant.id, "gone": gone.id}
    yield ids
    with m.app.app_context():
        for p in m.ResidentProfile.query.filter_by(unit_id=ids["unit"]).all():
            u = p.user
            m.db.session.delete(p); m.db.session.flush()
            if u:
                m.db.session.delete(u)
        for model, key in ((m.Owner, "owner"), (m.Tenant, "tenant"), (m.Tenant, "gone")):
            obj = m.db.session.get(model, ids[key])
            if obj:
                m.db.session.delete(obj)
        m.db.session.flush()
        m.db.session.delete(m.db.session.get(m.Unit, ids["unit"]))
        m.db.session.commit()


def create(sa, ids, username="ra.owner", **extra):
    body = {"username": username, "password": NEW_PW, "unitId": ids["unit"], "personType": "Owner",
            "personId": ids["owner"], **extra}
    return sa.post(BASE, json=body)


def test_only_superadmin_may_use_it(app_module):
    for role in ("admin", "manager", "staff", "accounting", "resident"):
        client = api_client(app_module, f"test_{role}", PASSWORD)
        assert client.get(BASE).status_code == 403, role
        assert client.post(BASE, json={}).status_code == 403, role
    assert app_module.app.test_client().get(BASE).status_code == 401


def test_create_linked_account_and_list_it(app_module, sa, unit_with_people):
    resp = create(sa, unit_with_people)
    assert resp.status_code == 201, resp.get_json()
    acc = resp.get_json()["account"]
    assert acc["displayName"] == "Olivia Owner"          # defaults to the owner's name
    assert acc["linked"] and acc["status"] == "active" and acc["mustChangePassword"]
    assert acc["unit"]["unitNo"] == "RA-TEST-01"
    with app_module.app.app_context():
        u = app_module.db.session.get(app_module.User, acc["id"])
        assert u.role == "resident" and u.resident_profile.person_id == unit_with_people["owner"]
    data = sa.get(f"{BASE}?q=olivia").get_json()
    assert [a["username"] for a in data["accounts"]] == ["ra.owner"]
    assert "pbkdf2" not in str(data) and "scrypt" not in str(data)
    # The new resident can sign in to the portal (temporary password -> must change it).
    login = api_client(app_module, "ra.owner", NEW_PW).get("/api/auth/me").get_json()["user"]
    assert login["unitId"] == unit_with_people["unit"]


def test_create_validation(sa, unit_with_people):
    ids = unit_with_people
    errs = sa.post(BASE, json={"username": "x", "password": "short", "unitId": 999999}).get_json()["error"]["fields"]
    assert {"username", "password", "unitId"} <= set(errs)
    # moved-out tenant, wrong type, missing name
    f = create(sa, ids, "ra.gone", personType="Tenant", personId=ids["gone"]).get_json()["error"]["fields"]
    assert "no longer a current tenant" in f["personId"]
    f = create(sa, ids, "ra.nobody", personId=None).get_json()["error"]["fields"]
    assert "displayName" in f
    assert create(sa, ids).status_code == 201
    f = create(sa, ids, "RA.OWNER").get_json()["error"]["fields"]           # username case-insensitive
    assert "already taken" in f["username"]
    f = create(sa, ids, "ra.owner2").get_json()["error"]["fields"]          # one account per owner record
    assert "already has a portal account" in f["personId"]
    # Unlinked account with a typed name is allowed (shows as not linked to a person).
    resp = create(sa, ids, "ra.family", personId=None, displayName="Family Member")
    assert resp.status_code == 201 and not resp.get_json()["account"]["linked"]


def test_options_list_current_people_and_existing_accounts(sa, unit_with_people):
    assert create(sa, unit_with_people).status_code == 201
    unit = next(u for u in sa.get(f"{BASE}/options").get_json()["units"] if u["id"] == unit_with_people["unit"])
    names = {p["name"]: p for p in unit["people"]}
    assert set(names) == {"Olivia Owner", "Tomas Tenant"}            # moved-out tenant not offered
    assert names["Olivia Owner"]["account"] == "ra.owner" and names["Tomas Tenant"]["account"] is None


def test_deactivate_reactivate_and_relink(app_module, sa, unit_with_people):
    ids = unit_with_people
    uid = create(sa, ids).get_json()["account"]["id"]
    resident = api_client(app_module, "ra.owner", NEW_PW)
    resp = sa.patch(f"{BASE}/{uid}", json={"active": False, "reason": "Sold the unit"})
    assert resp.status_code == 200 and resp.get_json()["account"]["status"] == "inactive"
    assert resident.get("/api/auth/me").status_code == 401                    # signed out everywhere
    assert sa.patch(f"{BASE}/{uid}", json={"active": False}).status_code == 400   # nothing to change
    assert sa.patch(f"{BASE}/{uid}", json={"active": True}).get_json()["account"]["status"] == "active"
    # Re-link to the tenant record.
    acc = sa.patch(f"{BASE}/{uid}", json={"unitId": ids["unit"], "personType": "Tenant", "personId": ids["tenant"],
                                          "displayName": "Tomas Tenant"}).get_json()["account"]
    assert acc["personType"] == "Tenant" and acc["linkedName"] == "Tomas Tenant"
    with app_module.app.app_context():
        log = app_module.AuditLog.query.filter(app_module.AuditLog.action.like("%resident portal user ra.owner: deactivated%")).first()
        assert log is not None and log.reason == "Sold the unit"


def test_moved_out_person_ends_access_and_shows_reason(app_module, sa, unit_with_people):
    ids = unit_with_people
    uid = create(sa, ids, "ra.tenant", personType="Tenant", personId=ids["tenant"]).get_json()["account"]["id"]
    with app_module.app.app_context():
        app_module.db.session.get(app_module.Tenant, ids["tenant"]).status = "Moved Out"
        app_module.db.session.commit()
    acc = next(a for a in sa.get(f"{BASE}?status=ended").get_json()["accounts"] if a["id"] == uid)
    assert "no longer listed as a current tenant" in acc["statusReason"]
    # Renaming keeps the (moved-out) link instead of failing on it.
    assert sa.patch(f"{BASE}/{uid}", json={"unitId": ids["unit"], "personType": "Tenant", "personId": ids["tenant"],
                                           "displayName": "T. Tenant"}).status_code == 200


def test_resident_login_without_profile_is_listed_and_can_be_linked(app_module, sa, unit_with_people):
    m = app_module
    with m.app.app_context():
        u = m.User(username="ra.orphan", password_hash=generate_password_hash("Orphan-Pass-1"), role="resident", active=True)
        m.db.session.add(u); m.db.session.commit()
        uid = u.id
    try:
        acc = next(a for a in sa.get(f"{BASE}?status=unlinked").get_json()["accounts"] if a["id"] == uid)
        assert acc["unit"] is None
        assert sa.patch(f"{BASE}/{uid}", json={"displayName": "X"}).status_code == 400      # unit required
        acc = sa.patch(f"{BASE}/{uid}", json={"unitId": unit_with_people["unit"], "personType": "Tenant",
                                              "personId": unit_with_people["tenant"]}).get_json()["account"]
        assert acc["status"] == "active" and acc["linkedName"] == "Tomas Tenant"
    finally:
        with m.app.app_context():
            u = m.db.session.get(m.User, uid)
            if u and not u.resident_profile:
                m.db.session.delete(u); m.db.session.commit()


def test_reset_password_is_temporary(app_module, sa, unit_with_people):
    uid = create(sa, unit_with_people).get_json()["account"]["id"]
    assert sa.post(f"{BASE}/{uid}/password", json={"newPassword": "x"}).status_code == 400
    assert sa.post(f"{BASE}/{uid}/password", json={"newPassword": "Fresh-Resident-Pass-2"}).status_code == 204
    with app_module.app.app_context():
        u = app_module.db.session.get(app_module.User, uid)
        assert check_password_hash(u.password_hash, "Fresh-Resident-Pass-2") and u.must_change_password
    # Staff accounts aren't managed here.
    with app_module.app.app_context():
        staff_id = app_module.User.query.filter_by(username="test_staff").first().id
    assert sa.post(f"{BASE}/{staff_id}/password", json={"newPassword": "Fresh-Resident-Pass-2"}).status_code == 404


def test_requires_csrf(app_module, sa):
    sa.environ_base.pop("HTTP_X_CSRFTOKEN")
    assert sa.post(BASE, json={}).status_code in (400, 403)


def test_classic_page_redirects_and_old_forms_change_nothing(app_module, superadmin, unit_with_people):
    resp = superadmin.get("/resident-users")
    assert resp.status_code == 302 and resp.headers["Location"].endswith(APP_URL)
    with app_module.app.app_context():
        before = app_module.User.query.count()
    resp = superadmin.post("/resident-users", data={"username": "classic_res", "password": NEW_PW,
                                                    "unit_id": unit_with_people["unit"], "display_name": "Classic"})
    assert resp.status_code == 303 and resp.headers["Location"].endswith(f"{APP_URL}?moved=form")
    with app_module.app.app_context():
        assert app_module.User.query.count() == before
    html = superadmin.get("/change-password").get_data(as_text=True)
    assert f'href="{APP_URL}"' in html and 'href="/resident-users"' not in html
