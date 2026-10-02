"""/api/admin/users — Users & Access Management for the React app.

    GET    /api/admin/users?q=&role=&status=&page=&perPage=   list (server-side filter + pagination)
    POST   /api/admin/users                                   create  {username, password, role}
    PATCH  /api/admin/users/<id>                              change role and/or status {role?, active?, reason?}
    POST   /api/admin/users/<id>/password                     reset another account's password {newPassword}
    DELETE /api/admin/users/<id>                              delete

Same permission keys and rules as the classic /users page (backend/app/core/permissions.py):
  users                -> list, create, change role/status (all Superadmin only)
  reset_user_password  -> reset another account's password
  delete_user          -> delete
Rules kept from the classic page, enforced here (the UI only explains them):
  * Superadmin passwords can't be reset from User Management (own Change Password only)
  * nobody can delete the account they're signed in with
  * the last active Superadmin can't be deleted
Added:
  * an account linked to a resident profile is managed through Resident Accounts, not here
    (deleting it would orphan the profile; the classic page failed with a database error)
  * role/status changes: never on your own account, never removing the last active Superadmin;
    a role change or deactivation ends the account's sessions at once
  * every change and its audit entry (who, what, before/after, optional reason) are saved in one
    transaction; the Superadmin rows are locked while the "last active Superadmin" rule is checked
Password hashes are never returned. Passwords follow app.core.security.password_problem; a created
or reset password is temporary (must be changed at the next sign-in) and a reset ends the
account's existing sessions.
"""
import re

from flask import Blueprint, jsonify, request
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from ..core import security
from ..core.roles import ALL_ROLES, RESIDENT, ROLE_LABELS, SUPER_ADMIN
from ..utils.auth import json_error, permission_required, protect_api_blueprint, signed_in_user

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,80}$")
STAFF_ROLES = tuple(r for r in ALL_ROLES if r != RESIDENT)  # residents are created under Resident Accounts
PER_PAGE_MAX = 100
REASON_MAX = 500


def _iso(value):
    # Stored as naive UTC (datetime.utcnow); sent with an explicit Z, seconds precision.
    return f"{value.isoformat(timespec='seconds')}Z" if value else None


def field_errors(errors, status=400):
    """Same shape as json_error, plus per-field messages for the form."""
    response = jsonify({"error": {"status": status, "message": next(iter(errors.values())), "fields": errors}})
    response.status_code = status
    return response


def make_users_blueprint(*, db, User, audit, generate_password_hash, set_password, end_sessions):
    bp = protect_api_blueprint(Blueprint("api_users", __name__, url_prefix="/api/admin/users"))

    def active_superadmins_except(user_id, lock=False):
        query = User.query.filter(User.role == SUPER_ADMIN, User.active.is_(True), User.id != user_id)
        if lock:
            # Two Superadmins demoting/deleting each other at the same moment must not leave none.
            return len(query.with_for_update().all())
        return query.count()

    def is_resident_account(target):
        return target.role == RESIDENT or getattr(target, "resident_profile", None) is not None

    def reset_block(target):
        if target.role == SUPER_ADMIN:
            return "Superadmin passwords can't be reset here. The account owner changes it from their own account menu."
        return None

    def delete_block(target, me, lock=False):
        if me and target.id == me.id:
            return "You can't delete the account you're signed in with."
        if target.role == SUPER_ADMIN and active_superadmins_except(target.id, lock) == 0:
            return "This is the last active Superadmin account, so it can't be deleted."
        if getattr(target, "resident_profile", None) is not None:
            return "This account belongs to a resident. Remove or deactivate it under Resident Accounts."
        return None

    def edit_block(target, me):
        if me and target.id == me.id:
            return "You can't change your own role or status. Another Superadmin can do it."
        if is_resident_account(target):
            return "This account belongs to a resident. Manage it under Resident Accounts."
        return None

    def row(u, me):
        r, d, e = reset_block(u), delete_block(u, me), edit_block(u, me)
        return {
            "id": u.id,
            "username": u.username,
            "role": u.role,
            "roleLabel": ROLE_LABELS.get(u.role, u.role),
            "active": bool(u.active),
            "createdAt": _iso(u.created_at),
            "mustChangePassword": bool(getattr(u, "must_change_password", False)),
            "passwordChangedAt": _iso(getattr(u, "password_changed_at", None)),
            "isSelf": bool(me and u.id == me.id),
            "canEdit": e is None,
            "editBlockedReason": e,
            "canResetPassword": r is None,
            "resetBlockedReason": r,
            "canDelete": d is None,
            "deleteBlockedReason": d,
        }

    @bp.get("")
    @permission_required("users")
    def list_users():
        me = signed_in_user()
        q = request.args.get("q", "").strip()
        role = request.args.get("role", "").strip()
        status = request.args.get("status", "").strip()
        try:
            page = max(int(request.args.get("page", 1)), 1)
            per_page = min(max(int(request.args.get("perPage", 20)), 1), PER_PAGE_MAX)
        except ValueError:
            return json_error(400, "page and perPage must be numbers.")
        if role and role not in ALL_ROLES:
            return json_error(400, "Unknown role filter.")
        if status and status not in ("active", "inactive"):
            return json_error(400, "Status filter must be active or inactive.")

        query = User.query
        if q:
            escaped = q.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
            query = query.filter(func.lower(User.username).like(f"%{escaped.lower()}%", escape="\\"))
        if role:
            query = query.filter(User.role == role)
        if status:
            query = query.filter(User.active.is_(status == "active"))
        total = query.count()
        users = query.order_by(func.lower(User.username), User.id).offset((page - 1) * per_page).limit(per_page).all()
        return jsonify({
            "users": [row(u, me) for u in users],
            "total": total,
            "page": page,
            "perPage": per_page,
            "roles": [{"value": r, "label": ROLE_LABELS.get(r, r)} for r in ALL_ROLES],
            "creatableRoles": [{"value": r, "label": ROLE_LABELS.get(r, r)} for r in STAFF_ROLES],
            "minPasswordLength": security.PASSWORD_MIN_LENGTH,
        })

    @bp.post("")
    @permission_required("users")
    def create_user():
        data = request.get_json(silent=True) or {}
        username = str(data.get("username", "")).strip()
        password = str(data.get("password", ""))
        role = str(data.get("role", "")).strip()
        errors = {}
        if not USERNAME_RE.match(username):
            errors["username"] = "Use 3–80 letters, numbers, dots, dashes or underscores."
        elif User.query.filter(func.lower(User.username) == username.lower()).first():
            errors["username"] = "That username is already taken."
        problem = security.password_problem(password, username)
        if problem:
            errors["password"] = problem
        if role == RESIDENT:
            errors["role"] = "Resident accounts are created under Resident Accounts, linked to the owner or tenant."
        elif role not in STAFF_ROLES:
            errors["role"] = "Choose a role from the list."
        if errors:
            return field_errors(errors)
        # The administrator chose a temporary password: the user must change it at first sign-in.
        user = User(username=username, password_hash=generate_password_hash(password), role=role, active=True,
                    must_change_password=True)
        db.session.add(user)
        try:
            db.session.flush()   # assigns the id; a username taken a moment ago fails here (unique key)
            audit(f"Created user {username} ({role})", entity_type="user", entity_id=user.id,
                  details={"username": username, "role": role}, commit=False)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return field_errors({"username": "That username is already taken."})
        return jsonify({"user": row(user, signed_in_user())}), 201

    @bp.patch("/<int:user_id>")
    @permission_required("users")
    def update_user(user_id):
        target = db.session.get(User, user_id)
        if not target:
            return json_error(404, "User not found.")
        me = signed_in_user()
        blocked = edit_block(target, me)
        if blocked:
            return json_error(409 if "Resident Accounts" in blocked else 403, blocked)
        data = request.get_json(silent=True) or {}
        errors = {}
        new_role = target.role
        if "role" in data:
            new_role = str(data.get("role") or "").strip()
            if new_role == RESIDENT:
                errors["role"] = "Resident accounts are managed under Resident Accounts."
            elif new_role not in STAFF_ROLES:
                errors["role"] = "Choose a role from the list."
        new_active = bool(target.active)
        if "active" in data:
            if not isinstance(data["active"], bool):
                errors["active"] = "Status must be active or inactive."
            else:
                new_active = data["active"]
        reason = str(data.get("reason") or "").strip()
        if len(reason) > REASON_MAX:
            errors["reason"] = f"Keep the note under {REASON_MAX} characters."
        if errors:
            return field_errors(errors)
        before = {"role": target.role, "active": bool(target.active)}
        after = {"role": new_role, "active": new_active}
        if before == after:
            return json_error(400, "Nothing to change: the role and status are already like this.")
        loses_superadmin = target.role == SUPER_ADMIN and target.active and (new_role != SUPER_ADMIN or not new_active)
        if loses_superadmin and active_superadmins_except(target.id, lock=True) == 0:
            db.session.rollback()
            return json_error(409, "This is the last active Superadmin account. Make another account an active Superadmin first.")

        target.role, target.active = new_role, new_active
        if new_role != before["role"] or (before["active"] and not new_active):
            # New permissions or no access: every open session of the account ends now.
            end_sessions(target)
        changes = []
        if new_role != before["role"]:
            changes.append(f"role {ROLE_LABELS.get(before['role'], before['role'])} -> {ROLE_LABELS.get(new_role, new_role)}")
        if new_active != before["active"]:
            changes.append("activated" if new_active else "deactivated")
        audit(f"Updated user {target.username}: {', '.join(changes)}", entity_type="user", entity_id=target.id,
              reason=reason or None, details={"before": before, "after": after}, commit=False)
        db.session.commit()
        return jsonify({"user": row(target, me)})

    @bp.post("/<int:user_id>/password")
    @permission_required("reset_user_password")
    def reset_password(user_id):
        target = db.session.get(User, user_id)
        if not target:
            return json_error(404, "User not found.")
        me = signed_in_user()
        if target.id == me.id:
            return json_error(403, "To change your own password, use Change password in your account menu.")
        blocked = reset_block(target)
        if blocked:
            return json_error(403, blocked)
        new_password = str((request.get_json(silent=True) or {}).get("newPassword", ""))
        problem = security.password_problem(new_password, target.username)
        if problem:
            return field_errors({"newPassword": problem})
        # Temporary password: every session of the account ends now; a new password is required at next sign-in.
        set_password(target, new_password, temporary=True)
        audit(f"Reset password for user {target.username}", entity_type="user", entity_id=target.id, commit=False)
        db.session.commit()
        return "", 204

    @bp.delete("/<int:user_id>")
    @permission_required("delete_user")
    def delete_user(user_id):
        target = db.session.get(User, user_id)
        if not target:
            return json_error(404, "User not found.")
        blocked = delete_block(target, signed_in_user(), lock=True)
        if blocked:
            db.session.rollback()
            return json_error(409 if "Resident Accounts" in blocked else 403, blocked)
        username, role, uid = target.username, target.role, target.id
        audit(f"Deleted user {username} ({role})", entity_type="user", entity_id=uid,
              details={"username": username, "role": role, "active": bool(target.active)}, commit=False)
        db.session.delete(target)
        db.session.commit()
        return "", 204

    return bp
