"""Units Directory API (/api/units): permissions, create (with first owner/tenant), validation,
parking/storage assignment rules, owners/tenants (representative, move-out), audit, CSRF, and the
retired classic Units forms."""
import io
from datetime import date

import pytest
from openpyxl import Workbook, load_workbook

from conftest import PASSWORD, SA_PASSWORD, SA_USERNAME, api, login

BASE = "/api/units"
from app.services.unit_bulk import LEGACY_UNIT_BULK_FIELDS, PREVIOUS_UNIT_BULK_FIELDS, UNIT_BULK_FIELDS


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


def unit_workbook(rows):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Units"
    sheet.append(UNIT_BULK_FIELDS)
    for row in rows:
        sheet.append([row.get(field) for field in UNIT_BULK_FIELDS])
    stream = io.BytesIO()
    workbook.save(stream)
    stream.seek(0)
    return stream


def legacy_unit_workbook(rows):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Units"
    sheet.append(LEGACY_UNIT_BULK_FIELDS)
    for row in rows:
        sheet.append([row.get(field) for field in LEGACY_UNIT_BULK_FIELDS])
    stream = io.BytesIO()
    workbook.save(stream)
    stream.seek(0)
    return stream


def previous_unit_workbook(rows):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Units"
    sheet.append(PREVIOUS_UNIT_BULK_FIELDS)
    for row in rows:
        sheet.append([row.get(field) for field in PREVIOUS_UNIT_BULK_FIELDS])
    stream = io.BytesIO()
    workbook.save(stream)
    stream.seek(0)
    return stream


def test_permissions(app_module):
    uid = unit_id(app_module, "TEST-501")
    for role in ("manager", "staff", "accounting", "resident"):
        client = api(login(app_module, f"test_{role}", PASSWORD)[0])
        assert client.get(BASE).status_code == 403, role
        assert client.get(f"{BASE}/{uid}").status_code == 403, role
        assert client.put(f"{BASE}/{uid}", json={}).status_code == 403, role
        assert client.post(BASE, json={}).status_code == 403, role
        assert client.get(f"{BASE}/bulk-template").status_code == 403, role
        assert client.post(f"{BASE}/bulk-import/check", data={}, content_type="multipart/form-data").status_code == 403, role
        assert client.post(f"{BASE}/bulk-import", data={}, content_type="multipart/form-data").status_code == 403, role
    assert api(login(app_module, "test_admin", PASSWORD)[0]).get(BASE).status_code == 200
    assert app_module.app.test_client().get(BASE).status_code == 401


def test_bulk_unit_template_and_preview(app_module, sa):
    template = sa.get(f"{BASE}/bulk-template")
    assert template.status_code == 200
    assert template.data[:2] == b"PK"
    workbook = load_workbook(io.BytesIO(template.data))
    units_sheet = workbook["Units"]
    assert units_sheet.auto_filter.ref == "A1:AA3001"
    assert any(str(validation.sqref) == "B2:B3001" for validation in units_sheet.data_validations.dataValidation)
    assert any(str(validation.sqref) == "C2:C3001" for validation in units_sheet.data_validations.dataValidation)
    assert any(str(validation.sqref) == "E2:E3001" for validation in units_sheet.data_validations.dataValidation)
    assert units_sheet["B1"].comment is not None and units_sheet["E1"].comment is not None
    category_validation = next(item for item in units_sheet.data_validations.dataValidation if str(item.sqref) == "B2:B3001")
    assert category_validation.formula1 == '"RESIDENTIAL,COMMERCIAL,DRYING AREA,STORAGE,PARKING"'
    assert all(
        any(str(validation.sqref) == f"{letter}2:{letter}3001" for validation in units_sheet.data_validations.dataValidation)
        for letter in ("W", "X", "Y", "Z", "AA")
    )
    readme = "\n".join(str(value) for row in workbook["Read Me"].iter_rows(values_only=True) for value in row if value)
    assert "defaults to STUDIO TYPE" in readme and "2F, UG, or LG" in readme and "Charge responsibility" in readme
    workbook.close()
    response = sa.post(f"{BASE}/bulk-import/check", data={
        "file": (unit_workbook([
            {"unit_no": "BULK-901", "category": "RESIDENTIAL", "area_sqm": 30},
            {"unit_no": "BULK-902", "category": "NOT-A-CATEGORY", "area_sqm": 30},
        ]), "units.xlsx"),
    }, content_type="multipart/form-data")
    assert response.status_code == 200
    report = response.get_json()
    assert (report["total"], report["valid"], report["invalid"]) == (2, 1, 1)
    assert report["rows"][1]["errors"]
    with app_module.app.app_context():
        assert app_module.Unit.query.filter(app_module.Unit.unit_no.like("BULK-90%")).count() == 0


def test_bulk_unit_blank_residential_subtype_defaults_to_studio(app_module, sa):
    response = sa.post(f"{BASE}/bulk-import/check", data={
        "file": (unit_workbook([{"unit_no": "BULK-DEFAULT-STUDIO", "category": "RESIDENTIAL", "area_sqm": 30}]), "units.xlsx"),
    }, content_type="multipart/form-data")
    assert response.status_code == 200
    assert response.get_json()["valid"] == 1

    response = sa.post(f"{BASE}/bulk-import", data={
        "file": (unit_workbook([{"unit_no": "BULK-DEFAULT-STUDIO", "category": "RESIDENTIAL", "area_sqm": 30}]), "units.xlsx"),
    }, content_type="multipart/form-data")
    assert response.status_code == 201, response.get_json()
    with app_module.app.app_context():
        unit = app_module.Unit.query.filter_by(unit_no="BULK-DEFAULT-STUDIO").first()
        assert unit is not None and unit.unit_type == "STUDIO TYPE"
        app_module.db.session.delete(unit)
        app_module.db.session.commit()


def test_bulk_commercial_type_is_stored_for_shared_water_readings(app_module, sa, cleanup):
    cleanup.append("BULK-COMMERCIAL")
    response = sa.post(f"{BASE}/bulk-import", data={
        "file": (unit_workbook([{
            "unit_no": "BULK-COMMERCIAL", "category": "COMMERCIAL", "commercial_type": "UG", "area_sqm": 60,
        }]), "units.xlsx"),
    }, content_type="multipart/form-data")
    assert response.status_code == 201, response.get_json()
    with app_module.app.app_context():
        unit = app_module.Unit.query.filter_by(unit_no="BULK-COMMERCIAL").first()
        assert unit is not None and unit.unit_type == "COMMERCIAL" and unit.floor == "UG"
        app_module.db.session.delete(unit)
        app_module.db.session.commit()


def test_bulk_upload_accepts_previously_downloaded_template(sa):
    response = sa.post(f"{BASE}/bulk-import/check", data={
        "file": (legacy_unit_workbook([
            {"unit_no": "BULK-LEGACY-COMMERCIAL", "unit_type": "COMMERCIAL", "floor": "LG", "area_sqm": 45},
            {"unit_no": "BULK-LEGACY-DEFAULT", "area_sqm": 30},
        ]), "old-units.xlsx"),
    }, content_type="multipart/form-data")
    assert response.status_code == 200, response.get_json()
    report = response.get_json()
    assert report["valid"] == 2
    assert [row["type"] for row in report["rows"]] == ["COMMERCIAL", "STUDIO TYPE"]


def test_bulk_upload_accepts_previous_category_template(sa):
    response = sa.post(f"{BASE}/bulk-import/check", data={
        "file": (previous_unit_workbook([{
            "unit_no": "BULK-PREVIOUS-CATEGORY", "category": "RESIDENTIAL", "area_sqm": 30,
            "occupancy": "Tenant",
        }]), "previous-units.xlsx"),
    }, content_type="multipart/form-data")
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["valid"] == 1


def test_bulk_unit_create_is_atomic_and_includes_optional_person(app_module, sa):
    good = {"unit_no": "BULK-911", "category": "RESIDENTIAL", "residential_type": "1 BEDROOM", "area_sqm": 40,
            "owner_name": "Bulk Owner", "owner_email": "bulk.owner@example.com",
            "occupancy": "Tenant", "condo_payer": "Tenant", "water_payer": "Owner"}
    bad = {"unit_no": "BULK-912", "category": "RESIDENTIAL", "residential_type": "INVALID", "area_sqm": 20}
    response = sa.post(f"{BASE}/bulk-import", data={
        "file": (unit_workbook([good, bad]), "units.xlsx"),
    }, content_type="multipart/form-data")
    assert response.status_code == 409
    with app_module.app.app_context():
        assert app_module.Unit.query.filter(app_module.Unit.unit_no.in_(("BULK-911", "BULK-912"))).count() == 0

    response = sa.post(f"{BASE}/bulk-import", data={
        "file": (unit_workbook([good]), "units.xlsx"),
    }, content_type="multipart/form-data")
    assert response.status_code == 201, response.get_json()
    assert response.get_json()["created"] == 1
    with app_module.app.app_context():
        unit = app_module.Unit.query.filter_by(unit_no="BULK-911").first()
        assert unit is not None and unit.area_sqm == 40
        assert unit.condo_charge_payer == "Tenant" and unit.water_charge_payer == "Owner"
        assert unit.parking_charge_payer == unit.storage_charge_payer == unit.other_charge_payer == "Tenant"
        owner = app_module.Owner.query.filter_by(unit_id=unit.id).first()
        assert owner is not None and owner.owner_name == "Bulk Owner"
        assert app_module.AuditLog.query.filter_by(action="Created unit BULK-911 by bulk upload").count() == 1
        app_module.db.session.delete(unit)
        app_module.db.session.commit()


def test_bulk_unit_accepts_full_names_with_punctuation(app_module, sa, cleanup):
    cleanup.append("BULK-FULL-NAME")
    row = {
        "unit_no": "BULK-FULL-NAME",
        "category": "COMMERCIAL",
        "commercial_type": "2F",
        "area_sqm": 40,
        "owner_name": "AAREMA SHIPPING & TRADING CO., INC.",
        "tenant_name": "JORIZ DE VEGA/BIOCARE",
    }
    response = sa.post(f"{BASE}/bulk-import/check", data={
        "file": (unit_workbook([row]), "units.xlsx"),
    }, content_type="multipart/form-data")
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["valid"] == 1
    assert response.get_json()["rows"][0]["errors"] == []

    response = sa.post(f"{BASE}/bulk-import", data={
        "file": (unit_workbook([row]), "units.xlsx"),
    }, content_type="multipart/form-data")
    assert response.status_code == 201, response.get_json()
    with app_module.app.app_context():
        unit = app_module.Unit.query.filter_by(unit_no="BULK-FULL-NAME").first()
        assert unit is not None
        owner = app_module.Owner.query.filter_by(unit_id=unit.id).one()
        tenant = app_module.Tenant.query.filter_by(unit_id=unit.id).one()
        assert owner.owner_name == row["owner_name"]
        assert tenant.tenant_name == row["tenant_name"]


def test_bulk_unit_skips_duplicate_names_in_workbook_and_database(app_module, sa, cleanup):
    cleanup.append("BULK-DUP-1")
    first = {"unit_no": "BULK-DUP-1", "category": "RESIDENTIAL", "residential_type": "1 BEDROOM", "area_sqm": 40,
             "owner_name": "First Owner"}
    repeated = {"unit_no": "bulk-dup-1", "category": "NOT-A-CATEGORY"}
    existing = {"unit_no": "test-501", "category": "NOT-A-CATEGORY"}

    response = sa.post(f"{BASE}/bulk-import/check", data={
        "file": (unit_workbook([first, repeated, existing]), "units.xlsx"),
    }, content_type="multipart/form-data")
    assert response.status_code == 200, response.get_json()
    preview = response.get_json()
    assert (preview["total"], preview["valid"], preview["invalid"], preview["skipped"]) == (3, 1, 0, 2)
    assert preview["rows"][1]["skipped"] and preview["rows"][1]["errors"] == []
    assert preview["rows"][2]["skipped"] and preview["rows"][2]["errors"] == []

    response = sa.post(f"{BASE}/bulk-import", data={
        "file": (unit_workbook([first, repeated, existing]), "units.xlsx"),
    }, content_type="multipart/form-data")
    assert response.status_code == 201, response.get_json()
    assert response.get_json()["created"] == 1
    assert response.get_json()["skipped"] == 2
    assert response.get_json()["unitNumbers"] == ["BULK-DUP-1"]
    with app_module.app.app_context():
        assert app_module.Unit.query.filter(app_module.Unit.unit_no.ilike("BULK-DUP-1")).count() == 1
        created_unit = app_module.Unit.query.filter_by(unit_no="BULK-DUP-1").first()
        owner = app_module.Owner.query.filter_by(unit_id=created_unit.id).first()
        assert owner is not None and owner.owner_name == "First Owner"
        assert app_module.Unit.query.filter_by(unit_no="TEST-501").count() == 1


def test_bulk_unit_upload_accepts_3000_rows_and_rejects_3001(app_module, sa):
    rows = [
        {"unit_no": f"BULK-LIMIT-{index}", "category": "RESIDENTIAL", "residential_type": "1 BEDROOM", "area_sqm": 40}
        for index in range(3000)
    ]
    response = sa.post(f"{BASE}/bulk-import/check", data={
        "file": (unit_workbook(rows), "units.xlsx"),
    }, content_type="multipart/form-data")
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["valid"] == 3000

    rows.append({"unit_no": "BULK-LIMIT-3000", "category": "RESIDENTIAL", "residential_type": "1 BEDROOM", "area_sqm": 40})
    response = sa.post(f"{BASE}/bulk-import/check", data={
        "file": (unit_workbook(rows), "units.xlsx"),
    }, content_type="multipart/form-data")
    assert response.status_code == 400
    assert "limit of 3000 unit rows" in response.get_json()["error"]["message"]


def test_list_kinds_and_dues(sa):
    data = sa.get(f"{BASE}?perPage=100").get_json()
    assert data["counts"]["residential"] >= 4 and data["counts"]["PARKING"] >= 1
    assert all(u["type"] not in ("PARKING", "STORAGE") for u in data["units"])
    row = next(u for u in data["units"] if u["unitNo"] == "TEST-501")
    assert row["parkingUnit"]["unitNo"] == "P-TEST-01" and row["dues"]["parking"] == "1000.00"
    assert {"condo", "parking", "storage"} == set(row["dues"]) and row["latestBill"] is not None


def test_commercial_units_have_a_separate_directory_category(app_module, sa, cleanup):
    cleanup.append("UT-COMMERCIAL-01")
    created = sa.post(BASE, json={
        "unitNo": "UT-COMMERCIAL-01", "type": "COMMERCIAL", "floor": "UG",
        "areaSqm": "40", "autoRate": True, "occupancy": "Owner", "status": "Vacant",
    })
    assert created.status_code == 201, created.get_json()

    commercial = sa.get(f"{BASE}?kind=COMMERCIAL&perPage=100").get_json()
    assert commercial["counts"]["COMMERCIAL"] == 1
    assert [unit["unitNo"] for unit in commercial["units"]] == ["UT-COMMERCIAL-01"]
    residential = sa.get(f"{BASE}?kind=residential&perPage=100").get_json()
    assert all(unit["type"] != "COMMERCIAL" for unit in residential["units"])


def test_create_with_owner_and_tenant(app_module, sa, cleanup):
    cleanup.append("UT-901")
    body = {"unitNo": "UT-901", "floor": "9", "type": "STUDIO TYPE", "areaSqm": "30.5", "autoRate": True,
            "occupancy": "Tenant", "status": "Occupied",
            "chargePayers": {"condo": "Owner", "water": "Tenant", "parking": "Owner", "storage": "Tenant", "other": "Owner"},
            "owner": {"name": "Olga Owner", "email": "olga@example.com", "receiveSoaEmail": True},
            "tenant": {"name": "Tito Tenant", "moveIn": "2026-09-01"}}
    resp = sa.post(BASE, json=body)
    assert resp.status_code == 201, resp.get_json()
    u = resp.get_json()["unit"]
    assert u["ownerName"] == "Olga Owner" and u["tenantName"] == "Tito Tenant"
    assert u["chargePayers"] == body["chargePayers"]
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


def test_bedroom_types_share_the_residential_rate_and_manual_dues_are_billed(sa, cleanup):
    """Every residential subtype uses the common Residential rate; manual dues remain supported."""
    cleanup.extend(["UT-903", "UT-908"])
    u = sa.post(BASE, json={"unitNo": "UT-903", "type": "2 BEDROOM", "areaSqm": "40", "autoRate": True}).get_json()["unit"]
    assert u["effectiveRatePerSqm"] == "50" and u["dues"]["condo"] == "2000.00" and u["zeroDues"] is False
    u = sa.post(BASE, json={"unitNo": "UT-908", "type": "1 BEDROOM", "areaSqm": "40", "duesMode": "manual",
                            "manualMonthlyDues": "2500"}).get_json()["unit"]
    assert u["dues"]["condo"] == "2500.00"


def test_create_without_residential_subtype_defaults_to_studio(sa, cleanup):
    cleanup.append("UT-910")
    response = sa.post(BASE, json={"unitNo": "UT-910", "areaSqm": "30", "autoRate": True})
    assert response.status_code == 201, response.get_json()
    unit = response.get_json()["unit"]
    assert unit["type"] == "STUDIO TYPE" and unit["effectiveRatePerSqm"] == "50"


def test_commercial_unit_type_is_available_and_uses_normal_unit_billing(sa, cleanup):
    cleanup.append("UT-909")
    options = sa.get(f"{BASE}/options").get_json()
    assert "COMMERCIAL" in options["unitTypes"] and options["typeRates"]["COMMERCIAL"] == "0"

    response = sa.post(BASE, json={"unitNo": "UT-909", "type": "COMMERCIAL", "areaSqm": "70",
                                   "ratePerSqm": "120", "autoRate": False})
    assert response.status_code == 201, response.get_json()
    unit = response.get_json()["unit"]
    assert unit["type"] == "COMMERCIAL" and unit["dues"]["condo"] == "8400.00"
    assert any(row["unitNo"] == "UT-909" for row in sa.get(f"{BASE}?kind=COMMERCIAL&perPage=100").get_json()["units"])


def test_drying_area_is_billable_and_has_its_own_category_and_rate(sa, cleanup, app_module):
    cleanup.append("UT-DRYING-01")
    with app_module.app.app_context():
        rate_setting = app_module.Setting.query.filter_by(key="drying_area_rate_per_sqm").first()
        original_rate = rate_setting.value if rate_setting else None
        if rate_setting:
            rate_setting.value = "24"
        else:
            app_module.db.session.add(app_module.Setting(key="drying_area_rate_per_sqm", value="24"))
        app_module.db.session.commit()
    try:
        options = sa.get(f"{BASE}/options").get_json()
        assert "DRYING AREA" in options["unitTypes"] and options["typeRates"]["DRYING AREA"] == "24"
        response = sa.post(BASE, json={
            "unitNo": "UT-DRYING-01", "type": "DRYING AREA", "areaSqm": "10", "autoRate": True,
        })
        assert response.status_code == 201, response.get_json()
        unit = response.get_json()["unit"]
        assert unit["type"] == "DRYING AREA" and unit["effectiveRatePerSqm"] == "24" and unit["dues"]["condo"] == "240.00"
        assert [row["unitNo"] for row in sa.get(f"{BASE}?kind=DRYING%20AREA&perPage=100").get_json()["units"]] == ["UT-DRYING-01"]
        assert "UT-DRYING-01" not in [row["unitNo"] for row in sa.get(f"{BASE}?kind=residential&perPage=100").get_json()["units"]]
    finally:
        with app_module.app.app_context():
            rate_setting = app_module.Setting.query.filter_by(key="drying_area_rate_per_sqm").first()
            if original_rate is None:
                app_module.db.session.delete(rate_setting)
            else:
                rate_setting.value = original_rate
            app_module.db.session.commit()


def test_bulk_upload_accepts_billable_drying_area(sa, app_module, cleanup):
    cleanup.append("BULK-DRYING-AREA")
    row = {
        "unit_no": "BULK-DRYING-AREA", "category": "DRYING AREA", "area_sqm": 12,
        "rate_per_sqm": 20, "auto_rate": "No",
    }
    response = sa.post(f"{BASE}/bulk-import/check", data={
        "file": (unit_workbook([row]), "units.xlsx"),
    }, content_type="multipart/form-data")
    assert response.status_code == 200 and response.get_json()["valid"] == 1
    response = sa.post(f"{BASE}/bulk-import", data={
        "file": (unit_workbook([row]), "units.xlsx"),
    }, content_type="multipart/form-data")
    assert response.status_code == 201 and response.get_json()["created"] == 1
    with app_module.app.app_context():
        unit = app_module.Unit.query.filter_by(unit_no="BULK-DRYING-AREA").one()
        assert unit.unit_type == "DRYING AREA" and unit.unit_rate_per_sqm == 20


def test_edit_unit_and_audit(app_module, sa, cleanup):
    cleanup.append("UT-904")
    uid = sa.post(BASE, json={"unitNo": "UT-904", "type": "STUDIO TYPE", "areaSqm": "20"}).get_json()["unit"]["id"]
    body = {"floor": "12", "type": "STUDIO TYPE", "areaSqm": "22", "ratePerSqm": "60", "status": "Vacant", "occupancy": "Owner",
            "chargePayers": {"condo": "Tenant", "water": "Owner", "parking": "Tenant", "storage": "Owner", "other": "Tenant"}}
    resp = sa.put(f"{BASE}/{uid}", json=body).get_json()
    assert resp["changed"] and resp["unit"]["dues"]["condo"] == "1320.00" and resp["unit"]["chargePayers"] == body["chargePayers"]
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
