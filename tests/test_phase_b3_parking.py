"""Phase B3: one parking model - parking is a PARKING unit assigned to a residential unit."""
from conftest import PASSWORD, login


def unit(m, no):
    return m.Unit.query.filter_by(unit_no=no).first()


def test_parking_charge_comes_from_the_assigned_parking_unit(app_module):
    m = app_module
    with m.app.test_request_context("/"):
        assert m.parking_dues(unit(m, "TEST-501")) == 1000.0       # P-TEST-01: 10 sqm x 100
        assert m.parking_dues(unit(m, "TEST-503")) == 0.0          # no parking assigned


def test_parking_unit_without_its_own_rate_uses_the_default_parking_rate(app_module):
    m = app_module
    with m.app.test_request_context("/"):
        p = unit(m, "P-TEST-02")
        saved = p.unit_rate_per_sqm
        p.unit_rate_per_sqm = 0
        try:
            expected = round(10 * m.setting_float("parking_rate_per_sqm", 0), 2)
            assert m.parking_dues(unit(m, "TEST-502")) == expected
        finally:
            p.unit_rate_per_sqm = saved
            m.db.session.rollback()


def test_parking_page_lists_parking_units_and_who_has_them(app_module):
    m = app_module
    admin, _ = login(m, "test_admin", PASSWORD)
    page = admin.get("/parking").get_data(as_text=True)
    assert "P-TEST-01" in page and "TEST-501" in page and "1,000.00" in page
    staff, _ = login(m, "test_staff", PASSWORD)
    assert staff.get("/parking").status_code == 302          # PROPERTY = super admin / admin


def test_old_parking_lot_routes_are_gone(app_module):
    m = app_module
    admin, _ = login(m, "superadmin", "Test-Admin-Pass-1")
    with m.app.app_context():
        uid = unit(m, "TEST-501").id
    assert admin.post(f"/unit/{uid}/parking/add", data={}).status_code == 404
    assert "add_parking" not in m.app.view_functions and "remove_parking" not in m.app.view_functions


def test_soa_shows_the_parking_line_it_charges(app_module):
    """Before B3 the line was hidden unless a parking lot had an 'include in SOA' flag,
    while the amount was still in the total."""
    m = app_module
    admin, _ = login(m, "superadmin", "Test-Admin-Pass-1")
    admin.post("/billing", data={"month": "2034-01"})
    with m.app.app_context():
        bill = m.Billing.query.filter_by(unit_id=unit(m, "TEST-501").id, billing_month="2034-01").first()
        assert float(bill.parking_dues) == 1000.0
        bid = bill.id
    page = admin.get(f"/billing/{bid}").get_data(as_text=True)
    assert "Parking — Unit P-TEST-01" in page


def test_unit_page_shows_the_assigned_parking_unit(app_module):
    m = app_module
    admin, _ = login(m, "superadmin", "Test-Admin-Pass-1")
    with m.app.app_context():
        uid = unit(m, "TEST-502").id
    page = admin.get(f"/unit/{uid}").get_data(as_text=True)
    assert "P-TEST-02" in page and "Parking Lots" not in page
