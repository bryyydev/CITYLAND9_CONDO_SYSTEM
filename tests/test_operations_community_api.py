"""Front-desk and community APIs: Gate Passes, Move In/Out Certificates, Expenses
(/api/gate-passes, /api/certificates, /api/expenses) and Maintenance, Announcements, Vendors,
Documents (/api/maintenance, /api/announcements, /api/vendors, /api/documents). Permissions,
validation, persistence, audit, CSRF, resident document visibility and the retired classic pages."""
from datetime import date

import pytest
from werkzeug.security import generate_password_hash

from conftest import PASSWORD, SA_PASSWORD, SA_USERNAME, api, login

RES_PW = "Res-Dummy-Pass-1"


@pytest.fixture
def sa(app_module):
    return api(login(app_module, SA_USERNAME, SA_PASSWORD)[0])


def as_role(m, role):
    return api(login(m, f"test_{role}", PASSWORD)[0])


def unit(m, no):
    with m.app.app_context():
        return m.Unit.query.filter_by(unit_no=no).first().id


_audit_start = {}


@pytest.fixture(autouse=True)
def _mark_audit(app_module):
    """Audit rows written by earlier tests (SQLite reuses deleted ids) are not counted."""
    with app_module.app.app_context():
        _audit_start["id"] = app_module.db.session.query(app_module.db.func.max(app_module.AuditLog.id)).scalar() or 0


def audited(m, entity_type, entity_id):
    with m.app.app_context():
        return m.AuditLog.query.filter(m.AuditLog.id > _audit_start["id"]).filter_by(entity_type=entity_type, entity_id=entity_id).count()


@pytest.fixture
def resident(app_module):
    """A resident account for the Current owner of TEST-504."""
    m = app_module
    with m.app.app_context():
        u = m.Unit.query.filter_by(unit_no="TEST-504").first()
        person = m.Owner.query.filter_by(unit_id=u.id, status="Current").first()
        user = m.User(username="ops_resident", password_hash=generate_password_hash(RES_PW), role="resident", active=True)
        m.db.session.add(user); m.db.session.flush()
        m.db.session.add(m.ResidentProfile(user_id=user.id, unit_id=u.id, person_type="Owner", person_id=person.id, display_name="Ops Resident"))
        m.db.session.commit()
        uid, unit_id = user.id, u.id
    client = api(login(m, "ops_resident", RES_PW)[0])
    yield client, unit_id
    with m.app.app_context():
        user = m.db.session.get(m.User, uid)
        m.db.session.delete(user.resident_profile)
        m.db.session.delete(user)
        m.db.session.commit()


# ------------------------------------------------------------------ permissions
@pytest.mark.parametrize("url,allowed", [
    ("/api/gate-passes", {"admin", "staff"}),
    ("/api/certificates", {"admin", "staff"}),
    ("/api/certificates/options", {"admin", "staff"}),
    ("/api/expenses", {"admin", "staff"}),
    ("/api/maintenance", {"admin", "staff"}),
    ("/api/announcements", {"admin", "manager"}),
    ("/api/vendors", {"admin"}),
    ("/api/documents", {"admin"}),
])
def test_role_permissions_match_the_classic_screens(app_module, url, allowed):
    for role in ("admin", "manager", "staff", "accounting", "resident"):
        status = as_role(app_module, role).get(url).status_code
        assert status == (200 if role in allowed else 403), (url, role, status)
    assert app_module.app.test_client().get(url).status_code == 401


def test_mutations_need_the_csrf_header(app_module):
    client, _ = login(app_module, SA_USERNAME, SA_PASSWORD)
    for url in ("/api/gate-passes", "/api/certificates", "/api/expenses", "/api/maintenance", "/api/announcements", "/api/vendors", "/api/documents"):
        assert client.post(url, json={}).status_code == 403, url


# ------------------------------------------------------------------ gate passes
def test_office_issues_a_gate_pass_for_an_existing_unit(app_module, sa):
    m = app_module
    bad = sa.post("/api/gate-passes", json={"unitNo": "NOPE-1", "type": "Visitor", "name": "", "purpose": "x"})
    assert bad.status_code == 400 and {"unitNo", "name"} <= set(bad.get_json()["error"]["fields"])
    resp = sa.post("/api/gate-passes", json={"unitNo": "test-501", "type": "Delivery", "date": date.today().isoformat(),
                                             "name": "LBC Express", "purpose": "Parcel"})
    assert resp.status_code == 201
    p = resp.get_json()["pass"]
    assert p["unitNo"] == "TEST-501" and p["status"] == "Issued" and p["source"] == "office"
    assert audited(m, "gate_pass", p["id"]) == 1
    assert sa.post(f"/api/gate-passes/{p['id']}/review", json={"decision": "approve"}).status_code == 409   # not a request
    with m.app.app_context():
        m.GatePass.query.filter_by(id=p["id"]).delete(); m.db.session.commit()


# ------------------------------------------------------------------ certificates
def test_certificate_is_numbered_printable_and_checked(app_module, sa):
    m = app_module
    opts = sa.get("/api/certificates/options").get_json()["units"]
    u = next(x for x in opts if x["unitNo"] == "TEST-502")
    assert all(x["unitNo"] not in ("P-1",) for x in opts)
    tenant = next(p for p in u["people"] if p["type"] == "Tenant")
    other_unit = unit(m, "TEST-501")
    wrong = sa.post("/api/certificates", json={"unitId": other_unit, "moveType": "Move In", "personType": "Tenant", "personId": tenant["id"]})
    assert wrong.status_code == 400 and "personId" in wrong.get_json()["error"]["fields"]
    assert sa.post("/api/certificates", json={"unitId": u["id"], "moveType": "Sideways", "personType": "Tenant",
                                              "personId": tenant["id"]}).status_code == 400
    resp = sa.post("/api/certificates", json={"unitId": u["id"], "moveType": "Move In", "personType": "Tenant",
                                              "personId": tenant["id"], "certificateDate": "2026-10-04"})
    assert resp.status_code == 201
    c = resp.get_json()["certificate"]
    assert c["certificateNo"] == f"CL9-2026-{c['id']:05d}" and c["personName"] == tenant["name"] and c["issuedBy"] == SA_USERNAME
    detail = sa.get(f"/api/certificates/{c['id']}").get_json()
    assert detail["certificate"]["unitNo"] == "TEST-502" and detail["corporation"]
    assert any(x["id"] == c["id"] for x in sa.get("/api/certificates").get_json()["certificates"])
    assert audited(m, "move_certificate", c["id"]) == 1
    with m.app.app_context():
        m.MoveCertificate.query.filter_by(id=c["id"]).delete(); m.db.session.commit()


# ------------------------------------------------------------------ expenses
def test_expenses_validate_and_total_by_month(app_module):
    m = app_module
    staff = as_role(m, "staff")
    bad = staff.post("/api/expenses", json={"date": "2031-02-30", "category": "", "description": "x", "amount": "-5"})
    assert bad.status_code == 400 and {"date", "category", "amount"} <= set(bad.get_json()["error"]["fields"])
    ids = []
    for cat, amt in (("Supplies", "100.50"), ("Supplies", "20"), ("Repairs", "1,000")):
        r = staff.post("/api/expenses", json={"date": "2031-03-05", "category": cat, "description": "probe", "amount": amt})
        assert r.status_code == 201
        ids.append(r.get_json()["expense"]["id"])
    month = staff.get("/api/expenses?month=2031-03").get_json()
    assert month["total"] == "1120.50" and month["byCategory"] == {"Repairs": "1000.00", "Supplies": "120.50"}
    assert len(month["expenses"]) == 3 and "Supplies" in month["categories"]
    assert staff.get("/api/expenses?month=2031-13").status_code == 400
    assert audited(m, "expense", ids[0]) == 1
    with m.app.app_context():
        m.Expense.query.filter(m.Expense.id.in_(ids)).delete(synchronize_session=False); m.db.session.commit()


# ------------------------------------------------------------------ maintenance
def test_office_files_and_updates_a_ticket_with_a_vendor(app_module, sa):
    m = app_module
    v = sa.post("/api/vendors", json={"name": "Aqua Fix Plumbing", "serviceType": "Plumbing"}).get_json()["vendor"]
    staff = as_role(m, "staff")
    resp = staff.post("/api/maintenance", json={"unitId": unit(m, "TEST-501"), "category": "Plumbing", "priority": "High",
                                                "title": "Leaking pipe", "description": "Under the sink"})
    assert resp.status_code == 201
    t = resp.get_json()["ticket"]
    assert t["ticketNo"].startswith("MT-") and t["status"] == "Open" and t["source"] == "office"
    assert staff.put(f"/api/maintenance/{t['id']}", json={"status": "Done"}).status_code == 400
    assert staff.put(f"/api/maintenance/{t['id']}", json={"vendorId": 999999}).status_code == 400
    upd = staff.put(f"/api/maintenance/{t['id']}", json={"status": "In Progress", "vendorId": v["id"], "assignedTo": "Mang Jose",
                                                         "resolution": "Plumber scheduled Tuesday"}).get_json()["ticket"]
    assert upd["status"] == "In Progress" and upd["vendor"] == "Aqua Fix Plumbing" and upd["assignedTo"] == "Mang Jose"
    assert audited(m, "maintenance", t["id"]) == 2
    listed = staff.get("/api/maintenance").get_json()
    assert any(x["id"] == t["id"] for x in listed["tickets"]) and any(x["id"] == v["id"] for x in listed["vendors"])
    with m.app.app_context():
        m.MaintenanceTicket.query.filter_by(id=t["id"]).delete()
        m.Vendor.query.filter_by(id=v["id"]).delete(); m.db.session.commit()


# ------------------------------------------------------------------ announcements
def test_published_announcements_reach_residents_drafts_do_not(app_module, resident):
    m = app_module
    client, _ = resident
    admin = as_role(m, "admin")
    assert admin.post("/api/announcements", json={"title": "", "message": "x"}).status_code == 400
    pub = admin.post("/api/announcements", json={"title": "Water interruption", "message": "Oct 10, 9am-12nn"}).get_json()["announcement"]
    draft = admin.post("/api/announcements", json={"title": "Draft notice", "message": "not yet", "published": False}).get_json()["announcement"]
    assert pub["published"] and not draft["published"]
    staff_only = admin.post("/api/announcements", json={"title": "Payroll cut-off", "message": "staff only", "audience": "staff"}).get_json()["announcement"]
    everyone = admin.post("/api/announcements", json={"title": "Fire drill", "message": "all", "audience": "all"}).get_json()["announcement"]
    titles = [n["title"] for n in client.get("/api/resident/notices").get_json()["notices"]]
    assert "Water interruption" in titles and "Fire drill" in titles
    assert "Draft notice" not in titles and "Payroll cut-off" not in titles
    assert client.get("/api/announcements").status_code == 403
    ids = [pub["id"], draft["id"], staff_only["id"], everyone["id"]]
    with m.app.app_context():
        m.Announcement.query.filter(m.Announcement.id.in_(ids)).delete(synchronize_session=False); m.db.session.commit()


# ------------------------------------------------------------------ vendors
def test_vendor_add_edit_and_validation(app_module, sa):
    m = app_module
    bad = sa.post("/api/vendors", json={"name": "", "email": "nope", "status": "Maybe"})
    assert bad.status_code == 400 and {"name", "email", "status"} <= set(bad.get_json()["error"]["fields"])
    v = sa.post("/api/vendors", json={"name": "Cool Air Co", "serviceType": "Aircon", "email": "hi@coolair.test"}).get_json()["vendor"]
    edited = sa.put(f"/api/vendors/{v['id']}", json={**v, "status": "Inactive"}).get_json()["vendor"]
    assert edited["status"] == "Inactive" and edited["email"] == "hi@coolair.test"
    assert sa.put("/api/vendors/999999", json=v).status_code == 404
    with m.app.app_context():
        m.Vendor.query.filter_by(id=v["id"]).delete(); m.db.session.commit()


# ------------------------------------------------------------------ documents
def test_documents_are_shown_only_to_their_audience(app_module, sa, resident):
    m = app_module
    client, unit_id = resident
    assert sa.post("/api/documents", json={"title": "Unit doc", "audience": "unit"}).status_code == 400   # unit missing
    made = [sa.post("/api/documents", json=body).get_json()["document"] for body in (
        {"title": "House Rules 2026", "audience": "residents", "fileName": "house-rules.pdf"},
        {"title": "Board minutes", "audience": "admin"},
        {"title": "Unit 504 deed copy", "audience": "unit", "unitId": unit_id},
        {"title": "Unit 501 contract", "audience": "unit", "unitId": unit(m, "TEST-501")},
    )]
    assert made[2]["unitNo"] == "TEST-504"
    seen = [d["title"] for d in client.get(f"/api/resident/units/{unit_id}/documents").get_json()["documents"]]
    assert sorted(seen) == ["House Rules 2026", "Unit 504 deed copy"]
    assert client.get(f"/api/resident/units/{unit(m, 'TEST-501')}/documents").status_code == 403   # not their unit
    assert client.get("/api/documents").status_code == 403
    with m.app.app_context():
        m.DocumentRecord.query.filter(m.DocumentRecord.id.in_([d["id"] for d in made])).delete(synchronize_session=False); m.db.session.commit()


# ------------------------------------------------------------------ retired classic pages
@pytest.mark.parametrize("old,new", [
    ("/expenses", "/app/staff/expenses"), ("/gate-pass", "/app/staff/gate-passes"),
    ("/move-certificate", "/app/staff/certificates"), ("/move-certificates", "/app/staff/certificates"),
    ("/maintenance", "/app/staff/maintenance"),
])
def test_classic_pages_redirect(app_module, old, new):
    assert as_role(app_module, "staff").get(old).headers["Location"] == new


def test_classic_post_from_old_tab_changes_nothing(app_module, sa):
    m = app_module
    with m.app.app_context():
        before = m.GatePass.query.count(), m.Vendor.query.count()
    for url, data in (("/gate-pass", {"unit_no": "TEST-501", "visitor_name": "x"}), ("/vendors", {"vendor_name": "Old tab vendor"})):
        resp = sa.post(url, data=data)
        assert resp.status_code == 303 and "moved=form" in resp.headers["Location"]
    with m.app.app_context():
        assert (m.GatePass.query.count(), m.Vendor.query.count()) == before


def test_resident_old_links_go_to_their_portal(app_module, resident):
    client, _ = resident
    assert client.get("/maintenance").headers["Location"].endswith("/my-maintenance")
    assert "/documents" not in client.get("/documents").headers["Location"]   # no permission: refused as before
