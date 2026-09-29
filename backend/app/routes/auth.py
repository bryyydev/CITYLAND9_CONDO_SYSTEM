"""/api/auth — session-cookie authentication for the React frontend.

Same accounts, password hashes, roles and session keys as the legacy login, so a
user signed in through React is also signed in on the legacy pages (and back).

    GET  /api/auth/csrf     -> {"csrfToken": "..."}         (call before any POST)
    POST /api/auth/login    {"username", "password"}        -> {"user": {...}, "csrfToken"}
    POST /api/auth/logout                                   -> 204
    GET  /api/auth/me                                       -> {"user": {...}} or 401

user = {username, role, roleLabel, portal, home, unitId, permissions[]}
"""
from flask import Blueprint, jsonify, request, session

from ..core.permissions import permissions_for_role
from ..core.roles import PORTALS, RESIDENT, ROLE_LABELS
from ..utils.auth import csrf_token, json_error, protect_api_blueprint, signed_in_user


def make_auth_blueprint(*, User, audit, check_password_hash, home_endpoint, resident_unit_id):
    """Build the blueprint with the legacy app's objects (no import cycle)."""
    bp = protect_api_blueprint(Blueprint("api_auth", __name__, url_prefix="/api"))

    def user_payload(user):
        return {
            "username": user.username,
            "role": user.role,
            "roleLabel": ROLE_LABELS.get(user.role, user.role),
            "portal": PORTALS.get(user.role),
            "home": home_endpoint(user),
            "unitId": resident_unit_id(user) if user.role == RESIDENT else None,
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
        if user.role == RESIDENT and resident_unit_id(user) is None:
            return json_error(403, "Your resident account is not linked to a unit yet. Please contact the administrator.")
        session.clear()
        session["user_id"] = user.id
        session["username"] = user.username
        if user.role == RESIDENT:
            # Convenience copy for the UI. Authorization always re-reads the unit from the database.
            session["unit_id"] = resident_unit_id(user)
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
        return jsonify({"user": user_payload(user)})

    @bp.route("/<path:unknown>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def not_found(unknown):
        return json_error(404, "Unknown API endpoint.")

    return bp
