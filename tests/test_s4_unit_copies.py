"""S4: owner/tenant details are read from the owner and tenant records, not from copies on the unit."""
from conftest import PASSWORD, api, login


def unit_nos(client, q):
    return [u["unitNo"] for u in client.get(f"/api/units?q={q}&perPage=100").get_json()["units"]]


def ids(m, unit_no):
    with m.app.app_context():
        u = m.Unit.query.filter_by(unit_no=unit_no).first()
        return u.id, u.current_owner.id


def test_unit_shows_the_current_owner_and_tenant(app_module):
    m = app_module
    with m.app.app_context():
        u = m.Unit.query.filter_by(unit_no="TEST-502").first()
        assert (u.owner_name, u.contact_no, u.email) == ("Maria Santos", "09170000002", "maria.owner@example.com")
        assert u.tenant_name == "Carlos Santos"
        assert m.Unit.query.filter_by(unit_no="P-TEST-01").first().owner_name is None


def test_units_search_finds_current_tenants(app_module):
    """The tenant copy was never filled for TEST-502, so searching 'Carlos' found nothing before S4."""
    m = app_module
    admin = api(login(m, "test_admin", PASSWORD)[0])
    assert "TEST-502" in unit_nos(admin, "Carlos")
    found = unit_nos(admin, "Pedro")
    assert "TEST-503" in found and "TEST-502" not in found


def test_edit_unit_keeps_the_owner(app_module):
    """The Edit Unit form has no owner fields; saving it used to blank the owner copy."""
    m = app_module
    uid, _ = ids(m, "TEST-504")
    client = api(login(m, "superadmin", "Test-Admin-Pass-1")[0])
    assert client.put(f"/api/units/{uid}", json={"floor": "6F", "type": "3 BEDROOM", "areaSqm": "80", "ratePerSqm": "125",
                                                 "duesMode": "per_sqm", "manualMonthlyDues": "0", "status": "Occupied"}).status_code == 200
    assert client.get("/api/units?q=TEST-504").get_json()["units"][0]["ownerName"] == "Ana Garcia"


def test_owner_edits_show_everywhere_and_past_owners_drop_out(app_module):
    m = app_module
    uid, oid = ids(m, "TEST-503")
    client = api(login(m, "superadmin", "Test-Admin-Pass-1")[0])
    form = {"name": "Pedro Reyes Jr.", "contactNo": "0917", "email": "pedro.jr@example.com", "status": "Current"}
    url = f"/api/units/{uid}/owners/{oid}"
    assert client.put(url, json=form).status_code == 200
    assert client.get("/api/units?q=Reyes").get_json()["units"][0]["ownerName"] == "Pedro Reyes Jr."
    assert client.put(url, json={**form, "status": "Past"}).status_code == 200
    with m.app.app_context():
        assert m.db.session.get(m.Unit, uid).owner_name is None
    assert "TEST-503" not in unit_nos(client, "Reyes")
    assert client.put(url, json={**form, "name": "Pedro Reyes", "email": "pedro.test@example.com", "contactNo": "09170000003"}).status_code == 200


def test_billing_search_uses_the_owner_record(app_module):
    m = app_module
    admin = api(login(m, "superadmin", "Test-Admin-Pass-1")[0])
    admin.post("/api/billing/generate", json={"month": "2035-01"})
    bills = {b["unitNo"]: b for b in admin.get("/api/billing?month=2035-01").get_json()["bills"]}
    assert bills["TEST-504"]["payerName"] == "Ana Garcia" and bills["TEST-501"]["payerName"] != "Ana Garcia"
