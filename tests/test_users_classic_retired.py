"""The classic Users & Access screen moved to the React app (/app/superadmin/users).

The old URLs only redirect: GET opens the React page, and a form posted from an old open tab
changes nothing (all account changes go through /api/admin/users and its rules). Classic screens
link to the React page and back to the workspace, so moving between the two designs is consistent.
"""
from werkzeug.security import check_password_hash, generate_password_hash

from conftest import PASSWORD, login

USERS_APP = "/app/superadmin/users"


def classic_client(app_module, username, password):
    return login(app_module, username, password)[0]


def make_staff(app_module, username="classic_target"):
    with app_module.app.app_context():
        u = app_module.User(username=username, password_hash=generate_password_hash("Keep-This-Pass-1"), role="staff", active=True)
        app_module.db.session.add(u)
        app_module.db.session.commit()
        return u.id


def drop(app_module, user_id):
    with app_module.app.app_context():
        u = app_module.db.session.get(app_module.User, user_id)
        if u:
            app_module.db.session.delete(u)
            app_module.db.session.commit()


def test_classic_users_page_opens_the_react_page(app_module, superadmin):
    resp = superadmin.get("/users")
    assert resp.status_code == 302 and resp.headers["Location"].endswith(USERS_APP)


def test_classic_forms_change_nothing(app_module, superadmin):
    target = make_staff(app_module)
    try:
        with app_module.app.app_context():
            before = app_module.User.query.count()
        resp = superadmin.post("/users", data={"username": "classic_new", "password": "Long-enough-Pass-1", "role": "resident"})
        assert resp.status_code == 303 and resp.headers["Location"].endswith(f"{USERS_APP}?moved=form")
        assert superadmin.post(f"/users/{target}/reset-password",
                               data={"new_password": "Changed-Pass-99", "confirm_password": "Changed-Pass-99"}).status_code == 303
        assert superadmin.post(f"/users/{target}/delete").status_code == 303
        with app_module.app.app_context():
            assert app_module.User.query.count() == before
            assert app_module.User.query.filter_by(username="classic_new").first() is None
            kept = app_module.db.session.get(app_module.User, target)
            assert kept is not None and check_password_hash(kept.password_hash, "Keep-This-Pass-1")
    finally:
        drop(app_module, target)


def test_classic_user_urls_still_refuse_other_roles_and_signed_out(app_module):
    target = make_staff(app_module)
    try:
        anon = app_module.app.test_client()
        assert "/login" in anon.get("/users").headers["Location"]
        staff = classic_client(app_module, "test_staff", PASSWORD)
        resp = staff.get("/users")
        assert not resp.headers.get("Location", "").endswith(USERS_APP)          # not sent to the Superadmin page
        assert staff.post(f"/users/{target}/delete").status_code in (302, 403)
        with app_module.app.app_context():
            assert app_module.db.session.get(app_module.User, target) is not None
    finally:
        drop(app_module, target)


def test_classic_forms_still_need_the_csrf_token(app_module, superadmin):
    superadmin.auto_csrf = False
    try:
        resp = superadmin.post("/users", data={"username": "no_token"})
    finally:
        superadmin.auto_csrf = True
    assert resp.status_code == 400 and "expired" in resp.get_data(as_text=True).lower()


def test_classic_screens_link_to_react_users_and_back(app_module, superadmin):
    html = superadmin.get("/change-password").get_data(as_text=True)
    assert f'href="{USERS_APP}"' in html and 'href="/users"' not in html
    assert 'href="/app/"' in html and "Back to workspace" in html
