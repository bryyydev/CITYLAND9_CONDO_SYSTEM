import os
import sys
import shutil
import sqlite3
import re
import secrets
import smtplib
import time
from email.message import EmailMessage
from datetime import datetime, date, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, g, jsonify, abort, has_request_context, has_app_context, get_flashed_messages
from urllib.parse import urlencode
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_, inspect, func, event as sa_event
from sqlalchemy.orm import selectinload, validates, deferred, declared_attr
from sqlalchemy.dialects.mysql import ENUM as MYSQL_ENUM
from werkzeug.security import generate_password_hash, check_password_hash
from io import BytesIO

try:
    from openpyxl import Workbook, load_workbook
except ImportError:
    Workbook = load_workbook = None

try:
    import qrcode
except ImportError:
    qrcode = None

# This file lives in backend/, but .env, the SQLite database and import backups
# stay in the project root so existing installations keep their data.
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(BACKEND_DIR)
LEGACY_UI_DIR = os.path.join(BASE_DIR, "legacy_flask_ui")

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    pass

app = Flask(
    __name__,
    template_folder=os.path.join(LEGACY_UI_DIR, "templates"),
    static_folder=os.path.join(LEGACY_UI_DIR, "static"),
    static_url_path="/static",
)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # backend/ (app.core.*)
from app.core import security  # noqa: E402

# Production refuses a missing, short or placeholder SECRET_KEY (APP_ENV=development relaxes it).
app.secret_key = security.check_secret_key(os.getenv("SECRET_KEY"))

# Database selection (docs/migration.md):
#   DATABASE_URL set        -> use it as-is
#   DB_ENGINE=mysql         -> local MySQL/MariaDB from MYSQL_HOST/PORT/DATABASE/USER/PASSWORD
#   otherwise (default)     -> the SQLite file cityland_condo_web.db in the project root
db_url = os.getenv("DATABASE_URL", "").strip()
if not db_url and os.getenv("DB_ENGINE", "sqlite").strip().lower() == "mysql":
    from sqlalchemy.engine import URL as _URL
    db_url = _URL.create(
        "mysql+pymysql",
        username=os.getenv("MYSQL_USER", ""), password=os.getenv("MYSQL_PASSWORD", ""),
        host=os.getenv("MYSQL_HOST", "127.0.0.1"), port=int(os.getenv("MYSQL_PORT", "3306")),
        database=os.getenv("MYSQL_DATABASE", "cityland9"), query={"charset": "utf8mb4"},
    ).render_as_string(hide_password=False)
if not db_url:
    # Always keep the local SQLite database in the project root, regardless of
    # the directory from which the server is launched.
    db_path = os.path.join(BASE_DIR, "cityland_condo_web.db")
    db_url = "sqlite:///" + db_path.replace("\\", "/")

if db_url.startswith("mysql"):
    # PyMySQL rejects SQLite's "timeout" argument (migration issue I1). Recycle
    # connections before MySQL's idle timeout closes them.
    # READ COMMITTED: after a request takes a row lock (bill, receipt counter) it sees what other
    # cashiers committed meanwhile (REPEATABLE READ would keep a snapshot from the request's first
    # query, e.g. the sign-in check). It also avoids gap locks. A shorter lock wait lets
    # retry_on_deadlock retry promptly instead of keeping a cashier waiting 50 seconds.
    _engine_options = {"pool_pre_ping": True, "pool_recycle": 280, "isolation_level": "READ COMMITTED",
                       "connect_args": {"connect_timeout": 10, "init_command": "SET SESSION innodb_lock_wait_timeout = 15"}}
else:
    _engine_options = {"pool_pre_ping": True, "connect_args": {"timeout": 30}}
app.config.update(
    SQLALCHEMY_DATABASE_URI=db_url,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SQLALCHEMY_ENGINE_OPTIONS=_engine_options,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # Set SESSION_COOKIE_SECURE=1 once the server is reached over HTTPS (docs/run-guide/06).
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "0") == "1",
    SESSION_COOKIE_NAME="cityland9_session",
    # Largest accepted request body (Excel import). Bigger uploads get a friendly error (413).
    MAX_CONTENT_LENGTH=int(os.getenv("MAX_UPLOAD_MB", "20")) * 1024 * 1024,
)
# Excel import limits (protect the server from huge or malicious workbooks).
IMPORT_MAX_ROWS = int(os.getenv("IMPORT_MAX_ROWS", "50000"))          # per sheet
IMPORT_MAX_COLUMNS = 200
IMPORT_MAX_UNCOMPRESSED_MB = int(os.getenv("IMPORT_MAX_UNCOMPRESSED_MB", "200"))
db = SQLAlchemy(app)


class ChangeTracked:
    """Who last changed a row and when. Filled in automatically on every save
    (schema Phase A, docs/schema-design.md S12).

    The columns are deferred (loaded only when read), so normal queries do not depend
    on them: reading still works against a database that has not been migrated yet
    (e.g. the golden-master snapshot taken just before the migration)."""

    @declared_attr
    def updated_at(cls):
        return deferred(db.Column(db.DateTime, nullable=True))

    @declared_attr
    def updated_by(cls):
        return deferred(db.Column(db.String(80), nullable=True))


def month_check(column, name):
    """CHECK that a 'YYYY-MM' text column really holds a month (works on SQLite and MySQL)."""
    return db.CheckConstraint(
        f"{column} LIKE '____-__' AND SUBSTR({column}, 1, 4) BETWEEN '1900' AND '2999' "
        f"AND SUBSTR({column}, 6, 2) BETWEEN '01' AND '12'", name=name)


@sa_event.listens_for(db.session, "before_flush")
def _stamp_changes(sess, flush_context, instances):
    who = (session.get("username") if has_request_context() else None) or "system"
    now = datetime.utcnow()
    changed = list(sess.new) + [o for o in sess.dirty if sess.is_modified(o, include_collections=False)]
    for obj in changed:
        if isinstance(obj, ChangeTracked):
            obj.updated_at = now
            obj.updated_by = who


# -----------------------------
# Helpers
# -----------------------------
def money(v):
    """Internal values (database columns, computed figures) -> Decimal. NOT for user input:
    typed amounts go through form_amount()/form_decimal(), which reject bad input."""
    try:
        return Decimal(str(v or 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


from app.core.numbers import InputError, parse_amount, parse_decimal, round_money  # noqa: E402


def form_amount(name, label, **options):
    """A peso amount from the submitted form (strict; raises InputError)."""
    return parse_amount(request.form.get(name), label=label, **options)


def form_decimal(name, label, places, **options):
    """A non-money number from the submitted form (area, reading, rate; strict; raises InputError)."""
    return parse_decimal(request.form.get(name), label=label, places=places, **options)


def parse_date(v):
    if not v:
        return None
    try:
        return datetime.strptime(v, "%Y-%m-%d").date()
    except ValueError:
        return None


# Excel date cells may arrive as datetime/date objects or text; normalize them for SQLite Date columns.
def parse_excel_date(v):
    if not v:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    text = str(v).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def setting(key, default=""):
    """Read a Setting value. Returns the default string if the key is not yet
    seeded. Does NOT write to the database on read — keys are seeded once by
    init_db(). This avoids a commit-on-every-page-load race condition."""
    row = Setting.query.filter_by(key=key).first()
    return row.value if row else str(default)


def setting_float(key, default=0):
    try:
        return float(setting(key, default))
    except Exception:
        return float(default)


def audit(action, *, entity_type=None, entity_id=None, reason=None, details=None, commit=True):
    """Record an audit log entry.

    Financial changes call it with commit=False BEFORE their own commit, so the change and its
    audit entry are saved together or not at all. Other callers keep the old behaviour (separate
    commit after the main change); a failure there is logged instead of silently ignored.
    details: a dict of before/after values (stored as JSON). Never pass passwords or secrets.
    """
    import json
    entry = AuditLog(
        username=(session.get("username") if has_request_context() else None) or "system",
        action=action[:255], entity_type=entity_type, entity_id=entity_id,
        reason=(reason or None) and reason[:500],
        details=json.dumps(details, default=str, sort_keys=True) if details is not None else None,
    )
    db.session.add(entry)
    if not commit:
        return entry
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("Audit entry could not be saved: %s", action)
    return entry


def current_user():
    """The signed-in user, or None. Checked on EVERY request (classic pages and API):
    the account must still exist and be active, the session must belong to the account's
    current session version (password resets/changes and deactivation end older sessions),
    and the session must be within its idle and absolute lifetime. An invalid session is cleared."""
    if "cl9_user" in g:
        return g.cl9_user
    user = None
    uid = session.get("user_id")
    if uid:
        candidate = db.session.get(User, uid)
        valid = (candidate is not None and candidate.active
                 and session.get("sv") == (candidate.session_version or 0)
                 and not security.session_expired(session.get("iat"), session.get("seen")))
        if valid:
            user = candidate
            now = time.time()
            if now - float(session.get("seen", 0)) > 60:   # refresh activity at most once a minute
                session["seen"] = now
        else:
            session.clear()
    g.cl9_user = user
    return user


def start_session(user):
    """Sign `user` in: a fresh session bound to the account's current session version."""
    session.clear()
    now = time.time()
    session.update({"user_id": user.id, "username": user.username, "sv": user.session_version or 0, "iat": now, "seen": now})
    g.cl9_user = user


def end_other_sessions(user, keep_current=False):
    """Invalidate every existing session of `user` (call before commit). With keep_current the
    caller's own session stays valid (e.g. the user changed their own password)."""
    user.session_version = (user.session_version or 0) + 1
    if keep_current and session.get("user_id") == user.id:
        session["sv"] = user.session_version


def set_password(user, new_password, *, temporary, keep_current_session=False):
    """Store a new password hash. temporary=True forces a change at the next sign-in."""
    user.password_hash = generate_password_hash(new_password)
    user.must_change_password = bool(temporary)
    user.password_changed_at = datetime.utcnow()
    end_other_sessions(user, keep_current=keep_current_session)


def get_smtp_password():
    """The SMTP password, decrypted ("" when not set or unreadable after a SECRET_KEY change)."""
    return security.decrypt_secret(setting("smtp_password", ""), app.secret_key)


def set_smtp_password(plaintext):
    """Store the SMTP password encrypted at rest (call before commit). "" removes it."""
    row = Setting.query.filter_by(key="smtp_password").first() or Setting(key="smtp_password")
    row.value = security.encrypt_secret(plaintext, app.secret_key) if plaintext else ""
    db.session.add(row)


USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,80}$")


def client_address():
    """The client's IP for sign-in throttling. Proxy headers are honoured only from TRUSTED_PROXY (backend/run.py)."""
    return request.remote_addr or "unknown"


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        u = current_user()
        if not u:
            return redirect(url_for("login"))
        if u.role == RESIDENT and resident_access_problem(u):
            # Same rule as @guarded: a resident whose access ended is signed out.
            problem = resident_access_problem(u)
            session.clear()
            flash(problem, "warning")
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper


ROLE_LEVEL = {"resident": 5, "staff": 10, "accounting": 20, "admin": 30, "manager": 40, "super_admin": 50}

# Role-based access control: ONE permission matrix for pages, API, menus and buttons.
# Edit backend/app/core/permissions.py to change who may use what.
from app.core.permissions import PERMISSIONS, ANY_SIGNED_IN, can as _role_can  # noqa: E402
from app.core.roles import ACCOUNTING, ALL_ROLES, RESIDENT  # noqa: E402

# Kept under its old name for anything that still refers to it.
ENDPOINT_ROLES = PERMISSIONS


def can_access(endpoint, user=None):
    """True when the user's role may use `endpoint`. Unknown endpoints are denied."""
    u = user or current_user()
    return bool(u) and _role_can(u.role, endpoint)


# Landing page per role. Every role must be allowed to open its own landing
# page, otherwise a permission redirect would loop back onto itself.
ROLE_HOME = {
    "super_admin": "dashboard",
    "admin": "dashboard",
    "manager": "employees",
    "staff": "move_certificate",
    "accounting": "reports",
    "resident": "resident_portal",
}


def home_endpoint(user):
    endpoint = ROLE_HOME.get(user.role)
    # Unknown roles fall back to Change Password, which only requires a login.
    return endpoint if endpoint and can_access(endpoint, user) else "change_password"


def guarded(fn):
    """Page-level RBAC for legacy routes: the endpoint (function) name is looked up in
    the permission matrix. Signed out -> login page; not permitted -> flash + the
    role's home page (403 if that is the page itself). Endpoints missing from the
    matrix are denied (fail closed)."""
    endpoint = fn.__name__

    @wraps(fn)
    def wrapper(*args, **kwargs):
        u = current_user()
        if not u:
            return redirect(url_for("login"))
        if u.role == RESIDENT:
            problem = resident_access_problem(u)
            if problem:
                session.clear()
                flash(problem, "warning")
                return redirect(url_for("login"))
        if not _role_can(u.role, endpoint):
            flash("You do not have permission to access this function.", "danger")
            home = home_endpoint(u)
            if home == request.endpoint:
                abort(403)
            return redirect(url_for(home))
        return fn(*args, **kwargs)
    return wrapper


# Unit type -> Rates & Rules rate key. Matched without regard to case or extra spaces, so the
# types the Units form saves ("1 BEDROOM") and older spellings ("1 Bedroom", "Studio") all find
# their rate. (Fix 2026-10-04: "1/2/3 BEDROOM" used to find no rate, so auto-rate units billed ₱0.)
TYPE_RATE_KEYS = {
    "STUDIO": "studio_rate_per_sqm", "STUDIO TYPE": "studio_rate_per_sqm",
    "1 BEDROOM": "one_bed_rate_per_sqm", "2 BEDROOM": "two_bed_rate_per_sqm", "3 BEDROOM": "three_bed_rate_per_sqm",
}


def rate_for_type(unit_type):
    key = TYPE_RATE_KEYS.get(" ".join(str(unit_type or "").upper().split()))
    return setting_float(key, 0) if key else 0


def due_day_setting():
    """Validated due day (1-31) from Rates & Rules; 8 when unset or invalid."""
    try:
        day = int(Decimal(str(setting("due_day", "8")).strip()))
    except (InvalidOperation, ValueError):
        return 8
    return day if 1 <= day <= 31 else 8


def due_date_for(month):
    """Due date of a NEW bill for billing month YYYY-MM: the configured due day, or the month's
    last day when the month is shorter (due day 31 -> 30 April, 28/29 February)."""
    import calendar
    year, mon = (int(x) for x in month.split("-"))
    return date(year, mon, min(due_day_setting(), calendar.monthrange(year, mon)[1]))


def dec(value):
    """A stored number (DOUBLE/float, Decimal, text) as an exact Decimal. str() recovers the value
    as typed (e.g. 85.15, not 85.1499999...), so the arithmetic below is exact."""
    return Decimal(str(value or 0))


# Pricing rule (documented): amounts are computed in Decimal and rounded ONCE, to the centavo,
# half up (0.005 -> 0.01). Previously float math + round() could round a half-centavo down.
def unit_dues(unit):
    """Condo dues = the unit's manual monthly amount when it is set to "manual" (and the amount is
    above zero), otherwise total unit area × applicable rate per sqm.
    (Fix 2026-10-04: the manual amount was saved but ignored for residential units.)"""
    manual = money(getattr(unit, "manual_monthly_dues", 0) or 0)
    if getattr(unit, "dues_mode", "per_sqm") == "manual" and manual > 0:
        return round_money(manual)
    rate = dec(unit.unit_rate_per_sqm) or dec(rate_for_type(unit.unit_type))
    return round_money(dec(unit.area_sqm) * rate)


def assigned_asset(unit, field_name):
    # Prefer eager-loaded self relationships. This avoids an extra SELECT for
    # every unit when the Units directory contains hundreds of records.
    if field_name == "assigned_parking_unit_id":
        loaded = getattr(unit, "assigned_parking_unit", None)
    elif field_name == "assigned_storage_unit_id":
        loaded = getattr(unit, "assigned_storage_unit", None)
    else:
        loaded = None
    if loaded is not None:
        return loaded
    asset_id = getattr(unit, field_name, None)
    return db.session.get(Unit, asset_id) if asset_id else None


def asset_unit_charge(asset, default_rate_key):
    if not asset:
        return Decimal("0.00")
    if getattr(asset, "dues_mode", "per_sqm") == "manual" and money(getattr(asset, "manual_monthly_dues", 0)) > 0:
        return round_money(asset.manual_monthly_dues)
    rate = dec(asset.unit_rate_per_sqm) or dec(setting(default_rate_key, "0"))
    return round_money(dec(asset.area_sqm) * rate)


def parking_dues(unit):
    """Parking = the assigned PARKING unit's area × its rate per sqm (or the default parking rate).
    One parking model since Phase B3; the old per-unit parking lots were converted."""
    assigned = assigned_asset(unit, "assigned_parking_unit_id")
    if not assigned:
        return Decimal("0.00")
    rate = dec(assigned.unit_rate_per_sqm) or dec(setting("parking_rate_per_sqm", "0"))
    return round_money(dec(assigned.area_sqm) * rate)

def storage_dues(unit):
    if not getattr(unit, "include_storage", False):
        return Decimal("0.00")
    assigned = assigned_asset(unit, "assigned_storage_unit_id")
    return asset_unit_charge(assigned, "storage_rate_per_sqm")


def storage_cutoff():
    """First billing month whose SOA total includes storage dues (Rates & Rules > Storage).
    Earlier bills keep their original totals (decision on D13, 2026-09-30).
    '9999-12' (never) when the setting is missing, i.e. the old behaviour."""
    if has_app_context():
        if "_storage_cutoff" not in g:
            g._storage_cutoff = setting("storage_in_total_from", "9999-12")
        return g._storage_cutoff
    return setting("storage_in_total_from", "9999-12")


def storage_charged(bill):
    return bool(bill.billing_month) and bill.billing_month >= storage_cutoff()


def water_amount_for_reading(reading):
    if not reading or setting("water_auto_compute", "1") != "1":
        return Decimal("0")
    usage = max(dec(reading.current_reading) - dec(reading.previous_reading), Decimal("0"))
    return round_money(usage * (dec(reading.rate) or dec(setting("water_rate", "50"))))


def unpaid_previous_bills(unit_id, month):
    rows = _billing_history_for_unit(unit_id)
    return [b for b in reversed(rows) if b.billing_month < month and bill_unpaid_part(b) > 0]


def is_overdue(bill):
    return bool(bill.due_date and date.today() > bill.due_date)

def _billing_history_for_unit(unit_id):
    """Load all billing history once per web request and group it by unit.

    This avoids an N+1 query pattern when a page calculates balances for many
    units. It is especially important after importing hundreds of units and
    years of billing history from Excel.
    """
    cache = getattr(g, "_billing_history_by_unit", None)
    if cache is None:
        rows = Billing.query.order_by(Billing.unit_id.asc(), Billing.billing_month.asc(), Billing.id.asc()).all()
        cache = {}
        for row in rows:
            cache.setdefault(row.unit_id, []).append(row)
        g._billing_history_by_unit = cache
    return cache.get(unit_id, [])

def _water_reading_for_bill(bill):
    """Return the latest water reading for a unit/month, using one query per month per request."""
    cache = getattr(g, "_water_readings_by_month", None)
    if cache is None:
        cache = {}
        g._water_readings_by_month = cache
    month = bill.billing_month
    if month not in cache:
        rows = WaterReading.query.filter_by(reading_month=month).order_by(WaterReading.unit_id.asc(), WaterReading.id.desc()).all()
        latest = {}
        for row in rows:
            latest.setdefault(row.unit_id, row)
        cache[month] = latest
    return cache[month].get(bill.unit_id)

def _advance_applied_for_bill(bill):
    """Return advance credit applied to a bill, cached for the current request."""
    cache = getattr(g, "_advance_applied_cache", None)
    if cache is None:
        cache = {}
        g._advance_applied_cache = cache
    if bill.id not in cache:
        value = db.session.query(func.coalesce(func.sum(AdvanceApplication.amount), 0)).filter(AdvanceApplication.billing_id == bill.id).scalar() or Decimal("0")
        cache[bill.id] = money(value)
    return cache[bill.id]

def _advance_balance_cached(advance):
    cache = getattr(g, "_advance_balance_cache", None)
    if cache is None:
        cache = {}
        g._advance_balance_cache = cache
    if getattr(advance, "reversed_at", None):
        return Decimal("0.00")
    if advance.id not in cache:
        applied = db.session.query(func.coalesce(func.sum(AdvanceApplication.amount), 0)).filter(AdvanceApplication.advance_payment_id == advance.id).scalar() or Decimal("0")
        cache[advance.id] = max(money(advance.amount) - money(applied), Decimal("0")).quantize(Decimal("0.01"))
    return cache[advance.id]


def _prepare_bill_calculation_cache():
    """Bulk-calculate billing balances once per request.

    Imported Excel data can contain many units and months. Calculating each
    bill by walking all earlier bills creates an O(n²) workload. This routine
    loads billing, water and advance-payment data in bulk and calculates each
    bill once in chronological order per unit.
    """
    cache = getattr(g, "_bill_calc_cache", None)
    if cache is not None:
        return cache

    bills = (
        Billing.query
        .options(
            selectinload(Billing.unit).selectinload(Unit.assigned_parking_unit),
            selectinload(Billing.unit).selectinload(Unit.assigned_storage_unit),
            selectinload(Billing.unit).selectinload(Unit.tenants),
            selectinload(Billing.unit).selectinload(Unit.owners),
        )
        .order_by(Billing.unit_id.asc(), Billing.billing_month.asc(), Billing.id.asc())
        .all()
    )

    # Billing/SOA only needs the selected month's water reading. Historical
    # bills already store their water charge, so loading every historical
    # water-reading row here creates unnecessary work on large imports.
    # (The retired classic Billing list loaded only its month's readings here, so older bills there
    # used their stored water; every screen now loads all readings and shows the same figures.)
    water_map = {}
    water_rows = WaterReading.query.order_by(
        WaterReading.unit_id.asc(), WaterReading.reading_month.asc(), WaterReading.id.desc()
    ).all()
    for r in water_rows:
        water_map.setdefault((r.unit_id, r.reading_month), r)

    advance_map = {}
    for bid, total in (
        db.session.query(
            AdvanceApplication.billing_id,
            func.coalesce(func.sum(AdvanceApplication.amount), 0),
        )
        .group_by(AdvanceApplication.billing_id)
        .all()
    ):
        advance_map[bid] = money(total)

    result = {}
    penalty_rate = Decimal(str(setting_float("penalty_rate", 10))) / Decimal("100")
    penalty_include = {
        "condo": setting("penalty_include_condo", "1") == "1",
        "parking": setting("penalty_include_parking", "0") == "1",
        "storage": setting("penalty_include_storage", "0") == "1",
        "water": setting("penalty_include_water", "0") == "1",
    }

    # Balances carry over as ONE running balance per unit (fix 2026-10-05, bug B1):
    #   previous balance of a bill = everything charged on the unit's earlier bills minus everything
    #   paid on them. (It used to be the SUM of the earlier bills' full balances, but each balance
    #   already contains the ones before it, so unpaid months were counted again every month, and a
    #   payment on the latest bill never cleared the older bills, which came back on the next SOA.)
    # Payments settle the oldest charges first. That decides each bill's remaining balance and status
    # ("balance"; an old bill covered by a later payment is Paid), the part of it still unpaid
    # ("own_balance", which adds up to the unit's balance), and the penalty base (unpaid charges of
    # the selected types). A hand-corrected SOA (soa_manual_override) keeps its typed amounts and
    # sets the running balance from there.
    zero = Decimal("0.00")
    i = 0
    while i < len(bills):
        unit_id = bills[i].unit_id
        j = i
        while j < len(bills) and bills[j].unit_id == unit_id:
            j += 1
        unit_bills = bills[i:j]

        running = zero          # unit balance carried into the next month (credits are not carried)
        buckets = []            # [amount still unpaid, charge type] of earlier bills, oldest first
        order = []              # (bill, statement balance, running balance after its month, added charge)
        k = 0
        while k < len(unit_bills):
            month = unit_bills[k].billing_month
            m_end = k
            while m_end < len(unit_bills) and unit_bills[m_end].billing_month == month:
                m_end += 1
            month_rows = unit_bills[k:m_end]
            # Bills of the same month (duplicates) don't affect one another.
            penalty_base = sum((amt for amt, kind in buckets if penalty_include.get(kind)), zero).quantize(Decimal("0.01"))
            delta = zero
            month_buckets, month_paid = [], zero
            for bill in month_rows:
                reading = water_map.get((bill.unit_id, bill.billing_month))
                if getattr(bill, "soa_manual_override", False):
                    condo = money(bill.assessment)
                    parking = money(bill.parking_dues)
                    water = money(bill.water)
                    penalty = money(bill.penalty)
                    previous = money(bill.previous_balance)
                else:
                    # Issued bills are frozen: condo dues and parking are the amounts stored
                    # when the bill was generated (or last recalculated on purpose), not
                    # today's rates. Water follows the month's reading, as before.
                    condo = money(bill.assessment)
                    parking = money(bill.parking_dues)
                    water = money(reading.bill_amount) if reading else money(bill.water)
                    penalty = (penalty_base * penalty_rate).quantize(Decimal("0.01")) if is_overdue(bill) else zero
                    previous = running

                storage = money(bill.storage_dues) if storage_charged(bill) else zero
                water_paid_separately = bool(reading and getattr(reading, "paid", False))
                current = condo + parking + storage + water
                if water_paid_separately:
                    current -= water
                # Other charges and the adjustment (negative = discount/credit) entered with Edit SOA.
                # Fix 2026-10-04: they were stored but never counted in the total.
                other = money(getattr(bill, "other", 0))
                adjustment = money(getattr(bill, "adjustment", 0))
                current += other + adjustment

                advance = min(advance_map.get(bill.id, zero), max(current, zero))
                total = (current + previous + penalty - advance).quantize(Decimal("0.01"))
                paid = money(bill.amount_paid)
                statement_balance = (total - paid).quantize(Decimal("0.01"))
                charge = (total - previous).quantize(Decimal("0.01"))      # what this bill adds
                if getattr(bill, "soa_manual_override", False):
                    charge = (total - running).quantize(Decimal("0.01"))   # its typed previous balance replaces the running one
                delta += charge - paid

                result[bill.id] = {
                    "condo": condo, "parking": parking, "storage": storage, "water": water,
                    "other": other, "adjustment": adjustment, "penalty": penalty, "previous": previous,
                    "advance": advance, "current": current, "total": total,
                    "balance": statement_balance, "own_balance": zero, "charge": charge, "penalty_base": penalty_base,
                    "reading": reading,
                }
                # Charge types for the penalty base. Advances apply to Condo Dues only; water paid on
                # its own reading is not owed on the bill. Penalty, other charges and adjustments are
                # settled after the typed charges of the same bill.
                rest = penalty + other + adjustment
                month_buckets += [[max(condo - advance, zero), "condo"], [max(parking, zero), "parking"], [max(storage, zero), "storage"],
                                  [zero if water_paid_separately else max(water, zero), "water"], [max(rest, zero), "rest"]]
                month_paid += paid + max(-rest, zero)
                order.append((bill, statement_balance, charge))

            running = max(running + delta, zero)
            buckets += [x for x in month_buckets if x[0] > 0]
            # This month's payments settle the oldest unpaid charges first.
            pool = month_paid
            for bucket in buckets:
                if pool <= 0:
                    break
                used = min(bucket[0], pool)
                bucket[0] -= used
                pool -= used
            buckets = [x for x in buckets if x[0] > 0]
            k = m_end

        # Remaining balance of each bill after ALL the unit's payments (oldest charges settled first):
        # what is still owed counting this bill and the ones before it. Never more than the bill's own
        # statement balance; the latest bill's balance is the unit's balance.
        later = zero
        settled_upto = {}
        for bill, statement_balance, charge in reversed(order):
            owed_upto = max(zero, min(max(statement_balance, zero), running - later))
            settled_upto[bill.id] = owed_upto
            later += charge
        before = zero
        for bill, statement_balance, charge in order:
            calc = result[bill.id]
            owed_upto = settled_upto[bill.id]
            calc["balance"] = min(statement_balance, owed_upto)
            calc["own_balance"] = max(owed_upto - before, zero)
            before = max(before, owed_upto)
        i = j

    g._bill_calc_cache = result
    return result


def _bill_calc(bill):
    cache = _prepare_bill_calculation_cache()
    return cache.get(bill.id, {
        "condo": Decimal("0.00"),
        "parking": Decimal("0.00"),
        "storage": Decimal("0.00"),
        "water": money(bill.water),
        "other": Decimal("0.00"),
        "adjustment": Decimal("0.00"),
        "penalty": money(bill.penalty),
        "previous": money(bill.previous_balance),
        "advance": Decimal("0.00"),
        "current": money(bill.water),
        "total": money(bill.water),
        "balance": money(bill.water) - money(bill.amount_paid),
        "own_balance": max(money(bill.water) - money(bill.amount_paid), Decimal("0.00")),
        "charge": money(bill.water),
        "penalty_base": Decimal("0.00"),
        "reading": None,
    })


def overdue_total_for_bill(bill):
    return _bill_calc(bill)["previous"]


def bill_computed_components(bill):
    c = _bill_calc(bill)
    return c["condo"], c["parking"], c["water"], c["penalty"]


def authoritative_bill_values(bill):
    c = _bill_calc(bill)
    return c["condo"], c["parking"], c["water"], c["penalty"], c["previous"]


def bill_current_charges(bill):
    return _bill_calc(bill)["current"]


def previous_outstanding(unit_id, month):
    """The unit's unpaid balance from bills before `month` (bug B1: no longer counts arrears twice)."""
    total = Decimal("0.00")
    for b in _billing_history_for_unit(unit_id):
        if b.billing_month >= month:
            break
        total += bill_unpaid_part(b)
    return total.quantize(Decimal("0.01"))


def selected_penalty_base_for_unit(unit_id, month):
    """Prior-month unpaid balances of the charge types selected in Rates & Rules. Payments settle the
    unit's oldest charges first (bug B1: a payment on a later bill used to leave the older bill's
    charges in the base, so paid dues kept earning penalty)."""
    bill = Billing.query.filter_by(unit_id=unit_id, billing_month=month).order_by(Billing.id.asc()).first()
    if bill is not None:
        return _bill_calc(bill)["penalty_base"]
    # No bill for the month yet (bill generation): what the unit's earlier bills leave unpaid.
    enabled = {k for k in ("condo", "parking", "storage", "water")
               if setting(f"penalty_include_{k}", "1" if k == "condo" else "0") == "1"}
    probe = [b for b in _billing_history_for_unit(unit_id) if b.billing_month < month]
    return _penalty_base_after(probe, enabled)


def _penalty_base_after(prior_bills, enabled):
    """Unpaid charges of the enabled types left by `prior_bills` (oldest charges settled first)."""
    buckets = []
    for b in prior_bills:
        c = _bill_calc(b)
        reading = c.get("reading")
        rest = c["penalty"] + c["other"] + c["adjustment"]
        buckets += [[max(c["condo"] - c["advance"], Decimal("0")), "condo"], [max(c["parking"], Decimal("0")), "parking"],
                    [max(c["storage"], Decimal("0")), "storage"],
                    [Decimal("0") if reading and getattr(reading, "paid", False) else max(c["water"], Decimal("0")), "water"],
                    [max(rest, Decimal("0")), "rest"]]
        pool = money(b.amount_paid) + max(-rest, Decimal("0"))
        for bucket in buckets:
            if pool <= 0:
                break
            used = min(bucket[0], pool)
            bucket[0] -= used
            pool -= used
    return sum((amt for amt, kind in buckets if kind in enabled), Decimal("0.00")).quantize(Decimal("0.01"))


def penalty_for_unit(unit_id, month):
    bill = Billing.query.filter_by(unit_id=unit_id, billing_month=month).first()
    if not bill or not is_overdue(bill):
        return Decimal("0.00")
    base = selected_penalty_base_for_unit(unit_id, month)
    return (base * (Decimal(str(setting_float("penalty_rate", 10))) / Decimal("100"))).quantize(Decimal("0.01"))

def refresh_penalties(month):
    changed = False
    for b in Billing.query.filter_by(billing_month=month).all():
        new_penalty = penalty_for_unit(b.unit_id, month)
        if money(b.penalty) != new_penalty and not getattr(b, "soa_manual_override", False):
            b.penalty = new_penalty
            changed = True
    if changed:
        db.session.commit()


def allocate_advances_for_month(month, unit_id=None):
    """Apply scheduled advance condo-dues credits to bills for a billing month."""
    bills_q = Billing.query.filter_by(billing_month=month)
    if unit_id is not None:
        bills_q = bills_q.filter_by(unit_id=unit_id)
    bills = bills_q.all()
    changed = False

    for bill in bills:
        # Advance payments apply to condo dues only.
        condo = money(bill.assessment)  # issued (frozen) condo dues

        already = money(advance_for_bill(bill))
        need = max(money(condo) - already, Decimal("0"))
        if need <= 0:
            continue

        advances = (
            AdvancePayment.query
            .filter_by(unit_id=bill.unit_id)
            .filter(AdvancePayment.reversed_at.is_(None))
            .order_by(AdvancePayment.payment_date.asc(), AdvancePayment.id.asc())
            .with_for_update()          # concurrent payments of the same unit apply each advance once
            .all()
        )

        for adv in advances:
            start = adv.start_month
            end = month_shift(
                start,
                max(int(adv.coverage_months or 1), 1) - 1
            )
            if not (start <= month <= end):
                continue

            remaining = advance_balance(adv)
            if remaining <= 0:
                continue

            months = max(int(adv.coverage_months or 1), 1)
            monthly = money(adv.monthly_amount)
            if monthly <= 0:
                monthly = (money(adv.amount) / months).quantize(Decimal("0.01"))

            amount = min(need, monthly, remaining)
            if amount <= 0:
                continue

            existing = (
                AdvanceApplication.query
                .filter_by(
                    advance_payment_id=adv.id,
                    billing_id=bill.id
                )
                .first()
            )

            if existing:
                if money(existing.amount) != amount:
                    existing.amount = amount
                    changed = True
            else:
                db.session.add(
                    AdvanceApplication(
                        advance_payment_id=adv.id,
                        billing_id=bill.id,
                        billing_month=month,
                        amount=amount,
                    )
                )
                changed = True

            need -= amount
            if need <= 0:
                break

    if changed:
        db.session.flush()
    return changed


def current_contact_for_unit(unit):
    tenant = Tenant.query.filter_by(unit_id=unit.id, status="Current", representative=True).first()
    if tenant:
        return tenant
    tenant = Tenant.query.filter_by(unit_id=unit.id, status="Current").order_by(Tenant.id.desc()).first()
    if tenant and (tenant.email or tenant.contact_no):
        return tenant
    return Owner.query.filter_by(unit_id=unit.id, status="Current").order_by(Owner.id.desc()).first()

def bill_total(bill):
    return _bill_calc(bill)["total"]

def bill_balance(bill):
    """What is still owed on this bill: the latest bill's balance is the unit's balance; an older bill
    that later payments covered is 0 (payments settle the oldest charges first, bug B1)."""
    return _bill_calc(bill)["balance"]


def bill_unpaid_part(bill):
    """The part of this bill's own charges still unpaid (adds up to the unit's balance across bills)."""
    return _bill_calc(bill)["own_balance"]



def bill_status(bill):
    """Return the billing status based on the authoritative bill balance."""
    balance = bill_balance(bill)
    if balance <= Decimal("0.00"):
        return "Paid"
    calc = _bill_calc(bill)
    # Partly paid: on this bill, or by a later payment that settled part of its charges (bug B1).
    if money(getattr(bill, "amount_paid", 0)) > Decimal("0.00") or calc["own_balance"] < calc.get("charge", calc["own_balance"]):
        return "Partially Paid"
    if getattr(bill, "due_date", None) and date.today() > bill.due_date:
        return "Overdue"
    return "Unpaid"

def water_history_status(reading, bill=None):
    """Return water payment status, including partial payments."""
    amount = money(getattr(reading, "paid_amount", 0))
    total = money(getattr(reading, "bill_amount", 0))
    if getattr(reading, "paid", False) or (total > 0 and amount >= total):
        return "Paid"
    if amount > 0:
        return "Partially Paid"
    if bill is not None:
        return bill_status(bill)
    return "Unpaid"


def soa_bill_status(bill):
    """Status using the SOA balance rules, including exclusion of paid water."""
    bal = soa_bill_balance(bill)
    paid = money(bill.amount_paid)
    if bal <= 0:
        return "Paid"
    if paid > 0:
        return "Partially Paid"
    if bill.due_date and date.today() > bill.due_date:
        return "Overdue"
    return "Unpaid"


def soa_bill_total(bill, include_paid_water=False, penalty_amount=None):
    condo, parking, water, penalty, previous = authoritative_bill_values(bill)
    current = condo + parking + _bill_calc(bill)["storage"] + water
    reading = _water_reading_for_bill(bill)
    if reading and getattr(reading, "paid", False) and not include_paid_water:
        current -= water
    calc = _bill_calc(bill)
    current += calc["other"] + calc["adjustment"]      # counted, as in _bill_calc (fix 2026-10-04)
    applied_penalty = penalty if penalty_amount is None else penalty_amount
    advance = min(advance_for_bill(bill), current)
    return (current + previous + applied_penalty - advance).quantize(Decimal("0.01"))

def soa_bill_balance(bill, include_paid_water=False, penalty_amount=None):
    return soa_bill_total(bill, include_paid_water=include_paid_water, penalty_amount=penalty_amount) - money(bill.amount_paid)


def soa_penalty_base(bill):
    return selected_penalty_base_for_unit(bill.unit_id, bill.billing_month) if is_overdue(bill) else Decimal("0.00")

def soa_penalty_amount(bill):
    return (soa_penalty_base(bill) * (Decimal(str(setting_float("penalty_rate", 10))) / Decimal("100"))).quantize(Decimal("0.01")) if is_overdue(bill) else Decimal("0.00")

def water_payment_status(bill):
    if not bill:
        return "Unpaid"
    bal = bill_balance(bill)
    paid = money(bill.amount_paid)
    if bal <= 0:
        return "Paid"
    if paid > 0:
        return "Partially Paid"
    return "Unpaid"


def overdue_months_for_unit(unit_id, month=None):
    rows = Billing.query.filter(Billing.unit_id == unit_id)
    if month:
        rows = rows.filter(Billing.billing_month <= month)
    return [b.billing_month for b in rows.order_by(Billing.billing_month.desc()).all() if bill_balance(b) > 0 and b.due_date and date.today() > b.due_date]




# -----------------------------
# Models
# -----------------------------
class User(ChangeTracked, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    # SQLite keeps VARCHAR(40); MySQL gets a strict ENUM of the six roles (database/schema.sql).
    role = db.Column(db.String(40).with_variant(MYSQL_ENUM(*ALL_ROLES), "mysql"), default="staff")
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Session security (migration 0008). Raising session_version ends every existing session of
    # the account (password reset/change, deactivation). must_change_password forces a new
    # password at the next sign-in (temporary passwords, the old default admin password).
    session_version = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    must_change_password = db.Column(db.Boolean, nullable=False, default=False, server_default="0")
    password_changed_at = db.Column(db.DateTime, nullable=True)

    @validates("role")
    def _validate_role(self, key, value):
        # Same rule as the MySQL ENUM, enforced on SQLite too: no unknown roles can be saved.
        if value not in ALL_ROLES:
            raise ValueError(f"Invalid role {value!r}; allowed: {', '.join(ALL_ROLES)}")
        return value


class Unit(ChangeTracked, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    unit_no = db.Column(db.String(40), unique=True, nullable=False)
    floor = db.Column(db.String(20))
    unit_type = db.Column(db.String(50))

    # New Version 10 fields
    area_sqm = db.Column(db.Float, default=0)
    unit_rate_per_sqm = db.Column(db.Float, default=0)
    include_parking = db.Column(db.Boolean, default=False)
    include_storage = db.Column(db.Boolean, default=False)
    assigned_parking_unit_id = db.Column(db.Integer, db.ForeignKey("unit.id"), nullable=True)
    assigned_storage_unit_id = db.Column(db.Integer, db.ForeignKey("unit.id"), nullable=True)

    # Eager-loadable self-references used by the Units directory to avoid one
    # database query per row when showing assigned parking/storage.
    assigned_parking_unit = db.relationship("Unit", foreign_keys=[assigned_parking_unit_id], remote_side=[id], uselist=False)
    assigned_storage_unit = db.relationship("Unit", foreign_keys=[assigned_storage_unit_id], remote_side=[id], uselist=False)

    # S4: owner/tenant names, contact and email are no longer copied onto the unit. The
    # read-only properties below read them from the owner and tenant records.
    auto_rate = db.Column(db.Boolean, default=True)
    dues_mode = db.Column(db.String(20), default="per_sqm")  # per_sqm or manual
    manual_monthly_dues = db.Column(db.Numeric(12, 2), default=0)

    occupancy_type = db.Column(db.String(20), default="Owner")  # Owner or Tenant
    status = db.Column(db.String(30), default="Vacant")
    active = db.Column(db.Boolean, default=True)

    owners = db.relationship(
        "Owner",
        back_populates="unit",
        lazy=True,
        cascade="all, delete-orphan",
    )
    tenants = db.relationship(
        "Tenant",
        back_populates="unit",
        lazy=True,
        cascade="all, delete-orphan",
    )
    bills = db.relationship("Billing", back_populates="unit", lazy=True)

    @property
    def current_owner(self):
        """The most recently added owner with status Current (the one the old copy showed)."""
        current = [o for o in self.owners if o.status == "Current"]
        return max(current, key=lambda o: o.id) if current else None

    @property
    def current_tenant(self):
        """The representative current tenant, otherwise the most recently added current tenant."""
        current = [t for t in self.tenants if t.status == "Current"]
        if not current:
            return None
        return next((t for t in current if t.representative), None) or max(current, key=lambda t: t.id)

    @property
    def owner_name(self):
        return self.current_owner.owner_name if self.current_owner else None

    @property
    def contact_no(self):
        return self.current_owner.contact_no if self.current_owner else None

    @property
    def email(self):
        return self.current_owner.email if self.current_owner else None

    @property
    def tenant_name(self):
        return self.current_tenant.tenant_name if self.current_tenant else None


class Owner(ChangeTracked, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    unit_id = db.Column(db.Integer, db.ForeignKey("unit.id"), nullable=False)
    owner_name = db.Column(db.String(200), nullable=False)
    contact_no = db.Column(db.String(100))
    email = db.Column(db.String(200))
    move_in = db.Column(db.Date)
    move_out = db.Column(db.Date)
    notes = db.Column(db.Text)
    status = db.Column(db.String(20), default="Current")
    receive_soa_email = db.Column(db.Boolean, default=False)
    include_in_soa = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    unit = db.relationship("Unit", back_populates="owners")


class Tenant(ChangeTracked, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    unit_id = db.Column(db.Integer, db.ForeignKey("unit.id"), nullable=False)
    tenant_name = db.Column(db.String(200), nullable=False)
    contact_no = db.Column(db.String(80))
    email = db.Column(db.String(160))
    move_in = db.Column(db.Date)
    move_out = db.Column(db.Date)
    notes = db.Column(db.String(500))
    status = db.Column(db.String(30), default="Current")
    representative = db.Column(db.Boolean, default=False)
    receive_soa_email = db.Column(db.Boolean, default=False)
    include_in_soa = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    unit = db.relationship("Unit", back_populates="tenants")



class Billing(ChangeTracked, db.Model):
    __table_args__ = (db.Index("ix_billing_unit_month", "unit_id", "billing_month"), db.Index("ix_billing_month", "billing_month"),
                      db.UniqueConstraint("unit_id", "billing_month", name="uq_billing_unit_month"),
                      month_check("billing_month", "ck_billing_month_format"),
                      db.CheckConstraint("amount_paid >= 0", name="ck_billing_amount_paid_nonneg"),
                      db.CheckConstraint("previous_balance >= 0", name="ck_billing_previous_balance_nonneg"))
    id = db.Column(db.Integer, primary_key=True)
    unit_id = db.Column(db.Integer, db.ForeignKey("unit.id"), nullable=False)
    billing_month = db.Column(db.String(7), nullable=False)
    assessment = db.Column(db.Numeric(12, 2), default=0)
    parking_dues = db.Column(db.Numeric(12, 2), default=0)
    storage_dues = db.Column(db.Numeric(12, 2), default=0)
    water = db.Column(db.Numeric(12, 2), default=0)
    other = db.Column(db.Numeric(12, 2), default=0)
    penalty = db.Column(db.Numeric(12, 2), default=0)
    adjustment = db.Column(db.Numeric(12, 2), default=0)
    previous_balance = db.Column(db.Numeric(12, 2), default=0)
    amount_paid = db.Column(db.Numeric(12, 2), default=0)
    due_date = db.Column(db.Date)
    status = db.Column(db.String(30), default="Unpaid")
    paid_date = db.Column(db.Date)
    soa_note = db.Column(db.String(1000), default="")
    soa_manual_override = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    unit = db.relationship("Unit", back_populates="bills")
    payments = db.relationship("Payment", back_populates="billing", lazy=True, cascade="all, delete-orphan")


class Payment(ChangeTracked, db.Model):
    __table_args__ = (db.Index("ix_payment_billing", "billing_id"), db.CheckConstraint("amount >= 0", name="ck_payment_amount_nonneg"))
    id = db.Column(db.Integer, primary_key=True)
    billing_id = db.Column(db.Integer, db.ForeignKey("billing.id"), nullable=False)
    amount = db.Column(db.Numeric(12, 2), default=0)
    payment_date = db.Column(db.Date, default=date.today)
    payment_method = db.Column(db.String(20), default="CASH")
    payment_type = db.Column(db.String(20), default="FULL")
    reference = db.Column(db.String(100))
    remarks = db.Column(db.String(300))
    # Set when the payment's official receipt is voided (migration 0009). The row is kept.
    reversed_at = db.Column(db.DateTime, nullable=True)
    billing = db.relationship("Billing", back_populates="payments")


class AdvancePayment(ChangeTracked, db.Model):
    __table_args__ = (month_check("start_month", "ck_advance_payment_month_format"),
                      db.CheckConstraint("amount >= 0", name="ck_advance_payment_amount_nonneg"),
                      db.CheckConstraint("coverage_months >= 1", name="ck_advance_payment_coverage_min"))
    id = db.Column(db.Integer, primary_key=True)
    unit_id = db.Column(db.Integer, db.ForeignKey("unit.id"), nullable=False)
    payment_date = db.Column(db.Date, default=date.today)
    amount = db.Column(db.Numeric(12, 2), default=0)
    start_month = db.Column(db.String(7), nullable=False)
    coverage_months = db.Column(db.Integer, default=1)
    monthly_amount = db.Column(db.Numeric(12, 2), default=0)
    payment_method = db.Column(db.String(20), default="CASH")
    reference = db.Column(db.String(100))
    remarks = db.Column(db.String(300))
    # Set when the advance's official receipt is voided (migration 0009). The row is kept.
    reversed_at = db.Column(db.DateTime, nullable=True)
    unit = db.relationship("Unit")
    applications = db.relationship("AdvanceApplication", back_populates="advance", cascade="all, delete-orphan")


class AdvanceApplication(db.Model):
    __table_args__ = (db.Index("ix_advance_application_advance", "advance_payment_id"), db.Index("ix_advance_application_billing", "billing_id"),
                      db.UniqueConstraint("advance_payment_id", "billing_id", name="uq_advance_application_advance_billing"),
                      month_check("billing_month", "ck_advance_application_month_format"),
                      db.CheckConstraint("amount >= 0", name="ck_advance_application_amount_nonneg"))
    id = db.Column(db.Integer, primary_key=True)
    advance_payment_id = db.Column(db.Integer, db.ForeignKey("advance_payment.id"), nullable=False)
    billing_id = db.Column(db.Integer, db.ForeignKey("billing.id"), nullable=False)
    billing_month = db.Column(db.String(7), nullable=False)
    amount = db.Column(db.Numeric(12, 2), default=0)
    applied_date = db.Column(db.Date, default=date.today)
    advance = db.relationship("AdvancePayment", back_populates="applications")
    billing = db.relationship("Billing")


class WaterReading(ChangeTracked, db.Model):
    __table_args__ = (db.Index("ix_water_unit_month", "unit_id", "reading_month"), db.Index("ix_water_month", "reading_month"),
                      db.UniqueConstraint("unit_id", "reading_month", name="uq_water_unit_month"),
                      month_check("reading_month", "ck_water_month_format"))
    id = db.Column(db.Integer, primary_key=True)
    unit_id = db.Column(db.Integer, db.ForeignKey("unit.id"), nullable=False)
    reading_month = db.Column(db.String(7), nullable=False)
    previous_reading = db.Column(db.Float, default=0)
    current_reading = db.Column(db.Float, default=0)
    rate = db.Column(db.Float, default=50)
    reading_date = db.Column(db.Date, default=date.today)
    paid = db.Column(db.Boolean, default=False)
    paid_amount = db.Column(db.Numeric(12, 2), default=0)
    payment_method = db.Column(db.String(20), default="CASH")
    payment_type = db.Column(db.String(20), default="FULL")
    payment_reference = db.Column(db.String(100))
    paid_date = db.Column(db.Date)

    unit = db.relationship("Unit")

    @property
    def usage(self):
        return max(float(self.current_reading or 0) - float(self.previous_reading or 0), 0)

    @property
    def bill_amount(self):
        """usage × rate in Decimal, rounded half up to the centavo (same rule as all dues)."""
        usage = max(Decimal(str(self.current_reading or 0)) - Decimal(str(self.previous_reading or 0)), Decimal("0"))
        return (usage * Decimal(str(self.rate or 0))).quantize(Decimal("0.01"), rounding="ROUND_HALF_UP")


class Receipt(db.Model):
    """Official receipt: one per payment received at the office (Phase B2).
    Numbers run per year without gaps: OR-2026-000001, OR-2026-000002, ..."""
    __tablename__ = "receipt"
    __table_args__ = (db.UniqueConstraint("receipt_year", "receipt_seq", name="uq_receipt_year_seq"),
                      db.CheckConstraint("amount > 0", name="ck_receipt_amount_positive"))
    id = db.Column(db.Integer, primary_key=True)
    receipt_year = db.Column(db.Integer, nullable=False)
    receipt_seq = db.Column(db.Integer, nullable=False)
    receipt_no = db.Column(db.String(20), unique=True, nullable=False)
    unit_id = db.Column(db.Integer, db.ForeignKey("unit.id"), nullable=False, index=True)
    received_date = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    payment_method = db.Column(db.String(20), nullable=False, default="CASH")
    reference = db.Column(db.String(100))
    remarks = db.Column(db.String(300))
    received_by = db.Column(db.String(80))
    source = db.Column(db.String(20), nullable=False, default="cashier")  # cashier | backfill
    # A voided receipt keeps its number (no gaps) and its row (migration 0009).
    voided_at = db.Column(db.DateTime, nullable=True)
    voided_by = db.Column(db.String(80))
    void_reason = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    unit = db.relationship("Unit")
    allocations = db.relationship("ReceiptAllocation", back_populates="receipt", cascade="all, delete-orphan")


class ReceiptCounter(db.Model):
    """Last official-receipt sequence number per year. Its row is locked (SELECT ... FOR UPDATE)
    while a receipt is issued, so concurrent cashiers get consecutive, unique numbers."""
    __tablename__ = "receipt_counter"
    year = db.Column(db.Integer, primary_key=True, autoincrement=False)
    last_seq = db.Column(db.Integer, nullable=False)


class FormSubmission(db.Model):
    """One row per submitted payment form (its one-time form token). A second submission of the
    same form - double click, browser refresh, network retry - is refused instead of paying twice."""
    __tablename__ = "form_submission"
    token = db.Column(db.String(64), primary_key=True)
    action = db.Column(db.String(60), nullable=False)
    user_id = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class ReceiptAllocation(db.Model):
    """What a receipt paid: a bill payment, a water charge or an advance payment."""
    __tablename__ = "receipt_allocation"
    __table_args__ = (
        db.CheckConstraint("kind IN ('bill', 'water', 'advance')", name="ck_receipt_allocation_kind"),
        db.CheckConstraint("amount > 0", name="ck_receipt_allocation_amount_positive"),
        db.CheckConstraint("(kind = 'bill' AND payment_id IS NOT NULL) OR (kind = 'water' AND water_reading_id IS NOT NULL) "
                           "OR (kind = 'advance' AND advance_payment_id IS NOT NULL)", name="ck_receipt_allocation_target"),
    )
    id = db.Column(db.Integer, primary_key=True)
    receipt_id = db.Column(db.Integer, db.ForeignKey("receipt.id"), nullable=False, index=True)
    kind = db.Column(db.String(10), nullable=False)
    payment_id = db.Column(db.Integer, db.ForeignKey("payment.id"), index=True)
    water_reading_id = db.Column(db.Integer, db.ForeignKey("water_reading.id"), index=True)
    advance_payment_id = db.Column(db.Integer, db.ForeignKey("advance_payment.id"), index=True)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    receipt = db.relationship("Receipt", back_populates="allocations")
    payment = db.relationship("Payment")
    water_reading = db.relationship("WaterReading")
    advance_payment = db.relationship("AdvancePayment")


class DuplicateSubmission(Exception):
    """The same payment form was submitted twice; the first submission already took effect."""


def reset_financial_caches():
    """Forget per-request balance caches (after locking rows or changing payments)."""
    for name in ("_billing_history_by_unit", "_bill_calc_cache", "_advance_balance_cache", "_advance_applied_cache"):
        g.pop(name, None)


def lock_row(model, row_id):
    """Load a row with an exclusive row lock until commit (MariaDB/MySQL SELECT ... FOR UPDATE)."""
    return db.session.query(model).filter_by(id=row_id).with_for_update().populate_existing().first()


def claim_form_token(action, token=None):
    """One payment per rendered form. The token is put into every POST form by _security_headers
    (the React app sends its own per-form token); claiming it twice raises DuplicateSubmission
    (the second click/refresh changes nothing)."""
    from sqlalchemy.exc import IntegrityError
    token = ((request.form.get("form_token") if token is None else token) or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", token):
        raise InputError("form_token", "This form is out of date. Reload the page and enter the payment again.")
    try:
        with db.session.begin_nested():
            user = current_user()
            db.session.add(FormSubmission(token=token, action=action, user_id=user.id if user else None))
    except IntegrityError:
        raise DuplicateSubmission() from None


def books_closed_through():
    """Last closed billing month (YYYY-MM) from Rates & Rules, or "" when no period is closed."""
    value = (setting("books_closed_through", "") or "").strip()
    return value if re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value) else ""


def check_open_period(month_or_date, what):
    """Refuse changes dated in a closed period (InputError -> message, nothing saved)."""
    closed = books_closed_through()
    month = month_or_date.strftime("%Y-%m") if hasattr(month_or_date, "strftime") else str(month_or_date or "")[:7]
    if closed and month and month <= closed:
        raise InputError("period", f"The books are closed through {closed}. {what} dated {month} can't be changed; "
                                   "record a correction in an open month instead.")


def retry_on_deadlock(fn):
    """Re-run a financial request when the database aborts it because of a lock conflict
    (deadlock 1213 / lock wait timeout 1205). Each attempt starts from a clean transaction."""
    from sqlalchemy.exc import OperationalError

    @wraps(fn)
    def wrapper(*args, **kwargs):
        for attempt in range(3):
            try:
                return fn(*args, **kwargs)
            except OperationalError as exc:
                db.session.rollback()
                reset_financial_caches()
                code = getattr(getattr(exc, "orig", None), "args", [None])[0]
                if code not in (1205, 1213) or attempt == 2:
                    app.logger.exception("Financial transaction failed")
                    flash("The database was busy and nothing was saved. Please try again.", "danger")
                    return redirect(request.referrer or url_for("index"))
                time.sleep(0.05 * (attempt + 1))
    return wrapper


@app.errorhandler(DuplicateSubmission)
def _duplicate_submission(_exc):
    db.session.rollback()
    if request.path.startswith("/api/"):
        response = jsonify({"error": {"status": 409, "duplicate": True,
                                      "message": "This payment was already recorded, so it was not recorded again."}})
        response.status_code = 409
        return response
    flash("This form was already submitted, so it was not recorded again. Check the payment list below.", "warning")
    back = request.referrer or ""
    return redirect(back if back.startswith(request.host_url) else url_for("index"))


def next_receipt_number(year):
    """Next gap-free sequence number for `year`, under a row lock on its counter. The first receipt
    of a year creates the counter row; if two cashiers do that at the same moment, one insert fails
    on the primary key and simply waits for and continues from the other's row."""
    from sqlalchemy.exc import IntegrityError
    counter = db.session.query(ReceiptCounter).filter_by(year=year).with_for_update().first()
    if counter is None:
        start = db.session.query(func.max(Receipt.receipt_seq)).filter(Receipt.receipt_year == year).scalar() or 0
        try:
            with db.session.begin_nested():
                db.session.add(ReceiptCounter(year=year, last_seq=start))
        except IntegrityError:
            pass
        counter = db.session.query(ReceiptCounter).filter_by(year=year).with_for_update().populate_existing().first()
    counter.last_seq += 1
    return counter.last_seq


def issue_receipt(unit_id, received_date, method, reference, remarks, parts, source="cashier", received_by=None):
    """Create the official receipt for money received, in the caller's transaction.

    parts = [(kind, record, amount)] with kind 'bill' (Payment row), 'water' (WaterReading)
    or 'advance' (AdvancePayment). The existing payment records are not changed; the
    receipt only documents them, so balances are computed exactly as before."""
    parts = [(k, r, money(a)) for k, r, a in parts if r is not None and money(a) > 0]
    total = sum((a for _, _, a in parts), Decimal("0"))
    if total <= 0:
        return None
    db.session.flush()  # the paid records need their ids
    year = received_date.year
    seq = next_receipt_number(year)   # counter row locked until commit: unique, consecutive numbers
    receipt = Receipt(receipt_year=year, receipt_seq=seq, receipt_no=f"OR-{year}-{seq:06d}",
                      unit_id=unit_id, received_date=received_date, amount=total,
                      payment_method=(method or "CASH").upper(), reference=(reference or None),
                      remarks=((remarks or "").strip()[:300] or None), source=source,
                      received_by=received_by or ((session.get("username") if has_request_context() else None) or "system"))
    db.session.add(receipt)
    db.session.flush()
    for kind, record, amount in parts:
        db.session.add(ReceiptAllocation(
            receipt_id=receipt.id, kind=kind, amount=amount,
            payment_id=record.id if kind == "bill" else None,
            water_reading_id=record.id if kind == "water" else None,
            advance_payment_id=record.id if kind == "advance" else None))
    return receipt


class Employee(ChangeTracked, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_no = db.Column(db.String(50), unique=True, nullable=False)
    full_name = db.Column(db.String(200), nullable=False)
    position = db.Column(db.String(120))
    department = db.Column(db.String(120))
    employment_status = db.Column(db.String(40), default="Active")
    contact_no = db.Column(db.String(80))
    email = db.Column(db.String(160))
    date_hired = db.Column(db.Date)
    monthly_salary = db.Column(db.Numeric(12, 2), default=0)
    notes = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Expense(ChangeTracked, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    expense_date = db.Column(db.Date, default=date.today)
    category = db.Column(db.String(100))
    description = db.Column(db.String(300))
    amount = db.Column(db.Numeric(12, 2), default=0)


class MoveCertificate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    certificate_no = db.Column(db.String(50), unique=True, nullable=False)
    move_type = db.Column(db.String(20), nullable=False)
    person_type = db.Column(db.String(20), nullable=False)
    unit_id = db.Column(db.Integer, db.ForeignKey("unit.id"), nullable=False)
    person_id = db.Column(db.Integer, nullable=False)
    person_name = db.Column(db.String(200), nullable=False)
    move_date = db.Column(db.Date)
    certificate_date = db.Column(db.Date, nullable=False, default=date.today)
    issued_by = db.Column(db.String(80))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    unit = db.relationship("Unit")


GATE_PASS_TYPES = ("Visitor", "Delivery", "Move-in", "Move-out")
# Staff-issued passes start "Issued". A resident's request starts "Requested"; staff then
# approve it ("Issued") or reject it ("Rejected"), or the resident cancels it ("Cancelled").
GATE_PASS_STATUSES = ("Requested", "Issued", "Rejected", "Cancelled")


class GatePass(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pass_date = db.Column(db.Date, default=date.today)
    unit_no = db.Column(db.String(40))
    visitor_name = db.Column(db.String(200))
    purpose = db.Column(db.String(300))
    status = db.Column(db.String(30), default="Issued")
    # Resident requests (migration 0007). Empty on passes recorded before them.
    pass_type = db.Column(db.String(20))
    unit_id = db.Column(db.Integer, db.ForeignKey("unit.id"), nullable=True, index=True)
    requested_by = db.Column(db.String(80))
    requested_at = db.Column(db.DateTime)
    reviewed_by = db.Column(db.String(80))
    reviewed_at = db.Column(db.DateTime)
    review_note = db.Column(db.String(300))
    unit = db.relationship("Unit")


class AuditLog(db.Model):
    __table_args__ = (db.Index("ix_audit_log_entity", "entity_type", "entity_id"),)
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80))
    action = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Structured audit for financial events (migration 0009). details = JSON before/after values.
    entity_type = db.Column(db.String(40))
    entity_id = db.Column(db.Integer)
    reason = db.Column(db.String(500))
    details = db.Column(db.Text)


class Setting(ChangeTracked, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(80), unique=True)
    value = db.Column(db.String(255))


# -----------------------------
# V10.63 Community foundation
# -----------------------------
class ResidentProfile(ChangeTracked, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False)
    unit_id = db.Column(db.Integer, db.ForeignKey("unit.id"), nullable=False)
    person_type = db.Column(db.String(20), default="Owner")
    person_id = db.Column(db.Integer, nullable=True)
    display_name = db.Column(db.String(200), nullable=False)
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship("User", backref=db.backref("resident_profile", uselist=False))
    unit = db.relationship("Unit")


class Announcement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    audience = db.Column(db.String(30), default="residents")
    published = db.Column(db.Boolean, default=True)
    publish_date = db.Column(db.Date, default=date.today)
    created_by = db.Column(db.String(80))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Vendor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    vendor_name = db.Column(db.String(200), nullable=False)
    service_type = db.Column(db.String(120))
    contact_person = db.Column(db.String(160))
    contact_no = db.Column(db.String(80))
    email = db.Column(db.String(160))
    address = db.Column(db.String(300))
    status = db.Column(db.String(30), default="Active")
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class MaintenanceTicket(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticket_no = db.Column(db.String(40), unique=True, nullable=False)
    unit_id = db.Column(db.Integer, db.ForeignKey("unit.id"), nullable=True)
    resident_profile_id = db.Column(db.Integer, db.ForeignKey("resident_profile.id"), nullable=True)
    vendor_id = db.Column(db.Integer, db.ForeignKey("vendor.id"), nullable=True)
    category = db.Column(db.String(100), default="General")
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    priority = db.Column(db.String(20), default="Normal")
    status = db.Column(db.String(30), default="Open")
    assigned_to = db.Column(db.String(120))
    resolution = db.Column(db.Text)
    requested_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    unit = db.relationship("Unit")
    resident = db.relationship("ResidentProfile")
    vendor = db.relationship("Vendor")


class DocumentRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(100), default="General")
    description = db.Column(db.Text)
    file_name = db.Column(db.String(300))
    file_path = db.Column(db.String(500))
    audience = db.Column(db.String(30), default="admin")
    unit_id = db.Column(db.Integer, db.ForeignKey("unit.id"), nullable=True)
    active = db.Column(db.Boolean, default=True)
    uploaded_by = db.Column(db.String(80))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    unit = db.relationship("Unit")



# Advance-payment helper functions
def month_shift(month, delta):
    y, m = map(int, month.split("-"))
    idx = y * 12 + (m - 1) + int(delta)
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"

def advance_for_bill(bill):
    # Use the request cache when available; fall back to a direct aggregate query.
    return _advance_applied_for_bill(bill)

def advance_balance(advance):
    return _advance_balance_cached(advance)


# -----------------------------
# Template helpers
# -----------------------------
@app.template_global()
def units_count():
    return Unit.query.filter(Unit.active == True, ~Unit.unit_type.in_(["PARKING", "STORAGE"])).count()

# Register existing business helpers with Jinja without redefining them.
app.template_global(name="water_amount_for_reading")(water_amount_for_reading)
app.template_global(name="unpaid_previous_bills")(unpaid_previous_bills)
app.template_global(name="bill_current_charges")(bill_current_charges)
app.template_global(name="previous_outstanding")(previous_outstanding)
app.template_global(name="penalty_for_unit")(penalty_for_unit)
app.template_global(name="bill_total")(bill_total)
app.template_global(name="bill_balance")(bill_balance)
app.template_global(name="advance_for_bill")(advance_for_bill)
app.template_global(name="advance_balance")(advance_balance)
app.template_global(name="month_shift")(month_shift)
app.template_global(name="authoritative_bill_values")(authoritative_bill_values)
app.template_global(name="storage_charged")(storage_charged)
app.template_global(name="storage_cutoff")(storage_cutoff)


@app.template_global()
def condo_calc_text(bill):
    """'50.00 sqm × ₱75.00/sqm' when it explains the issued amount; empty when the bill was
    issued at a different rate (frozen) or edited by hand."""
    unit = bill.unit
    rate = float(unit.unit_rate_per_sqm or rate_for_type(unit.unit_type) or 0)
    area = float(unit.area_sqm or 0)
    if area and rate and abs(area * rate - float(money(bill.assessment))) < 0.005:
        return f"{area:,.2f} sqm × ₱{rate:,.2f}/sqm"
    return ""
app.template_global(name="rate_for_type")(rate_for_type)
app.template_global(name="water_payment_status")(water_payment_status)
app.template_global(name="overdue_months_for_unit")(overdue_months_for_unit)
app.template_global(name="unit_dues")(unit_dues)
app.template_global(name="parking_dues")(parking_dues)
app.template_global(name="storage_dues")(storage_dues)
app.template_global(name="assigned_asset")(assigned_asset)
app.template_global(name="asset_unit_charge")(asset_unit_charge)
app.template_global(name="bill_status_label")(bill_status)
app.template_global(name="water_history_status")(water_history_status)
app.template_global(name="soa_bill_status")(soa_bill_status)
app.template_global(name="soa_bill_total")(soa_bill_total)
app.template_global(name="soa_bill_balance")(soa_bill_balance)
app.template_global(name="soa_penalty_base")(soa_penalty_base)
app.template_global(name="soa_penalty_amount")(soa_penalty_amount)


@app.template_filter("currency")
def currency(v):
    return f"₱{money(v):,.2f}"


@app.template_filter("num")
def num(v):
    try:
        return f"{float(v):,.2f}"
    except Exception:
        return "0.00"


@app.template_filter("localtime")
def localtime(v, fmt="%Y-%m-%d %H:%M"):
    """Show a UTC timestamp (datetime.utcnow() columns) in the server PC's local time."""
    return v.replace(tzinfo=timezone.utc).astimezone().strftime(fmt) if v else ""


@app.template_filter("month_year")
def month_year(v):
    """Display YYYY-MM billing periods as a friendly month-year label."""
    if not v:
        return "—"
    text = str(v)
    try:
        return datetime.strptime(text[:7], "%Y-%m").strftime("%b %Y")
    except ValueError:
        return text


@app.context_processor
def inject_globals():
    return {
        "current_user": current_user(),
        "can_access": can_access,
        # Make the setting() helper available to all Jinja templates.
        # Billing and other pages use it for configurable rates/rules.
        "setting": setting,
        "corporation_name": setting("corporation_name", "CITYLAND 9 CONDOMINIUM CORPORATION"),
        "address": setting(
            "address",
            "9 Dela Rosa Condominium, Dela Rosa Street, Barangay Pio del Pilar, Makati City",
        ),
        "today": date.today(),
    }


# -----------------------------
# Dashboard
# -----------------------------
# The new React app (/app/) is the front door. The old login page, home redirect and
# resident portal are retired; old module pages stay until each is rebuilt in React.
APP_URL = "/app/"


def app_login_url():
    """/app/login, carrying the reason an old page signed the user out (e.g. moved out)."""
    reasons = [m for c, m in get_flashed_messages(with_categories=True) if c in ("danger", "warning")]
    return "/app/login" + (f"?{urlencode({'notice': reasons[-1]})}" if reasons else "")


# ------------------------------------------------------------------ request-wide security
# CSRF for the classic screens. The JSON API blueprints check X-CSRFToken themselves
# (app.utils.auth.protect_api_blueprint) with the same per-session token.
from app.utils.auth import csrf_token as _csrf_token  # noqa: E402
import hmac  # noqa: E402

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_FORM_TAG = re.compile(r"(<form\b[^>]*\bmethod\s*=\s*[\"']?post[\"']?[^>]*>)", re.IGNORECASE)
# Endpoints a user who must change their password may still use.
PASSWORD_CHANGE_ALLOWED = {"static", "logout", "login", "index", "change_password",
                           "api_auth.get_csrf", "api_auth.login", "api_auth.logout", "api_auth.me", "api_auth.change_password"}


@app.errorhandler(413)
def _too_large(_exc):
    limit = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
    if request.path.startswith("/api/"):
        response = jsonify({"error": {"status": 413, "message": f"The upload is larger than {limit} MB."}})
        response.status_code = 413
        return response
    flash(f"The file is larger than {limit} MB (MAX_UPLOAD_MB in .env sets the limit).", "danger")
    return redirect(url_for("settings") if can_access("settings") else url_for("index"))


@app.errorhandler(500)
def _server_error(exc):
    """Unexpected failure: undo the half-done transaction, log the details with a reference,
    and show the user a plain message (no internals) they can quote when reporting it."""
    db.session.rollback()
    ref = secrets.token_hex(4).upper()
    original = getattr(exc, "original_exception", exc)
    app.logger.error("Unhandled error ref=%s %s %s", ref, request.method, request.path, exc_info=original)
    if request.path.startswith("/api/"):
        response = jsonify({"error": {"status": 500, "message": f"Server error (reference {ref}). Nothing was saved."}})
        response.status_code = 500
        return response
    return (f"<!doctype html><title>Something went wrong</title><div style='font-family:sans-serif;margin:2rem;max-width:40rem'>"
            f"<h2>Something went wrong</h2><p>Nothing was saved. Please go back and try again. If it happens again, "
            f"tell the administrator this reference: <b>{ref}</b>.</p></div>", 500)


@app.errorhandler(InputError)
def _invalid_input(exc):
    """A typed value was rejected (app.core.numbers): nothing is saved, the user sees why."""
    db.session.rollback()
    if request.path.startswith("/api/"):
        response = jsonify({"error": {"status": 400, "message": exc.message, "fields": {exc.field: exc.message}}})
        response.status_code = 400
        return response
    flash(exc.message, "danger")
    back = request.referrer or ""
    same_site = back.startswith(request.host_url)
    return redirect(back if same_site else url_for("index"))


@app.before_request
def _classic_csrf_check():
    if request.method in UNSAFE_METHODS and not request.path.startswith("/api/"):
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRFToken", "")
        expected = session.get("csrf_token", "")
        if not expected or not sent or not hmac.compare_digest(str(sent), str(expected)):
            app.logger.warning("CSRF rejected: %s %s", request.method, request.path)
            return ("<!doctype html><title>Form expired</title><p style='font-family:sans-serif;margin:2rem'>"
                    "This form has expired or was not sent from CityLand 9. Go back, reload the page and try again.</p>", 400)


@app.before_request
def _force_password_change():
    if not session.get("user_id") or request.endpoint in PASSWORD_CHANGE_ALLOWED or (request.endpoint or "").startswith("react_app."):
        return None
    user = current_user()
    if user and user.must_change_password:
        if request.path.startswith("/api/"):
            response = jsonify({"error": {"status": 403, "code": "password_change_required",
                                          "message": "Please choose a new password before continuing."}})
            response.status_code = 403
            return response
        return redirect(APP_URL)   # the new app shows the change-password screen


@app.after_request
def _security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if request.is_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    ctype = response.mimetype or ""
    if session.get("user_id") and ctype in ("text/html", "application/json"):
        response.headers["Cache-Control"] = "no-store"   # signed-in pages/data are never cached
    # Put the CSRF token into every POST form of classic pages (all 46 forms, and any future one).
    if ctype == "text/html" and not response.direct_passthrough and request.endpoint != "react_app.serve" and response.status_code < 400:
        body = response.get_data(as_text=True)
        if "<form" in body.lower():
            field = f'<input type="hidden" name="csrf_token" value="{_csrf_token()}">'
            # Plus a one-time token per rendered form: payment routes accept each form only once.
            response.set_data(_FORM_TAG.sub(
                lambda m: m.group(1) + field + f'<input type="hidden" name="form_token" value="{secrets.token_urlsafe(24)}">', body))
    return response


@app.route("/")
def index():
    return redirect(APP_URL if current_user() else app_login_url())


@app.route("/dashboard")
@guarded
def dashboard():
    month = datetime.now().strftime("%Y-%m")
    units = [u for u in Unit.query.filter_by(active=True).all() if u.unit_type not in ("PARKING", "STORAGE")]
    bills = Billing.query.filter_by(billing_month=month).all()

    billed = sum((bill_total(b) for b in bills), Decimal("0"))
    collected = sum((money(b.amount_paid) for b in bills), Decimal("0"))
    overdue = sum(
        (bill_balance(b) for b in bills if b.due_date and b.due_date < date.today() and bill_balance(b) > 0),
        Decimal("0"),
    )
    occupied = sum(1 for u in units if u.status.lower() == "occupied")
    water_units = {r.unit_id for r in WaterReading.query.filter_by(reading_month=month).all()}
    pending_water = sum(1 for u in units if u.id not in water_units)

    return render_template(
        "dashboard.html",
        month=month,
        units=units,
        billed=billed,
        collected=collected,
        overdue=overdue,
        occupied=occupied,
        pending_water=pending_water,
        recent_bills=Billing.query.order_by(Billing.id.desc()).limit(8).all(),
    )


# -----------------------------
# Authentication
# -----------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    # Sign-in happens in the new app. A POST still works for scripts (CSRF token required,
    # same throttling and generic errors as the API sign-in).
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        client = client_address()
        if security.login_throttle.retry_after(username, client):
            app.logger.warning("Sign-in throttled for %r from %s", username, client)
            flash("Too many failed sign-in attempts. Please wait a few minutes and try again.", "danger")
            return redirect(app_login_url())
        user = User.query.filter_by(username=username, active=True).first()
        if user and check_password_hash(user.password_hash, password):
            security.login_throttle.success(username, client)
            if any(password == d for d in security.SHIPPED_DEFAULT_PASSWORDS):
                user.must_change_password = True
                db.session.commit()
            start_session(user)
            audit("Login")
            return redirect(APP_URL)
        security.login_throttle.failure(username, client)
        app.logger.warning("Failed sign-in for %r from %s", username, client)
        flash("Invalid username or password.", "danger")

    return redirect(app_login_url())


@app.route("/logout", methods=["GET", "POST"])
def logout():
    # Signing out changes state, so it requires POST (with the CSRF token). A GET never signs
    # anyone out; it just returns to the app, where the Sign out button is.
    if request.method == "GET":
        return redirect(APP_URL)
    if session.get("user_id"):
        audit("Logout")
    session.clear()
    return redirect("/app/login")


# -----------------------------
# Units
# -----------------------------
# Units Directory moved to the React app (/app/<workspace>/units, API /api/units,
# backend/app/routes/units.py): units, owners, tenants, parking and storage. Same permission keys.
# The old URLs stay (bookmarks, links from classic Billing pages) and only redirect; a form posted
# from an old open tab changes nothing and says so.
REACT_PORTALS = {"super_admin": "superadmin", "admin": "admin", "manager": "hr", "staff": "staff",
                 "accounting": "accounting", "resident": "resident"}


def react_url(path, **params):
    """/app/<the signed-in user's workspace><path>?params (classic URL -> its React page)."""
    u = current_user()
    url = f"/app/{REACT_PORTALS.get(u.role if u else '', '')}{path}"
    params = {k: v for k, v in params.items() if v not in (None, "")}
    return url + (f"?{urlencode(params)}" if params else "")


def _units_moved(unit_id=None, **params):
    if request.method == "POST":
        app.logger.info("Classic Units form posted from an old page by %s; nothing changed", session.get("username"))
        return redirect(react_url("/units", unit=unit_id, moved="form"), code=303)
    return redirect(react_url("/units", unit=unit_id, **params))


@app.route("/units", methods=["GET", "POST"])
@guarded
def units():
    return _units_moved(q=request.args.get("q"), kind=request.args.get("unit_type") if request.args.get("unit_type") in ("PARKING", "STORAGE") else None)


@app.route("/unit/<int:uid>")
@guarded
def unit_detail(uid):
    return _units_moved(uid)


@app.route("/unit/<int:uid>/edit", methods=["POST"])
@guarded
def edit_unit(uid):
    return _units_moved(uid)


@app.route("/unit/<int:uid>/tenant/add", methods=["POST"])
@guarded
def add_tenant(uid):
    return _units_moved(uid)


@app.route("/tenants")
@guarded
def tenants():
    return _units_moved(q=request.args.get("q"), past="1")


@app.route("/unit/<int:uid>/tenant/<int:tid>/edit", methods=["POST"])
@guarded
def edit_tenant(uid, tid):
    return _units_moved(uid)


@app.route("/tenant/<int:tid>/status", methods=["POST"])
@guarded
def tenant_status(tid):
    tenant = db.session.get(Tenant, tid)
    return _units_moved(tenant.unit_id if tenant else None)


@app.route("/unit/<int:uid>/owner/add", methods=["POST"])
@guarded
def add_owner(uid):
    return _units_moved(uid)


@app.route("/unit/<int:uid>/owner/<int:oid>/edit", methods=["POST"])
@guarded
def edit_owner(uid, oid):
    return _units_moved(uid)


@app.route("/parking")
@guarded
def parking():
    return _units_moved(kind="PARKING")



# -----------------------------
# Billing
# -----------------------------
# Billing & SOA moved to the React app (/app/<workspace>/billing, API /api/billing,
# backend/app/routes/billing.py). The money logic below is the classic screen's, now as plain
# functions (one implementation, same rules): generate_bills, record_bill_payment, correct_soa,
# recalculate_bill. The old URLs only redirect; a form posted from an old tab changes nothing.
def _billing_moved(bill_id=None, month=None):
    if request.method == "POST":
        app.logger.info("Classic Billing form posted from an old page by %s; nothing changed", session.get("username"))
        return redirect(react_url("/billing", bill=bill_id, month=month, moved="form"), code=303)
    return redirect(react_url("/billing", bill=bill_id, month=month))


@app.route("/billing", methods=["GET", "POST"])
@guarded
def billing():
    return _billing_moved(month=request.args.get("month"))


def generate_bills(month):
    """Create the missing bills of billing month YYYY-MM, one per active residential unit (bills
    that exist are skipped). Returns (created, skipped, due_date). Commits."""
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month or ""):
        raise InputError("month", "Choose a valid billing month.")
    check_open_period(month, "Bills")
    due_date = due_date_for(month)
    created = skipped = 0
    for u in Unit.query.filter_by(active=True).all():
        if u.unit_type in ("PARKING", "STORAGE"):
            continue
        if Billing.query.filter_by(unit_id=u.id, billing_month=month).first():
            skipped += 1
            continue
        previous = previous_outstanding(u.id, month)
        penalty = penalty_for_unit(u.id, month)
        reading = WaterReading.query.filter_by(unit_id=u.id, reading_month=month).order_by(WaterReading.id.desc()).first()
        water = water_amount_for_reading(reading)
        condo = Decimal(str(unit_dues(u)))
        parking = Decimal(str(parking_dues(u)))
        storage = Decimal(str(storage_dues(u)))
        bill = Billing(unit_id=u.id, billing_month=month, assessment=condo, parking_dues=parking,
            storage_dues=storage, water=water, other=0, penalty=penalty, adjustment=0, previous_balance=max(previous, Decimal("0")),
            amount_paid=0, due_date=due_date, status="Unpaid")
        db.session.add(bill)
        created += 1
    allocate_advances_for_month(month)
    audit(f"Generated {created} detailed bills for {month}", entity_type="billing_month", reason=None,
          details={"month": month, "created": created, "due_date": due_date.isoformat()}, commit=False)
    db.session.commit()
    return created, skipped, due_date


# Advance Payments moved to the React app (/app/<workspace>/advances, API /api/advances,
# backend/app/routes/advances.py). The rules are below as one function (record_advance_payment).
@app.route("/billing/advance", methods=["GET", "POST"])
@guarded
def advance_payments():
    if request.method == "POST":
        app.logger.info("Classic advance payment form posted from an old page by %s; nothing changed", session.get("username"))
        return redirect(react_url("/advances", moved="form"), code=303)
    return redirect(react_url("/advances", unit=request.args.get("unit_id")))


def record_advance_payment(*, unit_id, amount, start_month, months, method, reference, remarks, payment_date, form_token):
    """Record prepaid condo dues and issue the official receipt (the classic rules): split evenly per
    month, applied automatically to the unit's bills from start_month (condo dues only). One payment
    per form token; refused in a closed period. Returns (advance, receipt). Commits."""
    unit = db.session.get(Unit, unit_id) if unit_id else None
    if not unit or not unit.active or unit.unit_type in ("PARKING", "STORAGE"):
        raise InputError("unitId", "Choose an active residential unit.")
    if amount <= 0:
        raise InputError("amount", "Enter the amount received.")
    if not isinstance(months, int) or not 1 <= months <= 120:
        raise InputError("months", "Months covered must be between 1 and 120.")
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", start_month or ""):
        raise InputError("startMonth", "Choose the first billing month it covers.")
    method = (method or "CASH").upper()
    if method not in ("CASH", "CHECK", "ONLINE"):
        raise InputError("method", "Choose Cash, Check or Online.")
    reference = (reference or "").strip()
    remarks = (remarks or "").strip()
    if method in ("CHECK", "ONLINE") and not reference:
        raise InputError("reference", "Reference number is required for Check or Online payments.")
    payment_date = payment_date or date.today()
    check_open_period(payment_date, "Payments")
    claim_form_token("advance_payment", form_token)
    monthly = round_money(amount / months)
    adv = AdvancePayment(unit_id=unit.id, payment_date=payment_date, amount=amount, start_month=start_month, coverage_months=months,
                         monthly_amount=monthly, payment_method=method, reference=reference, remarks=remarks)
    db.session.add(adv)
    db.session.flush()
    allocate_advances_for_month(start_month, unit.id)
    receipt = issue_receipt(unit.id, payment_date, method, reference, remarks, [("advance", adv, amount)])
    audit(f"Issued {receipt.receipt_no} - Recorded {months}-month advance condo dues payment for unit {unit.unit_no}: {amount:,.2f}",
          entity_type="receipt", entity_id=receipt.id,
          details={"advance_payment_id": adv.id, "amount": amount, "start_month": start_month, "months": months, "method": method},
          commit=False)
    db.session.commit()
    return adv, receipt


@app.route("/billing/<int:bid>")
@guarded
def billing_detail(bid):
    return _billing_moved(bill_id=bid)


@app.route("/billing/<int:bid>/pay", methods=["POST"])
@guarded
def mark_bill_paid(bid):
    return _billing_moved(bill_id=bid)


def record_bill_payment(bid, *, amount, payment_method, payment_type, reference, remarks, payment_date, form_token):
    """Record money received for a bill and issue its official receipt (the classic cashier rules).

    amount: Decimal > 0 (validated by the caller). The part needed to clear the bill is applied to
    it; any excess becomes an advance payment for the same unit from this billing month. One
    payment per form token (a second submit raises DuplicateSubmission). The bill row is locked
    until commit, so two cashiers paying the same bill are applied one after the other.
    Returns None when the bill doesn't exist, else a dict (bill, receipt, applied, excess). Commits."""
    b = lock_row(Billing, bid)
    if not b:
        return None
    reset_financial_caches()
    balance_before = max(bill_balance(b), Decimal("0"))
    payment_method = (payment_method or "CASH").upper()
    payment_type = (payment_type or "FULL").upper()
    reference = (reference or "").strip()
    remarks = (remarks or "").strip()
    payment_date = payment_date or date.today()
    if payment_method not in ("CASH", "CHECK", "ONLINE"):
        raise InputError("method", "Choose Cash, Check or Online.")
    if payment_type not in ("FULL", "PARTIAL"):
        raise InputError("type", "Choose a full or partial payment.")
    if amount <= 0:
        raise InputError("amount", "Please enter a payment amount.")
    # Payments may exceed the current SOA: the excess is recorded as an advance payment.
    if balance_before <= 0:
        excess_amount = amount
        amount_to_bill = Decimal("0.00")
    else:
        amount_to_bill = min(amount, balance_before)
        excess_amount = max(amount - balance_before, Decimal("0.00"))
    if payment_method in ("CHECK", "ONLINE") and not reference:
        raise InputError("reference", "Reference number is required for Check or Online payments.")
    check_open_period(payment_date, "Payments")
    claim_form_token("bill_payment", form_token)
    paid_before, status_before = money(b.amount_paid), b.status
    if amount_to_bill > 0:
        b.amount_paid = money(b.amount_paid) + amount_to_bill
        db.session.flush()
        reset_financial_caches()       # recompute the balances with the new amount paid

    if bill_balance(b) <= 0:
        if amount_to_bill > 0 and bill_total(b) - money(b.amount_paid) <= 0:
            b.amount_paid = bill_total(b)
        b.status = "Paid"
        b.paid_date = payment_date
    else:
        b.status = "Partially Paid"

    bill_payment = excess_adv = None
    if amount_to_bill > 0:
        bill_payment = Payment(billing_id=b.id, amount=amount_to_bill, payment_date=payment_date, payment_method=payment_method,
                               payment_type=payment_type, reference=reference, remarks=remarks)
        db.session.add(bill_payment)

    if excess_amount > 0:
        excess_adv = AdvancePayment(
            unit_id=b.unit_id, payment_date=payment_date, amount=excess_amount, start_month=b.billing_month,
            coverage_months=1, monthly_amount=excess_amount, payment_method=payment_method, reference=reference,
            remarks=(remarks + " " if remarks else "") + f"Automatic advance from excess payment for {b.billing_month}.")
        db.session.add(excess_adv)
        db.session.flush()
        # The bill is already settled above, so this advance remains available for the next month.
        allocate_advances_for_month(b.billing_month, b.unit_id)

    receipt = issue_receipt(b.unit_id, payment_date, payment_method, reference, remarks,
                            [("bill", bill_payment, amount_to_bill), ("advance", excess_adv, excess_amount)])
    audit(
        f"Issued {receipt.receipt_no} - "
        f"Recorded {payment_type.lower()} {payment_method.lower()} payment for unit "
        f"{b.unit.unit_no}, billing {b.billing_month}: applied ₱{amount_to_bill:,.2f}"
        + (f", excess ₱{excess_amount:,.2f} moved to advance." if excess_amount > 0 else ""),
        entity_type="receipt", entity_id=receipt.id, commit=False,
        details={"billing_id": b.id, "received": amount, "applied_to_bill": amount_to_bill, "excess_to_advance": excess_amount,
                 "amount_paid": {"before": paid_before, "after": money(b.amount_paid)}, "status": {"before": status_before, "after": b.status}},
    )
    db.session.commit()
    return {"bill": b, "receipt": receipt, "applied": money(amount_to_bill), "excess": money(excess_amount)}


def soa_email_contacts(unit):
    contacts=[]; seen=set()
    for owner in Owner.query.filter_by(unit_id=unit.id, status="Current").order_by(Owner.id.desc()).all():
        email=(owner.email or "").strip()
        if email and owner.receive_soa_email and email.lower() not in seen:
            contacts.append({"name":owner.owner_name or "Owner","email":email,"type":"Owner","id":owner.id}); seen.add(email.lower())
    for tenant in Tenant.query.filter_by(unit_id=unit.id, status="Current").order_by(Tenant.id.asc()).all():
        email=(tenant.email or "").strip()
        if email and tenant.receive_soa_email and email.lower() not in seen:
            contacts.append({"name":tenant.tenant_name or "Tenant","email":email,"type":"Tenant","id":tenant.id}); seen.add(email.lower())
    return contacts


def send_soa_email_to_contact(bill, contact):
    host=setting("smtp_host","").strip(); port=int(setting("smtp_port","587") or 587); sender=setting("smtp_sender","").strip()
    username=setting("smtp_username","").strip() or os.getenv("SMTP_USERNAME","").strip(); password=get_smtp_password() or os.getenv("SMTP_PASSWORD","")
    if not host or not sender: raise RuntimeError("Configure SMTP Host and Sender Email under Rates & Rules before sending email.")
    water_reading=WaterReading.query.filter_by(unit_id=bill.unit_id,reading_month=bill.billing_month).order_by(WaterReading.id.desc()).first()
    body_html=render_template("soa_email_body.html",bill=bill,contact=contact,recipient_name=contact["name"],water_reading=water_reading)
    msg=EmailMessage(); msg["Subject"]=f"Statement of Account - Unit {bill.unit.unit_no} - {bill.billing_month}"; msg["From"]=sender; msg["To"]=contact["email"]
    msg.set_content(f"Statement of Account for Unit {bill.unit.unit_no}, Billing Period {bill.billing_month}. Total Amount Due: {bill_total(bill):,.2f}"); msg.add_alternative(body_html,subtype="html")
    with smtplib.SMTP(host,port,timeout=20) as smtp:
        smtp.ehlo(); smtp.starttls(); smtp.ehlo()
        if username and password: smtp.login(username,password)
        smtp.send_message(msg)


@app.route("/billing/email")
@guarded
def billing_email():
    return _billing_moved(month=request.args.get("month"))


@app.route("/billing/email/send", methods=["POST"])
@guarded
def send_billing_emails():
    return _billing_moved(month=request.form.get("month"))


@app.route("/billing/<int:bid>/email", methods=["POST"])
@guarded
def email_bill(bid):
    return _billing_moved(bill_id=bid)


@app.route("/billing/<int:bid>/edit-soa", methods=["POST"])
@guarded
def edit_soa(bid):
    return _billing_moved(bill_id=bid)


@app.route("/billing/<int:bid>/recalculate", methods=["POST"])
@guarded
def recalculate_soa(bid):
    return _billing_moved(bill_id=bid)


def email_soas(bills):
    """Email each bill's SOA to the unit's opted-in owners/tenants. Returns (sent, skipped, failed, errors)."""
    sent = failed = skipped = 0
    errors = []
    for bill in bills:
        contacts = soa_email_contacts(bill.unit)
        if not contacts:
            skipped += 1
            continue
        for contact in contacts:
            try:
                send_soa_email_to_contact(bill, contact)
                sent += 1
                audit(f"Emailed SOA for unit {bill.unit.unit_no}, billing {bill.billing_month} to {contact['email']} ({contact['type']})")
            except Exception as exc:
                failed += 1
                errors.append(f"Unit {bill.unit.unit_no} / {contact['email']}: {exc}")
                app.logger.exception("SOA email failed")
    return sent, skipped, failed, errors


SOA_FIELDS = ("assessment", "parking_dues", "storage_dues", "water", "other", "penalty", "adjustment", "previous_balance")


def correct_soa(bid, amounts, due_date, note):
    """Manual SOA correction (classic Edit SOA): amounts = {field: Decimal} for any of SOA_FIELDS. Marks the
    bill as corrected by hand (it is then never re-priced or re-synced automatically). The change
    and its audit entry (before/after, the note as reason) are saved together. Returns the bill or None."""
    b = lock_row(Billing, bid)
    if not b:
        return None
    check_open_period(b.billing_month, "Statements")
    before = {k: getattr(b, k) for k in SOA_FIELDS + ("due_date", "soa_note")}
    for k in SOA_FIELDS:
        if k in amounts:          # fields not sent keep their value
            setattr(b, k, amounts[k])
    b.due_date = due_date or b.due_date
    b.soa_note = (note or "").strip()
    b.soa_manual_override = True
    reset_financial_caches()
    b.status = bill_status(b)
    changes = [k for k in before if before[k] != getattr(b, k)]
    audit(f"Manual SOA correction for unit {b.unit.unit_no}, billing {b.billing_month}; fields changed: {', '.join(changes) if changes else 'none'}",
          entity_type="billing", entity_id=b.id, reason=b.soa_note or None, commit=False,
          details={k: {"before": before[k], "after": getattr(b, k)} for k in changes})
    db.session.commit()
    return b


class SoaCorrectedByHand(Exception):
    """The SOA was corrected by hand, so it can't be re-priced from rates."""


def recalculate_bill(bid):
    """Re-price an issued bill from the unit's CURRENT rates (condo dues, parking, storage): the
    explicit, audited way to correct one (issued bills never change on their own).
    Returns (bill, changed) or None; raises SoaCorrectedByHand for a hand-corrected SOA. Commits."""
    b = lock_row(Billing, bid)
    if not b:
        return None
    if b.soa_manual_override:
        raise SoaCorrectedByHand()
    check_open_period(b.billing_month, "Statements")
    before = (money(b.assessment), money(b.parking_dues), money(b.storage_dues))
    b.assessment = Decimal(str(unit_dues(b.unit)))
    b.parking_dues = Decimal(str(parking_dues(b.unit)))
    b.storage_dues = Decimal(str(storage_dues(b.unit)))
    after = (money(b.assessment), money(b.parking_dues), money(b.storage_dues))
    reset_financial_caches()
    b.status = bill_status(b)
    if before != after:
        audit(f"Recalculated SOA for unit {b.unit.unit_no}, billing {b.billing_month} from current rates: "
              f"condo {before[0]:,.2f}->{after[0]:,.2f}, parking {before[1]:,.2f}->{after[1]:,.2f}, "
              f"storage {before[2]:,.2f}->{after[2]:,.2f}", entity_type="billing", entity_id=b.id, commit=False,
              details={"condo": {"before": before[0], "after": after[0]}, "parking": {"before": before[1], "after": after[1]},
                       "storage": {"before": before[2], "after": after[2]}})
    db.session.commit()
    return b, before != after


# -----------------------------
# Official receipts (Phase B2)
# -----------------------------
@app.template_global()
def receipt_no_for(kind, record_id):
    """OR number of the receipt that paid a bill payment / water charge / advance, or ''."""
    column = {"bill": ReceiptAllocation.payment_id, "water": ReceiptAllocation.water_reading_id,
              "advance": ReceiptAllocation.advance_payment_id}[kind]
    row = (db.session.query(Receipt.receipt_no).join(ReceiptAllocation)
           .filter(column == record_id).order_by(Receipt.id).first())
    return row[0] if row else ""


# Payments & ORs moved to the React app (/app/<workspace>/payments, API /api/receipts,
# backend/app/routes/receipts.py). The void rules are below as one function (void_receipt_record).
# The old URLs only redirect; a void posted from an old tab changes nothing and says so.
def _receipts_moved(receipt_id=None):
    if request.method == "POST":
        app.logger.info("Classic receipt form posted from an old page by %s; nothing changed", session.get("username"))
        return redirect(react_url("/payments", receipt=receipt_id, moved="form"), code=303)
    return redirect(react_url("/payments", receipt=receipt_id))


@app.route("/receipts")
@guarded
def receipts():
    return _receipts_moved()


@app.route("/receipts/<int:rid>")
@guarded
def receipt_detail(rid):
    return _receipts_moved(rid)


@app.route("/receipts/<int:rid>/void", methods=["POST"])
@guarded
def void_receipt(rid):
    return _receipts_moved(rid)


class VoidRefused(Exception):
    """The receipt can't be voided (already void, or its advance was already applied); message says why."""


def void_receipt_record(rid, reason, form_token):
    """Void an official receipt and reverse what it paid. Nothing is deleted: the receipt keeps its
    number (marked VOID with the reason), payment and advance rows are kept and marked reversed,
    and bill / water balances go back up by exactly the receipt's amounts. One audit entry records
    everything with before/after values. Refused in a closed period. Returns the receipt or None. Commits."""
    reason = (reason or "").strip()
    if len(reason) < 10:
        raise InputError("reason", "Give the reason for voiding this receipt (at least 10 characters).")
    if len(reason) > 500:
        raise InputError("reason", "Keep the reason under 500 characters.")
    r = lock_row(Receipt, rid)
    if not r:
        return None
    if r.voided_at:
        raise VoidRefused(f"{r.receipt_no} is already void.")
    check_open_period(r.received_date, "Receipts")
    claim_form_token("void_receipt", form_token)
    reset_financial_caches()
    now = datetime.utcnow()
    changes = []
    for a in r.allocations:
        amount = money(a.amount)
        if a.kind == "bill":
            payment = a.payment
            bill = lock_row(Billing, payment.billing_id)
            before = (money(bill.amount_paid), bill.status)
            bill.amount_paid = max(money(bill.amount_paid) - amount, Decimal("0"))
            payment.reversed_at = now
            reset_financial_caches()
            bill.status = bill_status(bill)
            if bill.status != "Paid":
                bill.paid_date = None
            changes.append({"kind": "bill", "billing_id": bill.id, "month": bill.billing_month, "amount": amount,
                            "amount_paid": {"before": before[0], "after": money(bill.amount_paid)},
                            "status": {"before": before[1], "after": bill.status}})
        elif a.kind == "water":
            reading = lock_row(WaterReading, a.water_reading_id)
            before = money(reading.paid_amount)
            reading.paid_amount = max(before - amount, Decimal("0"))
            reading.paid = money(reading.paid_amount) >= money(reading.bill_amount) and money(reading.paid_amount) > 0
            if not reading.paid:
                reading.paid_date = None
            changes.append({"kind": "water", "water_reading_id": reading.id, "month": reading.reading_month, "amount": amount,
                            "paid_amount": {"before": before, "after": money(reading.paid_amount)}})
        else:
            adv = lock_row(AdvancePayment, a.advance_payment_id)
            applied = db.session.query(func.coalesce(func.sum(AdvanceApplication.amount), 0)).filter(
                AdvanceApplication.advance_payment_id == adv.id).scalar() or 0
            if money(applied) > 0:
                db.session.rollback()
                raise VoidRefused(f"{r.receipt_no} can't be voided: ₱{money(applied):,.2f} of its advance payment was already applied "
                                  "to later bills. Record a correcting entry instead and ask Accounting to review.")
            adv.reversed_at = now
            changes.append({"kind": "advance", "advance_payment_id": adv.id, "amount": amount})
    r.voided_at, r.voided_by, r.void_reason = now, session.get("username"), reason[:500]
    audit(f"Voided {r.receipt_no} (₱{money(r.amount):,.2f}, unit {r.unit.unit_no})", entity_type="receipt", entity_id=r.id,
          reason=reason, details={"receipt_no": r.receipt_no, "amount": money(r.amount), "reversed": changes}, commit=False)
    db.session.commit()
    return r


@app.route("/billing/<int:bid>/qr")
@guarded
def billing_qr(bid):
    b = db.session.get(Billing, bid)
    if not b:
        return "Bill not found", 404
    if qrcode is None:
        return "Install qrcode to enable QR generation.", 500
    pay_url = setting("online_payment_url", "")
    if pay_url:
        sep = "&" if "?" in pay_url else "?"
        payload = f"{pay_url}{sep}unit={b.unit.unit_no}&billing_month={b.billing_month}&amount={float(max(bill_balance(b), Decimal('0'))):.2f}"
    else:
        payload = f"CITYLAND9|UNIT:{b.unit.unit_no}|BILL:{b.billing_month}|AMOUNT:{float(max(bill_balance(b), Decimal('0'))):.2f}"
    img = qrcode.make(payload)
    buf = BytesIO(); img.save(buf, format="PNG"); buf.seek(0)
    return send_file(buf, mimetype="image/png", download_name=f"bill_{b.unit.unit_no}_{b.billing_month}_qr.png")


# -----------------------------
# Water
# -----------------------------
@app.route("/water/previous", methods=["GET"])
@guarded
def water_previous():
    """Return the latest previous reading for a unit before the selected month."""
    try:
        unit_id = int(request.args.get("unit_id"))
    except (TypeError, ValueError):
        return jsonify({"previous_reading": 0, "found": False})
    month = request.args.get("month") or datetime.now().strftime("%Y-%m")
    r = WaterReading.query.filter(
        WaterReading.unit_id == unit_id,
        WaterReading.reading_month < month,
    ).order_by(WaterReading.reading_month.desc(), WaterReading.id.desc()).first()
    if r:
        return jsonify({"previous_reading": float(r.current_reading or 0), "found": True,
                        "source_month": r.reading_month})
    return jsonify({"previous_reading": 0, "found": False})

# Water Readings moved to the React app (/app/<workspace>/water-readings, API /api/water,
# backend/app/routes/water.py). The rules are below as functions (save_water_reading,
# record_water_payment). The old URLs only redirect; a form posted from an old tab changes nothing.
def _water_moved(month=None):
    if request.method == "POST":
        app.logger.info("Classic water form posted from an old page by %s; nothing changed", session.get("username"))
        return redirect(react_url("/water-readings", month=month, moved="form"), code=303)
    return redirect(react_url("/water-readings", month=month))


@app.route("/water", methods=["GET", "POST"])
@guarded
def water():
    return _water_moved(request.values.get("month"))


@app.route("/water/<int:rid>/paid", methods=["POST"])
@guarded
def mark_water_paid(rid):
    return _water_moved()


@app.route("/water/<int:rid>/edit", methods=["GET", "POST"])
@guarded
def edit_water(rid):
    r = db.session.get(WaterReading, rid)
    return _water_moved(r.reading_month if r else None)


def previous_water_reading(unit_id, month):
    """The unit's latest reading before `month` (its current reading is this month's previous)."""
    return WaterReading.query.filter(WaterReading.unit_id == unit_id, WaterReading.reading_month < month) \
        .order_by(WaterReading.reading_month.desc(), WaterReading.id.desc()).first()


def save_water_reading(*, unit_id, month, previous, current, rate, reading_date):
    """Create or correct the unit's reading for `month` (one per unit and month; the classic rules).
    previous/current/rate: Decimals already validated (rate None = Rates & Rules water rate).
    An already-generated bill for that month gets the new water amount, so it is refused when the
    books are closed for that month. Returns (reading, created, bill_updated). Commits."""
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month or ""):
        raise InputError("month", "Choose the reading month.")
    unit = db.session.get(Unit, unit_id) if unit_id else None
    if not unit or not unit.active:
        raise InputError("unitId", "Choose an active unit.")
    if current < previous:
        raise InputError("current", "The current reading can't be lower than the previous reading.")
    existing_bill = Billing.query.filter_by(unit_id=unit.id, billing_month=month).first()
    if existing_bill:
        check_open_period(month, "Water readings")
    reading = WaterReading.query.filter_by(unit_id=unit.id, reading_month=month).first()
    created = reading is None
    before = None if created else {"previous": reading.previous_reading, "current": reading.current_reading, "rate": reading.rate}
    if created:
        reading = WaterReading(unit_id=unit.id, reading_month=month)
        db.session.add(reading)
    reading.previous_reading = float(previous)
    reading.current_reading = float(current)
    reading.rate = float(rate if rate is not None else Decimal(str(setting_float("water_rate", 50))))
    reading.reading_date = reading_date or date.today()
    if existing_bill:
        existing_bill.water = money(reading.bill_amount)
        reset_financial_caches()
        existing_bill.status = bill_status(existing_bill)
    after = {"previous": reading.previous_reading, "current": reading.current_reading, "rate": reading.rate}
    db.session.flush()
    audit(f"{'Saved' if created else 'Edited'} water reading for unit {unit.unit_no} for {month}", entity_type="water_reading",
          entity_id=reading.id, details={"before": before, "after": after, "bill_updated": bool(existing_bill)}, commit=False)
    db.session.commit()
    return reading, created, bool(existing_bill)


def record_water_payment(rid, *, amount, payment_method, payment_type, reference, paid_date, form_token):
    """Record a water payment and issue its official receipt (the classic rules): at most the
    remaining balance is applied; one payment per form token; refused in a closed period; the
    reading row is locked until commit. Returns None (not found) or (reading, receipt, applied). Commits."""
    reading = lock_row(WaterReading, rid)
    if not reading:
        return None
    total = money(reading.bill_amount)
    current_paid = money(getattr(reading, "paid_amount", 0))
    balance = max(total - current_paid, Decimal("0"))
    payment_method = (payment_method or "CASH").upper()
    payment_type = (payment_type or "FULL").upper()
    reference = (reference or "").strip()
    paid_date = paid_date or date.today()
    if payment_method not in ("CASH", "CHECK", "ONLINE"):
        raise InputError("method", "Choose Cash, Check or Online.")
    if payment_type not in ("FULL", "PARTIAL"):
        raise InputError("type", "Choose a full or partial payment.")
    if amount <= 0:
        raise InputError("amount", "Please enter a payment amount.")
    if balance <= 0:
        raise InputError("amount", "This water reading has no remaining balance.")
    amount = min(amount, balance)
    if payment_method in ("CHECK", "ONLINE") and not reference:
        raise InputError("reference", "Reference number is required for Check or Online payments.")
    check_open_period(paid_date, "Payments")
    claim_form_token("water_payment", form_token)
    reading.paid_amount = current_paid + amount
    reading.payment_method = payment_method
    reading.payment_type = payment_type
    reading.payment_reference = reference
    reading.paid_date = paid_date
    reading.paid = money(reading.paid_amount) >= total
    receipt = issue_receipt(reading.unit_id, paid_date, payment_method, reference, "", [("water", reading, amount)])
    audit(f"Issued {receipt.receipt_no} - Recorded {payment_type.lower()} {payment_method.lower()} water payment for unit {reading.unit.unit_no} for {reading.reading_month}",
          entity_type="receipt", entity_id=receipt.id, commit=False,
          details={"water_reading_id": reading.id, "amount": amount, "paid_amount": {"before": current_paid, "after": money(reading.paid_amount)}})
    db.session.commit()
    return reading, receipt, money(amount)



# -----------------------------
# Employees
# -----------------------------
# HR moved to the React app (Manager: /app/hr/..., Staff: Daily Attendance Entry). APIs:
# backend/app/routes/hr.py, same permission keys. Old URLs only redirect; a form posted from an old
# tab changes nothing.
def _hr_moved(path, staff_path=None, **params):
    u = current_user()
    return _moved(staff_path if staff_path and u and u.role == "staff" else path, **params)


@app.route("/employees", methods=["GET", "POST"])
@guarded
def employees():
    return _hr_moved("/employees")


@app.route("/employee/<int:eid>/delete", methods=["POST"])
@guarded
def delete_employee(eid):
    return _hr_moved("/employees")



# -----------------------------
# Expenses / Gate Pass
# -----------------------------
# Front-desk screens moved to the React app: Expense Logs (/expenses), Move In/Out Certificates
# (/certificates), Gate Passes (/gate-passes). APIs: backend/app/routes/operations.py. Same
# permission keys. The old URLs only redirect; a form posted from an old tab changes nothing.
def _moved(path, **params):
    if request.method == "POST":
        app.logger.info("Classic form %s posted from an old page by %s; nothing changed", request.path, session.get("username"))
        return redirect(react_url(path, moved="form"), code=303)
    return redirect(react_url(path, **params))


@app.route("/expenses", methods=["GET", "POST"])
@guarded
def expenses():
    return _moved("/expenses")


@app.route("/move-certificate", methods=["GET", "POST"])
@guarded
def move_certificate():
    return _moved("/certificates", certificate=request.args.get("certificate_id"))


@app.route("/move-certificates")
@guarded
def move_certificates():
    return _moved("/certificates")


@app.route("/gate-pass", methods=["GET", "POST"])
@guarded
def gate_pass():
    return _moved("/gate-passes")


@app.route("/gate-pass/<int:pid>/review", methods=["POST"])
@guarded
def gate_pass_review(pid):
    return _moved("/gate-passes")



# -----------------------------
# Excel migration
# -----------------------------
def _excel_rows():
    return {
        "Units": Unit.query.order_by(Unit.id).all(),
        "Owners": Owner.query.order_by(Owner.id).all(),
        "Tenants": Tenant.query.order_by(Tenant.id).all(),
        "Billing": Billing.query.order_by(Billing.id).all(),
        "Payments": Payment.query.order_by(Payment.id).all(),
        "WaterReadings": WaterReading.query.order_by(WaterReading.id).all(),
        "Employees": Employee.query.order_by(Employee.id).all(),
        "Expenses": Expense.query.order_by(Expense.id).all(),
    }


@app.route("/database/export.xlsx")
@guarded
def database_export():
    if Workbook is None: return "Install openpyxl first.", 500
    wb=Workbook(); wb.remove(wb.active)
    data=_excel_rows()
    fields={
      "Units":["id","unit_no","floor","unit_type","area_sqm","unit_rate_per_sqm","dues_mode","manual_monthly_dues","include_parking","include_storage","assigned_parking_unit_id","assigned_storage_unit_id","occupancy_type","owner_name","contact_no","email","status","active"],
      "Owners":["id","unit_id","owner_name","contact_no","email","move_in","move_out","status","notes"],
      "Tenants":["id","unit_id","tenant_name","contact_no","email","move_in","move_out","status","representative","notes"],
      "Billing":["id","unit_id","billing_month","assessment","parking_dues","storage_dues","water","other","penalty","adjustment","previous_balance","amount_paid","due_date","status","paid_date"],
      "Payments":["id","billing_id","amount","payment_date","reference","remarks"],
      "WaterReadings":["id","unit_id","reading_month","previous_reading","current_reading","rate","reading_date","paid","paid_date"],
      "Employees":["id","employee_no","full_name","position","department","employment_status","contact_no","email","date_hired","monthly_salary","notes"],
      "Expenses":["id","expense_date","category","description","amount"],
    }
    for sheet, rows in data.items():
        ws=wb.create_sheet(sheet); fs=fields[sheet]; ws.append(fs)
        for obj in rows:
            ws.append([getattr(obj,f,None) for f in fs])
        ws.freeze_panes="A2"
    buf=BytesIO(); wb.save(buf); buf.seek(0)
    return send_file(buf, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", as_attachment=True, download_name="cityland9_database_export.xlsx")


# Settings (database export / import) and Rates & Rules moved to the React app:
# /app/superadmin/settings (API /api/admin/system, backend/app/routes/system_settings.py) and
# /app/superadmin/rates-rules (API /api/admin/rates). The Excel export download below is unchanged.
SETTINGS_APP_URL = "/app/superadmin/settings"
RATES_APP_URL = "/app/superadmin/rates-rules"


@app.route("/database/import", methods=["POST"])
@guarded
def database_import():
    """The classic import form (retired with the classic /settings screen). A form posted from an old
    open tab imports nothing and says so; imports run from Settings (run_excel_import below)."""
    app.logger.info("Classic Excel import form posted from an old page by %s; nothing imported", session.get("username"))
    return redirect(f"{SETTINGS_APP_URL}?moved=form", code=303)


def run_excel_import(f):
    """Fast, safer Excel importer. Returns (ok, message, counts); on failure nothing is committed.

    The previous importer performed a database lookup for nearly every Excel row.
    With hundreds/thousands of rows that created thousands of SELECT statements.
    This version reads the workbook in streaming mode, preloads existing records
    once per table, updates/inserts in memory, then commits one transaction.
    A database backup is taken first; if it can't be taken, nothing is imported.
    """
    if load_workbook is None:
        return False, "The Excel library (openpyxl) is not installed on the server.", None

    if not f or not (f.filename or "").lower().endswith((".xlsx", ".xlsm")):
        return False, "Please select an Excel .xlsx file.", None
    # An .xlsx file is a zip archive: check it before opening (unpacked size, number of parts).
    import zipfile
    try:
        with zipfile.ZipFile(f.stream) as archive:
            parts = archive.infolist()
            unpacked = sum(p.file_size for p in parts)
    except zipfile.BadZipFile:
        return False, "That file is not a valid Excel .xlsx workbook.", None
    if len(parts) > 2000 or unpacked > IMPORT_MAX_UNCOMPRESSED_MB * 1024 * 1024:
        return False, (f"That workbook is too large to import (over {IMPORT_MAX_UNCOMPRESSED_MB} MB when unpacked). "
                       "Split it or remove unused sheets."), None
    f.stream.seek(0)

    def as_bool(v, default=False):
        if v in (None, ""):
            return default
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "y", "on", "paid")

    def normalize_month(v):
        if v in (None, ""):
            return None
        if hasattr(v, "strftime"):
            return v.strftime("%Y-%m")
        text = str(v).strip()
        if len(text) >= 7 and text[4] == "-":
            return text[:7]
        for fmt in ("%m/%d/%Y", "%Y/%m/%d", "%B %Y", "%b %Y"):
            try:
                return datetime.strptime(text, fmt).strftime("%Y-%m")
            except ValueError:
                pass
        return text[:7]

    def rows(sheet):
        if sheet not in wb.sheetnames:
            return
        ws = wb[sheet]
        iterator = ws.iter_rows(values_only=True)
        try:
            header_row = next(iterator)
        except StopIteration:
            return
        headers = [str(x).strip() if x is not None else "" for x in header_row]
        if len(headers) > IMPORT_MAX_COLUMNS:
            raise InputError("excel_file", f"Sheet {sheet} has more than {IMPORT_MAX_COLUMNS} columns.")
        for count, values in enumerate(iterator, start=1):
            if count > IMPORT_MAX_ROWS:
                raise InputError("excel_file", f"Sheet {sheet} has more than {IMPORT_MAX_ROWS:,} rows. "
                                               "Import it in parts (IMPORT_MAX_ROWS in .env sets the limit).")
            if any(x not in (None, "") for x in values):
                yield dict(zip(headers, values))

    def val(r, *names, default=None):
        for n in names:
            if n in r and r[n] not in (None, ""):
                return r[n]
        return default

    def int_id(v):
        if v in (None, ""):
            return None
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    def set_if_present(obj, field, value):
        if value is not None:
            setattr(obj, field, value)

    def load_maps(model, natural_key=None):
        existing = model.query.all()
        by_id = {x.id: x for x in existing if getattr(x, "id", None) is not None}
        by_key = {}
        if natural_key:
            for x in existing:
                key = natural_key(x)
                if key not in (None, ""):
                    by_key[key] = x
        return by_id, by_key

    def backup_sqlite_database():
        """Create a consistent backup before a destructive import/update."""
        uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
        if uri.startswith("mysql"):
            # Local MySQL: mysqldump to database/backups/. No backup -> no import (issue I6).
            try:
                sys.path.insert(0, os.path.join(BASE_DIR, "database"))
                import local_mysql
                return local_mysql.backup(label="before_import")
            except BaseException as exc:
                raise RuntimeError(f"the automatic database backup failed ({exc}), so nothing was imported") from None
        if not uri.startswith("sqlite:///") or uri.startswith("sqlite:///:memory:"):
            return None
        db_file = uri[len("sqlite:///"):]
        if not os.path.isabs(db_file):
            db_file = os.path.abspath(db_file)
        if not os.path.exists(db_file):
            return None
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = os.path.join(BASE_DIR, f"cityland_condo_web_before_import_{stamp}.db")
        source = sqlite3.connect(db_file)
        target = sqlite3.connect(backup_file)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        return backup_file

    wb = None
    backup_file = None
    counts = {}
    try:
        # Save a consistent database snapshot before changing any records.
        db.session.commit()
        backup_file = backup_sqlite_database()

        # read_only=True avoids loading the entire workbook into RAM. data_only=True
        # returns calculated Excel values rather than formulas.
        wb = load_workbook(f, read_only=True, data_only=True)

        # Preload each table once. This replaces thousands of row-by-row SELECTs.
        unit_by_id, unit_by_no = load_maps(Unit, lambda x: str(x.unit_no or "").strip())
        owner_by_id, _ = load_maps(Owner)
        tenant_by_id, _ = load_maps(Tenant)
        billing_by_id, _ = load_maps(Billing)
        payment_by_id, _ = load_maps(Payment)
        water_by_id, _ = load_maps(WaterReading)
        employee_by_id, employee_by_no = load_maps(Employee, lambda x: str(x.employee_no or "").strip())
        expense_by_id, _ = load_maps(Expense)

        unit_id_map = {}
        billing_id_map = {}
        units_sheet_owners = []
        counts = {"Units": {"inserted": 0, "updated": 0},
                  "Owners": {"inserted": 0, "updated": 0},
                  "Tenants": {"inserted": 0, "updated": 0},
                  "WaterReadings": {"inserted": 0, "updated": 0},
                  "Billing": {"inserted": 0, "updated": 0},
                  "Payments": {"inserted": 0, "updated": 0},
                  "Employees": {"inserted": 0, "updated": 0},
                  "Expenses": {"inserted": 0, "updated": 0}}

        # ---------------------------------------------------------
        # Units first: child tables need the actual Unit primary keys.
        # ---------------------------------------------------------
        for r in rows("Units"):
            old_id = int_id(val(r, "id", "UnitID"))
            unit_no = str(val(r, "unit_no", "UnitNumber", "Unit No", default="")).strip()
            if not unit_no:
                continue
            obj = unit_by_id.get(old_id) if old_id is not None else None
            if obj is None:
                obj = unit_by_no.get(unit_no)
            if obj is None:
                obj = Unit(unit_no=unit_no)
                if old_id is not None and old_id not in unit_by_id:
                    obj.id = old_id
                db.session.add(obj)
                counts["Units"]["inserted"] += 1
            else:
                counts["Units"]["updated"] += 1
            obj.unit_no = unit_no
            obj.floor = str(val(r, "floor", "Floor", "FloorID", default=obj.floor or ""))
            obj.unit_type = str(val(r, "unit_type", "Type", "RoomType", "RoomTypeID", default=obj.unit_type or ""))
            obj.area_sqm = float(val(r, "area_sqm", "AreaSQM", "Area", default=obj.area_sqm or 0) or 0)
            obj.unit_rate_per_sqm = float(val(r, "unit_rate_per_sqm", "RatePerSQM", default=obj.unit_rate_per_sqm or 0) or 0)
            obj.dues_mode = str(val(r, "dues_mode", "DuesMode", default=obj.dues_mode or "per_sqm"))
            obj.manual_monthly_dues = money(val(r, "manual_monthly_dues", "ManualMonthlyDues", "MonthlyRate", default=0))
            obj.include_parking = as_bool(val(r, "include_parking", "WithParking"), getattr(obj, "include_parking", False))
            obj.include_storage = as_bool(val(r, "include_storage", "WithStorage"), getattr(obj, "include_storage", False))
            obj.occupancy_type = str(val(r, "occupancy_type", "OccupancyType", "ResponsibleParty", default=obj.occupancy_type or "Owner"))
            sheet_owner = str(val(r, "owner_name", "OwnerName", default="") or "").strip()
            if sheet_owner:   # S4: kept until the Owners sheet is read (see below)
                units_sheet_owners.append((obj, sheet_owner, val(r, "contact_no", "OwnerContact", "ContactNo", default=None),
                                           val(r, "email", "OwnerEmail", default=None)))
            obj.status = str(val(r, "status", "UnitStatus", default=obj.status or "Vacant"))
            obj.active = as_bool(val(r, "active", "IsActive"), True)
            unit_id_map[old_id] = obj
            if old_id is not None:
                unit_by_id[old_id] = obj
            unit_by_no[unit_no] = obj

        # Flush once so newly inserted Unit IDs are available before children.
        db.session.flush()

        # Resolve self-referencing parking/storage assignments after all Units exist.
        for r in rows("Units"):
            old_id = int_id(val(r, "id", "UnitID"))
            obj = unit_id_map.get(old_id) or unit_by_id.get(old_id)
            if obj is None:
                unit_no = str(val(r, "unit_no", "UnitNumber", "Unit No", default="")).strip()
                obj = unit_by_no.get(unit_no)
            if obj is None:
                continue
            p_old = int_id(val(r, "assigned_parking_unit_id", "AssignedParkingUnitID"))
            s_old = int_id(val(r, "assigned_storage_unit_id", "AssignedStorageUnitID"))
            p_obj = unit_id_map.get(p_old) or unit_by_id.get(p_old)
            s_obj = unit_id_map.get(s_old) or unit_by_id.get(s_old)
            obj.assigned_parking_unit_id = p_obj.id if p_obj else None
            obj.assigned_storage_unit_id = s_obj.id if s_obj else None

        # ---------------------------------------------------------
        # Owners / Tenants
        # ---------------------------------------------------------
        for r in rows("Owners"):
            old_id = int_id(val(r, "id", "OwnerID"))
            old_unit_id = int_id(val(r, "unit_id", "UnitID"))
            unit_obj = unit_id_map.get(old_unit_id) or unit_by_id.get(old_unit_id)
            if unit_obj is None:
                continue
            obj = owner_by_id.get(old_id) if old_id is not None else None
            name = str(val(r, "owner_name", "OwnerName", default="Owner"))
            if obj is None:
                obj = Owner(unit_id=unit_obj.id, owner_name=name)
                if old_id is not None and old_id not in owner_by_id:
                    obj.id = old_id
                db.session.add(obj)
                if old_id is not None:
                    owner_by_id[old_id] = obj
                counts["Owners"]["inserted"] += 1
            else:
                counts["Owners"]["updated"] += 1
            obj.unit_id = unit_obj.id
            obj.owner_name = name
            obj.contact_no = val(r, "contact_no", "ContactNo", default=obj.contact_no)
            obj.email = val(r, "email", "Email", default=obj.email)
            obj.move_in = parse_excel_date(val(r, "move_in", "MoveIn", default=obj.move_in))
            obj.move_out = parse_excel_date(val(r, "move_out", "MoveOut", default=obj.move_out))
            obj.status = val(r, "status", "Status", default=obj.status)
            obj.notes = val(r, "notes", "Notes", default=obj.notes)

        # S4: an owner given only on the Units sheet (no Owners row) becomes a Current owner record.
        db.session.flush()
        for unit_obj, name, contact, email in units_sheet_owners:
            if not Owner.query.filter_by(unit_id=unit_obj.id).first():
                db.session.add(Owner(unit_id=unit_obj.id, owner_name=name, contact_no=contact, email=email, status="Current"))
                counts["Owners"]["inserted"] += 1

        for r in rows("Tenants"):
            old_id = int_id(val(r, "id", "TenantID"))
            old_unit_id = int_id(val(r, "unit_id", "UnitID"))
            unit_obj = unit_id_map.get(old_unit_id) or unit_by_id.get(old_unit_id)
            if unit_obj is None:
                continue
            obj = tenant_by_id.get(old_id) if old_id is not None else None
            name = str(val(r, "tenant_name", "TenantName", default="Tenant"))
            if obj is None:
                obj = Tenant(unit_id=unit_obj.id, tenant_name=name)
                if old_id is not None and old_id not in tenant_by_id:
                    obj.id = old_id
                db.session.add(obj)
                if old_id is not None:
                    tenant_by_id[old_id] = obj
                counts["Tenants"]["inserted"] += 1
            else:
                counts["Tenants"]["updated"] += 1
            obj.unit_id = unit_obj.id
            obj.tenant_name = name
            obj.contact_no = val(r, "contact_no", "ContactNo", default=obj.contact_no)
            obj.email = val(r, "email", "Email", default=obj.email)
            obj.move_in = parse_excel_date(val(r, "move_in", "MoveIn", default=obj.move_in))
            obj.move_out = parse_excel_date(val(r, "move_out", "MoveOut", default=obj.move_out))
            obj.status = val(r, "status", "Status", default=obj.status)
            obj.representative = as_bool(val(r, "representative", "Representative", "is_representative"), getattr(obj, "representative", False))
            obj.notes = val(r, "notes", "Notes", default=obj.notes)

        # ---------------------------------------------------------
        # Water readings before Billing so current water values can be synced.
        # ---------------------------------------------------------
        for r in rows("WaterReadings"):
            old_id = int_id(val(r, "id", "ReadingID"))
            old_unit_id = int_id(val(r, "unit_id", "UnitID"))
            unit_obj = unit_id_map.get(old_unit_id) or unit_by_id.get(old_unit_id)
            month = normalize_month(val(r, "reading_month", "ReadingMonth", "Month"))
            if unit_obj is None or month is None:
                continue
            obj = water_by_id.get(old_id) if old_id is not None else None
            if obj is None:
                obj = WaterReading(unit_id=unit_obj.id, reading_month=month)
                if old_id is not None and old_id not in water_by_id:
                    obj.id = old_id
                db.session.add(obj)
                if old_id is not None:
                    water_by_id[old_id] = obj
                counts["WaterReadings"]["inserted"] += 1
            else:
                counts["WaterReadings"]["updated"] += 1
            obj.unit_id = unit_obj.id
            obj.reading_month = month
            obj.previous_reading = float(val(r, "previous_reading", "PreviousReading", default=0) or 0)
            obj.current_reading = float(val(r, "current_reading", "CurrentReading", default=0) or 0)
            obj.rate = float(val(r, "rate", "Rate", default=setting_float("water_rate", 50)) or 0)
            obj.reading_date = parse_excel_date(val(r, "reading_date", "ReadingDate", default=date.today())) or date.today()
            obj.paid = as_bool(val(r, "paid", "Paid", default=False), False)
            if hasattr(obj, "paid_amount"):
                obj.paid_amount = money(val(r, "paid_amount", "PaidAmount", default=getattr(obj, "paid_amount", 0)))
            if hasattr(obj, "payment_method"):
                obj.payment_method = val(r, "payment_method", "PaymentMethod", default=getattr(obj, "payment_method", None))
            if hasattr(obj, "payment_type"):
                obj.payment_type = val(r, "payment_type", "PaymentType", default=getattr(obj, "payment_type", None))
            if hasattr(obj, "payment_reference"):
                obj.payment_reference = val(r, "payment_reference", "PaymentReference", "Reference", default=getattr(obj, "payment_reference", None))
            obj.paid_date = parse_excel_date(val(r, "paid_date", "PaidDate", default=obj.paid_date))

        db.session.flush()

        # ---------------------------------------------------------
        # Billing
        # ---------------------------------------------------------
        for r in rows("Billing"):
            old_id = int_id(val(r, "id", "BillingID"))
            old_unit_id = int_id(val(r, "unit_id", "UnitID"))
            unit_obj = unit_id_map.get(old_unit_id) or unit_by_id.get(old_unit_id)
            month = normalize_month(val(r, "billing_month", "BillingMonth", "Month"))
            if unit_obj is None or month is None:
                continue
            obj = billing_by_id.get(old_id) if old_id is not None else None
            if obj is None:
                obj = Billing(unit_id=unit_obj.id, billing_month=month)
                if old_id is not None and old_id not in billing_by_id:
                    obj.id = old_id
                db.session.add(obj)
                if old_id is not None:
                    billing_by_id[old_id] = obj
                counts["Billing"]["inserted"] += 1
            else:
                counts["Billing"]["updated"] += 1
            obj.unit_id = unit_obj.id
            obj.billing_month = month
            for field, names in {
                "assessment": ("assessment", "CondoDue", "CondoDues"),
                "parking_dues": ("parking_dues", "ParkingDues"),
                "storage_dues": ("storage_dues", "StorageDues"),
                "water": ("water", "WaterBill"),
                "other": ("other", "Other"),
                "penalty": ("penalty", "Penalty"),
                "adjustment": ("adjustment", "Adjustment"),
                "previous_balance": ("previous_balance", "PreviousBalance", "PrevBalance"),
                "amount_paid": ("amount_paid", "AmountPaid", "Paid"),
                "status": ("status", "Status"),
            }.items():
                v = val(r, *names)
                if v is not None:
                    setattr(obj, field, money(v) if field not in ("status",) else v)
            obj.due_date = parse_excel_date(val(r, "due_date", "DueDate", default=obj.due_date))
            obj.paid_date = parse_excel_date(val(r, "paid_date", "PaidDate", default=obj.paid_date))
            billing_id_map[old_id] = obj if old_id is not None else obj

        db.session.flush()

        # ---------------------------------------------------------
        # Payments
        # ---------------------------------------------------------
        for r in rows("Payments"):
            old_id = int_id(val(r, "id", "PaymentID"))
            old_billing_id = int_id(val(r, "billing_id", "BillingID"))
            billing_obj = billing_id_map.get(old_billing_id) or billing_by_id.get(old_billing_id)
            if billing_obj is None:
                continue
            obj = payment_by_id.get(old_id) if old_id is not None else None
            if obj is None:
                obj = Payment(billing_id=billing_obj.id, amount=money(val(r, "amount", "Amount", default=0)))
                if old_id is not None and old_id not in payment_by_id:
                    obj.id = old_id
                db.session.add(obj)
                if old_id is not None:
                    payment_by_id[old_id] = obj
                counts["Payments"]["inserted"] += 1
            else:
                counts["Payments"]["updated"] += 1
            obj.billing_id = billing_obj.id
            obj.amount = money(val(r, "amount", "Amount", default=obj.amount))
            obj.payment_date = parse_excel_date(val(r, "payment_date", "PaymentDate", default=obj.payment_date)) or date.today()
            if hasattr(obj, "payment_method"):
                obj.payment_method = val(r, "payment_method", "PaymentMethod", default=getattr(obj, "payment_method", "CASH"))
            if hasattr(obj, "payment_type"):
                obj.payment_type = val(r, "payment_type", "PaymentType", default=getattr(obj, "payment_type", "FULL"))
            obj.reference = val(r, "reference", "Reference", default=obj.reference)
            obj.remarks = val(r, "remarks", "Remarks", default=obj.remarks)

        # ---------------------------------------------------------
        # Employees / Expenses
        # ---------------------------------------------------------
        for r in rows("Employees"):
            old_id = int_id(val(r, "id", "EmployeeID"))
            eno = str(val(r, "employee_no", "EmployeeNo", default="")).strip()
            name = str(val(r, "full_name", "FullName", "EmployeeName", default="")).strip()
            if not eno or not name:
                continue
            obj = employee_by_id.get(old_id) if old_id is not None else employee_by_no.get(eno)
            if obj is None:
                obj = Employee(employee_no=eno, full_name=name)
                if old_id is not None and old_id not in employee_by_id:
                    obj.id = old_id
                db.session.add(obj)
                if old_id is not None:
                    employee_by_id[old_id] = obj
                employee_by_no[eno] = obj
                counts["Employees"]["inserted"] += 1
            else:
                counts["Employees"]["updated"] += 1
            obj.employee_no = eno
            obj.full_name = name
            obj.position = val(r, "position", "Position", default=obj.position)
            obj.department = val(r, "department", "Department", default=obj.department)
            obj.employment_status = val(r, "employment_status", "Status", default=obj.employment_status)
            obj.contact_no = val(r, "contact_no", "ContactNo", default=obj.contact_no)
            obj.email = val(r, "email", "Email", default=obj.email)
            obj.date_hired = parse_excel_date(val(r, "date_hired", "DateHired", default=obj.date_hired))
            obj.monthly_salary = money(val(r, "monthly_salary", "MonthlySalary", "Salary", default=obj.monthly_salary))
            obj.notes = val(r, "notes", "Notes", default=obj.notes)

        for r in rows("Expenses"):
            old_id = int_id(val(r, "id", "ExpenseID"))
            obj = expense_by_id.get(old_id) if old_id is not None else None
            if obj is None:
                obj = Expense()
                if old_id is not None and old_id not in expense_by_id:
                    obj.id = old_id
                db.session.add(obj)
                if old_id is not None:
                    expense_by_id[old_id] = obj
                counts["Expenses"]["inserted"] += 1
            else:
                counts["Expenses"]["updated"] += 1
            obj.expense_date = parse_excel_date(val(r, "expense_date", "ExpenseDate", "Date", default=date.today())) or date.today()
            obj.category = str(val(r, "category", "Category", default="OTHERS"))
            obj.description = str(val(r, "description", "Description", default=""))
            obj.amount = money(val(r, "amount", "Amount", default=0))

        # One final transaction for the whole workbook.
        db.session.commit()

        imported = ", ".join(
            f"{sheet}: {v['inserted']} new / {v['updated']} updated"
            for sheet, v in counts.items()
            if sheet in wb.sheetnames
        )
        audit(f"Imported database from Excel (optimized): {f.filename}")
        backup_note = f" Backup created: {os.path.basename(backup_file)}." if backup_file else ""
        found = {sheet: v for sheet, v in counts.items() if sheet in wb.sheetnames}
        return True, f"Excel import completed.{backup_note} {imported}".strip(), found
    except Exception as ex:
        db.session.rollback()
        app.logger.warning("Excel import failed: %s", ex)
        return False, f"Excel import failed. No changes were committed: {ex}", None
    finally:
        if wb is not None:
            try:
                wb.close()
            except Exception:
                pass


# -----------------------------
# Reports
# -----------------------------
# Reports moved to the React app: Property Reports (/reports; Accounting: Financial Reports,
# /financial-reports). API: backend/app/routes/reports.py, same permission keys. The old page's
# totals counted previous balances again (R1-R3 in docs/module-migration-checklist.md).
def _reports_moved():
    u = current_user()
    return _moved("/financial-reports" if u and u.role == ACCOUNTING else "/reports",
                  period=request.args.get("period"), **{"from": request.args.get("start_date"), "to": request.args.get("end_date")})


@app.route("/reports")
@guarded
def reports():
    return _reports_moved()


@app.route("/reports/export.xlsx")
@guarded
def reports_export():
    params = {k: v for k, v in (("from", request.args.get("start_date")), ("to", request.args.get("end_date"))) if v}
    return redirect("/api/reports/export.xlsx?" + urlencode({"period": "custom", **params}))


# -----------------------------
# Settings / Rates & Rules
# -----------------------------
# Rates & Rules numbers: (key, label, decimal places, minimum, maximum).
SETTINGS_NUMBERS = [
    ("water_rate", "Water rate", 4, "0", "100000"),
    ("studio_rate_per_sqm", "Studio rate per sqm", 4, "0", "100000"),
    ("one_bed_rate_per_sqm", "1 bedroom rate per sqm", 4, "0", "100000"),
    ("two_bed_rate_per_sqm", "2 bedroom rate per sqm", 4, "0", "100000"),
    ("three_bed_rate_per_sqm", "3 bedroom rate per sqm", 4, "0", "100000"),
    ("parking_rate_per_sqm", "Parking rate per sqm", 4, "0", "100000"),
    ("storage_rate_per_sqm", "Storage rate per sqm", 4, "0", "100000"),
    ("penalty_rate", "Penalty rate (%)", 4, "0", "100"),
    ("due_day", "Due day", 0, "1", "31"),
]
# (D8: "penalty_day" and "penalty_rate_per_sqm" were never used by billing - the penalty follows each
# bill's due date and the penalty rate in % - so they are no longer validated or offered.)


@app.route("/settings", methods=["GET", "POST"])
@guarded
def settings():
    """The classic Rates & Rules screen (retired): GET opens Rates & Rules in the React app; a form
    posted from an old open tab saves nothing and says so. Same permission key (settings)."""
    if request.method == "POST":
        app.logger.info("Classic Rates & Rules form posted from an old page by %s; nothing changed", session.get("username"))
        return redirect(f"{RATES_APP_URL}?moved=form", code=303)
    return redirect(RATES_APP_URL)


# -----------------------------
# Password Management
# -----------------------------
@app.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    u = current_user()
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not check_password_hash(u.password_hash, current_password):
            flash("Current password is incorrect.", "danger")
            return redirect(url_for("change_password"))
        problem = security.password_problem(new_password, u.username)
        if problem:
            flash(problem, "danger")
            return redirect(url_for("change_password"))
        if new_password != confirm_password:
            flash("New password and confirmation do not match.", "danger")
            return redirect(url_for("change_password"))
        if check_password_hash(u.password_hash, new_password):
            flash("New password must be different from the current password.", "danger")
            return redirect(url_for("change_password"))

        # Ends the account's other sessions; this one stays signed in.
        set_password(u, new_password, temporary=False, keep_current_session=True)
        db.session.commit()
        audit(f"Changed password for user {u.username}")
        flash("Your password has been changed successfully.", "success")
        return redirect(url_for("index"))

    return render_template("change_password.html")


# -----------------------------
# Users & Access: moved to the React app (/app/superadmin/users, API /api/admin/users)
# -----------------------------
# The classic screen had its own, weaker copy of the rules (e.g. it could create a Resident login
# without a resident profile). There is now ONE implementation: backend/app/routes/users.py.
# The old URLs stay (bookmarks, stale open tabs) but only redirect; a form posted from an old tab
# changes nothing and says so. The permission keys are unchanged (users / reset_user_password /
# delete_user, Superadmin only), so a signed-out or other-role request is refused as before.
USERS_APP_URL = "/app/superadmin/users"


def _users_moved(form_posted=False):
    if form_posted:
        app.logger.info("Classic Users & Access form posted from an old page by %s; nothing changed",
                        session.get("username"))
        return redirect(f"{USERS_APP_URL}?moved=form", code=303)
    return redirect(USERS_APP_URL)


@app.route("/users/<int:user_id>/reset-password", methods=["POST"])
@guarded
def reset_user_password(user_id):
    return _users_moved(form_posted=True)


@app.route("/users/<int:user_id>/delete", methods=["POST"])
@guarded
def delete_user(user_id):
    return _users_moved(form_posted=True)


@app.route("/users", methods=["GET", "POST"])
@guarded
def users():
    return _users_moved(form_posted=request.method == "POST")


# Audit Logs: moved to the React app (API /api/admin/audit-logs, backend/app/routes/audit_logs.py).
# Same permission key (audit_logs: Superadmin and Accounting); the old URL opens the page in the
# signed-in user's own workspace.
AUDIT_LOGS_APP_URLS = {"super_admin": "/app/superadmin/audit-logs", "accounting": "/app/accounting/audit-logs"}


@app.route("/audit")
@guarded
def audit_logs():
    return redirect(AUDIT_LOGS_APP_URLS.get(current_user().role, APP_URL))


# -----------------------------
# V10.63 Community / Resident Services
# -----------------------------
# Resident Accounts: moved to the React app (/app/superadmin/resident-accounts, API
# /api/admin/resident-accounts, backend/app/routes/resident_accounts.py). Same permission key
# (resident_users, Superadmin only). The old URL only redirects; a form posted from an old open
# tab changes nothing and says so.
RESIDENT_ACCOUNTS_APP_URL = "/app/superadmin/resident-accounts"


@app.route("/resident-users", methods=["GET", "POST"])
@guarded
def resident_users():
    if request.method == "POST":
        app.logger.info("Classic Resident Accounts form posted from an old page by %s; nothing changed",
                        session.get("username"))
        return redirect(f"{RESIDENT_ACCOUNTS_APP_URL}?moved=form", code=303)
    return redirect(RESIDENT_ACCOUNTS_APP_URL)


@app.route("/portal")
@guarded
def resident_portal():
    # Residents use the new portal (/app/resident). Staff keep this unit overview until it is rebuilt.
    if current_user().role == "resident":
        return redirect(APP_URL + "resident")
    profile = getattr(current_user(), "resident_profile", None)
    unit_id = profile.unit_id if profile else request.args.get("unit_id", type=int)
    recent_announcements = Announcement.query.filter_by(published=True).order_by(Announcement.publish_date.desc(), Announcement.id.desc()).limit(10).all()
    tickets_q = MaintenanceTicket.query.order_by(MaintenanceTicket.id.desc())
    if unit_id:
        tickets_q = tickets_q.filter(MaintenanceTicket.unit_id == unit_id)
    return render_template("resident_portal.html", profile=profile, unit_id=unit_id, announcements=recent_announcements, tickets=tickets_q.limit(10).all())

# Community screens moved to the React app: Announcements, Maintenance Tickets, Vendor Directory,
# Documents (APIs: backend/app/routes/community.py). Residents use their portal pages instead
# (/my-maintenance, /announcements with their documents). Same permission keys.
def _community_moved(staff_path, resident_path):
    u = current_user()
    return _moved(resident_path if u and u.role == RESIDENT else staff_path)


@app.route("/announcements", methods=["GET", "POST"])
@guarded
def announcements():
    return _community_moved("/announcements", "/announcements")


@app.route("/maintenance", methods=["GET", "POST"])
@guarded
def maintenance():
    return _community_moved("/maintenance", "/my-maintenance")


@app.route("/maintenance/<int:ticket_id>/update", methods=["POST"])
@guarded
def maintenance_update(ticket_id):
    return _moved("/maintenance")


@app.route("/vendors", methods=["GET", "POST"])
@guarded
def vendors():
    return _moved("/vendors")


@app.route("/documents", methods=["GET", "POST"])
@guarded
def documents():
    return _community_moved("/documents", "/announcements")

# -----------------------------
# Database initialization / migration
# -----------------------------
def schema_managed():
    """True on MariaDB/MySQL, where only versioned migrations change the schema."""
    return db.engine.url.drivername.startswith("mysql")


def ensure_table(model):
    """Create a table on the SQLite development database only. On MariaDB/MySQL tables come from
    migrations (the application account has no CREATE right)."""
    if not schema_managed():
        model.__table__.create(db.engine, checkfirst=True)


def verify_schema_present():
    """Refuse to start with a missing table instead of creating it (migrations do that)."""
    from sqlalchemy import inspect as sa_inspect
    existing = set(sa_inspect(db.engine).get_table_names())
    missing = sorted(set(db.metadata.tables) - existing)
    if missing:
        raise RuntimeError("The database is missing table(s): " + ", ".join(missing) +
                           ". New installation: python database/db_migrate.py install; "
                           "existing one: python database/db_migrate.py upgrade")


def init_db():
    """Startup checks and defaults.

    MariaDB/MySQL (production): the schema is changed ONLY by versioned migrations
    (database/db_migrate.py), run with the migration account. Startup never creates or alters
    tables; it only verifies they exist. SQLite (development/tests): tables and columns are
    created and upgraded here, as before."""
    with app.app_context():
        if schema_managed():
            verify_schema_present()
        else:
            db.create_all()

            # Add performance indexes to databases that already existed before this build.
            for table in (Billing, Payment, AdvanceApplication, WaterReading):
                for index in table.__table__.indexes:
                    try:
                        index.create(bind=db.engine, checkfirst=True)
                    except Exception as exc:
                        print(f"Index {index.name} could not be created: {exc}")

            # Add performance indexes for tables that are queried on nearly every page
            # but were created before explicit index declarations were added.
            with db.engine.begin() as conn:
                dialect = db.engine.dialect.name
                idx_defs = [
                    ("ix_owner_unit_id",        "owner",    "unit_id"),
                    ("ix_tenant_unit_id",        "tenant",   "unit_id"),
                    ("ix_tenant_status",         "tenant",   "status"),
                    ("ix_audit_log_created_at",  "audit_log","created_at"),
                ]
                existing_indexes = set()
                try:
                    inspector = inspect(db.engine)
                    for _, tbl, _ in idx_defs:
                        for idx in inspector.get_indexes(tbl):
                            existing_indexes.add(idx["name"])
                except Exception:
                    pass
                for idx_name, tbl, col in idx_defs:
                    if idx_name not in existing_indexes:
                        try:
                            conn.exec_driver_sql(
                                f'CREATE INDEX IF NOT EXISTS "{idx_name}" ON "{tbl}" ("{col}")'
                            )
                        except Exception as exc:
                            print(f"Index {idx_name} could not be created: {exc}")

            if db.engine.dialect.name == "sqlite":
                # Better read concurrency and much less lock contention for the LAN web app.
                with db.engine.connect() as conn:
                    conn.exec_driver_sql("PRAGMA journal_mode=WAL")
                    conn.exec_driver_sql("PRAGMA synchronous=NORMAL")
                    conn.exec_driver_sql("PRAGMA busy_timeout=30000")

                # SQLite/SQLAlchemy create_all() does not add columns to existing
                # tables. These are the columns introduced by later V4/V10 builds.
                migrations = {
                    "unit": {
                        "area_sqm": "FLOAT DEFAULT 0",
                        "unit_rate_per_sqm": "FLOAT DEFAULT 0",
                        "include_parking": "BOOLEAN DEFAULT 0",
                        "auto_rate": "BOOLEAN DEFAULT 1",
                        "dues_mode": "VARCHAR(20) DEFAULT 'per_sqm'",
                        "manual_monthly_dues": "NUMERIC(12,2) DEFAULT 0",
                        "occupancy_type": "VARCHAR(20) DEFAULT 'Owner'",
                        "include_storage": "BOOLEAN DEFAULT 0",
                        "assigned_parking_unit_id": "INTEGER",
                        "assigned_storage_unit_id": "INTEGER",
                    },
                    "tenant": {
                        "representative": "BOOLEAN DEFAULT 0",
                        "receive_soa_email": "BOOLEAN DEFAULT 0",
                        "include_in_soa": "BOOLEAN DEFAULT 1",
                    },
                    "owner": {
                        "receive_soa_email": "BOOLEAN DEFAULT 0",
                        "include_in_soa": "BOOLEAN DEFAULT 1",
                    },
                    "billing": {
                        "parking_dues": "NUMERIC(12,2) DEFAULT 0",
                        "storage_dues": "NUMERIC(12,2) DEFAULT 0",
                        "paid_date": "DATE",
                        "status": "VARCHAR(30) DEFAULT 'Unpaid'",
                        "soa_note": "VARCHAR(1000) DEFAULT ''",
                        "soa_manual_override": "BOOLEAN DEFAULT 0",
                    },
                    "water_reading": {
                        "paid": "BOOLEAN DEFAULT 0",
                        "paid_date": "DATE",
                    },
                    # Payment integrity (MySQL: migration 0009).
                    "receipt": {"voided_at": "DATETIME", "voided_by": "VARCHAR(80)", "void_reason": "VARCHAR(500)"},
                    "payment": {"reversed_at": "DATETIME"},
                    "advance_payment": {"reversed_at": "DATETIME"},
                    "audit_log": {"entity_type": "VARCHAR(40)", "entity_id": "INTEGER", "reason": "VARCHAR(500)", "details": "TEXT"},
                    # Session security (MySQL: migration 0008).
                    "user": {
                        "session_version": "INTEGER NOT NULL DEFAULT 0",
                        "must_change_password": "BOOLEAN NOT NULL DEFAULT 0",
                        "password_changed_at": "DATETIME",
                    },
                    # Resident gate pass / move requests (MySQL: migration 0007).
                    "gate_pass": {
                        "pass_type": "VARCHAR(20)",
                        "unit_id": "INTEGER REFERENCES unit(id)",
                        "requested_by": "VARCHAR(80)",
                        "requested_at": "DATETIME",
                        "reviewed_by": "VARCHAR(80)",
                        "reviewed_at": "DATETIME",
                        "review_note": "VARCHAR(300)",
                    },
                }
                for tracked_model in ChangeTracked.__subclasses__():
                    migrations.setdefault(tracked_model.__tablename__, {}).update({"updated_at": "DATETIME", "updated_by": "VARCHAR(80)"})
                pending = []
                with db.engine.connect() as conn:
                    for table, columns in migrations.items():
                        existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()}
                        if not existing:
                            continue  # table not created yet (HR tables are created on first use)
                        pending.extend((table, name, definition) for name, definition in columns.items() if name not in existing)

                if pending:
                    db_path = db.engine.url.database
                    if db_path and os.path.isfile(db_path):
                        backup = db_path + ".before_v10_cleanup.bak"
                        if not os.path.exists(backup):
                            shutil.copy2(db_path, backup)
                            print(f"Database backup created: {backup}")
                    with db.engine.begin() as conn:
                        for table, name, definition in pending:
                            conn.exec_driver_sql(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')

            # Cross-database schema upgrades for newer fields. SQLAlchemy create_all() does
            # not add columns to an existing SQL Server/PostgreSQL database.
            generic_migrations = {
                "unit": {
                    "include_storage": "BIT DEFAULT 0" if db.engine.dialect.name == "mssql" else ("BOOLEAN DEFAULT FALSE" if db.engine.dialect.name == "postgresql" else "BOOLEAN DEFAULT 0"),
                    "assigned_parking_unit_id": "INTEGER",
                    "assigned_storage_unit_id": "INTEGER",
                },
                "billing": {
                    "storage_dues": "NUMERIC(12,2) DEFAULT 0",
                    "soa_note": "NVARCHAR(1000) DEFAULT ''" if db.engine.dialect.name == "mssql" else "VARCHAR(1000) DEFAULT ''",
                    "soa_manual_override": "BIT DEFAULT 0" if db.engine.dialect.name == "mssql" else ("BOOLEAN DEFAULT FALSE" if db.engine.dialect.name == "postgresql" else "BOOLEAN DEFAULT 0"),
                },
                "owner": {
                    "receive_soa_email": "BIT DEFAULT 0" if db.engine.dialect.name == "mssql" else ("BOOLEAN DEFAULT FALSE" if db.engine.dialect.name == "postgresql" else "BOOLEAN DEFAULT 0"),
                    "include_in_soa": "BIT DEFAULT 1" if db.engine.dialect.name == "mssql" else ("BOOLEAN DEFAULT TRUE" if db.engine.dialect.name == "postgresql" else "BOOLEAN DEFAULT 1"),
                },
                "tenant": {
                    "receive_soa_email": "BIT DEFAULT 0" if db.engine.dialect.name == "mssql" else ("BOOLEAN DEFAULT FALSE" if db.engine.dialect.name == "postgresql" else "BOOLEAN DEFAULT 0"),
                    "include_in_soa": "BIT DEFAULT 1" if db.engine.dialect.name == "mssql" else ("BOOLEAN DEFAULT TRUE" if db.engine.dialect.name == "postgresql" else "BOOLEAN DEFAULT 1"),
                },
                "water_reading": {
                    "paid": "BIT DEFAULT 0" if db.engine.dialect.name == "mssql" else ("BOOLEAN DEFAULT FALSE" if db.engine.dialect.name == "postgresql" else "BOOLEAN DEFAULT 0"),
                    "paid_amount": "NUMERIC(12,2) DEFAULT 0",
                    "payment_method": "VARCHAR(20) DEFAULT 'CASH'",
                    "payment_type": "VARCHAR(20) DEFAULT 'FULL'",
                    "payment_reference": "VARCHAR(100)",
                    "paid_date": "DATE",
                },
                "payment": {
                    "payment_method": "VARCHAR(20) DEFAULT 'CASH'",
                    "payment_type": "VARCHAR(20) DEFAULT 'FULL'",
                }
            }
            inspector = inspect(db.engine)
            with db.engine.begin() as conn:
                for table, columns in generic_migrations.items():
                    if table not in inspector.get_table_names():
                        continue
                    existing = {c["name"] for c in inspector.get_columns(table)}
                    for name, definition in columns.items():
                        if name not in existing:
                            conn.exec_driver_sql(f'ALTER TABLE "{table}" ADD "{name}" {definition}')

        # Normalize the legacy Studio label to the new Unit Type option.
        for legacy_unit in Unit.query.filter_by(unit_type="Studio").all():
            legacy_unit.unit_type = "STUDIO TYPE"
        db.session.commit()

        # (Removed: startup used to reset EVERY bill's due date to the 8th. Each bill now keeps
        # the due date it was issued with; new bills use the configured due day.)

        defaults = {
            "corporation_name": "CITYLAND 9 CONDOMINIUM CORPORATION",
            "address": "9 Dela Rosa Condominium, Dela Rosa Street, Barangay Pio del Pilar, Makati City",
            "water_rate": "50",
            "water_auto_compute": "1",
            "due_day": "8",
            "studio_rate_per_sqm": "50",
            "one_bed_rate_per_sqm": "75",
            "two_bed_rate_per_sqm": "100",
            "three_bed_rate_per_sqm": "125",
            "parking_rate_per_sqm": "100",
            "storage_rate_per_sqm": "50",
            "storage_in_total_from": month_shift(datetime.now().strftime("%Y-%m"), 1),
            "penalty_rate": "10",
            "penalty_day": "8",
            "online_payment_url": "",
            "smtp_host": "",
            "smtp_port": "587",
            "smtp_sender": "",
            "smtp_username": "",
            "smtp_password": "",
        }
        for key, value in defaults.items():
            if not Setting.query.filter_by(key=key).first():
                db.session.add(Setting(key=key, value=value))

        # No default administrator is created any more (it used to be superadmin / admin123).
        # The first Superadmin is created on the server with:  python database\create_admin.py
        if not User.query.filter_by(role="super_admin", active=True).first():
            print("No active Superadmin account yet. Create one on this PC with:  "
                  r".venv\Scripts\python.exe database\create_admin.py")

        db.session.commit()


# ============================================================
# V10.39 - EMPLOYEE ATTENDANCE & PAYROLL
# ============================================================
from datetime import datetime as _dt_datetime, date as _dt_date, time as _dt_time
from decimal import Decimal as _Decimal

class EmployeeAttendance(db.Model):
    __tablename__ = "employee_attendance"
    __table_args__ = (db.UniqueConstraint("employee_id", "attendance_date", name="uq_employee_attendance_day"),)
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=False, index=True)
    attendance_date = db.Column(db.Date, nullable=False, index=True)
    time_in = db.Column(db.Time, nullable=True)
    time_out = db.Column(db.Time, nullable=True)
    status = db.Column(db.String(30), nullable=False, default="PRESENT")
    remarks = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=_dt_datetime.utcnow)

class EmployeePayroll(ChangeTracked, db.Model):
    __tablename__ = "employee_payroll"
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=False, index=True)
    period_start = db.Column(db.Date, nullable=False, index=True)
    period_end = db.Column(db.Date, nullable=False, index=True)
    basic_salary = db.Column(db.Numeric(12,2), nullable=False, default=0)
    overtime_pay = db.Column(db.Numeric(12,2), nullable=False, default=0)
    allowances = db.Column(db.Numeric(12,2), nullable=False, default=0)
    deductions = db.Column(db.Numeric(12,2), nullable=False, default=0)
    absences = db.Column(db.Numeric(12,2), nullable=False, default=0)
    late_undertime = db.Column(db.Numeric(12,2), nullable=False, default=0)
    net_pay = db.Column(db.Numeric(12,2), nullable=False, default=0)
    status = db.Column(db.String(20), nullable=False, default="DRAFT")
    remarks = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=_dt_datetime.utcnow)

def _v1039_ensure_tables():
    try:
        ensure_table(EmployeeAttendance)
        ensure_table(EmployeePayroll)
    except Exception:
        db.session.rollback()

def _v1039_employee_id_value(emp):
    return getattr(emp, "id", None)

def _v1039_employee_name(emp):
    for a in ("name", "full_name", "employee_name"):
        v = getattr(emp, a, None)
        if v:
            return str(v)
    first = getattr(emp, "first_name", "") or ""
    last = getattr(emp, "last_name", "") or ""
    return (str(first) + " " + str(last)).strip() or ("Employee #" + str(emp.id))

def _v1039_money(v):
    try:
        return float(v or 0)
    except Exception:
        return 0.0

def _v1039_calc_payroll(emp, start, end, basic, overtime, allowances, deductions):
    # Monthly salary is converted using the configurable working-day divisor.
    monthly = _v1039_money(getattr(emp, "monthly_salary", 0))
    basic = _v1039_money(basic) if basic is not None else monthly
    rows = EmployeeAttendance.query.filter(
        EmployeeAttendance.employee_id == emp.id,
        EmployeeAttendance.attendance_date >= start,
        EmployeeAttendance.attendance_date <= end
    ).all()
    working_days = max(_v1041_hr_float("hr_working_days", 26), 1) if "EmployeeHRSetting" in globals() else 26
    hours_per_day = max(_v1041_hr_float("hr_work_hours_per_day", 8), 1) if "EmployeeHRSetting" in globals() else 8
    grace = max(_v1041_hr_float("hr_grace_minutes", 5), 0) if "EmployeeHRSetting" in globals() else 5
    absent_days = sum(1 for r in rows if str(r.status).upper() == "ABSENT")
    absence_deduction = round((monthly / working_days) * absent_days, 2) if monthly else 0.0
    hourly = (monthly / working_days / hours_per_day) if monthly else 0.0
    late_deduction = 0.0
    for r in rows:
        status=str(r.status or "").upper()
        minutes=0
        if r.time_in and status in ("LATE","LATE/UNDERTIME","PRESENT"):
            scheduled=_dt_time(8,0)
            actual=r.time_in.hour*60+r.time_in.minute
            sched=scheduled.hour*60+scheduled.minute
            minutes=max(actual-sched-grace,0)
        if status in ("UNDERTIME","LATE/UNDERTIME") and r.time_out:
            scheduled_out=_dt_time(17,0)
            actual_out=r.time_out.hour*60+r.time_out.minute
            sched_out=scheduled_out.hour*60+scheduled_out.minute
            minutes += max(sched_out-actual_out,0)
        late_deduction += hourly * minutes / 60.0
    late_deduction=round(late_deduction,2)
    ded = _v1039_money(deductions) + absence_deduction + late_deduction
    net = basic + _v1039_money(overtime) + _v1039_money(allowances) - ded
    return round(basic,2), round(absence_deduction,2), round(late_deduction,2), round(net,2)

@app.route("/employees/attendance", methods=["GET","POST"])
@guarded
def employee_attendance():
    return _hr_moved("/attendance", "/attendance-entry", date=request.args.get("date"))

@app.route("/employees/attendance/history/<int:eid>")
@guarded
def employee_attendance_history(eid):
    return _hr_moved("/attendance", employee=eid)


class PayrollExists(Exception):
    """The employee already has a payroll for (part of) this period."""


def generate_payroll(*, employee_id, start, end, basic, overtime, allowances, deductions, status, remarks, form_token):
    """Create one employee's payroll for start..end with its Philippine statutory breakdown (the
    classic rules: attendance absences and late/undertime, approved overtime unless given, active
    loans deducted and their balances reduced). D6: everything is saved together or not at all.
    New: a second payroll overlapping the same employee's period is refused (it paid twice and
    deducted loans twice). basic/overtime: Decimal or None (None = monthly salary / approved overtime).
    Returns the payroll. Raises InputError / PayrollExists. Commits."""
    emp = db.session.get(Employee, employee_id) if employee_id else None
    if not emp:
        raise InputError("employeeId", "Choose the employee.")
    if not start or not end:
        raise InputError("period", "Enter the payroll period.")
    if end < start:
        raise InputError("period", "The period ends before it starts.")
    if (end - start).days > 31:
        raise InputError("period", "A payroll period can't be longer than a month.")
    if status not in ("DRAFT", "FINAL", "PAID"):
        raise InputError("status", "Payroll status must be Draft, Final or Paid.")
    overlap = EmployeePayroll.query.filter(EmployeePayroll.employee_id == emp.id, EmployeePayroll.period_start <= end,
                                           EmployeePayroll.period_end >= start).with_for_update().first()
    if overlap:
        raise PayrollExists(f"{emp.full_name} already has a payroll for {overlap.period_start.isoformat()} to "
                            f"{overlap.period_end.isoformat()}. Open it instead of generating another.")
    claim_form_token("payroll", form_token)
    approved_ot = sum(_v1039_money(x.amount) for x in EmployeeOvertime.query.filter(
        EmployeeOvertime.employee_id == emp.id, EmployeeOvertime.ot_date >= start, EmployeeOvertime.ot_date <= end,
        EmployeeOvertime.status == "APPROVED").all())
    ot = _v1039_money(approved_ot if overtime is None else overtime)
    basic_v, absence_ded, late_ded, net = _v1039_calc_payroll(emp, start, end, None if basic is None else float(basic), ot,
                                                              float(allowances), float(deductions))
    recurring_loans = EmployeeHRLoan.query.filter_by(employee_id=emp.id, status="ACTIVE").all()
    loan_deduction = sum(min(_v1039_money(x.monthly_deduction), _v1039_money(x.balance)) for x in recurring_loans)
    p = EmployeePayroll(employee_id=emp.id, period_start=start, period_end=end, basic_salary=basic_v, overtime_pay=ot,
                        allowances=float(allowances), deductions=float(deductions) + loan_deduction, absences=absence_ded,
                        late_undertime=late_ded, net_pay=net, status=status, remarks=(remarks or None))
    try:
        db.session.add(p)
        db.session.flush()
        gross = _v1039_money(p.basic_salary) + _v1039_money(p.overtime_pay) + _v1039_money(p.allowances)
        sc = _v1040_calc(gross, p.basic_salary, p.deductions, p.absences, p.late_undertime)
        row = EmployeePayrollStatutory(payroll_id=p.id)
        for k, v in sc.items():
            setattr(row, k, _v1039_money(v))
        db.session.add(row)
        p.net_pay = sc["net_pay_after_statutory"]
        remaining = loan_deduction
        for loan in recurring_loans:
            take = min(_v1039_money(loan.monthly_deduction), _v1039_money(loan.balance), remaining)
            if take > 0:
                loan.balance = max(_v1039_money(loan.balance) - take, 0)
                if loan.balance <= 0:
                    loan.status = "PAID"
                remaining -= take
        audit(f"Generated payroll for employee {emp.employee_no}, {start.isoformat()} to {end.isoformat()}", entity_type="payroll",
              entity_id=p.id, details={"gross": gross, "net": p.net_pay, "loan_deduction": loan_deduction, "status": status}, commit=False)
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("Payroll generation failed")
        raise
    return p


@app.route("/employees/payroll", methods=["GET","POST"])
@guarded
def employee_payroll():
    return _hr_moved("/payroll")


def _payroll_status():
    status = (request.form.get("status") or "DRAFT").strip().upper()
    if status not in ("DRAFT", "FINAL", "PAID"):          # the payroll form's choices
        raise InputError("status", "Payroll status must be Draft, Final or Paid.")
    return status


@app.route("/employees/payroll/<int:payroll_id>/print")
@guarded
def employee_payroll_print(payroll_id):
    return _hr_moved("/payroll", payslip=payroll_id)

try:
    with app.app_context():
        _v1039_ensure_tables()
except Exception:
    pass

# V10.40 Philippine statutory payroll
class EmployeePayrollStatutory(db.Model):
    __tablename__="employee_payroll_statutory"
    id=db.Column(db.Integer,primary_key=True)
    payroll_id=db.Column(db.Integer,db.ForeignKey("employee_payroll.id"),unique=True,index=True,nullable=False)
    sss_employee=db.Column(db.Numeric(12,2),default=0); sss_employer=db.Column(db.Numeric(12,2),default=0); sss_ec_employer=db.Column(db.Numeric(12,2),default=0)
    philhealth_employee=db.Column(db.Numeric(12,2),default=0); philhealth_employer=db.Column(db.Numeric(12,2),default=0)
    pagibig_employee=db.Column(db.Numeric(12,2),default=0); pagibig_employer=db.Column(db.Numeric(12,2),default=0)
    withholding_tax=db.Column(db.Numeric(12,2),default=0); thirteenth_month=db.Column(db.Numeric(12,2),default=0)
    gross_pay=db.Column(db.Numeric(12,2),default=0); total_employee_deductions=db.Column(db.Numeric(12,2),default=0)
    total_employer_cost=db.Column(db.Numeric(12,2),default=0); net_pay_after_statutory=db.Column(db.Numeric(12,2),default=0)

def _v1041_hr_setting(key, default):
    try:
        row = EmployeeHRSetting.query.filter_by(key=key).first() if "EmployeeHRSetting" in globals() else None
        return row.value if row and row.value is not None else str(default)
    except Exception:
        return str(default)


def _v1041_hr_float(key, default):
    try:
        return float(_v1041_hr_setting(key, default))
    except Exception:
        return float(default)


def _v1040_calc(gross, basic, other=0, absences=0, late_undertime=0):
    """Configurable Philippine monthly payroll statutory computation.

    The default values reflect the official schedules currently used by this build,
    but every rate/threshold can be changed under Rates & Rules -> Payroll Rules.
    """
    gross=max(_v1039_money(gross),0); basic=max(_v1039_money(basic),0)
    sss_ee_rate=_v1041_hr_float("hr_sss_employee_rate",5)/100
    sss_er_rate=_v1041_hr_float("hr_sss_employer_rate",10)/100
    sss_max=_v1041_hr_float("hr_sss_max_msc",35000)
    msc=min(gross,sss_max)
    sss_ee=round(msc*sss_ee_rate,2); sss_er=round(msc*sss_er_rate,2)
    sss_ec_threshold=_v1041_hr_float("hr_sss_ec_threshold",14500)
    sss_ec_low=_v1041_hr_float("hr_sss_ec_low",10); sss_ec_high=_v1041_hr_float("hr_sss_ec_high",30)
    sss_ec=sss_ec_low if msc<=sss_ec_threshold else sss_ec_high

    ph_rate=_v1041_hr_float("hr_philhealth_rate",5)/100
    ph_min=_v1041_hr_float("hr_philhealth_min_base",10000); ph_max=_v1041_hr_float("hr_philhealth_max_base",100000)
    ph_share=_v1041_hr_float("hr_philhealth_employee_share",50)/100
    phbase=min(max(gross,ph_min),ph_max); pht=round(phbase*ph_rate,2)
    ph_ee=round(pht*ph_share,2); ph_er=round(pht-ph_ee,2)

    pib_max=_v1041_hr_float("hr_pagibig_max_base",5000)
    pib=min(gross,pib_max)
    pi_low=_v1041_hr_float("hr_pagibig_low_rate",1)/100
    pi_high=_v1041_hr_float("hr_pagibig_high_rate",2)/100
    pi_ee=round(pib*(pi_low if pib<=1500 else pi_high),2)
    pi_er=round(pib*(_v1041_hr_float("hr_pagibig_employer_rate",2)/100),2)

    # Monthly BIR withholding brackets. Employee mandatory contributions are deducted
    # before the taxable compensation base; other payroll deductions are not automatically
    # treated as non-taxable.
    taxable=max(gross-sss_ee-ph_ee-pi_ee,0)
    b2=_v1041_hr_float("hr_bir_bracket2",33332); b3=_v1041_hr_float("hr_bir_bracket3",66666)
    b4=_v1041_hr_float("hr_bir_bracket4",166666); b5=_v1041_hr_float("hr_bir_bracket5",666666)
    exempt=_v1041_hr_float("hr_bir_exempt_threshold",20833)
    r2=_v1041_hr_float("hr_bir_rate2",15)/100; r3=_v1041_hr_float("hr_bir_rate3",20)/100
    r4=_v1041_hr_float("hr_bir_rate4",25)/100; r5=_v1041_hr_float("hr_bir_rate5",30)/100; r6=_v1041_hr_float("hr_bir_rate6",35)/100
    if taxable<=exempt: tax=0
    elif taxable<=b2: tax=(taxable-exempt)*r2
    elif taxable<=b3: tax=1875+(taxable-b2)*r3
    elif taxable<=b4: tax=8541.8+(taxable-b3)*r4
    elif taxable<=b5: tax=33541.8+(taxable-b4)*r5
    else: tax=183541.8+(taxable-b5)*r6
    tax=round(max(tax,0),2)

    other_total=_v1039_money(other)+_v1039_money(absences)+_v1039_money(late_undertime)
    ded=round(sss_ee+ph_ee+pi_ee+tax+other_total,2)
    thirteenth=round(basic/12,2)
    return dict(
        sss_employee=sss_ee, sss_employer=sss_er, sss_ec_employer=sss_ec,
        philhealth_employee=ph_ee, philhealth_employer=ph_er,
        pagibig_employee=pi_ee, pagibig_employer=pi_er,
        withholding_tax=tax, thirteenth_month=thirteenth,
        gross_pay=gross, total_employee_deductions=ded,
        total_employer_cost=round(sss_er+sss_ec+ph_er+pi_er,2),
        net_pay_after_statutory=round(gross-ded,2)
    )

@app.route("/employees/payroll/<int:payroll_id>/statutory",methods=["GET","POST"])
@guarded
def employee_payroll_statutory(payroll_id):
    return _hr_moved("/payroll", payslip=payroll_id)

@app.route("/employees/payroll/<int:payroll_id>/statutory/print")
@guarded
def employee_payroll_statutory_print(payroll_id):
    return _hr_moved("/payroll", payslip=payroll_id)

@app.route("/employees/payroll/13th-month")
@guarded
def employee_13th_month():
    return _hr_moved("/payroll", tab="13th", year=request.args.get("year"))


# ============================================================
# V10.41 - COMPLETE HR SYSTEM
# Employee -> Attendance -> Leave -> Overtime -> Payroll ->
# Payslip -> Government Contributions -> 13th Month -> Reports
# ============================================================
class EmployeeLeave(db.Model):
    __tablename__ = "employee_leave"
    __table_args__ = (db.CheckConstraint("end_date >= start_date", name="ck_employee_leave_dates"),)
    id=db.Column(db.Integer,primary_key=True)
    employee_id=db.Column(db.Integer,db.ForeignKey("employee.id"),nullable=False,index=True)
    leave_type=db.Column(db.String(50),nullable=False)
    start_date=db.Column(db.Date,nullable=False)
    end_date=db.Column(db.Date,nullable=False)
    days=db.Column(db.Numeric(8,2),default=0)
    status=db.Column(db.String(20),default="PENDING")
    reason=db.Column(db.String(500))
    approved_by=db.Column(db.String(100))
    created_at=db.Column(db.DateTime,default=_dt_datetime.utcnow)

class EmployeeOvertime(db.Model):
    __tablename__="employee_overtime"
    id=db.Column(db.Integer,primary_key=True)
    employee_id=db.Column(db.Integer,db.ForeignKey("employee.id"),nullable=False,index=True)
    ot_date=db.Column(db.Date,nullable=False,index=True)
    hours=db.Column(db.Numeric(8,2),default=0)
    rate_multiplier=db.Column(db.Numeric(8,3),default=1.25)
    amount=db.Column(db.Numeric(12,2),default=0)
    status=db.Column(db.String(20),default="PENDING")
    reason=db.Column(db.String(500))
    approved_by=db.Column(db.String(100))
    created_at=db.Column(db.DateTime,default=_dt_datetime.utcnow)

class EmployeeHRLoan(ChangeTracked, db.Model):
    __tablename__="employee_hr_loan"
    id=db.Column(db.Integer,primary_key=True)
    employee_id=db.Column(db.Integer,db.ForeignKey("employee.id"),nullable=False,index=True)
    loan_type=db.Column(db.String(60),nullable=False)
    reference_no=db.Column(db.String(80))
    original_amount=db.Column(db.Numeric(12,2),default=0)
    balance=db.Column(db.Numeric(12,2),default=0)
    monthly_deduction=db.Column(db.Numeric(12,2),default=0)
    status=db.Column(db.String(20),default="ACTIVE")
    notes=db.Column(db.String(300))

class EmployeeHRSetting(ChangeTracked, db.Model):
    __tablename__="employee_hr_setting"
    id=db.Column(db.Integer,primary_key=True)
    key=db.Column(db.String(80),unique=True,nullable=False)
    value=db.Column(db.String(255))


def _v1041_ensure_tables():
    for model in (EmployeeLeave, EmployeeOvertime, EmployeeHRLoan, EmployeeHRSetting):
        try: ensure_table(model)
        except Exception: db.session.rollback()


def _v1041_emp(eid):
    return db.session.get(Employee,int(eid)) if eid else None

def _v1041_days(start,end):
    return max((end-start).days+1,0)

def _v1041_hourly(emp):
    return _v1039_money(getattr(emp,"monthly_salary",0))/26/8

@app.route("/employees/edit/<int:eid>",methods=["GET","POST"])
@guarded
def employee_edit(eid):
    return _hr_moved("/employees", employee=eid)

# D5 fix: leave/overtime status must be one of these; approving or rejecting records who did it.
REQUEST_STATUSES = ("PENDING", "APPROVED", "REJECTED")


def _request_status(default="PENDING"):
    status = (request.form.get("status") or default).strip().upper()
    if status not in REQUEST_STATUSES:
        raise InputError("status", "Status must be Pending, Approved or Rejected.")
    return status


def _decided_by(status):
    u = current_user()
    return (u.username if u else "") if status != "PENDING" else None


@app.route("/employees/leave",methods=["GET","POST"])
@guarded
def employee_leave():
    return _hr_moved("/leave")

@app.route("/employees/leave/<int:lid>/status",methods=["POST"])
@guarded
def employee_leave_status(lid):
    return _hr_moved("/leave")

@app.route("/employees/overtime",methods=["GET","POST"])
@guarded
def employee_overtime():
    return _hr_moved("/attendance", tab="overtime")

@app.route("/employees/overtime/<int:oid>/status",methods=["POST"])
@guarded
def employee_overtime_status(oid):
    return _hr_moved("/attendance", tab="overtime")

@app.route("/employees/hr/loans",methods=["GET","POST"])
@guarded
def employee_hr_loans():
    return _hr_moved("/payroll", tab="loans")

HR_SETTING_DEFAULTS = {
        "hr_working_days":"26", "hr_work_hours_per_day":"8", "hr_grace_minutes":"5",
        "hr_sss_employee_rate":"5", "hr_sss_employer_rate":"10", "hr_sss_max_msc":"35000",
        "hr_sss_ec_threshold":"14500", "hr_sss_ec_low":"10", "hr_sss_ec_high":"30",
        "hr_philhealth_rate":"5", "hr_philhealth_min_base":"10000", "hr_philhealth_max_base":"100000", "hr_philhealth_employee_share":"50",
        "hr_pagibig_max_base":"5000", "hr_pagibig_low_rate":"1", "hr_pagibig_high_rate":"2", "hr_pagibig_employer_rate":"2",
        "hr_bir_exempt_threshold":"20833", "hr_bir_bracket2":"33332", "hr_bir_bracket3":"66666", "hr_bir_bracket4":"166666", "hr_bir_bracket5":"666666",
        "hr_bir_rate2":"15", "hr_bir_rate3":"20", "hr_bir_rate4":"25", "hr_bir_rate5":"30", "hr_bir_rate6":"35", "hr_13th_month_ceiling":"90000"
    }


@app.route("/employees/hr-settings", methods=["GET","POST"])
@guarded
def employee_hr_settings():
    return _hr_moved("/tax-rules")


@app.route("/employees/payroll-reports")
@guarded
def employee_payroll_reports():
    return _hr_moved("/payroll", tab="reports", year=request.args.get("year"))

@app.route("/employees/13th-month")
@guarded
def employee_13th_month_full():
    return _hr_moved("/payroll", tab="13th", year=request.args.get("year"))

# Make existing 13th-month link point to the full HR calculation by adding a distinct endpoint.
try:
    with app.app_context(): _v1041_ensure_tables()
except Exception: pass

# ============================================================
# REST API + React frontend (migration in progress, docs/migration.md)
# Additive only: the legacy pages above keep working unchanged.
# ============================================================
from app.routes.auth import make_auth_blueprint  # noqa: E402
from app.routes.resident import make_resident_blueprint  # noqa: E402
from app.routes.users import make_users_blueprint  # noqa: E402
from app.routes.resident_accounts import make_resident_accounts_blueprint  # noqa: E402
from app.routes.rates import make_rates_blueprint  # noqa: E402
from app.routes.audit_logs import make_audit_logs_blueprint  # noqa: E402
from app.routes.system_settings import make_system_settings_blueprint  # noqa: E402
from app.routes.units import make_units_blueprint  # noqa: E402
from app.routes.billing import make_billing_blueprint  # noqa: E402
from app.routes.receipts import make_receipts_blueprint  # noqa: E402
from app.routes.advances import make_advances_blueprint  # noqa: E402
from app.routes.water import make_water_blueprint  # noqa: E402
from app.routes.operations import make_operations_blueprint  # noqa: E402
from app.routes.community import make_community_blueprint  # noqa: E402
from app.routes.reports import make_reports_blueprint  # noqa: E402
from app.routes.hr import make_hr_blueprint  # noqa: E402
from app.routes.spa import make_spa_blueprint  # noqa: E402
from app.utils.auth import init_auth  # noqa: E402


def resident_access_problem(user):
    """Why a resident may NOT use the portal right now, or None when access is fine.

    Access ends automatically when the owner/tenant record the account is linked to is
    no longer "Current" (moved out) or has left the unit, or when the account or unit is
    deactivated. Accounts not linked to a specific owner/tenant cannot be checked this
    way and keep access to their unit (link them under Resident Accounts)."""
    profile = getattr(user, "resident_profile", None)
    if not profile:
        return "Your resident account is not linked to a unit yet. Please contact the administrator."
    if not profile.active:
        return "Your resident portal access has been deactivated. Please contact the administrator."
    unit = db.session.get(Unit, profile.unit_id)
    if not unit or not unit.active:
        return "Your unit is no longer active in the system. Please contact the administrator."
    if profile.person_id:
        model = Tenant if (profile.person_type or "").lower() == "tenant" else Owner
        person = db.session.get(model, profile.person_id)
        if not person or person.unit_id != profile.unit_id or person.status != "Current":
            role_name = "tenant" if model is Tenant else "owner"
            return (f"Your resident portal access has ended because you are no longer listed as a current "
                    f"{role_name} of unit {unit.unit_no}. Please contact the administrator if this is a mistake.")
    return None


def resident_unit_id(user):
    """The resident's unit, or None when they currently have no portal access."""
    return None if resident_access_problem(user) else user.resident_profile.unit_id


init_auth(app, current_user=current_user, resident_unit_id=resident_unit_id,
          resident_access_problem=resident_access_problem)
app.register_blueprint(make_auth_blueprint(
    User=User, audit=audit, check_password_hash=check_password_hash, commit=db.session.commit,
    home_endpoint=home_endpoint, resident_access_problem=resident_access_problem,
    start_session=start_session, set_password=set_password, client_address=client_address,
))
app.register_blueprint(make_resident_blueprint(sys.modules[__name__]))
def _expected_schema_revision():
    """Latest migration revision shipped with this code (None if the migration scripts are absent)."""
    try:
        from alembic.config import Config as _AlembicConfig
        from alembic.script import ScriptDirectory
        return ScriptDirectory.from_config(_AlembicConfig(os.path.join(BASE_DIR, "database", "alembic.ini"))).get_current_head()
    except Exception:
        return None


from app.routes.health import make_health_blueprint  # noqa: E402
app.register_blueprint(make_health_blueprint(db=db, root=BASE_DIR, expected_revision=_expected_schema_revision()))
app.register_blueprint(make_users_blueprint(db=db, User=User, audit=audit, generate_password_hash=generate_password_hash,
                                            set_password=set_password, end_sessions=end_other_sessions))
app.register_blueprint(make_resident_accounts_blueprint(sys.modules[__name__], end_sessions=end_other_sessions))
app.register_blueprint(make_rates_blueprint(sys.modules[__name__]))
app.register_blueprint(make_audit_logs_blueprint(sys.modules[__name__]))
app.register_blueprint(make_system_settings_blueprint(sys.modules[__name__]))
app.register_blueprint(make_units_blueprint(sys.modules[__name__]))
app.register_blueprint(make_billing_blueprint(sys.modules[__name__]))
app.register_blueprint(make_receipts_blueprint(sys.modules[__name__]))
app.register_blueprint(make_advances_blueprint(sys.modules[__name__]))
app.register_blueprint(make_water_blueprint(sys.modules[__name__]))
app.register_blueprint(make_operations_blueprint(sys.modules[__name__]))
app.register_blueprint(make_community_blueprint(sys.modules[__name__]))
app.register_blueprint(make_reports_blueprint(sys.modules[__name__]))
app.register_blueprint(make_hr_blueprint(sys.modules[__name__]))
app.register_blueprint(make_spa_blueprint(os.path.join(BASE_DIR, "frontend", "dist")))

# ============================================================
# APPLICATION STARTUP
# Keep this block at the very end so all routes (including HR)
# are registered before Flask starts when this file is run directly.
# Preferred entry point: backend/run.py
# ============================================================
if __name__ == "__main__":
    # Start through backend/run.py, which applies the production/development rules.
    sys.exit("Start the server with:  python backend/run.py --lan   (see docs/run-guide/03-BACKEND_SETUP.md)")
