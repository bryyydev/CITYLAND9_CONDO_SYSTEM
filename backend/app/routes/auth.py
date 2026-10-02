"""/api/auth — session-cookie authentication for the React frontend.

Same accounts, password hashes, roles and session as the classic screens, so a user signed
in through React is also signed in on the classic pages (and back).

    GET  /api/auth/csrf     -> {"csrfToken": "..."}         (call before any POST)
    POST /api/auth/login    {"username", "password"}        -> {"user": {...}, "csrfToken"}
    POST /api/auth/logout                                   -> 204
    GET  /api/auth/me                                       -> {"user": {...}} or 401
    POST /api/auth/password {"currentPassword", "newPassword"}  -> 204 (other sessions end)

user = {username, role, roleLabel, portal, home, unitId, permissions[], mustChangePassword, passwordPolicy}

Sign-in is throttled per account+client and per client (app.core.security.LoginThrottle);
failures always answer "Invalid username or password." Sessions are validated on every request
by the application's current_user() (active account, session version, idle/absolute expiry).
"""
from flask import Blueprint, current_app, jsonify, request, session

from ..core import security
from ..core.permissions import permissions_for_role
from ..core.roles import PORTALS, RESIDENT, ROLE_LABELS
from ..utils.auth import csrf_token, json_error, protect_api_blueprint, signed_in_user


def make_auth_blueprint(*, User, audit, check_password_hash, commit, home_endpoint, resident_access_problem,
                        start_session, set_password, client_address):
    """Build the blueprint with the application's objects (no import cycle)."""
    bp = protect_api_blueprint(Blueprint("api_auth", __name__, url_prefix="/api"))

    def user_payload(user):
        return {
            "username": user.username,
            "role": user.role,
            "roleLabel": ROLE_LABELS.get(user.role, user.role),
            "portal": PORTALS.get(user.role),
            "home": home_endpoint(user),
            "unitId": user.resident_profile.unit_id if user.role == RESIDENT and user.resident_profile else None,
            "permissions": permissions_for_role(user.role),
            "mustChangePassword": bool(user.must_change_password),
            "passwordPolicy": security.password_policy(),
        }

    @bp.get("/auth/csrf")
    def get_csrf():
        return jsonify({"csrfToken": csrf_token()})

    @bp.post("/auth/login")
    def login():
        data = request.get_json(silent=True) or {}
        username = str(data.get("username", "")).strip()
        password = str(data.get("password", ""))
        if not username or not password:
            return json_error(400, "Username and password are required.")
        client = client_address()
        wait = security.login_throttle.retry_after(username, client)
        if wait:
            current_app.logger.warning("Sign-in throttled for %r from %s", username, client)
            response = json_error(429, f"Too many failed sign-in attempts. Try again in {max(wait // 60, 1)} minute(s).")
            response.headers["Retry-After"] = str(wait)
            return response
        # Only active accounts may sign in; every failure gets the same message.
        user = User.query.filter_by(username=username, active=True).first()
        if not user or not check_password_hash(user.password_hash, password):
            security.login_throttle.failure(username, client)
            current_app.logger.warning("Failed sign-in for %r from %s", username, client)
            return json_error(401, "Invalid username or password.")
        if user.role == RESIDENT:
            problem = resident_access_problem(user)
            if problem:
                return json_error(403, problem)
        security.login_throttle.success(username, client)
        if password in security.SHIPPED_DEFAULT_PASSWORDS and not user.must_change_password:
            user.must_change_password = True   # still the old automatic default password
            commit()
        start_session(user)
        if user.role == RESIDENT:
            # Convenience copy for the UI. Authorization always re-reads the unit from the database.
            session["unit_id"] = user.resident_profile.unit_id
        csrf_token()  # new token for the new session
        audit("Login")
        return jsonify({"user": user_payload(user), "csrfToken": session["csrf_token"]})

    @bp.post("/auth/logout")
    def logout():
        if session.get("user_id"):
            audit("Logout")
        session.clear()
        return "", 204

    @bp.get("/auth/me")
    def me():
        user = signed_in_user()
        if not user:
            return json_error(401, "Not signed in.")
        if user.role == RESIDENT and resident_access_problem(user):
            session.clear()  # access ended (e.g. moved out) while signed in -> back to the login page
            return json_error(401, "Not signed in.")
        return jsonify({"user": user_payload(user)})

    @bp.post("/auth/password")
    def change_password():
        """The signed-in user changes their OWN password (also the forced first-sign-in change)."""
        user = signed_in_user()
        if not user:
            return json_error(401, "Not signed in.")
        data = request.get_json(silent=True) or {}
        current = str(data.get("currentPassword", ""))
        new = str(data.get("newPassword", ""))
        if not check_password_hash(user.password_hash, current):
            return json_error(400, "Current password is incorrect.")
        problem = security.password_problem(new, user.username)
        if problem:
            return json_error(400, problem)
        if check_password_hash(user.password_hash, new):
            return json_error(400, "New password must be different from the current password.")
        # Ends the account's other sessions (other PCs/browsers); this one stays signed in.
        set_password(user, new, temporary=False, keep_current_session=True)
        commit()
        audit(f"Changed password for user {user.username}")
        return "", 204

    @bp.route("/<path:unknown>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def not_found(unknown):
        return json_error(404, "Unknown API endpoint.")

    return bp
