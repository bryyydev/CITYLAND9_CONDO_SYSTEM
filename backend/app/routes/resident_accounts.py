"""/api/admin/resident-accounts — Resident Accounts (resident portal logins) for the React app.

    GET   /api/admin/resident-accounts?q=&status=&page=&perPage=   list (filter + pagination)
    GET   /api/admin/resident-accounts/options                     active units with their current owners/tenants
    POST  /api/admin/resident-accounts                             create {username, password, unitId, personType, personId?, displayName?}
    PATCH /api/admin/resident-accounts/<user_id>                   change link / name / status {unitId?, personType?, personId?, displayName?, active?, reason?}
    POST  /api/admin/resident-accounts/<user_id>/password          set a temporary password {newPassword}

Every route needs the classic page's permission key `resident_users` (Superadmin only,
backend/app/core/permissions.py). Rows are keyed by the USER id, so resident logins that the old
screens left without a resident profile are listed too and can be linked here (Users & Access
refuses to manage resident logins and sends the administrator to this page).

Rules (the UI only explains them):
  * username 3–80 letters/numbers/._- , unique ignoring case; password per security.password_problem;
    a created or reset password is temporary (must be changed at the next sign-in)
  * the unit must be active; an owner/tenant link must be a CURRENT owner/tenant of that unit, and
    one owner/tenant record can have only one active portal account
  * no delete: maintenance tickets and the audit log refer to the profile. Deactivating ends access
    (account + profile inactive) and signs the resident out everywhere; so does moving them to
    another unit or changing the link (their session caches the unit)
  * each change and its audit entry are saved in one transaction
Access also ends automatically when the linked owner/tenant moves out (legacy.resident_access_problem);
the list shows that reason. Password hashes are never returned.
"""
import re

from flask import Blueprint, jsonify, request
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from ..core import security
from ..core.roles import RESIDENT
from ..utils.auth import json_error, permission_required, protect_api_blueprint

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,80}$")
PERSON_TYPES = ("Owner", "Tenant")
STATUSES = ("active", "ended", "inactive", "unlinked")
PER_PAGE_MAX = 100
NAME_MAX = 200
REASON_MAX = 500


def _iso(value):
    return f"{value.isoformat(timespec='seconds')}Z" if value else None


def field_errors(errors, status=400):
    response = jsonify({"error": {"status": status, "message": next(iter(errors.values())), "fields": errors}})
    response.status_code = status
    return response


def make_resident_accounts_blueprint(legacy, *, end_sessions):
    """`legacy` is the legacy_app module (models, audit, resident_access_problem, password helpers)."""
    db = legacy.db
    User, Profile, Unit, Owner, Tenant = legacy.User, legacy.ResidentProfile, legacy.Unit, legacy.Owner, legacy.Tenant
    bp = protect_api_blueprint(Blueprint("api_resident_accounts", __name__, url_prefix="/api/admin/resident-accounts"))

    def person_model(person_type):
        return Tenant if person_type == "Tenant" else Owner

    def person_name(person):
        return getattr(person, "tenant_name", None) or getattr(person, "owner_name", None)

    def linked_person(profile):
        if not profile or not profile.person_id:
            return None
        person = db.session.get(person_model(profile.person_type), profile.person_id)
        return person if person and person.unit_id == profile.unit_id else None

    def resident_users():
        with_profile = db.session.query(Profile.user_id)
        return User.query.filter((User.role == RESIDENT) | User.id.in_(with_profile))

    def status_of(user, profile):
        """(status, reason): active | inactive (deactivated here) | ended (moved out, unit closed) | unlinked (no unit)."""
        if not profile:
            return "unlinked", "This login isn't linked to a unit, so it can't use the resident portal. Link it to a unit."
        if not user.active or not profile.active:
            return "inactive", "Deactivated. The resident can't sign in until the account is reactivated."
        problem = legacy.resident_access_problem(user)
        return ("ended", problem) if problem else ("active", None)

    def row(user):
        profile = getattr(user, "resident_profile", None)
        person = linked_person(profile)
        status, reason = status_of(user, profile)
        unit = db.session.get(Unit, profile.unit_id) if profile else None
        return {
            "id": user.id,
            "username": user.username,
            "displayName": profile.display_name if profile else user.username,
            "unit": {"id": unit.id, "unitNo": unit.unit_no} if unit else None,
            "personType": (profile.person_type if profile else None) or "Owner",
            "personId": profile.person_id if profile else None,
            "linked": person is not None,
            "linkedName": person_name(person) if person else None,
            "linkedStatus": person.status if person else None,
            "status": status,
            "statusReason": reason,
            "active": bool(user.active and profile and profile.active),
            "mustChangePassword": bool(getattr(user, "must_change_password", False)),
            "createdAt": _iso((profile.created_at if profile else None) or user.created_at),
        }

    def validate_link(data, errors, current_user_id=None, partial=None):
        """Reads unitId / personType / personId / displayName. `partial` is the existing profile on
        PATCH (missing fields keep their value). Returns (unit, person_type, person, display_name)."""
        def pick(key, existing):
            return data[key] if key in data else existing

        unit_id = pick("unitId", partial.unit_id if partial else None)
        person_type = str(pick("personType", partial.person_type if partial else "Owner") or "Owner")
        person_id = pick("personId", partial.person_id if partial else None)
        display_name = str(pick("displayName", partial.display_name if partial else "") or "").strip()

        unit = None
        try:
            unit = db.session.get(Unit, int(unit_id)) if unit_id not in (None, "") else None
        except (TypeError, ValueError):
            unit = None
        if not unit or not unit.active:
            errors["unitId"] = "Choose an active unit."
        if person_type not in PERSON_TYPES:
            errors["personType"] = "Choose Owner or Tenant."
        person = None
        if person_id not in (None, ""):
            try:
                person = db.session.get(person_model(person_type), int(person_id))
            except (TypeError, ValueError):
                person = None
            unchanged = (partial is not None and person is not None and partial.person_id == person.id
                         and partial.person_type == person_type and unit is not None and partial.unit_id == unit.id)
            if not person or (unit and person.unit_id != unit.id):
                errors["personId"] = f"Choose a {person_type.lower()} of this unit."
                person = None
            elif unchanged:
                pass   # keeping an existing link (even after a move-out: access then stays ended)
            elif person.status != "Current":
                errors["personId"] = f"{person_name(person)} is no longer a current {person_type.lower()} of this unit."
                person = None
            else:
                taken = (Profile.query.join(User, User.id == Profile.user_id)
                         .filter(Profile.person_type == person_type, Profile.person_id == person.id,
                                 Profile.active.is_(True), User.active.is_(True)))
                if current_user_id is not None:
                    taken = taken.filter(Profile.user_id != current_user_id)
                other = taken.first()
                if other:
                    errors["personId"] = f"{person_name(person)} already has a portal account ({other.user.username})."
        if not display_name and person:
            display_name = person_name(person)
        if not display_name:
            errors["displayName"] = "Enter the resident's name."
        elif len(display_name) > NAME_MAX:
            errors["displayName"] = f"Keep the name under {NAME_MAX} characters."
        return unit, person_type, person, display_name

    @bp.get("")
    @permission_required("resident_users")
    def list_accounts():
        q = request.args.get("q", "").strip().lower()
        status = request.args.get("status", "").strip()
        try:
            page = max(int(request.args.get("page", 1)), 1)
            per_page = min(max(int(request.args.get("perPage", 20)), 1), PER_PAGE_MAX)
        except ValueError:
            return json_error(400, "page and perPage must be numbers.")
        if status and status not in STATUSES:
            return json_error(400, "Unknown status filter.")
        # A condominium has hundreds of residents at most; the status needs the access rules, so
        # filtering happens here rather than in SQL.
        rows = [row(u) for u in resident_users().order_by(func.lower(User.username), User.id).all()]
        counts = {s: sum(1 for r in rows if r["status"] == s) for s in STATUSES}
        counts["notLinkedToPerson"] = sum(1 for r in rows if r["unit"] and not r["linked"])
        if q:
            rows = [r for r in rows if q in r["username"].lower() or q in (r["displayName"] or "").lower()
                    or (r["unit"] and q in r["unit"]["unitNo"].lower()) or q in (r["linkedName"] or "").lower()]
        if status:
            rows = [r for r in rows if r["status"] == status]
        start = (page - 1) * per_page
        return jsonify({
            "accounts": rows[start:start + per_page],
            "total": len(rows),
            "page": page,
            "perPage": per_page,
            "counts": counts,
            "minPasswordLength": security.PASSWORD_MIN_LENGTH,
        })

    @bp.get("/options")
    @permission_required("resident_users")
    def options():
        linked = {(p.person_type, p.person_id): p.user.username
                  for p in Profile.query.join(User, User.id == Profile.user_id)
                  .filter(Profile.person_id.isnot(None), Profile.active.is_(True), User.active.is_(True)).all()}
        units = []
        for unit in Unit.query.filter_by(active=True).order_by(Unit.unit_no).all():
            people = []
            for person_type, model in (("Owner", Owner), ("Tenant", Tenant)):
                for p in model.query.filter_by(unit_id=unit.id, status="Current").order_by(model.id).all():
                    people.append({"id": p.id, "personType": person_type, "name": person_name(p),
                                   "account": linked.get((person_type, p.id))})
            units.append({"id": unit.id, "unitNo": unit.unit_no, "people": people})
        return jsonify({"units": units, "personTypes": list(PERSON_TYPES), "minPasswordLength": security.PASSWORD_MIN_LENGTH})

    @bp.post("")
    @permission_required("resident_users")
    def create_account():
        data = request.get_json(silent=True) or {}
        username = str(data.get("username", "")).strip()
        password = str(data.get("password", ""))
        errors = {}
        if not USERNAME_RE.match(username):
            errors["username"] = "Use 3–80 letters, numbers, dots, dashes or underscores."
        elif User.query.filter(func.lower(User.username) == username.lower()).first():
            errors["username"] = "That username is already taken."
        problem = security.password_problem(password, username)
        if problem:
            errors["password"] = problem
        unit, person_type, person, display_name = validate_link(data, errors)
        if errors:
            return field_errors(errors)
        user = User(username=username, password_hash=legacy.generate_password_hash(password), role=RESIDENT,
                    active=True, must_change_password=True)
        db.session.add(user)
        try:
            db.session.flush()
            db.session.add(Profile(user_id=user.id, unit_id=unit.id, person_type=person_type,
                                   person_id=person.id if person else None, display_name=display_name, active=True))
            legacy.audit(f"Created resident portal user {username} for unit {unit.unit_no}", entity_type="user",
                         entity_id=user.id, commit=False,
                         details={"username": username, "unit": unit.unit_no, "personType": person_type,
                                  "personId": person.id if person else None, "displayName": display_name})
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return field_errors({"username": "That username is already taken."})
        return jsonify({"account": row(user)}), 201

    def get_target(user_id):
        user = db.session.get(User, user_id)
        if not user or not (user.role == RESIDENT or getattr(user, "resident_profile", None)):
            return None
        return user

    @bp.patch("/<int:user_id>")
    @permission_required("resident_users")
    def update_account(user_id):
        user = get_target(user_id)
        if not user:
            return json_error(404, "Resident account not found.")
        profile = user.resident_profile
        data = request.get_json(silent=True) or {}
        errors = {}
        link_keys = {"unitId", "personType", "personId", "displayName"}
        changes_link = bool(link_keys & set(data)) or profile is None
        if profile is None and "unitId" not in data:
            errors["unitId"] = "Choose the unit this login belongs to."
        if changes_link:
            unit, person_type, person, display_name = validate_link(data, errors, current_user_id=user.id, partial=profile)
        new_active = bool(user.active and (profile.active if profile else True))
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

        def snapshot():
            return {"unitId": profile.unit_id if profile else None, "personType": profile.person_type if profile else None,
                    "personId": profile.person_id if profile else None, "displayName": profile.display_name if profile else None,
                    "active": bool(user.active and (profile.active if profile else True))}

        before = snapshot()
        if changes_link:
            after_link = {"unitId": unit.id, "personType": person_type, "personId": person.id if person else None,
                          "displayName": display_name}
        else:
            after_link = {k: before[k] for k in ("unitId", "personType", "personId", "displayName")}
        after = {**after_link, "active": new_active}
        if before == after:
            return json_error(400, "Nothing to change: the account is already like this.")

        if profile is None:
            profile = Profile(user_id=user.id, unit_id=after["unitId"], display_name=after["displayName"])
            db.session.add(profile)
        profile.unit_id, profile.person_type = after["unitId"], after["personType"]
        profile.person_id, profile.display_name = after["personId"], after["displayName"]
        profile.active = new_active
        user.active = new_active
        if user.role != RESIDENT:
            user.role = RESIDENT   # a profile only ever belongs to a resident login
        moved = before["unitId"] != after["unitId"] or before["personId"] != after["personId"] \
            or before["personType"] != after["personType"]
        if (before["active"] and not new_active) or (moved and before["unitId"] is not None):
            end_sessions(user)

        parts = []
        if before["unitId"] != after["unitId"]:
            unit_no = db.session.get(Unit, after["unitId"]).unit_no
            parts.append(f"unit -> {unit_no}")
        if (before["personType"], before["personId"]) != (after["personType"], after["personId"]):
            parts.append(f"linked to {after['personType'].lower()} #{after['personId']}" if after["personId"] else "owner/tenant link removed")
        if before["displayName"] != after["displayName"]:
            parts.append("name changed")
        if before["active"] != new_active:
            parts.append("reactivated" if new_active else "deactivated")
        legacy.audit(f"Updated resident portal user {user.username}: {', '.join(parts)}", entity_type="user",
                     entity_id=user.id, reason=reason or None, details={"before": before, "after": after}, commit=False)
        db.session.commit()
        return jsonify({"account": row(user)})

    @bp.post("/<int:user_id>/password")
    @permission_required("resident_users")
    def reset_password(user_id):
        user = get_target(user_id)
        if not user:
            return json_error(404, "Resident account not found.")
        new_password = str((request.get_json(silent=True) or {}).get("newPassword", ""))
        problem = security.password_problem(new_password, user.username)
        if problem:
            return field_errors({"newPassword": problem})
        legacy.set_password(user, new_password, temporary=True)
        legacy.audit(f"Reset password for resident portal user {user.username}", entity_type="user",
                     entity_id=user.id, commit=False)
        db.session.commit()
        return "", 204

    return bp
