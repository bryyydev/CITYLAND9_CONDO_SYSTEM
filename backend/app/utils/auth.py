"""API authorization decorators (JSON responses).

    @role_required(["ADMIN", "ACCOUNTING"])       role check           -> 401 / 403 JSON
    @permission_required("billing")               matrix check         -> 401 / 403 JSON
    @require_unit_ownership("unit_id")            IDOR guard (bug D2)  -> 403 JSON for other units

The application registers how to find the signed-in user (init_auth), so this module
does not import the models and can be reused by every blueprint.
"""
import hmac
import secrets
from functools import wraps

from flask import current_app, g, jsonify, request, session

from ..core.permissions import can
from ..core.roles import RESIDENT, normalize_role

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def init_auth(app, *, current_user, resident_unit_id, resident_access_problem=None, resident_unit_ids=None):
    """current_user() -> User or None;  resident_unit_id(user) -> int or None (None = no access);
    resident_unit_ids(user) -> every unit the resident may open (one account, several units);
    resident_access_problem(user) -> reason text or None."""
    app.extensions["cityland9_auth"] = {"current_user": current_user, "resident_unit_id": resident_unit_id,
                                        "resident_access_problem": resident_access_problem,
                                        "resident_unit_ids": resident_unit_ids}


def _hooks():
    return current_app.extensions["cityland9_auth"]


def signed_in_user():
    if "cityland9_user" not in g:
        g.cityland9_user = _hooks()["current_user"]()
    return g.cityland9_user


def json_error(status, message):
    response = jsonify({"error": {"status": status, "message": message}})
    response.status_code = status
    return response


def _authorize(check):
    """Shared wrapper: 401 when signed out, 403 when `check(user)` is False."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = signed_in_user()
            if not user or not user.active:
                return json_error(401, "Not signed in.")
            if not check(user):
                current_app.logger.warning("403 %s %s for user=%s role=%s", request.method, request.path, user.username, user.role)
                return json_error(403, "You do not have permission to access this function.")
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def role_required(roles):
    """Allow only the given roles. Accepts 'SUPERADMIN'/'super_admin' spellings."""
    allowed = {normalize_role(r) for r in roles}
    return _authorize(lambda user: user.role in allowed)


def permission_required(permission):
    """Allow the roles that core/permissions.py grants for `permission`."""
    return _authorize(lambda user: can(user.role, permission))


def require_unit_ownership(unit_param="unit_id"):
    """IDOR protection for unit-scoped endpoints.

    A RESIDENT may only reach the units their account is linked to (and still eligible for): the unit id in
    the URL (route parameter `unit_param`) must match, otherwise 403. Other roles pass
    through unchanged (their access is decided by @role_required / @permission_required,
    which must be applied as well).

    The resident's unit is read from the database on every request, not trusted from the
    browser, so it cannot go stale or be forged.
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = signed_in_user()
            if not user:
                return json_error(401, "Not signed in.")
            if user.role == RESIDENT:
                ids_hook = _hooks().get("resident_unit_ids")
                own_units = ids_hook(user) if ids_hook else [u for u in [_hooks()["resident_unit_id"](user)] if u is not None]
                requested = kwargs.get(unit_param)
                if not own_units:
                    reason = _hooks()["resident_access_problem"]
                    return json_error(403, (reason(user) if reason else None) or "Your resident portal access is not active.")
                if requested is None or int(requested) not in {int(u) for u in own_units}:
                    current_app.logger.warning("IDOR blocked: resident %s requested unit %s (own units %s)", user.username, requested, own_units)
                    return json_error(403, "You can only view your own unit.")
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# ---- CSRF (cookie sessions need it; see docs/architecture.md section 3) -------------------
def csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = session["csrf_token"] = secrets.token_urlsafe(32)
    return token


def protect_api_blueprint(bp):
    """CSRF check on state-changing requests + JSON error pages for one API blueprint."""
    @bp.before_request
    def _check_csrf():
        if request.method in UNSAFE_METHODS:
            sent = request.headers.get("X-CSRFToken", "")
            expected = session.get("csrf_token", "")
            if not expected or not hmac.compare_digest(sent, expected):
                return json_error(403, "Missing or invalid CSRF token. Reload the page and try again.")

    for status, message in ((400, "Bad request."), (404, "Not found."), (405, "Method not allowed."),
                            (500, "Server error. The error was logged.")):
        bp.register_error_handler(status, lambda exc, s=status, m=message: json_error(s, m))
    return bp
