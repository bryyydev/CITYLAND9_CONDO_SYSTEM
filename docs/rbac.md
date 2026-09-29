# CityLand 9 — Role-Based Access Control (RBAC)

**Status:** implemented 2026-09-30 and tested (57 automated tests plus 28 browser checks). **Source of truth:** [`backend/app/core/permissions.py`](../backend/app/core/permissions.py). Change access **there and nowhere else**. Pages, API, both menus and buttons all follow it.

## 1. Who can use what

✅ = allowed · — = not allowed · **bold** = changed from V10.64 · ❓ = my interpretation of the specification, **please confirm**

| Module | Superadmin | Admin | Manager (HR) | Staff | Accounting | Resident |
|---|---|---|---|---|---|---|
| Dashboard | ✅ | ✅ | — | — | — | — |
| Units, owners, tenants, parking (B3: parking list is read-only) | ✅ | ✅ (**edit units / add owners fixed, D3**) | — (**was edit-only**) | — (**was edit-only**) | — | — (**was any unit page, D2**) |
| Billing Management, SOA, payments, QR | ✅ | ✅ | — | — | **✅ read/write** | own SOA only (portal) |
| Recalculate an issued SOA from current rates (new, B1) | ✅ | ✅ | — | — | ✅ | — |
| Official Receipts ledger + printable OR (new, B2) | ✅ | ✅ | — | — | ✅ | own OR numbers on own SOA |
| Advance Payments | ✅ | ✅ | — | — | **✅** | — |
| SOA Email | ✅ | ✅ | — | — | — ❓ | — |
| Water Readings (incl. water payments) | ✅ | ✅ | — | — | — ❓ | — |
| Move In / Out, Gate Pass, Expenses | ✅ | ✅ | — | ✅ | — | — |
| Employees | ✅ | **—** ❓ | ✅ | — | — | — |
| Attendance | ✅ | **—** | ✅ | ✅ (entry) | — | — |
| Leave, Overtime | ✅ | **—** | ✅ | **—** ❓ | — | — |
| Payroll, Loans, 13th Month, Payroll Rules, Payroll Reports | ✅ | **—** ❓ | ✅ | — | — | — |
| Resident Portal | ✅ (preview) | ✅ (preview) | — | — | — | ✅ |
| Resident Accounts (create logins) | ✅ | **—** | — | — | — | — |
| Announcements | ✅ | ✅ | ✅ | **—** | **—** | ✅ (published only in the portal) |
| Maintenance | ✅ | ✅ | — | ✅ | — | own unit only |
| Vendors | ✅ | ✅ | — | **—** ❓ | **—** ❓ | — |
| Documents | ✅ | ✅ | **—** ❓ | **—** | **—** | **—** ❓ |
| Condo Reports (+ Excel) | ✅ | ✅ | — | **—** | ✅ | — |
| Audit Logs | ✅ | **—** | — | **—** | ✅ | — |
| Users & Access | ✅ | — | — | — | — | — |
| Rates & Rules | ✅ | **—** ❓ | — | — | — | — |
| Excel database export/import | ✅ | **—** | **— (D4)** | — | — | — |

Everyone can use Change Password.

### ❓ Items to confirm with the client
1. **Rates & Rules taken from Admin.** The spec says Admin is "blocked from system settings". If Rates & Rules (billing rates, penalties) should stay with Admin, change `"settings"` in the matrix.
2. **HR taken from Admin.** The spec describes HR under Manager only.
3. **Accounting has no SOA Email or water payments.** The spec says "Billing, Payments and Advance Payments". Water payments are also payments.
4. **Staff has no Leave or Overtime entry.** The spec says staff enters attendance only.
5. **Vendors and Documents** are now Superadmin/Admin only. Residents lost Documents (the spec says "Resident Portal only").

### When a resident's access ends (automatic)
A resident account is linked to a unit and, optionally, to an owner or tenant record (Resident Accounts → person). Portal access ends **immediately and automatically** when:
- the linked tenant or owner is set to **Inactive ("Past")** on the unit page, which is the normal move-out step, or is moved to another unit
- the resident account or the unit is deactivated

The resident is then signed out of both the React portal and the legacy pages, sees the reason on the login page, and gets 403 from the resident API. Setting the person back to Active restores access. It's one rule (`resident_access_problem()` in `backend/legacy_app.py`), tested in `tests/test_resident_access.py`.

**Accounts not linked to a specific owner or tenant can't be checked this way** and keep access to their unit. Link every resident account to its person.

## 2. How it is enforced

| Layer | Mechanism | File |
|---|---|---|
| Database (MySQL) | `user.role` is `ENUM('super_admin','admin','manager','staff','accounting','resident')`. The ORM `@validates` rejects other values on SQLite too. | `database/schema.sql`, `User` model |
| Legacy pages | `@guarded` looks up the page's endpoint in the matrix. Unknown endpoint = denied (fail closed). | `backend/legacy_app.py` |
| REST API | `@role_required([...])`, `@permission_required("key")` → **401/403 JSON** | `backend/app/utils/auth.py` |
| Resident data (IDOR / D2) | `@require_unit_ownership("unit_id")`: a resident's unit comes **from the database**, never from the browser, and must equal the unit in the URL. SOA ids are also checked to belong to that unit. | `backend/app/utils/auth.py`, `backend/app/routes/resident.py` |
| CSRF | Token required on every POST/PUT/PATCH/DELETE under `/api` | `protect_api_blueprint()` |
| Legacy menu and buttons | `can_access()` reads the matrix. Edit SOA / Send Email buttons follow it. | `base.html`, `billing*.html` |
| React | `ProtectedRoute` (role portal + permission), `Can` component, menus built from server-sent permissions | `frontend/src/auth/` |

The role values stay lowercase, as already stored. The decorators also accept `SUPERADMIN`, `ADMIN`, `MANAGER`/`HR`, `STAFF`, `ACCOUNTING`, `RESIDENT`.

## 3. Directory layout

```
backend/app/
├── core/
│   ├── roles.py            six roles, labels, portal names
│   └── permissions.py      THE matrix (endpoint → roles)
├── utils/
│   └── auth.py             @role_required, @permission_required, @require_unit_ownership, CSRF
└── routes/
    ├── auth.py             /api/auth/{csrf,login,logout,me}   (me → role, portal, unitId, permissions)
    ├── resident.py         /api/resident/units/<unit_id>/{summary,soa,soa/<id>,maintenance}, /api/resident/notices
    └── spa.py              serves the React build at /app/

frontend/src/
├── auth/AuthContext.jsx    user, role, unitId, portal, can(), hasRole(), <Can>
├── auth/ProtectedRoute.jsx
├── navigation.js           staff-side menu (keys = permissions) + resident menu
└── pages/
    ├── superadmin/Home.jsx     /app/superadmin/*
    ├── admin/Home.jsx          /app/admin/*
    ├── hr/Home.jsx             /app/hr/*          (Manager)
    ├── staff/Home.jsx          /app/staff/*
    ├── accounting/Home.jsx     /app/accounting/*
    ├── resident/               /app/resident/*    Home, Statements, Statement, Maintenance, Notices
    └── shared/                 Login, LegacyModule (bridge), PortalHome, Forbidden, NotFound
```

Module pages are **shared, not copied** per portal: `/app/admin/billing` and `/app/accounting/billing` render the same component. The portal decides the menu and the URL; the permission decides access.

## 4. Migrating the remaining routes (strategy)

**Step 1 (done): one gate for every existing page.** All 65 legacy `@roles(...)` lists were replaced by `@guarded`, which reads the matrix. No route body changed. `tests/test_rbac.py::test_legacy_pages_follow_the_matrix` opens every page as every role and checks it against the matrix. `test_every_legacy_page_has_a_matrix_entry` fails if a new page is added without a permission.

**Step 2 (per module, in the order in `docs/module-migration-checklist.md` §7):**
1. Add `backend/app/routes/<module>.py` with JSON endpoints, each decorated with `@permission_required("<same key as the legacy page>")`. Unit-scoped endpoints also get `@require_unit_ownership`.
2. Call the **existing** legacy functions (billing engine, payroll engine), as `routes/resident.py` does. Don't copy formulas.
3. Build the React page in `pages/shared/<Module>.jsx`, wrap elevated buttons in `<Can permission="...">`, and replace the `LegacyModule` bridge for that route.
4. Tests: API role matrix, the module's golden-master figures, and a browser check. Then mark the module in the checklist.
5. Only after sign-off: remove the legacy page and its template.

## 5. Known issue made more visible by the resident portal

**D13 (storage not charged):** the resident SOA shows the Storage line, but the total leaves it out, exactly like the legacy printed and emailed SOA. For example, TEST-502 lists ₱3,750 + ₱1,000 + ₱250 + ₱750, but the total is ₱5,500. Please decide D13 before residents get portal access.
