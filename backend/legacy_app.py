import os
import sys
import shutil
import sqlite3
import re
import smtplib
from email.message import EmailMessage
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, g, jsonify, abort, has_request_context, has_app_context
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
app.secret_key = os.getenv("SECRET_KEY", "cityland9-v10-change-this-secret")

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
    _engine_options = {"pool_pre_ping": True, "pool_recycle": 280, "connect_args": {"connect_timeout": 10}}
else:
    _engine_options = {"pool_pre_ping": True, "connect_args": {"timeout": 30}}
app.config.update(
    SQLALCHEMY_DATABASE_URI=db_url,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SQLALCHEMY_ENGINE_OPTIONS=_engine_options,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
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
    try:
        return Decimal(str(v or 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


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


def audit(action):
    """Record an audit log entry and commit it immediately.

    audit() is called after the main db.session.commit() in all routes,
    so this separate commit only ever contains the AuditLog row itself.
    """
    try:
        db.session.add(
            AuditLog(
                username=session.get("username", "system"),
                action=action,
            )
        )
        db.session.commit()
    except Exception:
        db.session.rollback()


def current_user():
    uid = session.get("user_id")
    return db.session.get(User, uid) if uid else None


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
from app.core.roles import ALL_ROLES, RESIDENT  # noqa: E402

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


def rate_for_type(unit_type):
    return {
        "Studio": setting_float("studio_rate_per_sqm", 0),
        "STUDIO TYPE": setting_float("studio_rate_per_sqm", 0),
        "1 Bedroom": setting_float("one_bed_rate_per_sqm", 0),
        "2 Bedroom": setting_float("two_bed_rate_per_sqm", 0),
        "3 Bedroom": setting_float("three_bed_rate_per_sqm", 0),
    }.get(unit_type, 0)


def unit_dues(unit):
    """Condo dues = total unit area × applicable rate per sqm."""
    rate = float(unit.unit_rate_per_sqm or rate_for_type(unit.unit_type))
    return round(float(unit.area_sqm or 0) * rate, 2)


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
        return 0.0
    if getattr(asset, "dues_mode", "per_sqm") == "manual" and money(getattr(asset, "manual_monthly_dues", 0)) > 0:
        return round(float(asset.manual_monthly_dues), 2)
    rate = float(asset.unit_rate_per_sqm or setting_float(default_rate_key, 0))
    return round(float(asset.area_sqm or 0) * rate, 2)


def parking_dues(unit):
    """Parking = the assigned PARKING unit's area × its rate per sqm (or the default parking rate).
    One parking model since Phase B3; the old per-unit parking lots were converted."""
    assigned = assigned_asset(unit, "assigned_parking_unit_id")
    if not assigned:
        return 0.0
    rate = float(assigned.unit_rate_per_sqm or setting_float("parking_rate_per_sqm", 0))
    return round(float(assigned.area_sqm or 0) * rate, 2)

def storage_dues(unit):
    if not getattr(unit, "include_storage", False):
        return 0.0
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
    usage = max(Decimal(str(reading.current_reading or 0)) - Decimal(str(reading.previous_reading or 0)), Decimal("0"))
    return usage * Decimal(str(reading.rate or setting_float("water_rate", 50)))


def unpaid_previous_bills(unit_id, month):
    rows = _billing_history_for_unit(unit_id)
    return [b for b in reversed(rows) if b.billing_month < month and bill_balance(b) > 0]


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
        )
        .order_by(Billing.unit_id.asc(), Billing.billing_month.asc(), Billing.id.asc())
        .all()
    )

    # Billing/SOA only needs the selected month's water reading. Historical
    # bills already store their water charge, so loading every historical
    # water-reading row here creates unnecessary work on large imports.
    water_map = {}
    if request.endpoint == "billing":
        target_month = request.args.get("month") or datetime.now().strftime("%Y-%m")
        water_rows = (
            WaterReading.query
            .filter_by(reading_month=target_month)
            .order_by(WaterReading.unit_id.asc(), WaterReading.id.desc())
            .all()
        )
    else:
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

    i = 0
    while i < len(bills):
        unit_id = bills[i].unit_id
        running_previous = Decimal("0")
        # Outstanding balances of the selected charge types from prior months.
        running_previous_penalty_base = Decimal("0")
        j = i

        while j < len(bills) and bills[j].unit_id == unit_id:
            month = bills[j].billing_month
            k = j
            month_rows = []
            while (
                k < len(bills)
                and bills[k].unit_id == unit_id
                and bills[k].billing_month == month
            ):
                month_rows.append(bills[k])
                k += 1

            month_balances = []
            for bill in month_rows:
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
                    reading = water_map.get((bill.unit_id, bill.billing_month))
                    water = money(reading.bill_amount) if reading else money(bill.water)
                    penalty = (
                        (running_previous_penalty_base * penalty_rate).quantize(Decimal("0.01"))
                        if is_overdue(bill) else Decimal("0.00")
                    )
                    previous = running_previous

                storage = money(bill.storage_dues) if storage_charged(bill) else Decimal("0.00")
                current = condo + parking + storage + water
                reading = water_map.get((bill.unit_id, bill.billing_month))
                if reading and getattr(reading, "paid", False):
                    current -= water

                advance = min(
                    advance_map.get(bill.id, Decimal("0")),
                    max(current, Decimal("0")),
                )
                total = (current + previous + penalty - advance).quantize(Decimal("0.01"))
                balance = (total - money(bill.amount_paid)).quantize(Decimal("0.01"))

                result[bill.id] = {
                    "condo": condo,
                    "parking": parking,
                    "storage": storage,
                    "water": water,
                    "penalty": penalty,
                    "previous": previous,
                    "advance": advance,
                    "current": current,
                    "total": total,
                    "balance": balance,
                    "reading": reading,
                }
                month_balances.append(max(balance, Decimal("0")))

            # Earlier billing logic used billing_month < current month, so
            # duplicate bills in the same month must not affect one another.
            running_previous += sum(month_balances, Decimal("0"))

            # Build the penalty base from this month's unpaid selected charges.
            # Payments are allocated Condo Dues -> Parking -> Storage -> Water.
            # Advances are applied to Condo Dues only.
            month_penalty_base = Decimal("0.00")
            for bill in month_rows:
                calc = result[bill.id]
                condo_base = max(calc["condo"] - calc["advance"], Decimal("0"))
                parking_base = max(calc["parking"], Decimal("0"))
                storage_base = max(calc["storage"], Decimal("0"))
                water_base = max(calc["water"], Decimal("0"))
                reading = calc.get("reading")
                if reading and getattr(reading, "paid", False):
                    water_base = Decimal("0.00")

                remaining_paid = max(money(bill.amount_paid), Decimal("0"))
                paid_condo = min(remaining_paid, condo_base); remaining_paid -= paid_condo
                paid_parking = min(remaining_paid, parking_base); remaining_paid -= paid_parking
                paid_storage = min(remaining_paid, storage_base); remaining_paid -= paid_storage
                paid_water = min(remaining_paid, water_base)
                selected = {
                    "condo": max(condo_base - paid_condo, Decimal("0")),
                    "parking": max(parking_base - paid_parking, Decimal("0")),
                    "storage": max(storage_base - paid_storage, Decimal("0")),
                    "water": max(water_base - paid_water, Decimal("0")),
                }
                month_penalty_base += sum(
                    (selected[key] for key, enabled in penalty_include.items() if enabled),
                    Decimal("0.00")
                )
            running_previous_penalty_base += month_penalty_base
            j = k

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
        "penalty": money(bill.penalty),
        "previous": money(bill.previous_balance),
        "advance": Decimal("0.00"),
        "current": money(bill.water),
        "total": money(bill.water),
        "balance": money(bill.water) - money(bill.amount_paid),
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
    total = Decimal("0.00")
    for b in _billing_history_for_unit(unit_id):
        if b.billing_month >= month:
            break
        total += max(bill_balance(b), Decimal("0"))
    return total.quantize(Decimal("0.01"))


def selected_penalty_base_for_unit(unit_id, month):
    """Return prior-month unpaid balances for the charge types selected in Rates & Rules."""
    enabled = {
        "condo": setting("penalty_include_condo", "1") == "1",
        "parking": setting("penalty_include_parking", "0") == "1",
        "storage": setting("penalty_include_storage", "0") == "1",
        "water": setting("penalty_include_water", "0") == "1",
    }
    total = Decimal("0.00")
    for prior in _billing_history_for_unit(unit_id):
        if prior.billing_month >= month:
            break
        condo = money(prior.assessment)
        parking = money(prior.parking_dues)
        storage = max(money(prior.storage_dues), Decimal("0")) if storage_charged(prior) else Decimal("0.00")
        water = money(prior.water)
        reading = _water_reading_for_bill(prior)
        if reading and getattr(reading, "paid", False):
            water = Decimal("0.00")

        advance = min(advance_for_bill(prior), max(condo, Decimal("0")))
        condo = max(condo - advance, Decimal("0"))
        remaining_paid = max(money(prior.amount_paid), Decimal("0"))
        paid_condo = min(remaining_paid, condo); remaining_paid -= paid_condo
        paid_parking = min(remaining_paid, parking); remaining_paid -= paid_parking
        paid_storage = min(remaining_paid, storage); remaining_paid -= paid_storage
        paid_water = min(remaining_paid, water)
        outstanding = {
            "condo": max(condo - paid_condo, Decimal("0")),
            "parking": max(parking - paid_parking, Decimal("0")),
            "storage": max(storage - paid_storage, Decimal("0")),
            "water": max(water - paid_water, Decimal("0")),
        }
        total += sum((outstanding[k] for k, on in enabled.items() if on), Decimal("0.00"))
    return total.quantize(Decimal("0.01"))


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
            .order_by(AdvancePayment.payment_date.asc(), AdvancePayment.id.asc())
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
    return bill_total(bill) - money(bill.amount_paid)



def bill_status(bill):
    """Return the billing status based on the authoritative bill balance."""
    balance = bill_balance(bill)
    if balance <= Decimal("0.00"):
        return "Paid"
    if money(getattr(bill, "amount_paid", 0)) > Decimal("0.00"):
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

    # Legacy fields retained for compatibility
    owner_name = db.Column(db.String(200))
    contact_no = db.Column(db.String(80))
    email = db.Column(db.String(160))
    tenant_name = db.Column(db.String(200))
    parking_rate_per_sqm = db.Column(db.Float, default=0)
    monthly_rate = db.Column(db.Numeric(12, 2), default=0)
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
        return round(self.usage * float(self.rate or 0), 2)


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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    unit = db.relationship("Unit")
    allocations = db.relationship("ReceiptAllocation", back_populates="receipt", cascade="all, delete-orphan")


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
    last = (db.session.query(func.max(Receipt.receipt_seq)).filter(Receipt.receipt_year == year)
            .with_for_update().scalar()) or 0   # locks the year's numbers until commit (no duplicates)
    receipt = Receipt(receipt_year=year, receipt_seq=last + 1, receipt_no=f"OR-{year}-{last + 1:06d}",
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


class GatePass(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pass_date = db.Column(db.Date, default=date.today)
    unit_no = db.Column(db.String(40))
    visitor_name = db.Column(db.String(200))
    purpose = db.Column(db.String(300))
    status = db.Column(db.String(30), default="Issued")


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80))
    action = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


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
@app.route("/")
def index():
    u = current_user()
    return redirect(url_for(home_endpoint(u) if u else "login"))


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
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username, active=True).first()

        if user and check_password_hash(user.password_hash, password):
            session.clear()
            session["user_id"] = user.id
            session["username"] = user.username
            audit("Login")
            return redirect(url_for(home_endpoint(user)))

        flash("Invalid username or password.", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    if session.get("user_id"):
        audit("Logout")
    session.clear()
    return redirect(url_for("login"))


# -----------------------------
# Units
# -----------------------------
@app.route("/units", methods=["GET", "POST"])
@guarded
def units():
    if request.method == "POST":
        d = request.form
        unit_no = d.get("unit_no", "").strip()

        if not unit_no:
            flash("Unit number is required.", "danger")
            return redirect(url_for("units"))

        if Unit.query.filter_by(unit_no=unit_no).first():
            flash("Unit number already exists.", "danger")
            return redirect(url_for("units"))

        u = Unit(
            unit_no=unit_no,
            floor=d.get("floor", "").strip(),
            unit_type=d.get("unit_type", ""),
            area_sqm=float(d.get("area_sqm") or 0),
            unit_rate_per_sqm=float(d.get("unit_rate_per_sqm") or rate_for_type(d.get("unit_type"))),
            owner_name=d.get("owner_name", "").strip(),
            contact_no=d.get("contact_no", "").strip(),
            email=d.get("email", "").strip(),
            occupancy_type=d.get("occupancy_type", "Owner"),
            status=d.get("status", "Vacant"),
            include_parking=d.get("include_parking") == "on",
            include_storage=d.get("include_storage") == "on",
            assigned_parking_unit_id=(int(d.get("assigned_parking_unit_id") or 0) or None) if d.get("include_parking") == "on" else None,
            assigned_storage_unit_id=(int(d.get("assigned_storage_unit_id") or 0) or None) if d.get("include_storage") == "on" else None,
            auto_rate=d.get("auto_rate") == "on",
            dues_mode=d.get("dues_mode", "per_sqm"),
            manual_monthly_dues=money(d.get("manual_monthly_dues")),
            active=True,
        )
        if u.auto_rate:
            u.unit_rate_per_sqm = rate_for_type(u.unit_type)
        u.monthly_rate = unit_dues(u)

        db.session.add(u)
        db.session.commit()

        # Optional first owner
        if d.get("owner_name", "").strip():
            db.session.add(
                Owner(
                    unit_id=u.id,
                    owner_name=d["owner_name"].strip(),
                    contact_no=d.get("contact_no", "").strip(),
                    email=d.get("email", "").strip(),
                    status="Current",
                    receive_soa_email=d.get("owner_receive_soa_email") == "on",
                    include_in_soa=d.get("owner_include_in_soa") == "on",
                )
            )

        # Optional first tenant
        tenant_name = d.get("tenant_name", "").strip()
        if tenant_name:
            db.session.add(
                Tenant(
                    unit_id=u.id,
                    tenant_name=tenant_name,
                    contact_no=d.get("tenant_contact", "").strip(),
                    email=d.get("tenant_email", "").strip(),
                    move_in=parse_date(d.get("tenant_move_in")),
                    status="Current",
                    receive_soa_email=d.get("tenant_receive_soa_email") == "on",
                    include_in_soa=d.get("tenant_include_in_soa") == "on",
                )
            )

        db.session.commit()
        audit(f"Created unit {unit_no}")
        flash(f"Unit {unit_no} created successfully.", "success")
        return redirect(url_for("unit_detail", uid=u.id))

    q = request.args.get("q", "").strip()
    floor = request.args.get("floor", "")
    unit_type = request.args.get("unit_type", "")
    status = request.args.get("status", "")

    query = Unit.query.options(
        selectinload(Unit.tenants),
        selectinload(Unit.assigned_parking_unit),
        selectinload(Unit.assigned_storage_unit),
    ).filter_by(active=True)

    if q:
        query = query.filter(
            or_(
                Unit.unit_no.ilike(f"%{q}%"),
                Unit.owner_name.ilike(f"%{q}%"),
                Unit.tenant_name.ilike(f"%{q}%"),
            )
        )
    if floor:
        query = query.filter_by(floor=floor)
    if unit_type:
        query = query.filter_by(unit_type=unit_type)
    if status:
        query = query.filter_by(status=status)

    # Server-side pagination keeps the browser responsive even with 800+ units.
    page = max(int(request.args.get("page", 1) or 1), 1)
    pagination = db.paginate(query.order_by(Unit.unit_no), page=page, per_page=20, error_out=False)
    units_list = pagination.items
    floors = [x[0] for x in db.session.query(Unit.floor).filter(Unit.floor.isnot(None)).distinct().all() if x[0]]

    # Fetch SOAs only for the visible page instead of the entire billing history.
    page_unit_ids = [u.id for u in units_list]
    latest_bills = {}
    if page_unit_ids:
        for b in Billing.query.filter(Billing.unit_id.in_(page_unit_ids)).order_by(Billing.billing_month.desc(), Billing.id.desc()).all():
            if b.unit_id not in latest_bills:
                latest_bills[b.unit_id] = b

    return render_template(
        "units.html",
        units=units_list,
        pagination=pagination,
        floors=sorted(floors),
        q=q,
        selected_floor=floor,
        selected_type=unit_type,
        selected_status=status,
        parking_units=Unit.query.filter_by(active=True, unit_type="PARKING").order_by(Unit.unit_no).all(),
        storage_units=Unit.query.filter_by(active=True, unit_type="STORAGE").order_by(Unit.unit_no).all(),
        latest_bills=latest_bills,
    )


@app.route("/unit/<int:uid>")
@guarded
def unit_detail(uid):
    u = db.session.get(Unit, uid)
    if not u:
        flash("Unit not found.", "danger")
        return redirect(url_for("units"))

    return render_template(
        "unit_detail.html",
        unit=u,
        assigned_parking=assigned_asset(u, "assigned_parking_unit_id"),
        assigned_storage=assigned_asset(u, "assigned_storage_unit_id"),
        parking_units=Unit.query.filter_by(active=True, unit_type="PARKING").order_by(Unit.unit_no).all(),
        storage_units=Unit.query.filter_by(active=True, unit_type="STORAGE").order_by(Unit.unit_no).all(),
        tenants=Tenant.query.filter_by(unit_id=uid).order_by(Tenant.status.desc(), Tenant.id.desc()).all(),
        owners=Owner.query.filter_by(unit_id=uid).order_by(Owner.id.desc()).all(),
        bills=Billing.query.filter_by(unit_id=uid).order_by(Billing.billing_month.desc()).all(),
    )


@app.route("/unit/<int:uid>/edit", methods=["POST"])
@guarded
def edit_unit(uid):
    u = db.session.get(Unit, uid)
    if not u:
        flash("Unit not found.", "danger")
        return redirect(url_for("units"))

    d = request.form
    u.floor = d.get("floor", "")
    u.unit_type = d.get("unit_type", "")
    u.area_sqm = float(d.get("area_sqm") or 0)
    u.unit_rate_per_sqm = float(d.get("unit_rate_per_sqm") or rate_for_type(u.unit_type))
    u.owner_name = d.get("owner_name", "")
    u.contact_no = d.get("contact_no", "")
    u.email = d.get("email", "")
    u.occupancy_type = d.get("occupancy_type", "Owner")
    u.status = d.get("status", "Vacant")
    u.include_parking = d.get("include_parking") == "on"
    u.include_storage = d.get("include_storage") == "on"
    u.assigned_parking_unit_id = (int(d.get("assigned_parking_unit_id") or 0) or None) if u.include_parking else None
    u.assigned_storage_unit_id = (int(d.get("assigned_storage_unit_id") or 0) or None) if u.include_storage else None
    u.auto_rate = d.get("auto_rate") == "on"
    u.dues_mode = d.get("dues_mode", "per_sqm")
    u.manual_monthly_dues = money(d.get("manual_monthly_dues"))
    if u.auto_rate and u.dues_mode == "per_sqm":
        u.unit_rate_per_sqm = rate_for_type(u.unit_type)
    u.monthly_rate = unit_dues(u)

    db.session.commit()
    audit(f"Updated unit {u.unit_no}")
    flash("Unit updated successfully.", "success")
    return redirect(url_for("unit_detail", uid=uid))


# -----------------------------
# Multiple tenants
# -----------------------------
@app.route("/unit/<int:uid>/tenant/add", methods=["POST"])
@guarded
def add_tenant(uid):
    u = db.session.get(Unit, uid)
    if not u:
        flash("Unit not found.", "danger")
        return redirect(url_for("units"))

    name = request.form.get("tenant_name", "").strip()
    if not name:
        flash("Tenant name is required.", "danger")
        return redirect(url_for("unit_detail", uid=uid))

    is_rep = request.form.get("representative") == "1"
    if is_rep:
        Tenant.query.filter_by(unit_id=uid, representative=True).update({"representative": False}, synchronize_session=False)
    tenant = Tenant(
        unit_id=uid,
        tenant_name=name,
        contact_no=request.form.get("contact_no", "").strip(),
        email=request.form.get("email", "").strip(),
        move_in=parse_date(request.form.get("move_in")),
        move_out=parse_date(request.form.get("move_out")),
        notes=request.form.get("notes", "").strip(),
        status="Past" if request.form.get("move_out") else "Current",
        representative=is_rep,
        receive_soa_email=request.form.get("receive_soa_email") == "on",
    )
    db.session.add(tenant)
    u.tenant_name = name if tenant.status == "Current" else u.tenant_name
    u.status = "Occupied"
    db.session.commit()

    audit(f"Added tenant {name} to unit {u.unit_no}")
    flash("Tenant added. Existing tenants were preserved.", "success")
    return redirect(url_for("unit_detail", uid=uid))


@app.route("/tenants")
@guarded
def tenants():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "Current")
    query = Tenant.query
    if q:
        like = f"%{q}%"
        query = query.join(Unit).filter(or_(Tenant.tenant_name.ilike(like), Tenant.contact_no.ilike(like), Tenant.email.ilike(like), Unit.unit_no.ilike(like)))
    if status in ("Current", "Past"):
        query = query.filter(Tenant.status == status)
    rows = query.order_by(Tenant.tenant_name).all()
    return render_template("tenants.html", tenants=rows, q=q, status=status)


@app.route("/unit/<int:uid>/tenant/<int:tid>/edit", methods=["POST"])
@guarded
def edit_tenant(uid, tid):
    tenant = db.session.get(Tenant, tid)
    if not tenant or tenant.unit_id != uid:
        flash("Tenant not found.", "danger")
        return redirect(url_for("unit_detail", uid=uid))
    name = request.form.get("tenant_name", "").strip()
    if not name:
        flash("Tenant name is required.", "danger")
        return redirect(url_for("unit_detail", uid=uid))
    is_rep = request.form.get("representative") == "1"
    if is_rep:
        Tenant.query.filter(Tenant.unit_id == uid, Tenant.id != tid, Tenant.representative == True).update({"representative": False}, synchronize_session=False)
    tenant.representative = is_rep
    tenant.tenant_name = name
    tenant.contact_no = request.form.get("contact_no", "").strip()
    tenant.email = request.form.get("email", "").strip()
    tenant.receive_soa_email = request.form.get("receive_soa_email") == "on"
    tenant.include_in_soa = request.form.get("include_in_soa") == "on"
    tenant.move_in = parse_date(request.form.get("move_in"))
    tenant.move_out = parse_date(request.form.get("move_out"))
    tenant.notes = request.form.get("notes", "").strip()
    tenant.status = request.form.get("status", tenant.status)
    if tenant.status == "Past" and not tenant.move_out:
        tenant.move_out = date.today()
    if tenant.status == "Current":
        tenant.move_out = None
    db.session.commit()
    audit(f"Updated tenant {tenant.tenant_name} in unit {tenant.unit.unit_no}")
    flash("Tenant updated.", "success")
    return redirect(url_for("unit_detail", uid=uid))


@app.route("/tenant/<int:tid>/status", methods=["POST"])
@guarded
def tenant_status(tid):
    tenant = db.session.get(Tenant, tid)
    if not tenant:
        flash("Tenant not found.", "danger")
        return redirect(url_for("units"))

    new_status = request.form.get("status", "Current")
    tenant.status = new_status
    if new_status == "Past":
        tenant.representative = False
    if new_status == "Past" and not tenant.move_out:
        tenant.move_out = date.today()

    db.session.commit()
    audit(f"Changed tenant {tenant.tenant_name} status to {new_status}")
    return redirect(url_for("unit_detail", uid=tenant.unit_id))


# -----------------------------
# Owners
# -----------------------------
@app.route("/unit/<int:uid>/owner/add", methods=["POST"])
@guarded
def add_owner(uid):
    u = db.session.get(Unit, uid)
    if not u:
        flash("Unit not found.", "danger")
        return redirect(url_for("units"))

    name = request.form.get("owner_name", "").strip()
    if not name:
        flash("Owner name is required.", "danger")
        return redirect(url_for("unit_detail", uid=uid))

    db.session.add(
        Owner(
            unit_id=uid,
            owner_name=name,
            contact_no=request.form.get("contact_no", ""),
            email=request.form.get("email", ""),
            move_in=parse_date(request.form.get("move_in")),
            status="Current",
            receive_soa_email=request.form.get("receive_soa_email") == "on",
        )
    )
    u.owner_name = name
    u.contact_no = request.form.get("contact_no", "")
    u.email = request.form.get("email", "")
    db.session.commit()
    audit(f"Added owner {name} to unit {u.unit_no}")
    flash("Owner added.", "success")
    return redirect(url_for("unit_detail", uid=uid))


@app.route("/unit/<int:uid>/owner/<int:oid>/edit", methods=["POST"])
@guarded
def edit_owner(uid, oid):
    owner = db.session.get(Owner, oid)
    if not owner or owner.unit_id != uid:
        flash("Owner not found.", "danger")
        return redirect(url_for("unit_detail", uid=uid))
    owner.owner_name = request.form.get("owner_name", "").strip()
    owner.contact_no = request.form.get("contact_no", "").strip()
    owner.email = request.form.get("email", "").strip()
    owner.status = request.form.get("status", owner.status)
    owner.receive_soa_email = request.form.get("receive_soa_email") == "on"
    owner.include_in_soa = request.form.get("include_in_soa") == "on"
    if owner.status == "Past" and not owner.move_out:
        owner.move_out = date.today()
    if owner.status == "Current":
        owner.move_out = None
    unit = db.session.get(Unit, uid)
    if unit and owner.status == "Current":
        unit.owner_name = owner.owner_name
        unit.contact_no = owner.contact_no
        unit.email = owner.email
    db.session.commit()
    audit(f"Updated owner {owner.owner_name} in unit {unit.unit_no if unit else uid}; SOA email opt-in={'Yes' if owner.receive_soa_email else 'No'}")
    flash("Owner updated.", "success")
    return redirect(url_for("unit_detail", uid=uid))


# -----------------------------
# Parking
# -----------------------------
@app.route("/parking")
@guarded
def parking():
    """Parking = PARKING-type units assigned to residential units (Phase B3)."""
    assigned_to = {u.assigned_parking_unit_id: u for u in
                   Unit.query.filter(Unit.assigned_parking_unit_id.isnot(None)).all()}
    rows = []
    for asset in Unit.query.filter_by(unit_type="PARKING").order_by(Unit.unit_no).all():
        rate = float(asset.unit_rate_per_sqm or setting_float("parking_rate_per_sqm", 0))
        rows.append({"asset": asset, "unit": assigned_to.get(asset.id), "rate": rate,
                     "charge": round(float(asset.area_sqm or 0) * rate, 2)})
    return render_template("parking.html", rows=rows)



# -----------------------------
# Billing
# -----------------------------
@app.route("/billing", methods=["GET", "POST"])
@guarded
def billing():
    month = request.args.get("month") or datetime.now().strftime("%Y-%m")

    if request.method == "POST":
        month = request.form.get("month") or month
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            flash("Invalid billing month.", "danger")
            return redirect(url_for("billing"))
        due_day = 8
        created = 0
        for u in Unit.query.filter_by(active=True).all():
            if u.unit_type in ("PARKING", "STORAGE"):
                continue
            if Billing.query.filter_by(unit_id=u.id, billing_month=month).first():
                continue
            due_date = date.fromisoformat(f"{month}-01").replace(day=due_day)
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
        db.session.commit()
        audit(f"Generated {created} detailed bills for {month}")
        flash(f"Generated {created} bills for {month}.", "success")
        return redirect(url_for("billing_email", month=month))

    # GET must remain read-only and fast. Advance allocation and penalty
    # refresh are performed when bills/payments are changed, not on every
    # Billing/SOA page view.

    # Normalize due dates only when necessary.
    due_date_changed = False
    month_due_date = date.fromisoformat(f"{month}-01").replace(day=8)
    month_bills_for_sync = Billing.query.filter_by(billing_month=month).all()
    for existing_bill in month_bills_for_sync:
        if existing_bill.due_date != month_due_date:
            existing_bill.due_date = month_due_date
            due_date_changed = True

    # One query for the month's readings instead of one query per bill.
    current_readings = (
        WaterReading.query
        .filter_by(reading_month=month)
        .order_by(WaterReading.unit_id.asc(), WaterReading.id.desc())
        .all()
    )
    reading_by_unit = {}
    for r in current_readings:
        reading_by_unit.setdefault(r.unit_id, r)
    sync_changed = False
    for b in month_bills_for_sync:
        r = reading_by_unit.get(b.unit_id)
        if r and money(b.water) != money(r.bill_amount):
            b.water = money(r.bill_amount)
            sync_changed = True
    if due_date_changed or sync_changed:
        db.session.commit()

    q = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "").strip()
    unit_type_filter = request.args.get("unit_type", "").strip()
    try:
        page = max(int(request.args.get("page", 1)), 1)
    except ValueError:
        page = 1
    try:
        per_page = min(max(int(request.args.get("per_page", 20)), 10), 100)
    except ValueError:
        per_page = 20

    bill_query = Billing.query.join(Unit).outerjoin(Tenant, Tenant.unit_id == Unit.id).filter(Billing.billing_month == month)
    if q:
        like = f"%{q}%"
        bill_query = bill_query.filter(or_(Unit.unit_no.ilike(like), Unit.owner_name.ilike(like), Tenant.tenant_name.ilike(like)))
    if unit_type_filter:
        bill_query = bill_query.filter(Unit.unit_type == unit_type_filter)
    bills_base = bill_query.distinct().order_by(Billing.id.desc()).all()

    # Status is calculated from the current balance/due date. Keep KPI counts based
    # on the search/month/unit-type selection, then apply the selected KPI tab to
    # the displayed rows. Pending combines unpaid and partially paid bills.
    kpi_paid = sum(1 for b in bills_base if bill_status(b) == "Paid")
    kpi_partial = sum(1 for b in bills_base if bill_status(b) == "Partially Paid")
    kpi_overdue = sum(1 for b in bills_base if bill_status(b) == "Overdue")
    kpi_pending = sum(1 for b in bills_base if bill_status(b) in ("Unpaid", "Partially Paid"))

    bills_all = bills_base
    if status_filter == "Pending":
        bills_all = [b for b in bills_all if bill_status(b) in ("Unpaid", "Partially Paid")]
    elif status_filter:
        bills_all = [b for b in bills_all if bill_status(b) == status_filter]

    total_count = len(bills_all)
    total_pages = max((total_count + per_page - 1) // per_page, 1)
    page = min(page, total_pages)
    start = (page - 1) * per_page
    bills = bills_all[start:start + per_page]

    previous_map = {b.id: unpaid_previous_bills(b.unit_id, month) for b in bills}
    current_readings = {r.unit_id: r for r in current_readings}
    reading_map = {b.unit_id: current_readings.get(b.unit_id) for b in bills}
    return render_template(
        "billing.html",
        bills=bills,
        month=month,
        q=q,
        status_filter=status_filter,
        unit_type_filter=unit_type_filter,
        previous_map=previous_map,
        reading_map=reading_map,
        total_count=total_count,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        kpi_paid=kpi_paid,
        kpi_partial=kpi_partial,
        kpi_overdue=kpi_overdue,
        kpi_pending=kpi_pending,
        today=date.today().isoformat(),
    )


@app.route("/billing/advance", methods=["GET", "POST"])
@guarded
def advance_payments():
    if request.method == "POST":
        unit_id = request.form.get("unit_id", type=int)
        amount = money(request.form.get("amount"))
        start_month = (request.form.get("start_month") or "").strip()
        months = request.form.get("coverage_months", type=int) or 1
        method = (request.form.get("payment_method") or "CASH").upper()
        reference = (request.form.get("reference") or "").strip()
        payment_date = parse_date(request.form.get("payment_date")) or date.today()
        if not unit_id or not db.session.get(Unit, unit_id):
            flash("Please select a valid unit.", "danger")
            return redirect(url_for("advance_payments"))
        if amount <= 0 or months < 1:
            flash("Advance amount and coverage months are required.", "danger")
            return redirect(url_for("advance_payments"))
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", start_month):
            flash("Please enter a valid start billing month.", "danger")
            return redirect(url_for("advance_payments"))
        if method not in ("CASH", "CHECK", "ONLINE"):
            flash("Invalid payment method.", "danger")
            return redirect(url_for("advance_payments"))
        if method in ("CHECK", "ONLINE") and not reference:
            flash("Reference number is required for Check or Online payments.", "danger")
            return redirect(url_for("advance_payments"))
        monthly = (amount / months).quantize(Decimal("0.01"))
        adv = AdvancePayment(unit_id=unit_id, payment_date=payment_date, amount=amount, start_month=start_month, coverage_months=months, monthly_amount=monthly, payment_method=method, reference=reference, remarks=request.form.get("remarks", "").strip())
        db.session.add(adv)
        db.session.flush()
        allocate_advances_for_month(start_month, unit_id)
        receipt = issue_receipt(unit_id, payment_date, method, reference, request.form.get("remarks", ""),
                                [("advance", adv, amount)])
        db.session.commit()
        audit(f"Issued {receipt.receipt_no} - Recorded {months}-month advance condo dues payment for unit {adv.unit.unit_no}: {amount:,.2f}")
        flash(f"Advance payment of ₱{amount:,.2f} recorded for {months} month(s) (official receipt {receipt.receipt_no}).", "success")
        return redirect(url_for("advance_payments", unit_id=unit_id))

    units = Unit.query.filter(Unit.active.is_(True), ~Unit.unit_type.in_(["PARKING", "STORAGE"])).order_by(Unit.unit_no).all()
    advances = AdvancePayment.query.join(Unit).order_by(AdvancePayment.id.desc()).all()
    rows = [(a, advance_balance(a)) for a in advances]
    return render_template("advance_payments.html", units=units, advances=rows, today=date.today().isoformat(), selected_unit=request.args.get("unit_id", type=int), current_month=datetime.now().strftime("%Y-%m"))


@app.route("/billing/<int:bid>")
@guarded
def billing_detail(bid):
    b=db.session.get(Billing,bid)
    if not b: return "Bill not found",404

    # Every SOA is due on the 8th of its billing month.
    expected_due_date = date.fromisoformat(f"{b.billing_month}-01").replace(day=8)
    if b.due_date != expected_due_date:
        b.due_date = expected_due_date
        db.session.commit()

    # A water reading may have been entered after the bill was generated.
    # Synchronize the bill here as well so the SOA never silently omits the
    # current month's water charge.
    water_reading = WaterReading.query.filter_by(
        unit_id=b.unit_id, reading_month=b.billing_month
    ).order_by(WaterReading.id.desc()).first()
    if water_reading:
        expected_water = money(water_reading.bill_amount)
        if money(b.water) != expected_water:
            b.water = expected_water

    # Keep the stored penalty aligned with the detailed SOA computation.
    # The calculation covers prior outstanding monthly balances and includes
    # the current bill only after it becomes overdue. A water reading marked
    # Paid is excluded from the current SOA balance.
    computed_penalty = soa_penalty_amount(b)
    if not b.soa_manual_override and money(b.penalty) != computed_penalty:
        b.penalty = computed_penalty

    allocate_advances_for_month(b.billing_month, b.unit_id)
    b.status = bill_status(b)
    db.session.commit()

    water_history = WaterReading.query.filter_by(unit_id=b.unit_id).order_by(
        WaterReading.reading_month.desc(), WaterReading.id.desc()
    ).all()
    water_bill_map = {(x.unit_id, x.billing_month): x for x in Billing.query.filter_by(unit_id=b.unit_id).all()}
    water_reading_map = {(x.unit_id, x.reading_month): x for x in water_history}

    return render_template(
        "billing_detail.html", bill=b, previous=unpaid_previous_bills(b.unit_id,b.billing_month),
        contact=current_contact_for_unit(b.unit),
        assigned_parking=assigned_asset(b.unit, "assigned_parking_unit_id"), assigned_storage=assigned_asset(b.unit, "assigned_storage_unit_id"),
        unit_overdue=overdue_months_for_unit(b.unit_id,b.billing_month),
        water_reading=water_reading, water_history=water_history, water_bill_map=water_bill_map, water_reading_map=water_reading_map,
        advance_applied=advance_for_bill(b),
    )


@app.route("/billing/<int:bid>/pay", methods=["POST"])
@guarded
def mark_bill_paid(bid):
    b = db.session.get(Billing, bid)
    if not b:
        flash("Bill not found.", "danger")
        return redirect(url_for("billing"))

    balance_before = max(bill_balance(b), Decimal("0"))
    amount = money(request.form.get("amount"))
    payment_method = (request.form.get("payment_method") or "CASH").upper()
    payment_type = (request.form.get("payment_type") or "FULL").upper()
    reference = (request.form.get("reference") or "").strip()
    payment_date = parse_date(request.form.get("payment_date")) or date.today()

    if payment_method not in ("CASH", "CHECK", "ONLINE"):
        flash("Invalid payment method.", "danger")
        return redirect(url_for("billing", month=b.billing_month))
    if payment_type not in ("FULL", "PARTIAL"):
        flash("Invalid payment type.", "danger")
        return redirect(url_for("billing", month=b.billing_month))
    if amount <= 0:
        flash("Please enter a payment amount.", "danger")
        return redirect(url_for("billing", month=b.billing_month))
    # Payments may exceed the current SOA. The amount needed to clear the
    # bill is applied to the bill, while the excess is automatically recorded
    # as an advance payment for the same unit starting on this billing month.
    if balance_before <= 0:
        excess_amount = amount
        amount_to_bill = Decimal("0.00")
    else:
        amount_to_bill = min(amount, balance_before)
        excess_amount = max(amount - balance_before, Decimal("0.00"))

    if amount_to_bill <= 0 and excess_amount <= 0:
        flash("This bill has no remaining balance.", "warning")
        return redirect(url_for("billing", month=b.billing_month))
    if payment_method in ("CHECK", "ONLINE") and not reference:
        flash("Reference number is required for Check or Online payments.", "danger")
        return redirect(url_for("billing", month=b.billing_month))

    if amount_to_bill > 0:
        b.amount_paid = money(b.amount_paid) + amount_to_bill

    if bill_balance(b) <= 0:
        b.amount_paid = bill_total(b)
        b.status = "Paid"
        b.paid_date = payment_date
    else:
        b.status = "Partially Paid"

    # Keep the billing payment record limited to the amount actually applied
    # to this SOA. The excess is tracked separately as an advance so it can
    # be automatically applied to future condo dues.
    bill_payment = excess_adv = None
    if amount_to_bill > 0:
        bill_payment = Payment(
            billing_id=b.id,
            amount=amount_to_bill,
            payment_date=payment_date,
            payment_method=payment_method,
            payment_type=payment_type,
            reference=reference,
            remarks=request.form.get("remarks", "")
        )
        db.session.add(bill_payment)

    if excess_amount > 0:
        excess_adv = AdvancePayment(
            unit_id=b.unit_id,
            payment_date=payment_date,
            amount=excess_amount,
            start_month=b.billing_month,
            coverage_months=1,
            monthly_amount=excess_amount,
            payment_method=payment_method,
            reference=reference,
            remarks=(request.form.get("remarks", "").strip() + " " if request.form.get("remarks", "").strip() else "") +
                    f"Automatic advance from excess payment for {b.billing_month}."
        )
        db.session.add(excess_adv)
        db.session.flush()
        # The bill is already settled above, so this advance remains available
        # for the next eligible billing month.
        allocate_advances_for_month(b.billing_month, b.unit_id)

    receipt = issue_receipt(b.unit_id, payment_date, payment_method, reference, request.form.get("remarks", ""),
                            [("bill", bill_payment, amount_to_bill), ("advance", excess_adv, excess_amount)])
    db.session.commit()
    audit(
        f"Issued {receipt.receipt_no} - "
        f"Recorded {payment_type.lower()} {payment_method.lower()} payment for unit "
        f"{b.unit.unit_no}, billing {b.billing_month}: applied ₱{amount_to_bill:,.2f}"
        + (f", excess ₱{excess_amount:,.2f} moved to advance." if excess_amount > 0 else "")
    )
    if excess_amount > 0:
        flash(
            f"Payment recorded (official receipt {receipt.receipt_no}). ₱{amount_to_bill:,.2f} applied to the SOA and "
            f"₱{excess_amount:,.2f} automatically added to Advance Payment.",
            "success"
        )
    else:
        flash(f"Payment recorded (official receipt {receipt.receipt_no}).", "success")
    return redirect(url_for("billing", month=b.billing_month, status="Paid" if b.status == "Paid" else "Partially Paid"))


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
    username=setting("smtp_username","").strip() or os.getenv("SMTP_USERNAME","").strip(); password=setting("smtp_password","") or os.getenv("SMTP_PASSWORD","")
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
    month=request.args.get("month") or datetime.now().strftime("%Y-%m")
    bills=Billing.query.join(Unit).filter(Billing.billing_month==month).order_by(Unit.unit_no).all()
    email_rows=[]; opted_in=0
    for b in bills:
        contacts=soa_email_contacts(b.unit); opted_in += len(contacts); email_rows.append({"bill":b,"contacts":contacts})
    return render_template("billing_email.html",month=month,email_rows=email_rows,opted_in=opted_in,smtp_configured=bool(setting("smtp_host","").strip() and setting("smtp_sender","").strip()))


@app.route("/billing/email/send", methods=["POST"])
@guarded
def send_billing_emails():
    month=request.form.get("month") or datetime.now().strftime("%Y-%m"); selected_ids=request.form.getlist("bill_ids"); send_all=request.form.get("send_all")=="1"
    all_bills=Billing.query.join(Unit).filter(Billing.billing_month==month).order_by(Unit.unit_no).all()
    bills=all_bills if send_all else [b for b in all_bills if str(b.id) in selected_ids]
    sent=failed=skipped=0; errors=[]
    for bill in bills:
        contacts=soa_email_contacts(bill.unit)
        if not contacts: skipped+=1; continue
        for contact in contacts:
            try:
                send_soa_email_to_contact(bill,contact); sent+=1
                audit(f"Emailed SOA for unit {bill.unit.unit_no}, billing {month} to {contact['email']} ({contact['type']})")
            except Exception as exc:
                failed+=1; errors.append(f"Unit {bill.unit.unit_no} / {contact['email']}: {exc}"); app.logger.exception("SOA email failed")
    if sent: flash(f"SOA email sending completed: {sent} email(s) sent.","success")
    if skipped: flash(f"{skipped} unit(s) skipped because no owner/tenant email was opted in.","warning")
    if failed: flash(f"{failed} email(s) failed. First error: {errors[0]}","danger")
    return redirect(url_for("billing_email",month=month))


@app.route("/billing/<int:bid>/email", methods=["POST"])
@guarded
def email_bill(bid):
    b=db.session.get(Billing,bid)
    if not b: flash("Bill not found.","danger"); return redirect(url_for("billing"))
    contacts=soa_email_contacts(b.unit)
    if not contacts:
        flash("No owner/tenant email address is opted in for this unit. Enable 'Receive SOA by Email' under Unit → Owners/Tenants.","warning")
        return redirect(url_for("billing_detail",bid=bid))
    sent=failed=0; errors=[]
    for contact in contacts:
        try: send_soa_email_to_contact(b,contact); sent+=1; audit(f"Emailed SOA for unit {b.unit.unit_no}, billing {b.billing_month} to {contact['email']} ({contact['type']})")
        except Exception as exc: failed+=1; errors.append(str(exc)); app.logger.exception("SOA email failed")
    if sent: flash(f"SOA emailed to {sent} opted-in recipient(s).","success")
    if failed: flash(f"{failed} email(s) failed. {errors[0]}","danger")
    return redirect(url_for("billing_detail",bid=bid))


@app.route("/billing/<int:bid>/edit-soa", methods=["POST"])
@guarded
def edit_soa(bid):
    b=db.session.get(Billing,bid)
    if not b: flash("Bill not found.","danger"); return redirect(url_for("billing"))
    before={k:getattr(b,k) for k in ["assessment","parking_dues","storage_dues","water","other","penalty","adjustment","previous_balance","due_date","soa_note"]}
    b.assessment=money(request.form.get("assessment")); b.parking_dues=money(request.form.get("parking_dues")); b.storage_dues=money(request.form.get("storage_dues")); b.water=money(request.form.get("water")); b.other=money(request.form.get("other")); b.penalty=money(request.form.get("penalty")); b.adjustment=money(request.form.get("adjustment")); b.previous_balance=max(money(request.form.get("previous_balance")),Decimal("0")); b.due_date=parse_date(request.form.get("due_date")) or b.due_date; b.soa_note=request.form.get("soa_note","").strip(); b.soa_manual_override=True; b.status=bill_status(b)
    db.session.commit()
    changes=[k for k in before if before[k]!=getattr(b,k)]
    audit(f"Manual SOA correction for unit {b.unit.unit_no}, billing {b.billing_month}; fields changed: {', '.join(changes) if changes else 'none'}")
    flash("SOA corrections saved and recorded in Audit Logs.","success")
    return redirect(url_for("billing_detail",bid=bid))


@app.route("/billing/<int:bid>/recalculate", methods=["POST"])
@guarded
def recalculate_soa(bid):
    """Re-price an issued bill from the unit's CURRENT rates (condo dues, parking, storage).
    Issued bills never change on their own; this is the explicit, audited way to correct one."""
    b = db.session.get(Billing, bid)
    if not b:
        flash("Bill not found.", "danger"); return redirect(url_for("billing"))
    if b.soa_manual_override:
        flash("This SOA was corrected by hand. Use Edit SOA to change its amounts.", "warning")
        return redirect(url_for("billing_detail", bid=bid))
    before = (money(b.assessment), money(b.parking_dues), money(b.storage_dues))
    b.assessment = Decimal(str(unit_dues(b.unit)))
    b.parking_dues = Decimal(str(parking_dues(b.unit)))
    b.storage_dues = Decimal(str(storage_dues(b.unit)))
    after = (money(b.assessment), money(b.parking_dues), money(b.storage_dues))
    g.pop("_bill_calc_cache", None)
    b.status = bill_status(b)
    db.session.commit()
    if before == after:
        flash("Recalculated: the current rates give the same amounts. Nothing changed.", "info")
    else:
        audit(f"Recalculated SOA for unit {b.unit.unit_no}, billing {b.billing_month} from current rates: "
              f"condo {before[0]:,.2f}->{after[0]:,.2f}, parking {before[1]:,.2f}->{after[1]:,.2f}, "
              f"storage {before[2]:,.2f}->{after[2]:,.2f}")
        flash("SOA recalculated from the current rates and recorded in the Audit Logs.", "success")
    return redirect(url_for("billing_detail", bid=bid))


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


@app.route("/receipts")
@guarded
def receipts():
    q = request.args.get("q", "").strip()
    start = parse_date(request.args.get("start")) or date.today().replace(day=1)
    end = parse_date(request.args.get("end")) or date.today()
    if end < start:
        start, end = end, start
    query = Receipt.query.join(Unit).filter(Receipt.received_date >= start, Receipt.received_date <= end)
    if q:
        query = query.filter(or_(Receipt.receipt_no.ilike(f"%{q}%"), Unit.unit_no.ilike(f"%{q}%"), Receipt.reference.ilike(f"%{q}%")))
    rows = query.order_by(Receipt.receipt_year.desc(), Receipt.receipt_seq.desc()).limit(500).all()
    total = sum((money(r.amount) for r in rows), Decimal("0"))
    by_method = {}
    for r in rows:
        by_method[r.payment_method] = by_method.get(r.payment_method, Decimal("0")) + money(r.amount)
    return render_template("receipts.html", rows=rows, q=q, start=start.isoformat(), end=end.isoformat(),
                           total=total, by_method=by_method)


@app.route("/receipts/<int:rid>")
@guarded
def receipt_detail(rid):
    r = db.session.get(Receipt, rid)
    if not r:
        flash("Receipt not found.", "danger"); return redirect(url_for("receipts"))
    contact = current_contact_for_unit(r.unit)
    name = getattr(contact, "tenant_name", None) or getattr(contact, "owner_name", None) or r.unit.owner_name or ""
    return render_template("receipt.html", r=r, received_from=name)


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

@app.route("/water", methods=["GET", "POST"])
@guarded
def water():
    month = request.args.get("month") or datetime.now().strftime("%Y-%m")

    if request.method == "POST":
        month = request.form.get("month") or month
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            flash("Invalid reading month.", "danger")
            return redirect(url_for("water"))
        try:
            unit_id = int(request.form.get("unit_id"))
        except (TypeError, ValueError):
            flash("Invalid unit.", "danger")
            return redirect(url_for("water", month=month))
        u = db.session.get(Unit, unit_id)
        if not u:
            flash("Unit not found.", "danger")
            return redirect(url_for("water"))

        reading = WaterReading.query.filter_by(
            unit_id=unit_id, reading_month=month
        ).first()

        if not reading:
            reading = WaterReading(unit_id=unit_id, reading_month=month)
            db.session.add(reading)

        reading.previous_reading = float(request.form.get("previous_reading") or 0)
        reading.current_reading = float(request.form.get("current_reading") or 0)
        reading.rate = float(request.form.get("rate") or setting_float("water_rate", 50))
        reading.reading_date = parse_date(request.form.get("reading_date")) or date.today()

        # Keep an already-generated bill synchronized with the corrected/current
        # water reading. This fixes the common workflow where water is entered
        # after monthly bills have already been generated.
        existing_bill = Billing.query.filter_by(unit_id=unit_id, billing_month=month).first()
        if existing_bill:
            existing_bill.water = money(reading.bill_amount)
            existing_bill.status = bill_status(existing_bill)

        db.session.commit()
        audit(f"Saved water reading for unit {u.unit_no} for {month}")
        flash("Water reading saved and the monthly bill was updated." if existing_bill else "Water reading saved.", "success")
        return redirect(url_for("water", month=month))

    selected_unit_id = request.args.get("unit_id", type=int)
    show_form = request.args.get("add") == "1"
    selected_previous = 0
    if show_form and selected_unit_id:
        prev = WaterReading.query.filter(
            WaterReading.unit_id == selected_unit_id,
            WaterReading.reading_month < month,
        ).order_by(WaterReading.reading_month.desc(), WaterReading.id.desc()).first()
        if prev:
            selected_previous = float(prev.current_reading or 0)

    readings = (
        WaterReading.query
        .options(selectinload(WaterReading.unit))
        .filter_by(reading_month=month)
        .order_by(WaterReading.id.desc())
        .all()
    )
    history = (
        WaterReading.query
        .options(selectinload(WaterReading.unit))
        .order_by(WaterReading.reading_month.desc(), WaterReading.unit_id, WaterReading.id.desc())
        .all()
    )

    # Do not load/calculate every Billing record just to render water status.
    # Water payment state is stored directly on WaterReading.
    outstanding_history = [
        r for r in history
        if water_history_status(r) in ("Unpaid", "Partially Paid", "Overdue")
    ]

    return render_template(
        "water.html",
        month=month,
        units=Unit.query.filter_by(active=True).order_by(Unit.unit_no).all(),
        readings=readings, history=history, bill_map={},
        outstanding_history=outstanding_history,
        show_form=show_form, selected_unit_id=selected_unit_id,
        selected_previous=selected_previous,
        today=date.today().isoformat(),
    )


@app.route("/water/<int:rid>/paid", methods=["POST"])
@guarded
def mark_water_paid(rid):
    reading = db.session.get(WaterReading, rid)
    if not reading:
        flash("Water reading not found.", "danger")
        return redirect(url_for("water"))

    total = money(reading.bill_amount)
    current_paid = money(getattr(reading, "paid_amount", 0))
    balance = max(total - current_paid, Decimal("0"))
    amount = money(request.form.get("amount"))
    payment_method = (request.form.get("payment_method") or "CASH").upper()
    payment_type = (request.form.get("payment_type") or "FULL").upper()
    reference = (request.form.get("reference") or "").strip()
    paid_date = parse_date(request.form.get("paid_date")) or date.today()

    if payment_method not in ("CASH", "CHECK", "ONLINE"):
        flash("Invalid payment method.", "danger")
        return redirect(url_for("water", month=reading.reading_month))
    if payment_type not in ("FULL", "PARTIAL"):
        flash("Invalid payment type.", "danger")
        return redirect(url_for("water", month=reading.reading_month))
    if amount <= 0:
        flash("Please enter a payment amount.", "danger")
        return redirect(url_for("water", month=reading.reading_month))
    amount = min(amount, balance)
    if amount <= 0:
        flash("This water reading has no remaining balance.", "warning")
        return redirect(url_for("water", month=reading.reading_month))
    if payment_method in ("CHECK", "ONLINE") and not reference:
        flash("Reference number is required for Check or Online payments.", "danger")
        return redirect(url_for("water", month=reading.reading_month))

    reading.paid_amount = current_paid + amount
    reading.payment_method = payment_method
    reading.payment_type = payment_type
    reading.payment_reference = reference
    reading.paid_date = paid_date
    reading.paid = money(reading.paid_amount) >= total

    receipt = issue_receipt(reading.unit_id, paid_date, payment_method, reference, "", [("water", reading, amount)])
    db.session.commit()
    audit(f"Issued {receipt.receipt_no} - Recorded {payment_type.lower()} {payment_method.lower()} water payment for unit {reading.unit.unit_no} for {reading.reading_month}")
    flash(f"Water payment recorded (official receipt {receipt.receipt_no}).", "success")
    return redirect(url_for("water", month=reading.reading_month))


@app.route("/water/<int:rid>/edit", methods=["GET", "POST"])
@guarded
def edit_water(rid):
    reading = db.session.get(WaterReading, rid)
    if not reading:
        flash("Water reading not found.", "danger")
        return redirect(url_for("water"))

    if request.method == "POST":
        try:
            previous_reading = float(request.form.get("previous_reading") or 0)
            current_reading = float(request.form.get("current_reading") or 0)
            rate = float(request.form.get("rate") or setting_float("water_rate", 50))
        except (TypeError, ValueError):
            flash("Please enter valid numeric water readings and rate.", "danger")
            return redirect(url_for("edit_water", rid=rid))

        if previous_reading < 0 or current_reading < 0 or rate < 0:
            flash("Water readings and rate cannot be negative.", "danger")
            return redirect(url_for("edit_water", rid=rid))

        reading.previous_reading = previous_reading
        reading.current_reading = current_reading
        reading.rate = rate
        reading.reading_date = parse_date(request.form.get("reading_date")) or date.today()

        existing_bill = Billing.query.filter_by(unit_id=reading.unit_id, billing_month=reading.reading_month).first()
        if existing_bill:
            existing_bill.water = money(reading.bill_amount)
            existing_bill.status = bill_status(existing_bill)

        db.session.commit()
        audit(f"Edited water reading for unit {reading.unit.unit_no} for {reading.reading_month}")
        flash("Water reading updated and the monthly bill was synchronized." if existing_bill else "Water reading updated.", "success")
        return redirect(url_for("water", month=reading.reading_month))

    return render_template("water_edit.html", reading=reading)



# -----------------------------
# Employees
# -----------------------------
@app.route("/employees", methods=["GET", "POST"])
@guarded
def employees():
    if request.method == "POST":
        emp_no = request.form.get("employee_no", "").strip()
        if not emp_no or not request.form.get("full_name", "").strip():
            flash("Employee No. and Full Name are required.", "danger")
            return redirect(url_for("employees"))
        if Employee.query.filter_by(employee_no=emp_no).first():
            flash("Employee No. already exists.", "danger")
            return redirect(url_for("employees"))
        db.session.add(Employee(employee_no=emp_no, full_name=request.form.get("full_name").strip(), position=request.form.get("position",""),
            department=request.form.get("department",""), employment_status=request.form.get("employment_status","Active"),
            contact_no=request.form.get("contact_no",""), email=request.form.get("email",""), date_hired=parse_date(request.form.get("date_hired")),
            monthly_salary=money(request.form.get("monthly_salary")), notes=request.form.get("notes","")))
        db.session.commit(); audit(f"Added employee {emp_no}"); flash("Employee saved.", "success")
        return redirect(url_for("employees"))
    return render_template("employees.html", employees=Employee.query.order_by(Employee.full_name).all())


@app.route("/employee/<int:eid>/delete", methods=["POST"])
@guarded
def delete_employee(eid):
    e=db.session.get(Employee,eid)
    if e:
        db.session.delete(e); db.session.commit(); audit(f"Deleted employee {e.employee_no}")
    return redirect(url_for("employees"))



# -----------------------------
# Expenses / Gate Pass
# -----------------------------
@app.route("/expenses", methods=["GET", "POST"])
@guarded
def expenses():
    if request.method == "POST":
        db.session.add(
            Expense(
                expense_date=parse_date(request.form.get("expense_date")) or date.today(),
                category=request.form.get("category", ""),
                description=request.form.get("description", ""),
                amount=money(request.form.get("amount")),
            )
        )
        db.session.commit()
        audit("Added expense")
        flash("Expense saved.", "success")
        return redirect(url_for("expenses"))

    return render_template("expenses.html", expenses=Expense.query.order_by(Expense.id.desc()).all())


@app.route("/move-certificate", methods=["GET", "POST"])
@guarded
def move_certificate():
    units_list = Unit.query.filter_by(active=True).order_by(Unit.unit_no).all()
    selected_unit = None
    people = []
    certificate = None

    if request.method == "GET" and request.args.get("certificate_id", type=int):
        record = db.session.get(MoveCertificate, request.args.get("certificate_id", type=int))
        if record:
            selected_unit = record.unit
            if record.person_type == "Owner":
                people = Owner.query.filter_by(unit_id=selected_unit.id).order_by(Owner.status.desc(), Owner.id.desc()).all()
                person = db.session.get(Owner, record.person_id)
            else:
                people = Tenant.query.filter_by(unit_id=selected_unit.id).order_by(Tenant.status.desc(), Tenant.id.desc()).all()
                person = db.session.get(Tenant, record.person_id)
            if person:
                certificate = {"id": record.id, "certificate_no": record.certificate_no, "move_type": record.move_type, "person_type": record.person_type, "person": person, "unit": selected_unit, "certificate_date": record.certificate_date, "move_date": record.move_date, "issued_by": record.issued_by}

    if request.method == "POST":
        uid = request.form.get("unit_id", type=int)
        move_type = request.form.get("move_type", "Move In")
        person_type = request.form.get("person_type", "Tenant")
        person_id = request.form.get("person_id", type=int)
        cert_date = parse_date(request.form.get("certificate_date")) or date.today()
        selected_unit = db.session.get(Unit, uid) if uid else None

        if selected_unit:
            if person_type == "Owner":
                people = Owner.query.filter_by(unit_id=selected_unit.id).order_by(Owner.status.desc(), Owner.id.desc()).all()
            else:
                people = Tenant.query.filter_by(unit_id=selected_unit.id).order_by(Tenant.status.desc(), Tenant.id.desc()).all()
            person = next((x for x in people if x.id == person_id), None)
            if person:
                person_name = person.owner_name if person_type == "Owner" else person.tenant_name
                move_date = person.move_in if move_type == "Move In" else person.move_out

                record = MoveCertificate(
                    certificate_no="PENDING",
                    move_type=move_type,
                    person_type=person_type,
                    unit_id=selected_unit.id,
                    person_id=person.id,
                    person_name=person_name,
                    move_date=move_date,
                    certificate_date=cert_date,
                    issued_by=current_user.username,
                )
                db.session.add(record)
                db.session.flush()
                record.certificate_no = f"CL9-{cert_date.year}-{record.id:05d}"
                db.session.commit()

                certificate = {
                    "id": record.id,
                    "certificate_no": record.certificate_no,
                    "move_type": move_type,
                    "person_type": person_type,
                    "person": person,
                    "unit": selected_unit,
                    "certificate_date": cert_date,
                    "move_date": move_date,
                    "issued_by": current_user.username,
                }
                audit(f"Generated {move_type} certificate {record.certificate_no} for {person_type.lower()} {person.id} in unit {selected_unit.unit_no}")

    return render_template(
        "move_certificate.html",
        units=units_list,
        selected_unit=selected_unit,
        people=people,
        certificate=certificate,
        today=date.today().isoformat(),
    )


@app.route("/move-certificates")
@guarded
def move_certificates():
    certificates = MoveCertificate.query.order_by(MoveCertificate.id.desc()).limit(500).all()
    return render_template("move_certificates.html", certificates=certificates)


@app.route("/gate-pass", methods=["GET", "POST"])
@guarded
def gate_pass():
    if request.method == "POST":
        db.session.add(
            GatePass(
                pass_date=parse_date(request.form.get("pass_date")) or date.today(),
                unit_no=request.form.get("unit_no", ""),
                visitor_name=request.form.get("visitor_name", ""),
                purpose=request.form.get("purpose", ""),
                status="Issued",
            )
        )
        db.session.commit()
        audit("Created gate pass")
        flash("Gate pass recorded.", "success")
        return redirect(url_for("gate_pass"))

    return render_template("gate_pass.html", passes=GatePass.query.order_by(GatePass.id.desc()).all())



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


@app.route("/database/import", methods=["POST"])
@guarded
def database_import():
    """Fast, safer Excel importer.

    The previous importer performed a database lookup for nearly every Excel row.
    With hundreds/thousands of rows that created thousands of SELECT statements.
    This version reads the workbook in streaming mode, preloads existing records
    once per table, updates/inserts in memory, then commits one transaction.
    """
    if load_workbook is None:
        return "Install openpyxl first.", 500

    f = request.files.get("excel_file")
    if not f or not f.filename.lower().endswith((".xlsx", ".xlsm")):
        flash("Please select an Excel .xlsx file.", "danger")
        return redirect(url_for("settings"))

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
        for values in iterator:
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
            obj.owner_name = val(r, "owner_name", "OwnerName", default=obj.owner_name)
            obj.contact_no = val(r, "contact_no", "OwnerContact", "ContactNo", default=obj.contact_no)
            obj.email = val(r, "email", "OwnerEmail", default=obj.email)
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
        flash(f"Excel import completed successfully using the optimized importer.{backup_note} {imported}", "success")
    except Exception as ex:
        db.session.rollback()
        flash(f"Excel import failed. No changes were committed: {ex}", "danger")
    finally:
        if wb is not None:
            try:
                wb.close()
            except Exception:
                pass
    return redirect(url_for("settings"))


# -----------------------------
# Reports
# -----------------------------
@app.route("/reports")
@guarded
def reports():
    # Reports are transaction-date based. Daily/weekly/monthly/yearly are
    # convenient presets, while custom dates allow accounting staff to run
    # any required period.
    today = date.today()
    period = (request.args.get("period") or "monthly").lower()

    if period == "daily":
        start_date = end_date = today
        period_label = today.strftime("%B %d, %Y")
    elif period == "weekly":
        start_date = today.fromordinal(today.toordinal() - today.weekday())
        end_date = start_date.fromordinal(start_date.toordinal() + 6)
        period_label = f"{start_date.strftime('%b %d, %Y')} - {end_date.strftime('%b %d, %Y')}"
    elif period == "yearly":
        start_date = date(today.year, 1, 1)
        end_date = date(today.year, 12, 31)
        period_label = str(today.year)
    elif period == "custom":
        start_date = parse_date(request.args.get("start_date")) or date(today.year, today.month, 1)
        end_date = parse_date(request.args.get("end_date")) or today
        if end_date < start_date:
            start_date, end_date = end_date, start_date
        period_label = f"{start_date.strftime('%b %d, %Y')} - {end_date.strftime('%b %d, %Y')}"
    else:
        period = "monthly"
        start_date = date(today.year, today.month, 1)
        if today.month == 12:
            end_date = date(today.year, 12, 31)
        else:
            end_date = date(today.year, today.month + 1, 1).fromordinal(date(today.year, today.month + 1, 1).toordinal() - 1)
        period_label = today.strftime("%B %Y")

    end_dt = datetime.combine(end_date, datetime.max.time())
    start_dt = datetime.combine(start_date, datetime.min.time())

    bills = Billing.query.filter(Billing.created_at >= start_dt, Billing.created_at <= end_dt).all()
    payments = Payment.query.filter(Payment.payment_date >= start_date, Payment.payment_date <= end_date).all()
    water_paid = WaterReading.query.filter(
        WaterReading.paid_date >= start_date, WaterReading.paid_date <= end_date,
        WaterReading.paid_amount > 0
    ).all()
    advances = AdvancePayment.query.filter(AdvancePayment.payment_date >= start_date, AdvancePayment.payment_date <= end_date).all()
    expenses = Expense.query.filter(Expense.expense_date >= start_date, Expense.expense_date <= end_date).all()

    def dec_sum(items, attr):
        return sum((money(getattr(x, attr, 0)) for x in items), Decimal("0"))

    billed_total = sum((bill_total(b) for b in bills), Decimal("0"))
    billing_collections = dec_sum(payments, "amount")
    water_collections = dec_sum(water_paid, "paid_amount")
    advance_collections = dec_sum(advances, "amount")
    total_collected = billing_collections + water_collections + advance_collections
    total_expenses = dec_sum(expenses, "amount")
    net_cash_flow = total_collected - total_expenses

    method_totals = {
        "CASH": Decimal("0"),
        "CHECK": Decimal("0"),
        "ONLINE": Decimal("0"),
    }
    for payment in payments:
        method = (payment.payment_method or "CASH").upper()
        method_totals[method] = method_totals.get(method, Decimal("0")) + money(payment.amount)
    for reading in water_paid:
        method = (reading.payment_method or "CASH").upper()
        method_totals[method] = method_totals.get(method, Decimal("0")) + money(reading.paid_amount)
    for advance in advances:
        method = (advance.payment_method or "CASH").upper()
        method_totals[method] = method_totals.get(method, Decimal("0")) + money(advance.amount)

    charge_totals = {
        "Condo Dues": sum((money(b.assessment) for b in bills), Decimal("0")),
        "Water": sum((money(b.water) for b in bills), Decimal("0")),
        "Parking": sum((money(b.parking_dues) for b in bills), Decimal("0")),
        "Penalty": sum((money(b.penalty) for b in bills), Decimal("0")),
        "Previous Balance": sum((money(b.previous_balance) for b in bills), Decimal("0")),
    }

    outstanding = sum((bill_balance(b) for b in Billing.query.all()), Decimal("0"))
    return render_template(
        "reports.html",
        units=Unit.query.filter_by(active=True).count(),
        tenants=Tenant.query.filter_by(status="Current").count(),
        parking=Unit.query.filter(Unit.active.is_(True), Unit.assigned_parking_unit_id.isnot(None)).count(),
        outstanding=outstanding,
        period=period, start_date=start_date, end_date=end_date, period_label=period_label,
        bills=bills, payments=payments, water_paid=water_paid, advances=advances, expenses=expenses,
        billed_total=billed_total, billing_collections=billing_collections, water_collections=water_collections,
        advance_collections=advance_collections, total_collected=total_collected, total_expenses=total_expenses,
        net_cash_flow=net_cash_flow, method_totals=method_totals, charge_totals=charge_totals,
    )


@app.route("/reports/export.xlsx")
@guarded
def reports_export():
    # Reuse the report calculations from the report page by invoking the same
    # endpoint internally is unnecessary; calculate a compact transaction export here.
    start_date = parse_date(request.args.get("start_date")) or date.today()
    end_date = parse_date(request.args.get("end_date")) or start_date
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    payments = Payment.query.filter(Payment.payment_date >= start_date, Payment.payment_date <= end_date).all()
    water_paid = WaterReading.query.filter(WaterReading.paid_date >= start_date, WaterReading.paid_date <= end_date, WaterReading.paid_amount > 0).all()
    advances = AdvancePayment.query.filter(AdvancePayment.payment_date >= start_date, AdvancePayment.payment_date <= end_date).all()
    expenses = Expense.query.filter(Expense.expense_date >= start_date, Expense.expense_date <= end_date).all()

    if Workbook is None:
        flash("Excel export requires openpyxl.", "danger")
        return redirect(url_for("reports", period="custom", start_date=start_date, end_date=end_date))

    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws.append(["CITYLAND 9 CONDO SYSTEM 2026", ""])
    ws.append(["Report Period", f"{start_date} to {end_date}"])
    ws.append([])
    ws.append(["Transaction Type", "Date", "Unit", "Description", "Payment Method", "Reference", "Amount"])
    for p in payments:
        ws.append(["Billing Payment", p.payment_date, p.billing.unit.unit_no if p.billing and p.billing.unit else "", f"Billing {p.billing.billing_month}" if p.billing else "", p.payment_method or "CASH", p.reference or "", float(money(p.amount))])
    for r in water_paid:
        ws.append(["Water Payment", r.paid_date, r.unit.unit_no if r.unit else "", f"Water {r.reading_month}", r.payment_method or "CASH", r.payment_reference or "", float(money(r.paid_amount))])
    for a in advances:
        ws.append(["Advance Payment", a.payment_date, a.unit.unit_no if a.unit else "", f"Advance {a.start_month} ({a.coverage_months} month(s))", a.payment_method or "CASH", a.reference or "", float(money(a.amount))])
    for e in expenses:
        ws.append(["Expense", e.expense_date, "", e.description or e.category or "", "", "", float(money(e.amount)) * -1])
    for sheet in wb.worksheets:
        for cell in sheet[1]:
            cell.font = cell.font.copy(bold=True)
        for col in sheet.columns:
            letter = col[0].column_letter
            sheet.column_dimensions[letter].width = min(max(max(len(str(c.value or "")) for c in col) + 2, 12), 32)
    out = BytesIO()
    wb.save(out); out.seek(0)
    filename = f"CityLand9_Report_{start_date}_{end_date}.xlsx"
    return send_file(out, as_attachment=True, download_name=filename, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# -----------------------------
# Settings / Rates & Rules
# -----------------------------
@app.route("/settings", methods=["GET", "POST"])
@guarded
def settings():
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
        "storage_in_total_from": setting("storage_in_total_from", "9999-12"),
        "penalty_rate": "10",
        "penalty_rate_per_sqm": "10",
        "penalty_day": "8",
        "penalty_include_condo": "1",
        "penalty_include_parking": "0",
        "penalty_include_storage": "0",
        "penalty_include_water": "0",
        "online_payment_url": "",
        "online_payment_instructions": "Please use the online payment link or scan the Payment QR shown on this SOA.",
        "smtp_host": "",
        "smtp_port": "587",
        "smtp_sender": "",
        "smtp_username": "",
        "smtp_password": "",
    }

    if request.method == "POST":
        checkbox_keys = {
            "penalty_include_condo", "penalty_include_parking",
            "penalty_include_storage", "penalty_include_water",
        }
        for key, default in defaults.items():
            row = Setting.query.filter_by(key=key).first() or Setting(key=key)
            if key in checkbox_keys:
                row.value = "1" if request.form.get(key) == "1" else "0"
            elif key == "storage_in_total_from":
                value = (request.form.get(key) or "").strip()
                if re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
                    row.value = value
                else:
                    if value:
                        flash("Storage start month must look like 2026-10; the previous value was kept.", "warning")
                    row.value = row.value or default
            else:
                row.value = request.form.get(key, default)
            db.session.add(row)
        db.session.commit()
        audit("Updated Rates & Rules")
        flash("Rates & Rules saved successfully.", "success")
        return redirect(url_for("settings"))

    vals = {k: setting(k, v) for k, v in defaults.items()}
    return render_template("settings.html", settings=vals, **vals)


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
        if len(new_password) < 8:
            flash("New password must be at least 8 characters.", "danger")
            return redirect(url_for("change_password"))
        if new_password != confirm_password:
            flash("New password and confirmation do not match.", "danger")
            return redirect(url_for("change_password"))
        if check_password_hash(u.password_hash, new_password):
            flash("New password must be different from the current password.", "danger")
            return redirect(url_for("change_password"))

        u.password_hash = generate_password_hash(new_password)
        db.session.commit()
        audit(f"Changed password for user {u.username}")
        flash("Your password has been changed successfully.", "success")
        return redirect(url_for("index"))

    return render_template("change_password.html")


@app.route("/users/<int:user_id>/reset-password", methods=["POST"])
@guarded
def reset_user_password(user_id):
    target = db.session.get(User, user_id)
    if not target:
        flash("User not found.", "danger")
        return redirect(url_for("users"))

    # Superadmin may manage passwords only for accounts below Superadmin hierarchy.
    # A Superadmin account must never be reset from another user's management form.
    if ROLE_LEVEL.get(target.role, 0) >= ROLE_LEVEL["super_admin"]:
        flash("Superadmin passwords cannot be changed from User Management. Use the account's own Change Password option.", "danger")
        return redirect(url_for("users"))

    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")
    if len(new_password) < 8:
        flash("Reset password must be at least 8 characters.", "danger")
        return redirect(url_for("users"))
    if new_password != confirm_password:
        flash("Reset password and confirmation do not match.", "danger")
        return redirect(url_for("users"))

    target.password_hash = generate_password_hash(new_password)
    db.session.commit()
    audit(f"Reset password for user {target.username}")
    flash(f"Password for {target.username} has been reset successfully.", "success")
    return redirect(url_for("users"))


# -----------------------------
# Users / Audit
# -----------------------------
@app.route("/users/<int:user_id>/delete", methods=["POST"])
@guarded
def delete_user(user_id):
    actor = current_user()
    target = db.session.get(User, user_id)
    if not target:
        flash("User not found.", "danger")
        return redirect(url_for("users"))

    # Never allow the currently signed-in Superadmin to delete their own account.
    if actor and target.id == actor.id:
        flash("You cannot delete the Superadmin account that is currently logged in.", "danger")
        return redirect(url_for("users"))

    # Keep at least one Superadmin account available for system recovery/access.
    if target.role == "super_admin" and User.query.filter_by(role="super_admin").count() <= 1:
        flash("The last Superadmin account cannot be deleted.", "danger")
        return redirect(url_for("users"))

    username = target.username
    role = target.role
    db.session.delete(target)
    db.session.commit()
    audit(f"Deleted user {username} ({role})")
    flash(f"User {username} was deleted.", "success")
    return redirect(url_for("users"))


@app.route("/users", methods=["GET", "POST"])
@guarded
def users():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            flash("Username and password are required.", "danger")
            return redirect(url_for("users"))

        if User.query.filter_by(username=username).first():
            flash("Username already exists.", "danger")
            return redirect(url_for("users"))

        role = request.form.get("role", "staff")
        if role not in ALL_ROLES:
            flash("Please choose a valid role.", "danger")
            return redirect(url_for("users"))

        db.session.add(
            User(
                username=username,
                password_hash=generate_password_hash(password),
                role=role,
                active=True,
            )
        )
        db.session.commit()
        audit(f"Created user {username}")
        flash("User created.", "success")
        return redirect(url_for("users"))

    return render_template("users.html", users=User.query.order_by(User.username).all())


@app.route("/audit")
@guarded
def audit_logs():
    return render_template(
        "audit.html",
        logs=AuditLog.query.order_by(AuditLog.id.desc()).limit(500).all(),
    )


# -----------------------------
# V10.63 Community / Resident Services
# -----------------------------
@app.route("/resident-users", methods=["GET", "POST"])
@guarded
def resident_users():
    if request.method == "POST":
        username=request.form.get("username", "").strip()
        password=request.form.get("password", "")
        unit_id=request.form.get("unit_id", type=int)
        person_type=request.form.get("person_type", "Owner")
        person_id=request.form.get("person_id", type=int) or None
        display_name=request.form.get("display_name", "").strip()
        if not username or not password or not unit_id or not display_name:
            flash("Username, password, unit and resident name are required.", "danger")
            return redirect(url_for("resident_users"))
        if User.query.filter_by(username=username).first():
            flash("That username already exists.", "danger")
            return redirect(url_for("resident_users"))
        user=User(username=username, password_hash=generate_password_hash(password), role="resident", active=True)
        db.session.add(user); db.session.flush()
        db.session.add(ResidentProfile(user_id=user.id, unit_id=unit_id, person_type=person_type, person_id=person_id, display_name=display_name))
        db.session.commit(); audit(f"Created resident portal user {username} for unit {unit_id}")
        flash("Resident portal account created.", "success")
        return redirect(url_for("resident_users"))
    units=Unit.query.filter_by(active=True).order_by(Unit.unit_no).all()
    profiles=ResidentProfile.query.order_by(ResidentProfile.id.desc()).all()
    return render_template("resident_users.html", units=units, profiles=profiles)


@app.route("/portal")
@guarded
def resident_portal():
    profile = getattr(current_user(), "resident_profile", None)
    if current_user().role == "resident" and not profile:
        flash("Your resident profile is not yet linked to a unit. Please contact the administrator.", "warning")
        return redirect(url_for("logout"))
    unit_id = profile.unit_id if profile else request.args.get("unit_id", type=int)
    recent_announcements = Announcement.query.filter_by(published=True).order_by(Announcement.publish_date.desc(), Announcement.id.desc()).limit(10).all()
    tickets_q = MaintenanceTicket.query.order_by(MaintenanceTicket.id.desc())
    if unit_id:
        tickets_q = tickets_q.filter(MaintenanceTicket.unit_id == unit_id)
    return render_template("resident_portal.html", profile=profile, unit_id=unit_id, announcements=recent_announcements, tickets=tickets_q.limit(10).all())

@app.route("/announcements", methods=["GET", "POST"])
@guarded
def announcements():
    if request.method == "POST":
        if current_user().role == "resident":
            flash("Residents cannot publish announcements.", "danger"); return redirect(url_for("announcements"))
        row = Announcement(title=request.form.get("title", "").strip(), message=request.form.get("message", "").strip(), audience=request.form.get("audience", "residents"), published=request.form.get("published") == "1", created_by=current_user().username)
        if not row.title or not row.message:
            flash("Title and message are required.", "danger"); return redirect(url_for("announcements"))
        db.session.add(row); db.session.commit(); audit(f"Created announcement: {row.title}")
        flash("Announcement published.", "success"); return redirect(url_for("announcements"))
    rows = Announcement.query.order_by(Announcement.publish_date.desc(), Announcement.id.desc()).all()
    return render_template("announcements.html", rows=rows)

@app.route("/maintenance", methods=["GET", "POST"])
@guarded
def maintenance():
    profile = getattr(current_user(), "resident_profile", None)
    if current_user().role == RESIDENT and not profile:
        # Without a linked unit a resident would otherwise see (and file for) every unit.
        flash("Your resident profile is not yet linked to a unit. Please contact the administrator.", "warning")
        return redirect(url_for("logout"))
    if request.method == "POST":
        unit_id = profile.unit_id if profile else request.form.get("unit_id", type=int)
        row = MaintenanceTicket(ticket_no=f"MT-{datetime.now().strftime('%Y%m%d%H%M%S')}-{MaintenanceTicket.query.count()+1:04d}", unit_id=unit_id, resident_profile_id=profile.id if profile else None, category=request.form.get("category", "General"), title=request.form.get("title", "").strip(), description=request.form.get("description", "").strip(), priority=request.form.get("priority", "Normal"))
        if not row.title or not row.description:
            flash("Title and description are required.", "danger"); return redirect(url_for("maintenance"))
        db.session.add(row); db.session.commit(); audit(f"Created maintenance ticket {row.ticket_no}")
        flash(f"Maintenance ticket {row.ticket_no} created.", "success"); return redirect(url_for("maintenance"))
    q = MaintenanceTicket.query.order_by(MaintenanceTicket.id.desc())
    if current_user().role == "resident" and profile: q = q.filter(MaintenanceTicket.unit_id == profile.unit_id)
    rows = q.limit(300).all()
    units = Unit.query.filter_by(active=True).order_by(Unit.unit_no).all() if current_user().role != "resident" else []
    vendors = Vendor.query.filter_by(status="Active").order_by(Vendor.vendor_name).all()
    return render_template("maintenance.html", rows=rows, units=units, vendors=vendors, profile=profile)

@app.route("/maintenance/<int:ticket_id>/update", methods=["POST"])
@guarded
def maintenance_update(ticket_id):
    row = db.session.get(MaintenanceTicket, ticket_id)
    if not row: flash("Maintenance ticket not found.", "danger"); return redirect(url_for("maintenance"))
    row.status=request.form.get("status", row.status); row.priority=request.form.get("priority", row.priority); row.assigned_to=request.form.get("assigned_to", row.assigned_to); row.vendor_id=request.form.get("vendor_id", type=int) or None; row.resolution=request.form.get("resolution", row.resolution)
    db.session.commit(); audit(f"Updated maintenance ticket {row.ticket_no}"); flash("Maintenance ticket updated.", "success"); return redirect(url_for("maintenance"))

@app.route("/vendors", methods=["GET", "POST"])
@guarded
def vendors():
    if request.method == "POST":
        row=Vendor(vendor_name=request.form.get("vendor_name", "").strip(), service_type=request.form.get("service_type", "").strip(), contact_person=request.form.get("contact_person", "").strip(), contact_no=request.form.get("contact_no", "").strip(), email=request.form.get("email", "").strip(), address=request.form.get("address", "").strip(), notes=request.form.get("notes", "").strip())
        if not row.vendor_name: flash("Vendor name is required.", "danger"); return redirect(url_for("vendors"))
        db.session.add(row); db.session.commit(); audit(f"Created vendor: {row.vendor_name}"); flash("Vendor saved.", "success"); return redirect(url_for("vendors"))
    return render_template("vendors.html", rows=Vendor.query.order_by(Vendor.vendor_name).all())

@app.route("/documents", methods=["GET", "POST"])
@guarded
def documents():
    profile=getattr(current_user(), "resident_profile", None)
    if request.method == "POST":
        if current_user().role == "resident": flash("Residents cannot publish documents.", "danger"); return redirect(url_for("documents"))
        row=DocumentRecord(title=request.form.get("title", "").strip(), category=request.form.get("category", "General"), description=request.form.get("description", "").strip(), file_name=request.form.get("file_name", "").strip(), file_path=request.form.get("file_path", "").strip(), audience=request.form.get("audience", "admin"), unit_id=request.form.get("unit_id", type=int) or None, uploaded_by=current_user().username)
        if not row.title: flash("Document title is required.", "danger"); return redirect(url_for("documents"))
        db.session.add(row); db.session.commit(); audit(f"Added document record: {row.title}"); flash("Document record saved.", "success"); return redirect(url_for("documents"))
    q=DocumentRecord.query.filter_by(active=True).order_by(DocumentRecord.created_at.desc())
    if current_user().role == "resident": q=q.filter((DocumentRecord.audience == "residents") | ((DocumentRecord.audience == "unit") & (DocumentRecord.unit_id == (profile.unit_id if profile else -1))))
    return render_template("documents.html", rows=q.all(), units=Unit.query.filter_by(active=True).order_by(Unit.unit_no).all() if current_user().role != "resident" else [], profile=profile)

# -----------------------------
# Database initialization / migration
# -----------------------------
def init_db():
    """Create missing tables and safely upgrade the bundled SQLite schema."""
    with app.app_context():
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
                    "parking_rate_per_sqm": "FLOAT DEFAULT 0",
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

        # Enforce the condominium billing rule: every statement is due on the 8th.
        for b in Billing.query.all():
            if b.billing_month:
                try: b.due_date = date.fromisoformat(b.billing_month + "-08")
                except ValueError: pass
        db.session.commit()

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

        if not User.query.first():
            db.session.add(User(
                username="superadmin",
                password_hash=generate_password_hash("admin123"),
                role="super_admin",
                active=True,
            ))
            print("Created default account: superadmin / admin123")

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
        EmployeeAttendance.__table__.create(db.engine, checkfirst=True)
        EmployeePayroll.__table__.create(db.engine, checkfirst=True)
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
    _v1039_ensure_tables()
    employees = Employee.query.order_by(Employee.id).all()
    selected_date = request.values.get("date") or _dt_date.today().isoformat()
    try:
        day = _dt_date.fromisoformat(selected_date)
    except Exception:
        day = _dt_date.today()
    if request.method == "POST":
        for emp in employees:
            sid = str(emp.id)
            status = (request.form.get("status_"+sid) or "PRESENT").upper()
            tin = request.form.get("time_in_"+sid) or None
            tout = request.form.get("time_out_"+sid) or None
            remarks = request.form.get("remarks_"+sid) or None
            row = EmployeeAttendance.query.filter_by(employee_id=emp.id, attendance_date=day).first()
            if not row:
                row = EmployeeAttendance(employee_id=emp.id, attendance_date=day)
                db.session.add(row)
            row.status = status
            row.remarks = remarks
            try:
                row.time_in = _dt_datetime.strptime(tin, "%H:%M").time() if tin else None
                row.time_out = _dt_datetime.strptime(tout, "%H:%M").time() if tout else None
            except Exception:
                pass
        db.session.commit()
        flash("Attendance saved successfully.", "success")
        return redirect(url_for("employee_attendance", date=day.isoformat()))
    records = {r.employee_id:r for r in EmployeeAttendance.query.filter_by(attendance_date=day).all()}
    return render_template("employee_attendance.html", employees=employees, records=records, selected_date=day.isoformat())

@app.route("/employees/attendance/history/<int:eid>")
@guarded
def employee_attendance_history(eid):
    _v1039_ensure_tables()
    emp = db.session.get(Employee, eid)
    if not emp:
        abort(404)
    today = _dt_date.today()
    default_start = today.replace(day=1)
    start_s = request.args.get("start") or default_start.isoformat()
    end_s = request.args.get("end") or today.isoformat()
    try:
        start = _dt_date.fromisoformat(start_s)
        end = _dt_date.fromisoformat(end_s)
    except Exception:
        start, end = default_start, today
    if end < start:
        start, end = end, start
    rows = EmployeeAttendance.query.filter(
        EmployeeAttendance.employee_id == eid,
        EmployeeAttendance.attendance_date >= start,
        EmployeeAttendance.attendance_date <= end
    ).order_by(EmployeeAttendance.attendance_date.desc()).all()
    counts = {}
    for r in rows:
        key = str(r.status or "PRESENT").upper()
        counts[key] = counts.get(key, 0) + 1
    return render_template("employee_attendance_history.html", employee=emp, rows=rows, start=start.isoformat(), end=end.isoformat(), counts=counts)

@app.route("/employees/payroll", methods=["GET","POST"])
@guarded
def employee_payroll():
    _v1039_ensure_tables()
    employees = Employee.query.order_by(Employee.id).all()
    today = _dt_date.today()
    start_s = request.values.get("start") or today.replace(day=1).isoformat()
    end_s = request.values.get("end") or today.isoformat()
    try:
        start = _dt_date.fromisoformat(start_s); end = _dt_date.fromisoformat(end_s)
    except Exception:
        start = today.replace(day=1); end = today
    if request.method == "POST":
        emp_id = int(request.form.get("employee_id"))
        emp = db.session.get(Employee, emp_id)
        if not emp:
            flash("Employee not found.", "error")
            return redirect(url_for("employee_payroll", start=start.isoformat(), end=end.isoformat()))
        approved_ot = sum(_v1039_money(x.amount) for x in EmployeeOvertime.query.filter(
            EmployeeOvertime.employee_id==emp.id, EmployeeOvertime.ot_date>=start, EmployeeOvertime.ot_date<=end, EmployeeOvertime.status=="APPROVED"
        ).all()) if "EmployeeOvertime" in globals() else 0
        ot_input = request.form.get("overtime_pay")
        if ot_input in (None, ""):
            ot_input = approved_ot
        basic, absence_ded, late_ded, net = _v1039_calc_payroll(
            emp, start, end, request.form.get("basic_salary"), ot_input, request.form.get("allowances"), request.form.get("deductions")
        )
        # Active recurring loans/deductions are included automatically for the period.
        recurring_loans = EmployeeHRLoan.query.filter_by(employee_id=emp.id, status="ACTIVE").all() if "EmployeeHRLoan" in globals() else []
        loan_deduction = sum(min(_v1039_money(x.monthly_deduction), _v1039_money(x.balance)) for x in recurring_loans)
        manual_deduction = _v1039_money(request.form.get("deductions"))
        total_other_deduction = manual_deduction + loan_deduction
        p = EmployeePayroll(
            employee_id=emp.id, period_start=start, period_end=end,
            basic_salary=basic, overtime_pay=_v1039_money(ot_input),
            allowances=_v1039_money(request.form.get("allowances")),
            deductions=total_other_deduction,
            absences=absence_ded, late_undertime=late_ded, net_pay=net,
            status=request.form.get("status") or "DRAFT",
            remarks=request.form.get("remarks")
        )
        db.session.add(p); db.session.commit()
        # Automatically prepare the Philippine statutory breakdown for the payroll.
        try:
            EmployeePayrollStatutory.__table__.create(db.engine,checkfirst=True)
            gross=_v1039_money(p.basic_salary)+_v1039_money(p.overtime_pay)+_v1039_money(p.allowances)
            sc=_v1040_calc(gross,p.basic_salary,p.deductions,p.absences,p.late_undertime)
            row=EmployeePayrollStatutory(payroll_id=p.id)
            for k,v in sc.items(): setattr(row,k,_v1039_money(v))
            db.session.add(row); db.session.flush()
            p.net_pay=sc["net_pay_after_statutory"]
            # Reduce active loan balances by the amount actually deducted this payroll.
            remaining_loan_deduction = loan_deduction
            for loan in recurring_loans:
                take=min(_v1039_money(loan.monthly_deduction), _v1039_money(loan.balance), remaining_loan_deduction)
                if take>0:
                    loan.balance=max(_v1039_money(loan.balance)-take,0)
                    if loan.balance<=0: loan.status="PAID"
                    remaining_loan_deduction-=take
            db.session.commit()
        except Exception:
            db.session.rollback()
        flash("Payroll record generated with statutory payroll breakdown.", "success")
        return redirect(url_for("employee_payroll", start=start.isoformat(), end=end.isoformat()))
    payroll = EmployeePayroll.query.filter(
        EmployeePayroll.period_start == start,
        EmployeePayroll.period_end == end
    ).order_by(EmployeePayroll.id.desc()).all()
    return render_template("employee_payroll.html", employees=employees, payroll=payroll, start=start.isoformat(), end=end.isoformat())

@app.route("/employees/payroll/<int:payroll_id>/print")
@guarded
def employee_payroll_print(payroll_id):
    p = db.session.get(EmployeePayroll, payroll_id)
    if not p:
        abort(404)
    emp = db.session.get(Employee, p.employee_id)
    EmployeePayrollStatutory.__table__.create(db.engine,checkfirst=True) if "EmployeePayrollStatutory" in globals() else None
    statutory=EmployeePayrollStatutory.query.filter_by(payroll_id=p.id).first() if "EmployeePayrollStatutory" in globals() else None
    if not statutory:
        class O: pass
        statutory=O()
        for k,v in _v1040_calc(_v1039_money(p.basic_salary)+_v1039_money(p.overtime_pay)+_v1039_money(p.allowances),p.basic_salary,p.deductions,p.absences,p.late_undertime).items(): setattr(statutory,k,v)
    return render_template("employee_payroll_print.html", p=p, employee=emp, statutory=statutory)

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
    EmployeePayrollStatutory.__table__.create(db.engine,checkfirst=True)
    p=db.session.get(EmployeePayroll,payroll_id)
    if not p: abort(404)
    emp=db.session.get(Employee,p.employee_id)
    c=_v1040_calc(_v1039_money(p.basic_salary)+_v1039_money(p.overtime_pay)+_v1039_money(p.allowances),p.basic_salary,p.deductions,p.absences,p.late_undertime)
    if request.method=="POST":
        row=EmployeePayrollStatutory.query.filter_by(payroll_id=p.id).first()
        if not row: row=EmployeePayrollStatutory(payroll_id=p.id); db.session.add(row)
        for k in c: setattr(row,k,_v1039_money(request.form.get(k,c[k])))
        db.session.commit(); flash("Philippine statutory payroll saved.","success")
        return redirect(url_for("employee_payroll_statutory",payroll_id=p.id))
    row=EmployeePayrollStatutory.query.filter_by(payroll_id=p.id).first()
    return render_template("employee_payroll_statutory.html",p=p,employee=emp,calc=(row.__dict__ if row else c))

@app.route("/employees/payroll/<int:payroll_id>/statutory/print")
@guarded
def employee_payroll_statutory_print(payroll_id):
    p=db.session.get(EmployeePayroll,payroll_id)
    if not p: abort(404)
    emp=db.session.get(Employee,p.employee_id); row=EmployeePayrollStatutory.query.filter_by(payroll_id=p.id).first()
    if not row:
        class O: pass
        row=O()
        for k,v in _v1040_calc(_v1039_money(p.basic_salary)+_v1039_money(p.overtime_pay)+_v1039_money(p.allowances),p.basic_salary,p.deductions,p.absences,p.late_undertime).items(): setattr(row,k,v)
    return render_template("employee_payroll_statutory_print.html",p=p,employee=emp,s=row)

@app.route("/employees/payroll/13th-month")
@guarded
def employee_13th_month():
    year=request.args.get("year") or str(_dt_date.today().year)
    employees=Employee.query.order_by(Employee.id).all()
    rows=[(e,_v1039_money(getattr(e,"monthly_salary",0))) for e in employees]
    return render_template("employee_13th_month.html",year=year,rows=rows)


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
        try: model.__table__.create(db.engine,checkfirst=True)
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
    e=_v1041_emp(eid)
    if not e: abort(404)
    if request.method=="POST":
        e.full_name=request.form.get("full_name","").strip(); e.position=request.form.get("position","").strip()
        e.department=request.form.get("department","").strip(); e.employment_status=request.form.get("employment_status","Active")
        e.contact_no=request.form.get("contact_no","").strip(); e.email=request.form.get("email","").strip()
        e.date_hired=parse_date(request.form.get("date_hired")); e.monthly_salary=money(request.form.get("monthly_salary")); e.notes=request.form.get("notes","").strip()
        db.session.commit(); audit(f"Updated employee {e.employee_no}"); flash("Employee changes saved.","success")
        return redirect(url_for("employees"))
    return render_template("employee_edit.html",employee=e)

@app.route("/employees/leave",methods=["GET","POST"])
@guarded
def employee_leave():
    _v1041_ensure_tables(); employees=Employee.query.order_by(Employee.full_name).all()
    if request.method=="POST":
        eid=int(request.form.get("employee_id")); start=parse_date(request.form.get("start_date")); end=parse_date(request.form.get("end_date"))
        if not start or not end or end<start: flash("Please enter a valid leave date range.","danger"); return redirect(url_for("employee_leave"))
        row=EmployeeLeave(employee_id=eid,leave_type=request.form.get("leave_type","VACATION"),start_date=start,end_date=end,days=_v1041_days(start,end),status=request.form.get("status","PENDING"),reason=request.form.get("reason",""))
        db.session.add(row); db.session.commit(); audit(f"Added leave request for employee {eid}"); flash("Leave request saved.","success"); return redirect(url_for("employee_leave"))
    rows=EmployeeLeave.query.order_by(EmployeeLeave.id.desc()).all()
    return render_template("employee_leave.html",employees=employees,rows=rows)

@app.route("/employees/leave/<int:lid>/status",methods=["POST"])
@guarded
def employee_leave_status(lid):
    row=db.session.get(EmployeeLeave,lid)
    if row:
        row.status=request.form.get("status","PENDING"); row.approved_by=current_user().username if current_user() else ""
        db.session.commit(); audit(f"Updated leave {lid} to {row.status}")
    return redirect(url_for("employee_leave"))

@app.route("/employees/overtime",methods=["GET","POST"])
@guarded
def employee_overtime():
    _v1041_ensure_tables(); employees=Employee.query.order_by(Employee.full_name).all()
    if request.method=="POST":
        eid=int(request.form.get("employee_id")); emp=_v1041_emp(eid); od=parse_date(request.form.get("ot_date")) or date.today(); hours=_v1039_money(request.form.get("hours")); mult=_v1039_money(request.form.get("rate_multiplier") or 1.25)
        amount=round(_v1041_hourly(emp)*hours*mult,2) if emp else 0
        row=EmployeeOvertime(employee_id=eid,ot_date=od,hours=hours,rate_multiplier=mult,amount=amount,status=request.form.get("status","PENDING"),reason=request.form.get("reason",""))
        db.session.add(row); db.session.commit(); audit(f"Added overtime for employee {eid}"); flash("Overtime request saved.","success"); return redirect(url_for("employee_overtime"))
    rows=EmployeeOvertime.query.order_by(EmployeeOvertime.id.desc()).all()
    return render_template("employee_overtime.html",employees=employees,rows=rows)

@app.route("/employees/overtime/<int:oid>/status",methods=["POST"])
@guarded
def employee_overtime_status(oid):
    row=db.session.get(EmployeeOvertime,oid)
    if row:
        row.status=request.form.get("status","PENDING"); row.approved_by=current_user().username if current_user() else ""
        db.session.commit(); audit(f"Updated overtime {oid} to {row.status}")
    return redirect(url_for("employee_overtime"))

@app.route("/employees/hr/loans",methods=["GET","POST"])
@guarded
def employee_hr_loans():
    _v1041_ensure_tables(); employees=Employee.query.order_by(Employee.full_name).all()
    if request.method=="POST":
        eid=int(request.form.get("employee_id")); original=_v1039_money(request.form.get("original_amount")); balance=_v1039_money(request.form.get("balance") or original)
        db.session.add(EmployeeHRLoan(employee_id=eid,loan_type=request.form.get("loan_type","OTHER"),reference_no=request.form.get("reference_no",""),original_amount=original,balance=balance,monthly_deduction=_v1039_money(request.form.get("monthly_deduction")),status=request.form.get("status","ACTIVE"),notes=request.form.get("notes","")))
        db.session.commit(); flash("HR loan/deduction saved.","success"); return redirect(url_for("employee_hr_loans"))
    rows=EmployeeHRLoan.query.order_by(EmployeeHRLoan.id.desc()).all()
    return render_template("employee_hr_loans.html",employees=employees,rows=rows)

@app.route("/employees/hr-settings", methods=["GET","POST"])
@guarded
def employee_hr_settings():
    _v1041_ensure_tables()
    defaults = {
        "hr_working_days":"26", "hr_work_hours_per_day":"8", "hr_grace_minutes":"5",
        "hr_sss_employee_rate":"5", "hr_sss_employer_rate":"10", "hr_sss_max_msc":"35000",
        "hr_sss_ec_threshold":"14500", "hr_sss_ec_low":"10", "hr_sss_ec_high":"30",
        "hr_philhealth_rate":"5", "hr_philhealth_min_base":"10000", "hr_philhealth_max_base":"100000", "hr_philhealth_employee_share":"50",
        "hr_pagibig_max_base":"5000", "hr_pagibig_low_rate":"1", "hr_pagibig_high_rate":"2", "hr_pagibig_employer_rate":"2",
        "hr_bir_exempt_threshold":"20833", "hr_bir_bracket2":"33332", "hr_bir_bracket3":"66666", "hr_bir_bracket4":"166666", "hr_bir_bracket5":"666666",
        "hr_bir_rate2":"15", "hr_bir_rate3":"20", "hr_bir_rate4":"25", "hr_bir_rate5":"30", "hr_bir_rate6":"35", "hr_13th_month_ceiling":"90000"
    }
    if request.method=="POST":
        for k,d in defaults.items():
            row=EmployeeHRSetting.query.filter_by(key=k).first() or EmployeeHRSetting(key=k)
            row.value=request.form.get(k,d)
            db.session.add(row)
        db.session.commit(); audit("Updated Philippine Payroll / HR statutory settings")
        flash("Payroll / HR statutory settings saved successfully.","success")
        return redirect(url_for("employee_hr_settings"))
    vals={k:_v1041_hr_setting(k,d) for k,d in defaults.items()}
    return render_template("employee_hr_settings.html",settings=vals)


@app.route("/employees/payroll-reports")
@guarded
def employee_payroll_reports():
    year=int(request.args.get("year") or _dt_date.today().year)
    rows=EmployeePayroll.query.filter(db.extract("year",EmployeePayroll.period_end)==year).order_by(EmployeePayroll.period_end,EmployeePayroll.employee_id).all()
    total_basic=sum(_v1039_money(x.basic_salary) for x in rows); total_ot=sum(_v1039_money(x.overtime_pay) for x in rows); total_allow=sum(_v1039_money(x.allowances) for x in rows); total_net=sum(_v1039_money(x.net_pay) for x in rows)
    stats=EmployeePayrollStatutory.query.filter(EmployeePayrollStatutory.payroll_id.in_([x.id for x in rows])).all() if rows else []
    return render_template("employee_payroll_reports.html",year=year,rows=rows,employees={e.id:e for e in Employee.query.all()},stats=stats,total_basic=total_basic,total_ot=total_ot,total_allow=total_allow,total_net=total_net)

@app.route("/employees/13th-month")
@guarded
def employee_13th_month_full():
    year=int(request.args.get("year") or _dt_date.today().year)
    employees=Employee.query.order_by(Employee.full_name).all(); rows=[]
    for e in employees:
        ps=EmployeePayroll.query.filter(EmployeePayroll.employee_id==e.id,db.extract("year",EmployeePayroll.period_end)==year).all()
        basic=sum(_v1039_money(p.basic_salary) for p in ps)
        rows.append((e,basic,round(basic/12,2)))
    total=sum(r[2] for r in rows)
    return render_template("employee_13th_month_full.html",year=year,rows=rows,total=total)

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
    User=User, audit=audit, check_password_hash=check_password_hash,
    home_endpoint=home_endpoint, resident_access_problem=resident_access_problem,
))
app.register_blueprint(make_resident_blueprint(sys.modules[__name__]))
app.register_blueprint(make_spa_blueprint(os.path.join(BASE_DIR, "frontend", "dist")))

# ============================================================
# APPLICATION STARTUP
# Keep this block at the very end so all routes (including HR)
# are registered before Flask starts when this file is run directly.
# Preferred entry point: backend/run.py
# ============================================================
if __name__ == "__main__":
    init_db()
    app.run(host=os.getenv("FLASK_HOST", "0.0.0.0"), port=int(os.getenv("FLASK_PORT", "5000")), debug=os.getenv("FLASK_DEBUG", "1") == "1")
