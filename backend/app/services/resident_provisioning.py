"""Resident portal account provisioning (Superadmin: permission `resident_users`).

An administrator registers an owner/tenant (or picks an existing one) and the system creates the
resident portal account: a generated username and a one-time ACTIVATION CODE. The resident never
signs up; they sign in once with the code and must choose their own password.

The activation code IS the existing temporary-password mechanism (no second mechanism):
  * stored only as a password hash; must_change_password restricts that first session to the
    password change; sign-in is throttled; the password policy applies to the chosen password
  * generated here (cryptographically random, 12 characters from an unambiguous alphabet, about
    59 bits), shown ONCE in the response to the administrator who issued it, never stored in plain
    text, never logged or audited, never in a URL or a later response
  * expires after `resident_activation_days` days (setting, default 7, 1–60): an expired code can't
    sign in or be used to set a password; issuing a new code replaces (invalidates) the previous one
    and ends the account's sessions
  * the chosen password replaces the code in one transaction (POST /api/auth/password)

Identity: one account per person. Owner/tenant records belong to one unit each, so there is no
stable cross-unit person id; the system never merges people by itself. When another account looks
like the same person (same name, email or phone), provisioning stops and lists those accounts for
the administrator, who either confirms the person is verified to be the same (the unit is ADDED to
that account) or confirms a separate account.

Eligibility (unchanged rules): a CURRENT owner/tenant of an active residential unit (not a parking
or storage unit); one active portal account per owner/tenant record (enforced by a UNIQUE key).
"""
import re
import secrets
import unicodedata
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError

from ..core import security
from ..core.numbers import InputError
from ..core.roles import RESIDENT

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"     # no 0/O, 1/I/L: easy to read aloud and type
ACTIVATION_DAYS_DEFAULT = 7
ACTIVATION_DAYS_MAX = 60
NON_RESIDENTIAL = ("PARKING", "STORAGE")


class ProvisionError(Exception):
    def __init__(self, status, message, field=None, **extra):
        super().__init__(message)
        self.status, self.message, self.field, self.extra = status, message, field, extra


def activation_days(legacy):
    try:
        days = int(str(legacy.setting("resident_activation_days", ACTIVATION_DAYS_DEFAULT)).strip())
    except ValueError:
        days = ACTIVATION_DAYS_DEFAULT
    return min(max(days, 1), ACTIVATION_DAYS_MAX)


def generate_activation_code(username=""):
    while True:
        raw = "".join(secrets.choice(ALPHABET) for _ in range(12))
        code = f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"
        if security.password_problem(code, username) is None:
            return code


def _ascii(text):
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()


def suggest_username(legacy, name, fallback):
    """first.last from the person's name (letters/digits only), made unique (case-insensitive)."""
    words = [re.sub(r"[^a-z0-9]", "", w) for w in _ascii(name).split()]
    words = [w for w in words if w]
    base = ".".join([words[0], words[-1]] if len(words) > 1 else words) if words else fallback
    base = re.sub(r"[^a-z0-9._-]", "", base)[:30].strip("._-") or fallback
    if len(base) < 3:
        base = (base + ".resident")[:30]
    User = legacy.User
    taken = {u.lower() for (u,) in legacy.db.session.query(User.username).filter(User.username.ilike(f"{base}%")).all()}
    if base.lower() not in taken:
        return base
    n = 2
    while f"{base}{n}".lower() in taken:
        n += 1
    return f"{base}{n}"


def person_model(legacy, person_type):
    return legacy.Tenant if person_type == "Tenant" else legacy.Owner


def person_name(person):
    return getattr(person, "tenant_name", None) or getattr(person, "owner_name", None) or ""


def _norm_name(text):
    return " ".join(_ascii(text).split())


def _digits(text):
    d = re.sub(r"\D", "", text or "")
    return d[-10:] if len(d) >= 7 else ""


def possible_matches(legacy, person_type, person):
    """Active resident accounts that MIGHT be the same person (never merged automatically):
    {user_id: {"user": User, "reasons": [...]}}."""
    name, email, phone = _norm_name(person_name(person)), (person.email or "").strip().lower(), _digits(person.contact_no)
    out = {}
    for link in legacy.ResidentUnitLink.query.filter_by(active=True).all():
        if link.person_type == person_type and link.person_id == person.id:
            continue
        other = legacy.db.session.get(person_model(legacy, link.person_type), link.person_id) if link.person_id else None
        user = link.user
        if not other or not user or not user.active:
            continue
        reasons = []
        if name and _norm_name(person_name(other)) == name:
            reasons.append("same name")
        if email and (other.email or "").strip().lower() == email:
            reasons.append("same email")
        if phone and _digits(other.contact_no) == phone:
            reasons.append("same phone number")
        if reasons:
            entry = out.setdefault(user.id, {"user": user, "reasons": set()})
            entry["reasons"].update(reasons)
    return out


def account_state(legacy, user):
    """none | pending | active | disabled | ended (no unit left) for one account."""
    if not user:
        return "none"
    profile = getattr(user, "resident_profile", None)
    if not user.active or (profile and not profile.active):
        return "disabled"
    if not legacy.resident_access_links(user):
        return "ended"
    return "pending" if user.must_change_password else "active"


def _eligible_person(legacy, person_type, person_id, lock=False):
    if person_type not in ("Owner", "Tenant"):
        raise ProvisionError(400, "Choose an owner or a tenant.", "personType")
    model = person_model(legacy, person_type)
    q = legacy.db.session.query(model).filter_by(id=person_id)
    person = (q.with_for_update() if lock else q).first() if isinstance(person_id, int) else None
    if not person:
        raise ProvisionError(404, f"{person_type} not found.", "personId")
    unit = legacy.db.session.get(legacy.Unit, person.unit_id)
    if not unit or not unit.active:
        raise ProvisionError(409, "The unit is not active, so it can't get portal access.", "personId")
    if (unit.unit_type or "").upper() in NON_RESIDENTIAL:
        raise ProvisionError(409, "Parking and storage units don't get their own portal access; residents see them through their residential unit.", "personId")
    if person.status != "Current":
        raise ProvisionError(409, f"{person_name(person)} is not a current {person_type.lower()} of unit {unit.unit_no}.", "personId")
    return person, unit


def _claim(legacy, action, token):
    """One provisioning per rendered confirmation (double clicks / retries change nothing)."""
    try:
        legacy.claim_form_token(action, token)
    except InputError:
        raise ProvisionError(400, "This form is out of date. Close it and try again.", "formToken")
    except legacy.DuplicateSubmission:
        raise ProvisionError(409, "This request was already submitted; nothing more was changed.", None, duplicate=True)


def _issue_code(legacy, user):
    code = generate_activation_code(user.username)
    expires = datetime.utcnow() + timedelta(days=activation_days(legacy))
    legacy.set_password(user, code, temporary=True, expires_at=expires)
    return code, expires


def _credentials(user, code, expires):
    return {"username": user.username, "activationCode": code, "expiresAt": f"{expires.isoformat(timespec='seconds')}Z"}


def provision(legacy, *, person_type, person_id, mode, link_user_id, actor, form_token):
    """Give an eligible owner/tenant portal access. mode:
         "auto"  create an account unless another account may be the same person (409 needsReview)
         "new"   create a separate account (the administrator confirmed it is a different person)
         "link"  add this unit to the verified same person's account `link_user_id` (no new code)
    Returns {"account_user": User, "credentials": {...} or None, "linked": bool}. Commits; on any
    error nothing is saved."""
    db, Link, Profile, User = legacy.db, legacy.ResidentUnitLink, legacy.ResidentProfile, legacy.User
    if mode not in ("auto", "new", "link"):
        raise ProvisionError(400, "Unknown provisioning mode.", "mode")
    try:
        person, unit = _eligible_person(legacy, person_type, person_id, lock=True)
        key = legacy.link_key(person_type, person.id)
        existing = Link.query.filter_by(active_key=key).first()
        if existing and existing.user and existing.user.active:
            raise ProvisionError(409, f"{person_name(person)} already has a portal account ({existing.user.username}).", "personId",
                                 existingUserId=existing.user_id)
        if existing:   # an inactive account still holds the key: release it into history
            existing.active, existing.active_key = False, None
            existing.ended_at, existing.ended_by, existing.end_reason = datetime.utcnow(), actor, "account disabled; access given to a new account"
        if mode == "auto":
            matches = possible_matches(legacy, person_type, person)
            if matches:
                db.session.rollback()
                raise ProvisionError(409, "Another portal account may belong to the same person. Confirm before continuing.", None,
                                     needsReview=True, candidates=[{
                                         "userId": m["user"].id, "username": m["user"].username,
                                         "displayName": m["user"].resident_profile.display_name if m["user"].resident_profile else m["user"].username,
                                         "units": [l.unit.unit_no for l in legacy.resident_links(m["user"]) if l.unit],
                                         "reasons": sorted(m["reasons"])} for m in matches.values()])
        _claim(legacy, "resident_provision", form_token)
        if mode == "link":
            target = db.session.get(User, link_user_id) if isinstance(link_user_id, int) else None
            if not target or target.role != RESIDENT or not getattr(target, "resident_profile", None):
                raise ProvisionError(404, "Resident account not found.", "linkUserId")
            if not target.active or not target.resident_profile.active:
                raise ProvisionError(409, "That account is disabled. Reactivate it first.", "linkUserId")
            if any(l.unit_id == unit.id for l in legacy.resident_links(target)):
                raise ProvisionError(409, f"{target.username} already has access to unit {unit.unit_no}.", "linkUserId")
            db.session.add(Link(user_id=target.id, unit_id=unit.id, person_type=person_type, person_id=person.id,
                                active=True, active_key=key, created_by=actor))
            db.session.flush()
            legacy.audit(f"Linked unit {unit.unit_no} ({person_type.lower()} #{person.id}) to resident portal account {target.username}",
                         entity_type="user", entity_id=target.id, commit=False,
                         details={"unit": unit.unit_no, "personType": person_type, "personId": person.id, "verifiedSamePerson": True})
            db.session.commit()
            return {"account_user": target, "credentials": None, "linked": True}

        name = person_name(person)
        username = suggest_username(legacy, name, f"unit{re.sub(r'[^a-z0-9]', '', unit.unit_no.lower())}")
        user = User(username=username, password_hash="!", role=RESIDENT, active=True, must_change_password=True)
        db.session.add(user)
        db.session.flush()
        code, expires = _issue_code(legacy, user)
        db.session.add(Profile(user_id=user.id, unit_id=unit.id, person_type=person_type, person_id=person.id,
                               display_name=name or username, active=True))
        db.session.add(Link(user_id=user.id, unit_id=unit.id, person_type=person_type, person_id=person.id,
                            active=True, active_key=key, created_by=actor))
        db.session.flush()
        legacy.audit(f"Provisioned resident portal account {username} for {person_type.lower()} #{person.id} (unit {unit.unit_no}); "
                     f"activation code issued, valid until {expires:%Y-%m-%d %H:%M} UTC", entity_type="user", entity_id=user.id, commit=False,
                     details={"username": username, "unit": unit.unit_no, "personType": person_type, "personId": person.id,
                              "separateFromPossibleMatch": mode == "new"})
        db.session.commit()
        return {"account_user": user, "credentials": _credentials(user, code, expires), "linked": False}
    except ProvisionError:
        db.session.rollback()
        raise
    except IntegrityError:
        db.session.rollback()
        raise ProvisionError(409, "This person was given a portal account at the same moment by another request. Refresh the list.")
    except Exception:
        db.session.rollback()
        raise


def reissue_code(legacy, user, *, actor, form_token):
    """A new activation code for the account; the previous code (or password) stops working and the
    account's sessions end. Returns the credentials (shown once). Commits."""
    db = legacy.db
    try:
        if not user.active or (user.resident_profile and not user.resident_profile.active):
            raise ProvisionError(409, "The account is disabled. Reactivate it before issuing a code.")
        _claim(legacy, "resident_activation_code", form_token)
        was_pending = bool(user.must_change_password)
        code, expires = _issue_code(legacy, user)
        legacy.audit(f"Issued a new activation code for resident portal account {user.username} "
                     f"({'previous code' if was_pending else 'password'} invalidated; valid until {expires:%Y-%m-%d %H:%M} UTC)",
                     entity_type="user", entity_id=user.id, commit=False, details={"actor": actor})
        db.session.commit()
        return _credentials(user, code, expires)
    except Exception:
        db.session.rollback()
        raise


def end_link(legacy, user, link_id, *, actor, reason):
    """End one unit link (history kept). The account's other units are not touched. Commits."""
    db = legacy.db
    link = db.session.query(legacy.ResidentUnitLink).filter_by(id=link_id, user_id=user.id).with_for_update().first()
    if not link:
        raise ProvisionError(404, "Unit link not found.")
    if not link.active:
        raise ProvisionError(409, "This unit link has already ended.")
    link.active, link.active_key = False, None
    link.ended_at, link.ended_by, link.end_reason = datetime.utcnow(), actor, (reason or None)
    unit_no = link.unit.unit_no if link.unit else link.unit_id
    legacy.audit(f"Ended resident portal access of {user.username} to unit {unit_no}", entity_type="user", entity_id=user.id,
                 reason=reason or None, commit=False, details={"linkId": link.id, "unit": unit_no})
    db.session.commit()
    return link
