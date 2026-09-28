"""/api/auth — session-cookie authentication for the React frontend.

Same accounts, password hashes, roles and session keys as the legacy login, so a
user signed in through React is also signed in on the legacy pages (and back).

    GET  /api/auth/csrf     -> {"csrfToken": "..."}         (call before any POST)
    POST /api/auth/login    {"username", "password"}        -> {"user": {...}}
    POST /api/auth/logout                                   -> 204
    GET  /api/auth/me                                       -> {"user": {...}} or 401

Every POST/PUT/PATCH/DELETE under /api must send the token in the X-CSRFToken
header (docs/architecture.md, section 3).
"""
import hmac
import secrets

from flask import Blueprint, jsonify, request, session

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = session["csrf_token"] = secrets.token_urlsafe(32)
    return token


def error(status, message):
    response = jsonify({"error": {"status": status, "message": message}})
    response.status_code = status
    return response


def make_auth_blueprint(*, db, User, audit, check_password_hash, current_user, home_endpoint, permissions_for):
    """Build the blueprint with the legacy app's objects (no import cycle)."""
    bp = Blueprint("api_auth", __name__, url_prefix="/api")

    def user_payload(user):
        return {
            "username": user.username,
            "role": user.role,
            "home": home_endpoint(user),
            "permissions": sorted(permissions_for(user)),
        }

    @bp.before_request
    def check_csrf():
        if request.method in UNSAFE_METHODS:
            sent = request.headers.get("X-CSRFToken", "")
            expected = session.get("csrf_token", "")
            if not expected or not hmac.compare_digest(sent, expected):
                return error(403, "Missing or invalid CSRF token. Reload the page and try again.")

    @bp.get("/auth/csrf")
    def get_csrf():
        return jsonify({"csrfToken": csrf_token()})

    @bp.post("/auth/login")
    def login():
        data = request.get_json(silent=True) or {}
        username = str(data.get("username", "")).strip()
        password = str(data.get("password", ""))
        if not username or not password:
            return error(400, "Username and password are required.")
        # Same rule as the legacy login: only active accounts may sign in.
        user = User.query.filter_by(username=username, active=True).first()
        if not user or not check_password_hash(user.password_hash, password):
            return error(401, "Invalid username or password.")
        session.clear()
        session["user_id"] = user.id
        session["username"] = user.username
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
        user = current_user()
        if not user:
            return error(401, "Not signed in.")
        return jsonify({"user": user_payload(user)})

    @bp.route("/<path:unknown>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def not_found(unknown):
        return error(404, "Unknown API endpoint.")

    for status, message in ((400, "Bad request."), (404, "Not found."), (405, "Method not allowed."),
                            (500, "Server error. The error was logged.")):
        bp.register_error_handler(status, lambda exc, s=status, m=message: error(s, m))

    return bp
