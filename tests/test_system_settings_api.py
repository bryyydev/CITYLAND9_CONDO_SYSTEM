"""Settings API (/api/admin/system) and the retired classic /settings screen and import form.

The import itself (backup first, limits, one transaction) is covered by test_smoke (round trip) and
test_security (row limit, size limit, non-workbook files)."""
import io
import json
import sqlite3

from openpyxl import load_workbook

from conftest import PASSWORD, SA_PASSWORD, SA_USERNAME, login

BASE = "/api/admin/system"


def api_client(app_module, username, password):
    client = app_module.app.test_client()
    token = client.get("/api/auth/csrf").get_json()["csrfToken"]
    resp = client.post("/api/auth/login", json={"username": username, "password": password}, headers={"X-CSRFToken": token})
    assert resp.status_code == 200, resp.get_json()
    client.environ_base["HTTP_X_CSRFTOKEN"] = client.get("/api/auth/csrf").get_json()["csrfToken"]
    return client


def test_only_superadmin(app_module):
    for role in ("admin", "manager", "staff", "accounting", "resident"):
        client = api_client(app_module, f"test_{role}", PASSWORD)
        assert client.get(BASE).status_code == 403, role
        assert client.get(f"{BASE}/import-template").status_code == 403, role
        assert client.post(f"{BASE}/backup").status_code == 403, role
        assert client.post(f"{BASE}/import", data={"file": (io.BytesIO(b"x"), "a.xlsx")}, content_type="multipart/form-data").status_code == 403, role
        assert client.get("/database/export.xlsx").status_code in (302, 403), role
    assert app_module.app.test_client().get(BASE).status_code == 401


def test_overview(app_module):
    data = api_client(app_module, SA_USERNAME, SA_PASSWORD).get(BASE).get_json()
    assert data["database"]["engine"] == "SQLite"          # the tests use a throwaway SQLite database
    assert data["export"]["url"] == "/database/export.xlsx" and data["import"]["allowed"] is True
    assert data["import"]["maxUploadMb"] >= 1 and data["import"]["maxRowsPerSheet"] >= 1
    assert {"ok", "status", "ageHours", "detail", "maxAgeHours"} <= set(data["backup"])
    assert data["backup"]["manualAllowed"] is True
    assert data["import"]["templateUrl"] == f"{BASE}/import-template"
    assert "password" not in str(data).lower()


def test_import_template_matches_export_schema_and_contains_safety_guidance(app_module):
    from app.services.excel_schema import EXPORT_FIELDS

    sa = api_client(app_module, SA_USERNAME, SA_PASSWORD)
    response = sa.get(f"{BASE}/import-template")
    assert response.status_code == 200
    assert response.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert "CITYLAND9_Import_Template.xlsx" in response.headers["Content-Disposition"]

    workbook = load_workbook(io.BytesIO(response.data), read_only=True, data_only=True)
    assert workbook.sheetnames == ["Read Me", *EXPORT_FIELDS]
    readme = "\n".join(str(value) for row in workbook["Read Me"].iter_rows(values_only=True) for value in row if value)
    assert "cannot be created through this importer" in readme
    assert "preserve every ID" in readme
    for name, fields in EXPORT_FIELDS.items():
        sheet = workbook[name]
        assert tuple(cell.value for cell in sheet[1]) == tuple(fields)
        assert not any(any(value not in (None, "") for value in row)
                       for row in sheet.iter_rows(min_row=2, values_only=True))
    assert "auto_rate" in EXPORT_FIELDS["Units"]
    assert EXPORT_FIELDS["CommercialWaterReadings"] == [
        "id", "commercial_type", "reading_month", "previous_reading", "current_reading", "rate", "reading_date",
        "reading_period_start", "reading_period_end",
    ]
    workbook.close()


def test_import_needs_a_file_and_csrf(app_module):
    sa = api_client(app_module, SA_USERNAME, SA_PASSWORD)
    assert sa.post(f"{BASE}/import", data={}, content_type="multipart/form-data").status_code == 400
    sa.environ_base.pop("HTTP_X_CSRFTOKEN")
    assert sa.post(f"{BASE}/backup").status_code == 403
    assert sa.post(f"{BASE}/import", data={"file": (io.BytesIO(b"x"), "a.xlsx")}, content_type="multipart/form-data").status_code == 403


def test_manual_backup_creates_and_verifies_sqlite_copy(app_module, monkeypatch, tmp_path):
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    sa = api_client(app_module, SA_USERNAME, SA_PASSWORD)
    response = sa.post(f"{BASE}/backup")
    assert response.status_code == 201, response.get_json()
    result = response.get_json()
    backup_path = tmp_path / "backups" / result["file"]
    assert backup_path.is_file() and backup_path.stat().st_size > 0
    with sqlite3.connect(backup_path) as backup:
        assert backup.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    status = json.loads((tmp_path / "backups" / "last_backup.json").read_text(encoding="utf-8"))
    assert status["status"] == "ok" and status["file"] == str(backup_path)


def test_classic_settings_and_import_form_change_nothing(app_module):
    m = app_module
    sa, _ = login(m, SA_USERNAME, SA_PASSWORD)
    assert sa.get("/settings").headers["Location"].endswith("/app/superadmin/rates-rules")
    with m.app.app_context():
        water = m.setting("water_rate")
        units = m.Unit.query.count()
    resp = sa.post("/settings", data={"water_rate": "777"})
    assert resp.status_code == 303 and resp.headers["Location"].endswith("/app/superadmin/rates-rules?moved=form")
    export = sa.get("/database/export.xlsx")
    assert export.status_code == 200 and export.data[:2] == b"PK"            # the export download is unchanged
    resp = sa.post("/database/import", data={"excel_file": (io.BytesIO(export.data), "db.xlsx")}, content_type="multipart/form-data")
    assert resp.status_code == 303 and resp.headers["Location"].endswith("/app/superadmin/settings?moved=form")
    with m.app.app_context():
        assert m.setting("water_rate") == water and m.Unit.query.count() == units
    staff, _ = login(m, "test_staff", PASSWORD)
    assert "rates-rules" not in staff.get("/settings").headers.get("Location", "")
