"""THE permission matrix: which roles may use which function.

Single source of truth for:
  * the legacy Flask pages   (@guarded in backend/legacy_app.py)
  * the REST API             (@permission_required / @role_required)
  * the legacy sidebar       (can_access in base.html)
  * the React menu & buttons (GET /api/auth/me -> "permissions")

Keys are Flask endpoint names (legacy page functions and API permissions).
Unknown keys are DENIED (fail closed). Change access here and nowhere else.

Role policy (client specification, 2026-09-30):
  SUPERADMIN  everything, incl. Users & Access, Audit Logs, Rates & Rules
  ADMIN       property operations: units/tenants/parking, billing & payments, water,
              reports, operations, community. NOT user accounts, NOT system settings,
              NOT audit logs, NOT HR/payroll
  MANAGER     HR & Philippine payroll + announcements
  STAFF       data entry only: move in/out, gate pass, expenses, attendance entry,
              maintenance tickets. NOT financial reports, NOT audit logs
  ACCOUNTING  condo reports + audit logs + full billing, payments, advance payments
  RESIDENT    resident portal only: own SOA/balance, own maintenance requests, notices

Lines marked CHANGED differ from V10.64 behaviour. Lines marked CONFIRM are an
interpretation of the specification that the client should confirm.
"""
from .roles import ACCOUNTING as ACC, ADMIN as A, MANAGER as M, RESIDENT as R, STAFF as S, SUPER_ADMIN as SA

PROPERTY = {SA, A}
BILLING_RW = {SA, A, ACC}
HR = {SA, M}

PERMISSIONS = {
    # ---- Workspace --------------------------------------------------------------
    "dashboard": PROPERTY,

    # ---- Units / owners / tenants / parking -------------------------------------
    "units": PROPERTY,
    "unit_detail": PROPERTY,          # CHANGED: was any logged-in user (bug D2: residents saw every unit)
    "edit_unit": PROPERTY,            # CHANGED: was SA/manager/staff, admin was blocked (bug D3)
    "add_owner": PROPERTY,            # CHANGED: was SA/manager/staff (bug D3)
    "edit_owner": PROPERTY,           # CHANGED: was SA/manager/admin/staff
    "add_tenant": PROPERTY, "edit_tenant": PROPERTY, "tenant_status": PROPERTY, "tenants": PROPERTY,
    "parking": PROPERTY,              # Phase B3: read-only list of PARKING units (lot routes retired)

    # ---- Billing, SOA, payments, advances (Accounting gets full read/write) -------
    "billing": BILLING_RW,            # CHANGED: + accounting (incl. Generate Bills)
    "billing_detail": BILLING_RW,     # CHANGED: + accounting
    "mark_bill_paid": BILLING_RW,     # CHANGED: + accounting
    "edit_soa": BILLING_RW,           # CHANGED: + accounting
    "billing_qr": BILLING_RW,         # CHANGED: + accounting
    "recalculate_soa": BILLING_RW,    # NEW (Phase B1): re-price an issued bill from current rates
    "receipts": BILLING_RW,           # NEW (Phase B2): official receipts ledger
    "receipt_detail": BILLING_RW,     # NEW (Phase B2): printable official receipt
    "void_receipt": {SA, ACC},        # NEW: void a receipt + reverse its payments (reason required)  CONFIRM
    "advance_payments": BILLING_RW,   # CHANGED: + accounting
    "billing_email": PROPERTY, "send_billing_emails": PROPERTY, "email_bill": PROPERTY,  # CONFIRM: SOA email not given to accounting

    # ---- Water ---------------------------------------------------------------------
    "water": PROPERTY, "water_previous": PROPERTY, "edit_water": PROPERTY,
    "mark_water_paid": PROPERTY,      # CONFIRM: water payments not given to accounting (Water page is admin-only)

    # ---- Operations ------------------------------------------------------------------
    "move_certificate": {SA, A, S}, "move_certificates": {SA, A, S},
    "gate_pass": {SA, A, S},
    "gate_pass_review": {SA, A, S},   # NEW: approve / reject residents' gate pass & move requests
    "expenses": {SA, A, S},

    # ---- HR & payroll (Manager) --------------------------------------------------------
    "employees": HR,                  # CHANGED: admin removed (HR belongs to Manager)  CONFIRM
    "employee_edit": HR,              # CHANGED: admin removed  CONFIRM
    "delete_employee": HR,
    "employee_attendance": {SA, M, S},          # staff: attendance ENTRY is allowed; CHANGED: admin removed
    "employee_attendance_history": {SA, M, S},  # CHANGED: admin removed
    "employee_leave": HR,             # CHANGED: staff and admin removed  CONFIRM (staff = attendance entry only)
    "employee_leave_status": HR,      # CHANGED: admin removed
    "employee_overtime": HR,          # CHANGED: staff and admin removed  CONFIRM
    "employee_overtime_status": HR,   # CHANGED: admin removed
    "employee_payroll": HR, "employee_payroll_print": HR,
    "employee_payroll_statutory": HR, "employee_payroll_statutory_print": HR,
    "employee_13th_month": HR, "employee_13th_month_full": HR,
    "employee_hr_loans": HR, "employee_hr_settings": HR, "employee_payroll_reports": HR,  # CHANGED: admin removed

    # ---- Community services ------------------------------------------------------------
    "resident_portal": {R, SA, A},    # admin keeps the portal preview
    "announcements": {SA, A, M, R},   # CHANGED: staff and accounting removed
    "maintenance": {SA, A, S, R},     # residents only ever see their own unit's tickets (enforced in the route)
    "maintenance_update": {SA, A, S},
    "vendors": PROPERTY,              # CHANGED: staff and accounting removed  CONFIRM
    "documents": PROPERTY,            # CHANGED: manager, staff, accounting, resident removed  CONFIRM

    # ---- Administration --------------------------------------------------------------------
    "reports": BILLING_RW,            # CHANGED: staff removed (no financial reports)
    "reports_export": BILLING_RW,     # CHANGED: staff removed
    "audit_logs": {SA, ACC},          # CHANGED: admin and staff removed
    "users": {SA}, "reset_user_password": {SA}, "delete_user": {SA},
    "resident_users": {SA},           # CHANGED: admin removed (creating accounts is Superadmin-only)
    "settings": {SA},                 # CHANGED: admin removed (Rates & Rules = system settings)  CONFIRM
    "database_export": {SA},          # CHANGED: manager removed (bug D4)
    "database_import": {SA},          # CHANGED: manager removed (bug D4)

    # ---- REST API (resident portal) --------------------------------------------------------
    "api_resident": {R, SA, A},       # own-unit data; residents are further limited to THEIR unit
}

# Pages every signed-in user may open (no matrix entry needed).
ANY_SIGNED_IN = {"index", "change_password"}

# Pages that need NO sign-in: health checks for monitoring (no sensitive data in them).
PUBLIC_ENDPOINTS = {"health.healthz", "health.readyz"}


def allowed_roles(permission):
    return PERMISSIONS.get(permission, set())


def can(role, permission):
    return permission in ANY_SIGNED_IN or role in allowed_roles(permission)


def permissions_for_role(role):
    return sorted(key for key, roles in PERMISSIONS.items() if role in roles)
