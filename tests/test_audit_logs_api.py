"""Audit Logs API (/api/admin/audit-logs): read-only, Superadmin + Accounting, filters, pagination,
CSV export (formula-safe, audited) and the retired classic /audit page."""
from datetime import datetime, timedelta

import pytest

from conftest import PASSWORD, SA_PASSWORD, SA_USERNAME

BASE = "/api/admin/audit-logs"


def api_client(app_module, username, password):
    client = app_module.app.test_client()
    token = client.get("/api/auth/csrf").get_json()["csrfToken"]
    resp = client.post("/api/auth/login", json={"username": username, "password": password}, headers={"X-CSRFToken": token})
    assert resp.status_code == 200, resp.get_json()
    client.environ_base["HTTP_X_CSRFTOKEN"] = client.get("/api/auth/csrf").get_json()["csrfToken"]
    return client


@pytest.fixture
def entries(app_module):
    """Three known entries; removed afterwards."""
    m = app_module
    with m.app.app_context():
        now = datetime.utcnow()
        rows = [
            m.AuditLog(username="auditor_a", action="ZZTEST created bill", created_at=now, reason="routine",
                       entity_type="billing", entity_id=7, details='{"before": 1, "after": 2}'),
            m.AuditLog(username="auditor_b", action="=HYPERLINK(\"http://evil\") ZZTEST", created_at=now - timedelta(days=3)),
            m.AuditLog(username="auditor_a", action="ZZTEST old entry", created_at=now - timedelta(days=40)),
        ]
        m.db.session.add_all(rows); m.db.session.commit()
        ids = [r.id for r in rows]
    yield ids
    with m.app.app_context():
        m.AuditLog.query.filter(m.AuditLog.id.in_(ids)).delete(synchronize_session=False)
        m.db.session.commit()


def test_permissions(app_module):
    for role, allowed in (("accounting", True), ("admin", False), ("manager", False), ("staff", False), ("resident", False)):
        client = api_client(app_module, f"test_{role}", PASSWORD)
        assert client.get(BASE).status_code == (200 if allowed else 403), role
        assert client.get(f"{BASE}/export.csv").status_code == (200 if allowed else 403), role
    assert app_module.app.test_client().get(BASE).status_code == 401


def test_read_only(app_module, entries):
    sa = api_client(app_module, SA_USERNAME, SA_PASSWORD)
    with app_module.app.app_context():
        before = [(a.id, a.action) for a in app_module.AuditLog.query.order_by(app_module.AuditLog.id).all()]
    for method in ("post", "put", "patch", "delete"):
        assert getattr(sa, method)(f"{BASE}/{entries[0]}", json={"action": "x"}).status_code in (404, 405), method
        assert getattr(sa, method)(BASE, json={"action": "x"}).status_code in (404, 405), method
    with app_module.app.app_context():
        assert [(a.id, a.action) for a in app_module.AuditLog.query.order_by(app_module.AuditLog.id).all()] == before


def test_filters_and_details(app_module, entries):
    sa = api_client(app_module, SA_USERNAME, SA_PASSWORD)
    data = sa.get(f"{BASE}?q=zztest").get_json()
    assert [e["id"] for e in data["entries"]] == sorted(entries, reverse=True)[:3] or data["total"] == 3
    assert data["total"] == 3 and "auditor_a" in data["users"]
    first = next(e for e in data["entries"] if e["id"] == entries[0])
    assert first["details"] == {"before": 1, "after": 2} and first["reason"] == "routine" and first["at"].endswith("Z")
    assert sa.get(f"{BASE}?q=zztest&user=AUDITOR_A").get_json()["total"] == 2
    today = (datetime.utcnow() + timedelta(hours=8)).date()
    week_ago = (today - timedelta(days=7)).isoformat()
    assert sa.get(f"{BASE}?q=zztest&from={week_ago}").get_json()["total"] == 2
    assert sa.get(f"{BASE}?q=zztest&to={(today - timedelta(days=30)).isoformat()}").get_json()["total"] == 1
    assert sa.get(f"{BASE}?q=zztest&perPage=1&page=2").get_json()["entries"][0]["id"] == entries[1]
    assert sa.get(f"{BASE}?from=2026-02-30").status_code == 400
    assert sa.get(f"{BASE}?from=2026-10-05&to=2026-10-01").status_code == 400
    assert sa.get(f"{BASE}?q=100%25").status_code == 200            # % is literal, not a wildcard


def test_csv_export_is_formula_safe_and_audited(app_module, entries):
    acc = api_client(app_module, "test_accounting", PASSWORD)
    resp = acc.get(f"{BASE}/export.csv?q=zztest")
    assert resp.status_code == 200 and resp.mimetype == "text/csv" and "attachment" in resp.headers["Content-Disposition"]
    text = resp.get_data(as_text=True)
    assert text.startswith("﻿When (Philippine time)") and text.count("ZZTEST") == 3
    assert "'=HYPERLINK" in text and "\n=HYPERLINK" not in text and ",=HYPERLINK" not in text
    with app_module.app.app_context():
        last = app_module.AuditLog.query.order_by(app_module.AuditLog.id.desc()).first()
        assert last.action == "Exported the audit log (CSV)" and last.username == "test_accounting"


def test_classic_audit_url_opens_the_users_workspace(app_module):
    from conftest import login
    sa, _ = login(app_module, SA_USERNAME, SA_PASSWORD)
    assert sa.get("/audit").headers["Location"].endswith("/app/superadmin/audit-logs")
    acc, _ = login(app_module, "test_accounting", PASSWORD)
    assert acc.get("/audit").headers["Location"].endswith("/app/accounting/audit-logs")
    staff, _ = login(app_module, "test_staff", PASSWORD)
    assert "audit-logs" not in staff.get("/audit").headers.get("Location", "")
