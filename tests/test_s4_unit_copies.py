"""S4: owner/tenant details are read from the owner and tenant records, not from copies on the unit."""
from conftest import PASSWORD, login


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
    admin, _ = login(m, "test_admin", PASSWORD)
    assert "TEST-502" in admin.get("/units?q=Carlos").get_data(as_text=True)
    page = admin.get("/units?q=Pedro").get_data(as_text=True)
    assert "TEST-503" in page and "TEST-502" not in page


def test_edit_unit_keeps_the_owner(app_module):
    """The Edit Unit form has no owner fields; saving it used to blank the owner copy."""
    m = app_module
    uid, _ = ids(m, "TEST-504")
    client, _ = login(m, "superadmin", "admin123")
    client.post(f"/unit/{uid}/edit", data={"floor": "6F", "unit_type": "3 BEDROOM", "area_sqm": "80", "unit_rate_per_sqm": "125",
                                            "dues_mode": "per_sqm", "manual_monthly_dues": "0", "status": "Occupied"})
    assert "Ana Garcia" in client.get("/units?q=TEST-504").get_data(as_text=True)


def test_owner_edits_show_everywhere_and_past_owners_drop_out(app_module):
    m = app_module
    uid, oid = ids(m, "TEST-503")
    client, _ = login(m, "superadmin", "admin123")
    form = {"owner_name": "Pedro Reyes Jr.", "contact_no": "0917", "email": "pedro.jr@example.com", "status": "Current"}
    client.post(f"/unit/{uid}/owner/{oid}/edit", data=form)
    assert "Pedro Reyes Jr." in client.get("/units?q=Reyes").get_data(as_text=True)
    client.post(f"/unit/{uid}/owner/{oid}/edit", data={**form, "status": "Past"})
    with m.app.app_context():
        assert m.db.session.get(m.Unit, uid).owner_name is None
    assert "TEST-503" not in client.get("/units?q=Reyes").get_data(as_text=True)
    client.post(f"/unit/{uid}/owner/{oid}/edit", data={**form, "owner_name": "Pedro Reyes", "email": "pedro.test@example.com",
                                                       "contact_no": "09170000003"})


def test_billing_search_uses_the_owner_record(app_module):
    m = app_module
    admin, _ = login(m, "superadmin", "admin123")
    admin.post("/billing", data={"month": "2035-01"})
    page = admin.get("/billing?month=2035-01&q=Garcia").get_data(as_text=True)
    assert "TEST-504" in page and "TEST-501" not in page
