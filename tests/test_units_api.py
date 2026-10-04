"""Units Directory API (/api/units): permissions, create (with first owner/tenant), validation,
parking/storage assignment rules, owners/tenants (representative, move-out), audit, CSRF, and the
retired classic Units forms."""
from datetime import date

import pytest

from conftest import PASSWORD, SA_PASSWORD, SA_USERNAME, api, login

BASE = "/api/units"


@pytest.fixture
def sa(app_module):
    return api(login(app_module, SA_USERNAME, SA_PASSWORD)[0])


@pytest.fixture
def cleanup(app_module):
    """Units created by a test (by unit number) are removed afterwards with their people."""
    made = []
    yield made
    m = app_module
    with m.app.app_context():
        for no in made:
            u = m.Unit.query.filter_by(unit_no=no).first()
            if u:
                m.db.session.delete(u)
        m.db.session.commit()


def unit_id(m, no):
    with m.app.app_context():
        return m.Unit.query.filter_by(unit_no=no).first().id


def test_permissions(app_module):
    uid = unit_id(app_module, "TEST-501")
    for role in ("manager", "staff", "accounting", "resident"):
        client = api(login(app_module, f"test_{role}", PASSWORD)[0])
        assert client.get(BASE).status_code == 403, role
        assert client.get(f"{BASE}/{uid}").status_code == 403, role
        assert client.put(f"{BASE}/{uid}", json={}).status_code == 403, role
        assert client.post(BASE, json={}).status_code == 403, role
    assert api(login(app_module, "test_admin", PASSWORD)[0]).get(BASE).status_code == 200
    assert app_module.app.test_client().get(BASE).status_code == 401


def test_list_kinds_and_dues(sa):
    data = sa.get(f"{BASE}?perPage=100").get_json()
    assert data["counts"]["residential"] >= 4 and data["counts"]["PARKING"] >= 1
    assert all(u["type"] not in ("PARKING", "STORAGE") for u in data["units"])
    row = next(u for u in data["units"] if u["unitNo"] == "TEST-501")
    assert row["parkingUnit"]["unitNo"] == "P-TEST-01" and row["dues"]["parking"] == "1000.00"
    assert {"condo", "parking", "storage"} == set(row["dues"]) and row["latestBill"] is not None


def test_create_with_owner_and_tenant(app_module, sa, cleanup):
    cleanup.append("UT-901")
    body = {"unitNo": "UT-901", "floor": "9", "type": "STUDIO TYPE", "areaSqm": "30.5", "autoRate": True,
            "occupancy": "Tenant", "status": "Occupied",
            "owner": {"name": "Olga Owner", "email": "olga@example.com", "receiveSoaEmail": True},
            "tenant": {"name": "Tito Tenant", "moveIn": "2026-09-01"}}
    resp = sa.post(BASE, json=body)
    assert resp.status_code == 201, resp.get_json()
    u = resp.get_json()["unit"]
    assert u["ownerName"] == "Olga Owner" and u["tenantName"] == "Tito Tenant"
    assert u["effectiveRatePerSqm"] == "50" and u["dues"]["condo"] == "1525.00"      # 30.5 m2 x studio rate 50
    assert u["tenants"][0]["representative"] and u["tenants"][0]["moveIn"] == "2026-09-01"
    with app_module.app.app_context():
        assert app_module.AuditLog.query.filter_by(action="Created unit UT-901").count() == 1


def test_create_validation(sa, cleanup):
    errs = sa.post(BASE, json={"unitNo": "TEST-501", "type": "CASTLE", "areaSqm": "-1", "status": "Maybe",
                               "owner": {"name": "X", "email": "bad"}}).get_json()["error"]["fields"]
    assert {"unitNo", "type", "areaSqm", "status", "owner.email"} <= set(errs)
    # P-TEST-01 is TEST-501's parking: it can't be assigned to a second unit.
    with_parking = {"unitNo": "UT-902", "type": "1 BEDROOM", "areaSqm": "40"}
    errs = sa.post(BASE, json={**with_parking, "parkingUnitId": sa.get(f"{BASE}?kind=PARKING&q=P-TEST-01").get_json()["units"][0]["id"]}).get_json()["error"]["fields"]
    assert "already assigned to unit TEST-501" in errs["parkingUnitId"]
    residential = sa.get(f"{BASE}?q=TEST-502").get_json()["units"][0]["id"]
    assert "parkingUnitId" in sa.post(BASE, json={**with_parking, "parkingUnitId": residential}).get_json()["error"]["fields"]


def test_bedroom_types_get_their_rate_and_manual_dues_are_billed(sa, cleanup):
    """Fixed 2026-10-04: '2 BEDROOM' (as the form saves it) used to find no rate and bill ₱0; a
    residential unit's manual monthly amount used to be ignored."""
    cleanup.extend(["UT-903", "UT-908"])
    u = sa.post(BASE, json={"unitNo": "UT-903", "type": "2 BEDROOM", "areaSqm": "40", "autoRate": True}).get_json()["unit"]
    assert u["effectiveRatePerSqm"] == "100" and u["dues"]["condo"] == "4000.00" and u["zeroDues"] is False
    u = sa.post(BASE, json={"unitNo": "UT-908", "type": "1 BEDROOM", "areaSqm": "40", "duesMode": "manual",
                            "manualMonthlyDues": "2500"}).get_json()["unit"]
    assert u["dues"]["condo"] == "2500.00"


def test_edit_unit_and_audit(app_module, sa, cleanup):
    cleanup.append("UT-904")
    uid = sa.post(BASE, json={"unitNo": "UT-904", "type": "STUDIO TYPE", "areaSqm": "20"}).get_json()["unit"]["id"]
    body = {"floor": "12", "type": "STUDIO TYPE", "areaSqm": "22", "ratePerSqm": "60", "status": "Vacant", "occupancy": "Owner"}
    resp = sa.put(f"{BASE}/{uid}", json=body).get_json()
    assert resp["changed"] and resp["unit"]["dues"]["condo"] == "1320.00"
    assert sa.put(f"{BASE}/{uid}", json=body).get_json()["changed"] is False            # same values: no audit entry
    with app_module.app.app_context():
        log = app_module.AuditLog.query.filter(app_module.AuditLog.action.like("Updated unit UT-904%")).all()
        assert len(log) == 1 and "areaSqm" in log[0].details


def test_tenants_representative_and_move_out(app_module, sa, cleanup):
    cleanup.append("UT-905")
    uid = sa.post(BASE, json={"unitNo": "UT-905", "type": "STUDIO TYPE", "areaSqm": "20"}).get_json()["unit"]["id"]
    a = sa.post(f"{BASE}/{uid}/tenants", json={"name": "Ann", "representative": True}).get_json()["unit"]
    assert a["status"] == "Occupied"                                                    # classic rule
    b = sa.post(f"{BASE}/{uid}/tenants", json={"name": "Ben", "representative": True}).get_json()["unit"]
    reps = {t["name"]: t["representative"] for t in b["tenants"]}
    assert reps == {"Ann": False, "Ben": True}
    ben = next(t for t in b["tenants"] if t["name"] == "Ben")
    after = sa.put(f"{BASE}/{uid}/tenants/{ben['id']}", json={**ben, "status": "Past"}).get_json()["unit"]
    ben = next(t for t in after["tenants"] if t["name"] == "Ben")
    assert ben["status"] == "Past" and ben["moveOut"] == date.today().isoformat() and ben["representative"] is False
    back = sa.put(f"{BASE}/{uid}/tenants/{ben['id']}", json={**ben, "status": "Current"}).get_json()["unit"]
    assert next(t for t in back["tenants"] if t["name"] == "Ben")["moveOut"] is None
    errs = sa.post(f"{BASE}/{uid}/tenants", json={"name": "", "moveIn": "2026-05-01", "moveOut": "2026-04-01"}).get_json()["error"]["fields"]
    assert {"name", "moveOut"} <= set(errs)


def test_owners(sa, cleanup):
    cleanup.append("UT-906")
    uid = sa.post(BASE, json={"unitNo": "UT-906", "type": "STUDIO TYPE", "areaSqm": "20"}).get_json()["unit"]["id"]
    u = sa.post(f"{BASE}/{uid}/owners", json={"name": "Oscar", "contactNo": "0917"}).get_json()["unit"]
    assert u["ownerName"] == "Oscar"
    oscar = u["owners"][0]
    assert sa.put(f"{BASE}/{uid}/owners/{oscar['id']}", json={**oscar, "receiveSoaEmail": True}).get_json()["error"]["fields"]["email"]
    u = sa.put(f"{BASE}/{uid}/owners/{oscar['id']}", json={**oscar, "status": "Past"}).get_json()["unit"]
    assert u["ownerName"] is None and u["owners"][0]["moveOut"] == date.today().isoformat()
    assert sa.put(f"{BASE}/{uid}/owners/999999", json={"name": "x"}).status_code == 404


def test_csrf_required(sa):
    sa.environ_base.pop("HTTP_X_CSRFTOKEN")
    assert sa.post(BASE, json={"unitNo": "UT-907"}).status_code == 403


def test_classic_unit_forms_change_nothing(app_module):
    m = app_module
    client, _ = login(m, SA_USERNAME, SA_PASSWORD)
    uid = unit_id(m, "TEST-504")
    with m.app.app_context():
        before = (m.db.session.get(m.Unit, uid).floor, m.Unit.query.count(), m.Tenant.query.count())
    assert client.get("/units").headers["Location"] == "/app/superadmin/units"
    assert client.get(f"/unit/{uid}").headers["Location"] == f"/app/superadmin/units?unit={uid}"
    for url, data in ((f"/unit/{uid}/edit", {"floor": "99"}), ("/units", {"unit_no": "UT-CLASSIC"}),
                      (f"/unit/{uid}/tenant/add", {"tenant_name": "Classic"})):
        resp = client.post(url, data=data)
        assert resp.status_code == 303 and "moved=form" in resp.headers["Location"], url
    with m.app.app_context():
        assert (m.db.session.get(m.Unit, uid).floor, m.Unit.query.count(), m.Tenant.query.count()) == before
    admin, _ = login(m, "test_admin", PASSWORD)
    assert admin.get("/units").headers["Location"] == "/app/admin/units"
