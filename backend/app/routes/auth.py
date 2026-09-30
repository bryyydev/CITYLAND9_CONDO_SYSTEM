"""/api/auth — session-cookie authentication for the React frontend.

Same accounts, password hashes, roles and session keys as the legacy login, so a
user signed in through React is also signed in on the legacy pages (and back).

    GET  /api/auth/csrf     -> {"csrfToken": "..."}         (call before any POST)
    POST /api/auth/login    {"username", "password"}        -> {"user": {...}, "csrfToken"}
    POST /api/auth/logout                                   -> 204
    GET  /api/auth/me                                       -> {"user": {...}} or 401
    POST /api/auth/password {"currentPassword", "newPassword"}  -> 204 (same rules as legacy)

user = {username, role, roleLabel, portal, home, unitId, permissions[]}
"""
from flask import Blueprint, jsonify, request, session

from ..core.permissions import permissions_for_role
from ..core.roles import PORTALS, RESIDENT, ROLE_LABELS
from ..utils.auth import csrf_token, json_error, protect_api_blueprint, signed_in_user


def make_auth_blueprint(*, User, audit, check_password_hash, generate_password_hash, commit, home_endpoint,
                        resident_access_problem):
    """Build the blueprint with the legacy app's objects (no import cycle)."""
    bp = protect_api_blueprint(Blueprint("api_auth", __name__, url_prefix="/api"))

    def user_payload(user):
        return {
            "username": user.username,
            "role": user.role,
            "roleLabel": ROLE_LABELS.get(user.role, user.role),
            "portal": PORTALS.get(user.role),
            "home": home_endpoint(user),
            "unitId": user.resident_profile.unit_id if user.role == RESIDENT else None,
            "permissions": permissions_for_role(user.role),
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
        # Same rule as the legacy login: only active accounts may sign in.
        user = User.query.filter_by(username=username, active=True).first()
        if not user or not check_password_hash(user.password_hash, password):
            return json_error(401, "Invalid username or password.")
        if user.role == RESIDENT:
            problem = resident_access_problem(user)
            if problem:
                return json_error(403, problem)
        session.clear()
        session["user_id"] = user.id
        session["username"] = user.username
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
        if not user or not user.active:
            return json_error(401, "Not signed in.")
        if user.role == RESIDENT and resident_access_problem(user):
            session.clear()  # access ended (e.g. moved out) while signed in -> back to the login page
            return json_error(401, "Not signed in.")
        return jsonify({"user": user_payload(user)})

    @bp.post("/auth/password")
    def change_password():
        user = signed_in_user()
        if not user or not user.active:
            return json_error(401, "Not signed in.")
        data = request.get_json(silent=True) or {}
        current = str(data.get("currentPassword", ""))
        new = str(data.get("newPassword", ""))
        # Same rules as the legacy Change Password page.
        if not check_password_hash(user.password_hash, current):
            return json_error(400, "Current password is incorrect.")
        if len(new) < 8:
            return json_error(400, "New password must be at least 8 characters.")
        if check_password_hash(user.password_hash, new):
            return json_error(400, "New password must be different from the current password.")
        user.password_hash = generate_password_hash(new)
        commit()
        audit(f"Changed password for user {user.username}")
        return "", 204

    @bp.route("/<path:unknown>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def not_found(unknown):
        return json_error(404, "Unknown API endpoint.")

    return bp
