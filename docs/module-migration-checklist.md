# CityLand 9 — Module Migration Checklist

**Scope:** every module in the current system, mapped from the legacy Flask monolith (`backend/legacy_app.py` + `legacy_flask_ui/templates/`) to the target architecture (Flask blueprints + REST API, React/Vite, MySQL).
**Baseline:** V10.64 after Stage 1 filesystem reorganization (2026-09-29).
**Source of truth for this inventory:** static analysis of `backend/legacy_app.py` (70 routes, 31 models), the Jinja templates, and runtime checks against a seeded test database. Line numbers refer to `backend/legacy_app.py` at this baseline.

> **Rule:** a module is **not migrated** until every box in its checklist *and* the global Definition of Done below is ticked. Until then the legacy Flask page stays the production UI for that module.

---

## 1. Status board

Status values: `LEGACY` (only the Jinja UI exists) · `API` (REST endpoints done and tested) · `UI` (React screen done) · `VERIFIED` (parity signed off) · `RETIRED` (legacy route removed).

| # | Sidebar group | Module (as named in the UI) | Legacy endpoints | Target blueprint | Status |
|---|---|---|---|---|---|
| 1 | Condo Management | Dashboard | `index`, `dashboard` | `dashboard` | LEGACY |
| 2 | Condo Management | Units / Unit Directory ¹ | `units`, `unit_detail`, `edit_unit`, `add_owner`, `edit_owner`, `add_tenant`, `edit_tenant`, `tenant_status`, `tenants`, `parking` (since B3 a read-only list of PARKING units; the 4 parking-lot routes were retired) | `units` | REACT (live; classic URLs redirect) |
| 3 | Condo Management | Billing & SOA / Billing Management ² | `billing`, `billing_detail`, `mark_bill_paid`, `edit_soa`, `billing_qr` | `billing` | REACT (live; classic URLs redirect) |
| 4 | Condo Management | Advance Payments | `advance_payments` | `billing` | REACT (live; classic URLs redirect) |
| 5 | Condo Management | SOA Email | `billing_email`, `send_billing_emails`, `email_bill` | `billing` | REACT (live; classic URLs redirect) |
| 6 | Condo Management | Water Readings | `water`, `water_previous`, `mark_water_paid`, `edit_water` | `water` | REACT (live; classic URLs redirect) |
| 7 | Operations | Move In / Out | `move_certificate`, `move_certificates` | `operations` | REACT (live; classic URLs redirect) |
| 8 | Operations | Gate Pass | `gate_pass` | `operations` | REACT (live; classic URLs redirect) |
| 9 | Operations | Expenses | `expenses` | `operations` | REACT (live; classic URLs redirect) |
| 10 | Employee Mgmt | Employees | `employees`, `employee_edit`, `delete_employee` | `employees` | REACT (live; classic URLs redirect) |
| 11 | Employee Mgmt | Attendance | `employee_attendance`, `employee_attendance_history` | `employees` | REACT (live; classic URLs redirect) |
| 12 | Employee Mgmt | Leave | `employee_leave`, `employee_leave_status` | `employees` | REACT (live; classic URLs redirect) |
| 13 | Employee Mgmt | Overtime | `employee_overtime`, `employee_overtime_status` | `employees` | REACT (live; classic URLs redirect) |
| 14 | Employee Mgmt | Payroll | `employee_payroll`, `employee_payroll_print`, `employee_payroll_statutory`, `employee_payroll_statutory_print` | `payroll` | REACT (live; classic URLs redirect) |
| 15 | Employee Mgmt | Loans & Deductions | `employee_hr_loans` | `payroll` | REACT (live; classic URLs redirect) |
| 16 | Employee Mgmt | 13th Month | `employee_13th_month_full` (+ older `employee_13th_month`) | `payroll` | REACT (live; classic URLs redirect) |
| 17 | Employee Mgmt | Payroll Rules | `employee_hr_settings` | `payroll` | REACT (live; classic URLs redirect) |
| 18 | Employee Mgmt | Payroll Reports | `employee_payroll_reports` | `payroll` | REACT (live; classic URLs redirect) |
| 19 | Community | Resident Portal | `resident_portal` | `community` | LEGACY |
| 20 | Community | Resident Accounts | `resident_users` | `community` | REACT (live; classic URLs redirect) |
| 21 | Community | Announcements | `announcements` | `community` | REACT (live; classic URLs redirect) |
| 22 | Community | Maintenance | `maintenance`, `maintenance_update` | `community` | REACT (live; classic URLs redirect) |
| 23 | Community | Vendors | `vendors` | `community` | REACT (live; classic URLs redirect) |
| 24 | Community | Documents | `documents` | `community` | REACT (live; classic URLs redirect) |
| 25 | Administration | Condo Reports | `reports`, `reports_export` | `reports` | REACT (live; classic URLs redirect) |
| 26 | Administration | Users & Access | `users`, `reset_user_password`, `delete_user` | `admin` | REACT (live; classic URLs redirect) |
| 27 | Administration | Audit Logs | `audit_logs` | `admin` | REACT (live; classic URLs redirect) |
| 28 | Administration | Rates & Rules | `settings`, `database_export`, `database_import` | `admin` | REACT (live; classic URLs redirect) |
| 29 | (no menu item) | Authentication & account | `login`, `logout`, `change_password` | `auth` | LEGACY |

¹ In the sidebar, "Units" is a group heading and "Unit Directory" is its only link (`/units`). **Tenants** (`/tenants`) and **Parking** (`/parking`) have no sidebar entry. They are reached through tabs on the Units pages, and they are part of module 2.
² In the sidebar, "Billing & SOA" is a group heading. Its links are Billing Management (`/billing`), Advance Payments and SOA Email.

### 1.1 Tracking matrix (the 22-item Phase 24 checklist)

Every module is tracked against the same 22 items:

- **Identification (items 1–11):** routes, models, tables, forms, validation, calculations, permissions, reports, import/export, email, file uploads.
- **Implementation and verification (items 12–22):** backend API, backend authorization, React page, React forms, loading states, error states, workflow tested, database operations tested, role permissions tested, audit logging verified, migration marked complete.

Items 1–11 are **done for all 29 modules** (2026-09-29). They were identified by static analysis of every route and template, plus targeted runtime checks on seeded data. The per-module detail is in §4. Items 12–22 have **not started for any module**. `n/a` means the item doesn't apply to that module, for example a module with no forms.

| # | Module | 1–11 Identified | 12 API | 13 AuthZ | 14 Page | 15 Forms | 16 Loading | 17 Errors | 18 Workflow | 19 DB ops | 20 Roles | 21 Audit | 22 Complete |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Dashboard | ✅ | ☐ | ☐ | ☐ | n/a | ☐ | ☐ | ☐ | n/a | ☐ | n/a | ☐ |
| 2 | Units / Unit Directory (+ Owners, Tenants, Parking) | ✅ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 3 | Billing & SOA / Billing Management | ✅ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 4 | Advance Payments | ✅ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 5 | SOA Email | ✅ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | n/a | ☐ | ☐ | ☐ |
| 6 | Water Readings | ✅ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 7 | Move In / Out | ✅ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 8 | Gate Pass | ✅ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 9 | Expenses | ✅ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 10 | Employees | ✅ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 11 | Attendance | ✅ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ ¹ | ☐ |
| 12 | Leave | ✅ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 13 | Overtime | ✅ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 14 | Payroll | ✅ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ ¹ | ☐ |
| 15 | Loans & Deductions | ✅ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ ¹ | ☐ |
| 16 | 13th Month | ✅ | ☐ | ☐ | ☐ | n/a | ☐ | ☐ | ☐ | n/a | ☐ | n/a | ☐ |
| 17 | Payroll Rules | ✅ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 18 | Payroll Reports | ✅ | ☐ | ☐ | ☐ | n/a | ☐ | ☐ | ☐ | n/a | ☐ | n/a | ☐ |
| 19 | Resident Portal | ✅ | ☐ | ☐ | ☐ | n/a | ☐ | ☐ | ☐ | n/a | ☐ | n/a | ☐ |
| 20 | Resident Accounts | ✅ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 21 | Announcements | ✅ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 22 | Maintenance | ✅ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 23 | Vendors | ✅ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 24 | Documents | ✅ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 25 | Condo Reports | ✅ | ☐ | ☐ | ☐ | n/a | ☐ | ☐ | ☐ | n/a | ☐ | n/a | ☐ |
| 26 | Users & Access | ✅ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 27 | Audit Logs | ✅ | ☐ | ☐ | ☐ | n/a | ☐ | ☐ | ☐ | n/a | ☐ | n/a | ☐ |
| 28 | Rates & Rules (+ Excel import/export) | ✅ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 29 | Authentication & account ² | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | n/a | ✅ | ✅ | ☐ |

² 2026-09-29: sign-in, sign-out and the session are done in React + `/api/auth`, and the session is shared with the legacy pages. Evidence: `tests/test_api_auth.py` (CSRF, bad and inactive logins, per-role permissions, audit entries, shared session) and a 25-check headless-browser run (all roles, mobile drawer, dark theme, server-side refusal of forbidden legacy URLs). **Not complete yet:** Change Password still opens the legacy page; login throttling and CSRF protection on the legacy Jinja forms are still to do. Every other module (1–28) is reachable from the React menu but opens its **legacy** page. None is migrated.

¹ The legacy system writes **no** audit entry for this module's main action (§3.5). Item 21 means "an entry is added and approved" or "no entry is kept, as a documented decision".

Update rule: tick an item only when there is evidence for it, meaning a passing test or a signed-off manual check recorded in `docs/migration.md`. Item 22 is ticked only when items 12–21 are all ✅ or n/a.

---

## 2. Global Definition of Done (applies to every module)

- [ ] **Route parity.** Every legacy endpoint listed for the module has a REST equivalent, or a written decision explaining why it doesn't.
- [ ] **Permission parity.** The API enforces the same roles as the legacy `@roles(...)` decorator, **on the server**. Where this document flags a permission defect, the chosen fix is written down and approved *before* the module ships.
- [ ] **Calculation parity (golden master).** Output is identical on a copy of the production database and on `seed_test_data.py` data. See §3.1.
- [ ] **Validation parity.** Every legacy rejection (each `flash(..., "danger")`) has a matching API error, with the right HTTP status (400/403/404/409) and the same message text.
- [ ] **Audit parity.** The same actions write `audit_log` rows with the same wording. Reports and staff depend on that text.
- [ ] **Side effects preserved.** Some legacy GETs write data (§3.3). The API does the same writes, in an explicit and documented place.
- [ ] **Print and export parity.** Printed pages, Excel files and the QR code match the legacy output, field for field.
- [ ] **React screen parity.** Same fields, filters, pagination, KPI counts and status tabs.
- [ ] **Automated tests** added under `tests/`: API tests for each endpoint, including the role matrix.
- [ ] **Works on both SQLite and MySQL.**
- [ ] **Legacy route kept** until the module is signed off as VERIFIED.

---

## 3. Cross-cutting concerns (migrate these first, as services)

### 3.1 Billing calculation engine — `backend/app/services/billing_engine.py`
The heart of the system. It must be moved **verbatim** before any billing-related module is migrated.

| Function (line) | What it does |
|---|---|
| `_prepare_bill_calculation_cache` (370) | Loads every bill in one query, processes them per unit in chronological order, and computes: condo, parking, water, penalty, previous balance, advance applied, current, total, balance. Cached on `flask.g` for each request. |
| `unit_dues` (252) | `area_sqm × (unit_rate_per_sqm or rate for unit type)`. |
| `parking_dues` | Since B3: the assigned PARKING unit's `area × rate` (falls back to the global `parking_rate_per_sqm`); 0 if none. (Before B3: active parking lots first, then the assigned PARKING unit.) |
| `storage_dues` / `asset_unit_charge` (295/273) | The assigned STORAGE unit's per-sqm charge, or its manual monthly dues. |
| `water_amount_for_reading` (302) | `max(current − previous, 0) × rate`, but only when the setting `water_auto_compute` = 1. |
| Penalty (425–531, 583–625, 788–792) | `penalty_rate`% × the unpaid **prior-month** charges of the types chosen in the `penalty_include_*` settings. Applied only once the bill is overdue. Payments are allocated in the order Condo → Parking → Storage → Water. |
| `allocate_advances_for_month` (638) | Applies advance payments to **condo dues only**, oldest first, within each advance's coverage window. Writes `advance_application` rows. |
| `bill_status` / `soa_bill_status` (737/761) | Paid / Partially Paid / Overdue / Unpaid. |
| `soa_bill_total` (774) | SOA total. Leaves out water that has already been paid (unless explicitly included). |

Business rules to keep exactly, and to confirm with the business owner where marked:
- [ ] **Totals are recalculated live from the unit's *current* rates.** The `assessment`/`parking_dues` stored on the bill are ignored, unless `soa_manual_override` is set. So changing a rate changes past bills too. Keep this behavior and confirm it's intended (**Q1**).
- [ ] **Storage dues are not included in `current`/`total`.** Line 472: `current = condo + parking + water`. Storage *does* feed the penalty base, and the SOA prints a Storage line. **Verified on the seed data:** TEST-501 (2026-09) stores `storage_dues = 250.00` and the SOA shows it, but `current = 3000.00` = condo 2000 + parking 1000 (water was paid). The ₱250 is never charged. Confirm whether storage is meant to be billed (**Q2**, likely existing defect).
- [ ] **The due date is hard-coded to the 8th** (lines 1851, 1888, 2031). The `due_day` and `penalty_day` settings are editable in Rates & Rules but **have no effect** (**Q3**).
- [ ] Only one bill per unit per month, enforced **in code only** (1856). There is no database constraint (see §5).
- [ ] Golden master: before migrating, export for every bill `{id, condo, parking, water, penalty, previous, advance, current, total, balance, status}` from the legacy engine on a copy of the production DB. Store it under `database/seeds/golden/`, and diff the new engine's output against it.

### 3.2 Payroll calculation engine — `backend/app/services/payroll_engine.py`
| Function (line) | Rule |
|---|---|
| `_v1039_calc_payroll` (3940) | Absence deduction = `monthly ÷ hr_working_days × ABSENT days`. Late/undertime is computed against a **hard-coded 08:00–17:00** schedule, minus `hr_grace_minutes`. |
| `_v1040_calc` (4156) | SSS, PhilHealth, Pag-IBIG and BIR withholding, all driven by `employee_hr_setting`. **13th month = basic ÷ 12.** |
| `employee_payroll` POST (4050) | Includes approved overtime and active loans automatically, reduces loan balances, and saves the statutory row. |
| `_v1041_hourly` (4331) | Overtime hourly rate = `monthly ÷ 26 ÷ 8`, **hard-coded**. It ignores `hr_working_days` and `hr_work_hours_per_day`, which payroll does use (**Q4**). |

- [ ] The BIR bracket boundaries can be configured, but the fixed tax amounts (1875, 8541.8, 33541.8, 183541.8) are hard-coded. Changing a bracket makes them inconsistent (**Q5**).
- [ ] The Pag-IBIG low/high threshold of 1,500 is hard-coded.
- [ ] `hr_13th_month_ceiling` is editable but **used nowhere**.
- [ ] Payroll maths uses `float`, not `Decimal`. Decide whether to keep it for parity or switch to `Decimal` (**Q6**). Either way, check against a golden master of the existing payroll rows.
- [ ] If the statutory step fails (`except Exception: rollback`, 4098), **loan balances are not reduced and no statutory row is saved, yet the user still sees "success"**. The API must return an error instead.

### 3.3 GET requests that write data (must be preserved deliberately)
| Endpoint | Writes on every view |
|---|---|
| `GET /billing` (1886–1916) | Resets that month's bill and parking-bill due dates to the 8th. Copies water amounts from the readings onto the bills. |
| `GET /billing/<id>` (2030–2057) | Due date, water sync, penalty recalculation, advance allocation, `status`. |
| `employee_*` pages | `CREATE TABLE IF NOT EXISTS` for the HR tables (`_v1039/_v1041_ensure_tables`). This moves to real migrations. |

In a REST design these become an explicit **`POST /api/billing/sync?month=`**, triggered by the SOA and billing screens. Or keep them on GET, but document it and add a test for it.

### 3.4 Settings
- Two key/value tables: `setting` (condo) and `employee_hr_setting` (payroll). `key` is a **MySQL reserved word**, so it must be quoted or renamed to `setting_key`.
- Saving Rates & Rules **also writes every `hr_*` key into `setting`** (3406–3417), reset to their defaults. Payroll reads `employee_hr_setting` instead, so these copies are dead data. Don't migrate them, or document that they're ignored.
- The SMTP password is stored in plain text and **echoed back into the page's HTML** (`value="{{settings.get('smtp_password')}}"`). The API must never return it.

### 3.5 Audit log
`audit()` commits its own row right after each action. The audit text is a de-facto report, so keep the wording. Most write actions are audited, but **four are not**: saving attendance, generating payroll, saving a statutory breakdown, and creating a loan. Keep that as-is, or add entries and document the change.

### 3.6 Authentication and roles
- Session cookie login (`session["user_id"]`) with Werkzeug password hashes. Roles: `super_admin`, `admin`, `manager`, `staff`, `accounting`, `resident`.
- Two separate permission sources: `ENDPOINT_ROLES` (sidebar visibility, line 146) and the `@roles()` decorators (actual enforcement). They **disagree** for several endpoints (see §5). The API needs **one** permission matrix.
- The landing page for each role is `ROLE_HOME` (line 197).
- User creation (`/users` POST) takes **any role string** from the form and has **no minimum password length**. Change-password and reset both require at least 8 characters.

### 3.7 Print pages (client side)
8 templates call `window.print()`: SOA, payslip, statutory payslip, move certificate, and others. Target library: `html2pdf.js`. Each print layout is a parity item in its module's checklist.

---

## 4. Per-module checklists

Legend for roles: **SA** super_admin · **A** admin · **M** manager · **S** staff · **Acc** accounting · **R** resident.

### 1 · Dashboard
| Aspect | Legacy behavior |
|---|---|
| Routes | `GET /` → redirects to the role's home page · `GET /dashboard` |
| Tables | `unit`, `billing`, `water_reading` (read only) |
| Calculations | For the current month: billed (`bill_total`), collected (`amount_paid`), overdue (balance of bills past due), occupied units (`status=="occupied"`), units without a water reading. PARKING and STORAGE units are excluded. The 8 most recent bills. |
| Permissions | SA, A |
| Relationships | Billing engine (§3.1), Water |

- [ ] `GET /api/dashboard/summary?month=` returns the same 5 KPIs and the recent bills
- [ ] Golden-master check of the KPIs for the current month
- [ ] React dashboard cards and recent-bills table
- [ ] Non-admin roles are redirected to their home page (`ROLE_HOME`)

### 2 · Units / Unit Directory (includes Owners, Tenants, Parking)
| Aspect | Legacy behavior |
|---|---|
| Routes | `GET/POST /units` · `GET /unit/<id>` · `POST /unit/<id>/edit` · `POST /unit/<id>/owner/add` · `POST /unit/<id>/owner/<oid>/edit` · `POST /unit/<id>/tenant/add` · `POST /unit/<id>/tenant/<tid>/edit` · `POST /tenant/<tid>/status` · `GET /tenants` · `GET /parking` (B3: the parking-lot add/edit/remove routes were retired; parking is assigned on the unit) |
| Tables | `unit` (self-referencing FKs `assigned_parking_unit_id` and `assigned_storage_unit_id` point to PARKING/STORAGE asset units), `owner`, `tenant`, `parking_lot`, `parking_billing`; reads `billing` |
| Create-unit form | `unit_no, floor, unit_type, area_sqm, unit_rate_per_sqm, auto_rate, dues_mode, manual_monthly_dues, occupancy_type, status, include_parking, assigned_parking_unit_id, include_storage, assigned_storage_unit_id, owner_name, contact_no, email, owner_receive_soa_email, tenant_name, tenant_contact, tenant_email, tenant_move_in, tenant_receive_soa_email` (the code also reads `parking_slot_no`, `parking_area_sqm`, … which **the form doesn't send**) |
| Validation | `unit_no` required and unique. Tenant/owner name required. Checkboxes use `== "on"`. |
| Calculations | `monthly_rate = unit_dues(unit)`. With `auto_rate` on, the rate comes from the unit type in Rates & Rules. Parking charge per lot is `area × rate`. |
| Listing | Search `q` (unit no / owner / tenant), filters `floor`, `unit_type`, `status`, 20 per page. Shows the latest SOA for each unit. |
| Permissions | List/create: SA, A · **Detail: any logged-in user** · **Edit unit & add owner: SA, M, S (not A)** · Edit owner: SA, M, A, S · Tenants/parking: SA, A |
| Relationships | Billing (dues, parking, storage), SOA Email (owner/tenant `receive_soa_email`, `include_in_soa`), Move In/Out (owner/tenant move dates), Resident Accounts, Maintenance, Documents |

- [ ] **Decide the permission fixes first** (D2, D3 in §5)
- [ ] `GET/POST /api/units`, `GET/PATCH /api/units/<id>`, `…/owners`, `…/tenants`, `…/parking`, `PATCH /api/tenants/<id>/status`
- [ ] PARKING and STORAGE asset units stay out of billing and resident listings
- [ ] Unit-number uniqueness enforced by the database *and* the API (409)
- [ ] The Tenants tab (search, status filter) and the Parking tab (month) are both kept
- [ ] Removing a parking lot keeps the same behavior: a soft delete (`active=False`, `status="Available"`), then recount the unit's `parking_slots` and set `include_parking = remaining > 0` (line 1821)

### 3 · Billing & SOA / Billing Management
| Aspect | Legacy behavior |
|---|---|
| Routes | `GET/POST /billing` (POST = generate the month's bills) · `GET /billing/<id>` (SOA) · `POST /billing/<id>/pay` · `POST /billing/<id>/edit-soa` · `GET /billing/<id>/qr` (PNG) |
| Tables | `billing`, `payment`, `parking_billing`, `advance_payment`, `advance_application`, `water_reading`, `unit`, `owner`, `tenant`, `setting` |
| Generate | Month `YYYY-MM` is validated. Covers all active non-asset units with no bill yet for the month. Stores condo/parking/storage/water/penalty/previous. Creates `parking_billing` rows. Allocates advances. Then redirects to SOA Email. |
| Pay form | `amount, payment_date, payment_method ∈ {CASH, CHECK, ONLINE}, payment_type ∈ {FULL, PARTIAL}, reference, remarks`. A reference is **required for CHECK/ONLINE**. Amount must be > 0. |
| Payment rule | The amount up to the balance goes to the bill (a `payment` row). **Any excess automatically becomes an `advance_payment`** starting that month. Settling sets `amount_paid = total`, `status = Paid`, and `paid_date`. |
| Edit SOA | Manual override of assessment, parking, storage, water, other, penalty, adjustment, previous_balance (≥ 0), due_date and note. Sets `soa_manual_override = True`. Audit logs which fields changed. |
| QR | `online_payment_url?unit=&billing_month=&amount=`, or a `CITYLAND9|UNIT:…|BILL:…|AMOUNT:…` payload |
| Listing | Search `q`, `unit_type`, KPI tabs Paid/Partially Paid/Overdue/Pending, `per_page` 10–100 |
| Permissions | SA, A |
| Relationships | Units, Water (bills sync water), Advance Payments, SOA Email, Reports, Rates & Rules |

- [ ] Billing engine service extracted and golden-master verified (§3.1) — **blocker**
- [ ] `POST /api/billing/generate {month}`, `GET /api/billing?month=&q=&status=&unit_type=&page=`, `GET /api/billing/<id>` (the full SOA payload: previous unpaid bills, unit and parking overdue months, water history, advance applied, contact)
- [ ] `POST /api/billing/<id>/payments` (excess becomes an advance, in the same transaction)
- [ ] `PATCH /api/billing/<id>/soa` (manual override plus the audit of changed fields)
- [ ] `GET /api/billing/<id>/qr.png`
- [ ] The GET side effects in §3.3 are preserved
- [ ] SOA print layout matches `billing_detail.html`, including Storage lines and history rows
- [ ] Answers to Q1, Q2 and Q3 recorded

### 4 · Advance Payments
| Aspect | Legacy behavior |
|---|---|
| Routes | `GET/POST /billing/advance` |
| Tables | `advance_payment`, `advance_application`, `unit` |
| Form | `unit_id, amount, start_month, coverage_months, payment_method, reference, payment_date, remarks` |
| Validation | Unit exists. Amount > 0 and months ≥ 1. `start_month` is `YYYY-MM`. Method is CASH/CHECK/ONLINE, with a reference required for CHECK/ONLINE. |
| Calculations | `monthly_amount = amount ÷ months`. Allocation applies to **condo dues only**, oldest advance first, within the coverage window. The balance is `amount − Σ applications`. |
| Permissions | SA, A |
| Relationships | Billing (every bill view and payment re-runs the allocation), Reports (advance collections) |

- [ ] `GET/POST /api/advances`, with the remaining balance on each row
- [ ] Allocation runs in the same transaction as creating the advance
- [ ] Automatic advances created from excess payments show up here

### 5 · SOA Email
| Aspect | Legacy behavior |
|---|---|
| Routes | `GET /billing/email?month=` · `POST /billing/email/send` (`bill_ids[]` or `send_all=1`) · `POST /billing/<id>/email` |
| Recipients | **Current** owners, then current tenants, who have an email address **and** `receive_soa_email`. Duplicates are removed case-insensitively. |
| Email | SMTP with STARTTLS. Host, port, sender and user come from `setting`; the password from `setting` or the `SMTP_PASSWORD` env var. HTML body is `soa_email_body.html`, with a plain-text fallback. Subject: `Statement of Account - Unit {no} - {YYYY-MM}`. |
| Reporting | Counts of sent, skipped (no recipients) and failed (first error shown). One audit row per email sent. |
| Permissions | SA, A |
| Relationships | Billing (generating bills redirects here), Units (contacts and opt-ins), Rates & Rules (SMTP) |

- [ ] An email service with a test double (no real SMTP in tests)
- [ ] Sending is synchronous today. Keep that, or add a job queue, and document which
- [ ] The HTML body renders identically (move the template into `backend/app/templates/email/`)
- [ ] The SMTP password is never returned by any API response

### 6 · Water Readings
| Aspect | Legacy behavior |
|---|---|
| Routes | `GET/POST /water` · `GET /water/previous?unit_id=&month=` (**already JSON**, used by `fetch()` in `water.html`) · `POST /water/<id>/paid` · `GET/POST /water/<id>/edit` |
| Tables | `water_reading`, `billing`, `unit` |
| Form | `month, unit_id, previous_reading, current_reading, rate (default: water_rate setting), reading_date` |
| Validation | Month format, unit exists. Edit: numbers ≥ 0. Pay: same method/type/reference rules as billing. The amount is **capped at the remaining balance** (no excess becomes an advance, unlike billing). |
| Calculations | `bill_amount = max(current − previous, 0) × rate` (a model property). Saving or editing a reading **overwrites `billing.water`** for that unit and month and recalculates the status. `paid` = `paid_amount ≥ bill_amount`. |
| Views | The month's readings, full history, and an "outstanding" list (Unpaid/Partially Paid) |
| Permissions | SA, A |
| Relationships | Billing (water charge, paid water left out of the SOA total), Reports (water collections), Dashboard (pending readings) |

- [ ] `GET/POST /api/water`, `GET /api/water/previous`, `POST /api/water/<id>/payments`, `PATCH /api/water/<id>`
- [ ] Syncing to the bill happens in the same transaction
- [ ] One reading per unit per month: an upsert (`first()` then update), with **no database constraint** (see §5)

### 7 · Move In / Out
| Aspect | Legacy behavior |
|---|---|
| Routes | `GET/POST /move-certificate` (`?certificate_id=` reprints) · `GET /move-certificates` (last 500) |
| Tables | `move_certificate` (**polymorphic `person_id`**: an owner or a tenant, depending on `person_type`, with no FK), `unit`, `owner`, `tenant` |
| Form | `unit_id, move_type ∈ {Move In, Move Out}, person_type ∈ {Owner, Tenant}, person_id, certificate_date` |
| Calculations | `certificate_no = CL9-{year}-{id:05d}`. `move_date` is copied from the person's `move_in`/`move_out`. |
| Print | `move_certificate.html` via `window.print()` |
| Permissions | SA, A, S |
| **Defect** | **D1: generating a certificate crashes (HTTP 500).** Line 2590 uses `current_user.username`, but `current_user` is a function. Confirmed at runtime. The history and reprint pages work. |

- [ ] Fix D1 in the legacy app first (one line: `current_user().username`), so there's a working reference to compare against
- [ ] `POST /api/move-certificates`, `GET /api/move-certificates`, `GET /api/move-certificates/<id>`
- [ ] Certificate numbers stay unique and in the same format
- [ ] Print layout parity

### 8 · Gate Pass
| Aspect | Legacy behavior |
|---|---|
| Routes | `GET/POST /gate-pass` |
| Tables | `gate_pass` (`unit_no` is **free text, not a foreign key**) |
| Form / validation | `pass_date, unit_no, visitor_name, purpose`. **No validation.** Status is always `Issued`. |
| Permissions | SA, A, S |

- [ ] `GET/POST /api/gate-passes`
- [ ] Decide whether `unit_no` should be validated against `unit` (**Q7**). Legacy accepts anything.

### 9 · Expenses
| Aspect | Legacy behavior |
|---|---|
| Routes | `GET/POST /expenses` |
| Tables | `expense` |
| Form / validation | `expense_date, category, description, amount`. **No validation** (0 or negative amounts accepted). Audit text is just `"Added expense"`. |
| Permissions | SA, A, S |
| Relationships | Reports (total expenses, net cash flow, Excel export as negative amounts) |

- [ ] `GET/POST /api/expenses`
- [ ] Keep accepting the same values, or add validation (documented change)

### 10 · Employees
| Aspect | Legacy behavior |
|---|---|
| Routes | `GET/POST /employees` · `GET/POST /employees/edit/<id>` · `POST /employee/<id>/delete` |
| Tables | `employee` (`employee_no` unique). The HR tables reference it by FK **with no ORM relationships and no cascade**. |
| Validation | `employee_no` and `full_name` required; `employee_no` unique. The edit form can't change `employee_no`. |
| Permissions | List/create/edit: SA, A, M · **Delete: SA, M only** |
| Relationships | Attendance, Leave, Overtime, Payroll, Loans, 13th Month, Excel export/import |

- [ ] Decide what deleting an employee who has attendance/payroll rows should do. SQLite doesn't enforce FKs here; MySQL InnoDB **will reject the delete** (**Q8**).
- [ ] `GET/POST /api/employees`, `GET/PATCH/DELETE /api/employees/<id>`

### 11 · Attendance
| Aspect | Legacy behavior |
|---|---|
| Routes | `GET/POST /employees/attendance?date=` (a bulk grid: `status_<id>`, `time_in_<id>`, `time_out_<id>`, `remarks_<id>`) · `GET /employees/attendance/history/<id>?start=&end=` |
| Tables | `employee_attendance` (one row per employee per date, upserted in code; no unique constraint) |
| Validation | Bad times are **silently ignored** (`except: pass`). Status defaults to PRESENT. |
| Calculations | History counts rows by status. Payroll uses ABSENT/LATE/UNDERTIME (§3.2). |
| Permissions | SA, A, M, S · **no audit entry** |

- [ ] `GET/PUT /api/attendance?date=` (bulk), `GET /api/employees/<id>/attendance?start=&end=`
- [ ] Add a unique `(employee_id, attendance_date)` constraint in MySQL, after checking for duplicates

### 12 · Leave
| Aspect | Legacy behavior |
|---|---|
| Routes | `GET/POST /employees/leave` · `POST /employees/leave/<id>/status` |
| Form | `employee_id, leave_type, start_date, end_date, status, reason`. `days = end − start + 1` (calendar days). |
| Validation | Both dates required, and end ≥ start. |
| Permissions | Create: SA, A, M, S · Approve: SA, A, M (`approved_by` = the current user) |
| **Gap** | **D5: the create form accepts `status`, so staff can create an already-APPROVED leave**, bypassing approval. |

- [ ] `GET/POST /api/leaves`, `PATCH /api/leaves/<id>/status`
- [ ] Decide on D5 (force PENDING for non-approvers?)

### 13 · Overtime
| Aspect | Legacy behavior |
|---|---|
| Routes | `GET/POST /employees/overtime` · `POST /employees/overtime/<id>/status` |
| Calculations | `amount = monthly ÷ 26 ÷ 8 × hours × rate_multiplier` (default 1.25, hard-coded divisors, Q4) |
| Permissions | Create: SA, A, M, S · Approve: SA, A, M · same D5 gap |
| Relationships | Payroll includes **APPROVED** overtime inside the period when the OT field is left blank |

- [ ] `GET/POST /api/overtime`, `PATCH /api/overtime/<id>/status`

### 14 · Payroll
| Aspect | Legacy behavior |
|---|---|
| Routes | `GET/POST /employees/payroll?start=&end=` · `GET …/<id>/print` · `GET/POST …/<id>/statutory` · `GET …/<id>/statutory/print` |
| Tables | `employee_payroll`, `employee_payroll_statutory` (1:1, unique `payroll_id`), `employee_overtime`, `employee_hr_loan`, `employee_attendance`, `employee_hr_setting` |
| Form | `employee_id, basic_salary, overtime_pay, allowances, deductions, status, remarks` |
| Calculations | §3.2. Stored `deductions` = manual + active loans. `net_pay` is overwritten with `net_pay_after_statutory`. Statutory edit lets any computed field be overridden. |
| Print | Payslip and statutory payslip |
| Permissions | SA, A, M · **no audit entry** |

- [ ] Payroll engine extracted and golden-master verified on the existing payroll rows — **blocker**
- [ ] `POST /api/payroll`, `GET /api/payroll?start=&end=`, `GET/PUT /api/payroll/<id>/statutory`
- [ ] The whole generation (payroll + statutory + loan reduction) runs in **one transaction**, and failures are reported
- [ ] Both print layouts match

### 15 · Loans & Deductions
| Aspect | Legacy behavior |
|---|---|
| Routes | `GET/POST /employees/hr/loans` |
| Form | `employee_id, loan_type, reference_no, original_amount, balance (defaults to original), monthly_deduction, status, notes` |
| Behavior | Payroll deducts `min(monthly_deduction, balance)` from each ACTIVE loan. When the balance reaches 0, status becomes PAID. **There's no edit route and no audit entry.** |
| Permissions | SA, A, M |

- [ ] `GET/POST /api/loans` (add PATCH only as a documented new feature)

### 16 · 13th Month
| Aspect | Legacy behavior |
|---|---|
| Routes | `GET /employees/13th-month?year=` (in the sidebar). The older `GET /employees/payroll/13th-month` (it lists monthly salaries only) isn't linked from the sidebar. |
| Calculation | For each employee: `Σ basic_salary of payrolls whose period_end falls in the year ÷ 12`, plus a grand total. The ceiling setting isn't applied. |
| Permissions | SA, A, M |

- [ ] `GET /api/payroll/13th-month?year=`
- [ ] Decide whether to retire the older unlinked page (**Q9**)

### 17 · Payroll Rules
| Aspect | Legacy behavior |
|---|---|
| Routes | `GET/POST /employees/hr-settings` |
| Tables | `employee_hr_setting` (28 `hr_*` keys, defaults in code at 4403) |
| Permissions | SA, A, M |

- [ ] `GET/PUT /api/payroll/rules`, with typed numeric validation (legacy stores any string)

### 18 · Payroll Reports
| Aspect | Legacy behavior |
|---|---|
| Routes | `GET /employees/payroll-reports?year=` |
| Calculation | Payroll rows whose `period_end` falls in the year: totals of basic, OT, allowances and net, plus each row's statutory values |
| Permissions | SA, A, M |

- [ ] `GET /api/payroll/reports?year=`, with totals matching the legacy page

### 19 · Resident Portal
| Aspect | Legacy behavior |
|---|---|
| Routes | `GET /portal` (an admin can preview it with `?unit_id=`) |
| Shows | The resident's name and unit, the 10 latest published announcements, and the unit's 10 latest maintenance tickets. **It doesn't show the unit's SOA or bills.** |
| Permissions | R, SA, A. A resident with no profile is logged out with a warning. |
| Relationships | Resident Accounts, Announcements, Maintenance |

- [ ] `GET /api/portal` (always scoped to the logged-in resident's unit on the server)
- [ ] Showing SOAs to residents would be a **new feature**, not part of this migration

### 20 · Resident Accounts
| Aspect | Legacy behavior |
|---|---|
| Routes | `GET/POST /resident-users` |
| Tables | `user` (role `resident`) + `resident_profile` (1:1 with `user`; `unit_id`; **polymorphic `person_id`** → owner/tenant) |
| Validation | username, password, unit and display name required; username unique; **no minimum password length** |
| Permissions | SA, A |

- [ ] `GET/POST /api/residents`. User and profile are created in one transaction.

### 21 · Announcements
| Aspect | Legacy behavior |
|---|---|
| Routes | `GET/POST /announcements` |
| Form | `title, message, audience, published` (title and message required) |
| Behavior | Anyone with access sees **all** announcements, including unpublished ones, on this page (the portal shows published only). Residents can't post. **No edit or delete.** |
| Permissions | View: all six roles · Create: everyone except R |

- [ ] `GET/POST /api/announcements`. Keep the visibility rules, or filter unpublished ones for residents (documented change).

### 22 · Maintenance
| Aspect | Legacy behavior |
|---|---|
| Routes | `GET/POST /maintenance` · `POST /maintenance/<id>/update` |
| Tables | `maintenance_ticket` (`ticket_no` unique = `MT-{yyyymmddHHMMSS}-{count+1:04d}`), FKs to `unit`, `resident_profile`, `vendor` |
| Behavior | A resident's ticket is tied to their own unit; residents see only their unit's tickets. Staff pick the unit. Update sets status, priority, assigned_to, vendor and resolution. |
| Permissions | View/create: R, SA, A, S · Update: SA, A, S |

- [ ] `GET/POST /api/maintenance`, `PATCH /api/maintenance/<id>`
- [ ] Keep the ticket-number format (race condition: two tickets in the same second could collide; that's acceptable at LAN scale, but note it)

### 23 · Vendors
| Aspect | Legacy behavior |
|---|---|
| Routes | `GET/POST /vendors` (vendor name required). **No edit or deactivate.** Maintenance lists only `status="Active"` vendors. |
| Permissions | SA, A, S, Acc |

- [ ] `GET/POST /api/vendors`

### 24 · Documents
| Aspect | Legacy behavior |
|---|---|
| Routes | `GET/POST /documents` |
| Tables | `document_record`. It stores `file_name` and `file_path` **as typed text. There's no actual file upload.** |
| Visibility | Residents see `audience="residents"`, plus `audience="unit"` for their own unit. Other roles see all active records. |
| Permissions | View: all six roles · Create: everyone except R |

- [ ] `GET/POST /api/documents`, with the same visibility filter on the server
- [ ] Real file upload would be a **new feature**. If added: stored outside the web root, extension/size allow-list, served through an authorized download endpoint.

### 25 · Condo Reports
| Aspect | Legacy behavior |
|---|---|
| Routes | `GET /reports?period=daily|weekly|monthly|yearly|custom&start_date=&end_date=` · `GET /reports/export.xlsx?start_date=&end_date=` |
| Calculations | Billed = bills **created** in the period (`bill_total`). Collections = billing payments + water payments + advances, split by method (CASH/CHECK/ONLINE). Expenses. Net cash flow. Charge totals come from the **stored** bill columns. "Outstanding" is the balance of **all** bills, whatever period is chosen. |
| Export | One "Summary" sheet listing every transaction; expenses appear as negative amounts |
| Permissions | SA, A, Acc, S |

- [ ] `GET /api/reports/summary?…`, `GET /api/reports/export.xlsx?…`
- [ ] Golden master of the figures for several periods
- [ ] Note: the charge totals use stored columns while "billed" uses live totals (§3.1), so the two can differ. Keep that.

### 26 · Users & Access
| Aspect | Legacy behavior |
|---|---|
| Routes | `GET/POST /users` · `POST /users/<id>/reset-password` · `POST /users/<id>/delete` |
| Rules | Username unique. Role taken from the form **without an allow-list**. Reset needs ≥ 8 characters plus confirmation, and can't reset a super_admin. You can't delete yourself or the last super_admin. |
| Permissions | SA only |

- [ ] `GET/POST /api/users`, `POST /api/users/<id>/reset-password`, `DELETE /api/users/<id>`
- [ ] Role allow-list and a minimum password length in the API (documented hardening)

### 27 · Audit Logs
| Aspect | Legacy behavior |
|---|---|
| Routes | `GET /audit` (latest 500, no filters) |
| Permissions | SA, A, Acc, S |

- [ ] `GET /api/audit?page=` (add filters only as a documented new feature)

### 28 · Rates & Rules (includes Database Migration)
| Aspect | Legacy behavior |
|---|---|
| Routes | `GET/POST /settings` · `GET /database/export.xlsx` · `POST /database/import` (the **only file upload** in the system: `excel_file`, `.xlsx/.xlsm`) |
| Settings | Corporation name and address, unit-type rates per sqm, parking/storage rates, water rate and auto-compute, penalty rate and inclusions, due/penalty day (**unused**, Q3), online payment URL and instructions, SMTP |
| Export | 10 sheets: Units, Owners, Tenants, ParkingLots, ParkingBilling, Billing, Payments, WaterReadings, Employees, Expenses. **It doesn't include** advances, users, the HR tables, community tables, settings or audit. Some columns are also left out (for example payment method on Payments and water payment fields). So this is a **partial** backup. |
| Import | Upserts by id or natural key, all in one transaction. **Takes an automatic SQLite backup** first (`cityland_condo_web_before_import_<ts>.db`). |
| Permissions | Settings: SA, A · **Export/Import: SA, M**. The import form is on the Settings page, which M can't open, while A can open it but is refused (D4). |

- [ ] `GET/PUT /api/settings` (the SMTP password is write-only)
- [ ] `GET /api/database/export.xlsx`, `POST /api/database/import`, with the same sheets and columns
- [ ] Replace the SQLite-only pre-import backup with a MySQL equivalent (`mysqldump`) before enabling import on MySQL
- [ ] Decide on D4

### 29 · Authentication & account
| Aspect | Legacy behavior |
|---|---|
| Routes | `GET/POST /login` · `GET /logout` · `GET/POST /change-password` |
| Rules | Only active users can log in. The session is cleared on login. Audit entries for Login/Logout. Change password: current password correct, new ≥ 8 characters, confirmation matches, new ≠ old. |
| Defaults | `superadmin/admin123` is auto-created when the `user` table is empty, **and the login page displays these credentials** |

- [ ] `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me` (role + `ROLE_HOME`), `POST /api/auth/change-password`
- [ ] Session-cookie approach with CSRF protection (recommended in the Stage 1 report). Waiting on your decision.
- [ ] Remove the credentials from the login page (security fix, documented)

---

## 5. Pre-existing defects and inconsistencies found

Each item must be either **fixed in the legacy app first** (so parity tests have a correct reference) or **carried over unchanged with a note**. The owner decides which.

| ID | Severity | Finding | Evidence |
|---|---|---|---|
| D1 | **FIXED 2026-10-04** · HIGH (feature broken) | Generating a Move In/Out certificate raises `AttributeError` (HTTP 500) | Line 2590 `current_user.username`; reproduced at runtime |
| D2 | HIGH (security) | `GET /unit/<id>` only requires a login, so a **resident can view any unit's owner, tenants and billing** | `@login_required` at 1503; reproduced: a resident of one unit opened TEST-502 and saw the owner's name and bills |
| D3 | MEDIUM (feature blocked) | **Admin cannot edit units or add owners.** `edit_unit` and `add_owner` allow SA, M, S, while Units is admin-only in the menu | Decorators at 1525 and 1673; reproduced: "You do not have permission" |
| D4 | MEDIUM | Database export/import allows SA and M, but the form lives on Settings (SA, A) | Decorators at 2668 and 2695 |
| D5 | **FIXED 2026-10-04** (status validated, approver recorded; only HR can create) · MEDIUM | Staff can create leave/overtime (and managers payroll) with any `status`, including APPROVED | Form field `status` accepted on create |
| D6 | **FIXED 2026-10-04** (payroll + statutory + loans saved together or not at all) · MEDIUM | Payroll reports success even when the statutory step fails, and loan balances are then not reduced | Bare `except` at 4098 |
| D7 | MEDIUM (security) | SMTP password stored in plain text and rendered into the HTML page | `settings.html` `value="{{…smtp_password}}"` |
| D8 | **FIXED 2026-10-04** (due day used; penalty day removed; 13th-month ceiling drives the exempt/taxable split) · LOW | `due_day`, `penalty_day` and `hr_13th_month_ceiling` are editable but ignored | §3.1, §3.2 |
| D9 | LOW | Saving Rates & Rules rewrites 28 unused `hr_*` keys in `setting` | 3375–3417 |
| D10 | LOW | Create-unit code reads `parking_slot_no` and other parking fields that the form never sends (dead path) | 1429 vs `units.html` fields |
| D11 | LOW | Users can be created with any role string and no minimum password length (same for resident accounts) | 3524, 3539 |
| D13 | HIGH (billing, pending Q2) | Storage dues are printed on the SOA but **left out of the amount due** | Line 472; verified on TEST-501/502 (₱250 each month not charged) |
| D12 | DATA | No unique constraints on `billing(unit_id, billing_month)`, `water_reading(unit_id, reading_month)`, `employee_attendance(employee_id, attendance_date)`, `parking_billing(parking_lot_id, billing_month)`. Uniqueness is enforced only in code. | Model metadata |

Open business questions: **Q1**–**Q9** above.

---

## 6. Data layer notes that affect every module (for the MySQL mapping)

- **31 tables.** The self-referencing `unit` FKs mean the load order is: asset units first, then residential units, then their FK updates.
- **Polymorphic IDs with no FK:** `move_certificate.person_id` and `resident_profile.person_id` (owner/tenant). `gate_pass.unit_no` is text.
- **Reserved word:** the `key` column in `setting` and `employee_hr_setting`.
- **Money types:** most amounts are `NUMERIC(12,2)` (map to `DECIMAL(12,2)`). But `unit.area_sqm`, `unit_rate_per_sqm`, `parking_lot.area_sqm/rate_per_sqm` and `water_reading.previous/current/rate` are **FLOAT**. Map them to `DECIMAL` for exactness only after the golden master confirms identical results.
- **Months** are stored as `VARCHAR(7)` `YYYY-MM` and compared as strings throughout the code. Keep that format.
- **Foreign keys are not enforced on SQLite** (no `PRAGMA foreign_keys=ON`). **MySQL InnoDB will enforce them**, so check for orphan rows before loading data, and define delete behavior (Q8).
- **Timestamps** use `datetime.utcnow` (naive UTC), while dates use local `date.today()`. Keep this; don't convert time zones during migration.
- The HR tables are currently created at runtime (`__table__.create(checkfirst=True)`). In the new backend they belong in `database/migrations/`.

---

## 7. Recommended migration order

1. **Foundation:** app factory, config, `extensions.py`, auth API, the single permission matrix, the audit service, the settings service, and the golden-master harness.
2. **Engines:** `billing_engine` and `payroll_engine` extracted as services, called by *both* the legacy routes and the new API. They must stay bit-identical.
3. **Low-risk CRUD first:** Gate Pass → Expenses → Vendors → Announcements → Documents → Audit Logs → Users & Access.
4. **Units** (after D2/D3 decisions) → **Water** → **Advance Payments** → **Billing & SOA** → **SOA Email** → **Dashboard** → **Condo Reports**.
5. **HR:** Employees → Attendance → Leave → Overtime → Loans → Payroll Rules → Payroll → 13th Month → Payroll Reports.
6. **Community:** Resident Accounts → Maintenance → Resident Portal.
7. **Move In / Out** (after D1 is fixed) and **Rates & Rules / Database import-export** last, because import touches every table.


## Defects found during the React migration (2026-10-04)

| # | Problem | Status |
|---|---|---|
| M1 | Unit types saved as "1/2/3 BEDROOM" found no Rates & Rules rate, so auto-rate units billed ₱0 condo dues | FIXED: type lookup ignores case/spacing |
| M2 | A residential unit's "manual monthly amount" was saved but billing used area × rate | FIXED: billing uses the manual amount when set |
| M3 | Billing "Other charges" and "Adjustment" (Edit SOA) were stored but never counted in the total | FIXED: counted (negative adjustment = credit) |
| M4 | Viewing a classic SOA re-saved its due date (undoing a hand-corrected due date), water, penalty and status | FIXED: the SOA is read-only (API) |
| M5 | The classic Billing list used only its month's water readings, so older bills could show other balances than elsewhere | FIXED: one calculation everywhere |
| M6 | The penalty rate/choices and the storage cut-off month are read when an SOA is computed, so changing them also changes unpaid bills | BY DESIGN (unchanged); the Rates & Rules page says so |
| M7 | The resident portal showed every published announcement, including ones marked for staff only | FIXED: residents see audience "residents"/"all" only |
| B1 | Balances carried over between months were counted again every month (3 unpaid months of ₱3,000 showed ₱12,000), and paying the latest SOA never cleared the older bills, so their amount came back on the next SOA and kept earning penalty | FIXED 2026-10-05: one running balance per unit; payments settle the oldest charges first (balances, statuses, penalty base, stored previous balance). Receipts, payments and audit history unchanged. Real data checked on a copy: no bill affected yet (one bill per unit) |
| R1 | Reports: "Total amount billed" added each bill's previous balance, so unpaid months were counted again in every later bill | FIXED: the period's own charges (dues, parking, storage, water, other, adjustment, penalty); advance credits shown separately |
| R2 | Reports: "Current outstanding" added the balances of all bills, each of which already contains the earlier unpaid ones | FIXED: what each unit still owes, counted once |
| R3 | Reports: water collections used each reading's cumulative paid amount on its last payment date (a partly paid reading counted in full in the month of its last payment) | FIXED: collections come from official receipts (as Payments & ORs), voided receipts excluded; payments without a receipt are counted and flagged |
| H1 | Payroll: generating twice for the same employee and period was allowed, paying twice and deducting loans twice | FIXED: an overlapping payroll is refused (open the existing one) |
| H2 | Payroll status could never move past the status chosen at generation | FIXED: Draft -> Final -> Paid, forward only; a Paid payroll can't be edited |
| H3 | Editing statutory amounts saved the typed totals and left the payslip's net pay unchanged | FIXED: totals and net pay are recomputed from the amounts |
| H4 | Leave / overtime could be re-decided any time; overlapping leave was accepted | FIXED: filed Pending, decided once (approver recorded); overlapping leave refused |
| H5 | Deleting an employee with records orphaned their payroll, attendance and loans | FIXED: refused; set the status to Separated |
| H6 | The prototype's payroll calculator used different statutory rules from the server (SSS brackets on basic vs raw gross; Pag-IBIG cap 10,000 vs 5,000; PhilHealth on basic vs gross) | FIXED (display): calculator and Tax Rules page now show the server's saved rules. OPEN DECISION: which statutory formulas are correct (not changed) |
| P1 | An overpayment's excess became an advance for the SAME month and was applied to the already settled bill (counted twice; hidden negative balance shown as ₱0.00); a prepaid advance recorded after a cash payment was likewise applied on top of it | FIXED 2026-10-06: excess = credit for the NEXT month; an advance never pays more than a bill still owes; recorded allocations are never rewritten. Read-only detector: database/tools/detect_overpayment_allocations.py (no historical repair). Real-data copy: no affected records |
| P2 | Balances were clamped to ₱0.00 in the SOA/unit APIs, hiding overpaid (negative) balances | FIXED: shown as a credit. NOTE: the sample unit TEST-501 shows a ₱550.00 credit (₱3,550 paid on a ₱3,000 bill in the sample data) |
| S1 | Issued SOAs changed when the penalty rate/eligibility, the storage cut-off or a water-reading row changed | FIXED (migration 0010): new bills store issue date, amount as first issued, penalty rate + eligible types, storage decision and the area × rate behind each dues line; water uses the bill's stored amount (the audited reading save still updates it, an explicit restatement) |
| S2 | LIMITATION: bills issued before migration 0010 have no snapshot (their rules were never recorded) | Not backfilled on purpose (nothing invented): they keep following the current settings; their SOA shows "Basis not recorded" unless the unit's current area × rate gives exactly the charged amount |
| S3 | Penalty default 10% (confirmed rule: 4%); water could be made penalty-eligible; rounding not explicit everywhere | FIXED: 4% for NEW installations only (a saved rate is never overwritten; the live setting is 10% and must be changed by a Superadmin in Rates & Rules); water refused/ignored in the penalty base; engine rounding ROUND_HALF_UP |
| S4 | SOA template / PDF / email | DONE: shared statement data (soa.py) for office, resident, PDF and email; server-generated A4 PDF (ReportLab, offline, Windows Arial); email lists the same figures and attaches the PDF |
| S5 | OPEN DECISIONS (code behaviour unchanged): one-time vs recurring penalty (now: assessed on each overdue statement); parking/storage penalty eligibility (now: configurable, off); penalty base after partial payment and payment allocation priority (now: oldest charges first, condo → parking → storage → water → penalty/other); grace period / timezone (now: none, server date); manual dues exceptions (now: allowed, shown as "fixed monthly amount"); credit carry-over (now: negative running balances are not carried to the next SOA) | Awaiting management |
| R1 | Resident portal accounts could only be made by typing a password per account, one unit per account | ADDED (migration 0011): Superadmin generates the account from an owner/tenant record ("Create resident portal account" when adding a person, or "Generate account" under Resident Accounts → Owners & tenants). The activation code is the existing temporary-password mechanism with an expiry (setting `resident_activation_days`, default 7), shown once; reissue replaces it. One account can hold several units after the Superadmin confirms it is the same verified person (possible matches by name/email/phone are flagged, never merged). Typed-password create/reset API kept for compatibility |
