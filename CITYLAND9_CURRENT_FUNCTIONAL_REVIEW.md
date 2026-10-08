# CITYLAND9 — current functional review

**Date:** 2026-10-09. **Type:** review and verification only. No application code, settings, migrations
or live records were changed. No live database, client data import or restore was used.

All verification ran on throwaway SQLite databases with synthetic records, plus one isolated local
SMTP capture server. The only file added is this report. The working tree had 27 uncommitted or
untracked entries before the review, and it has exactly the same entries after it.

**Not available for this review:** a legacy database dump, and client screenshots of the old system.
None were found in the project, `migration_data/`, Downloads, Desktop or Documents. Section 7 says
what that blocks.

**Status labels:**

| Label | Meaning |
|---|---|
| ✅ **Verified** | Observed working in this review (the evidence is named) |
| ❌ **Defective** | Reproduced |
| ◐ **Partial** | Part of the workflow exists |
| ⚪ **Unverified** | Implemented in source, not observed working here |
| ⛔ **Not implemented** | |
| ⚖ **Decision** | Blocked by an unresolved business decision |

---

## 1. Summary

- **The core property and billing system works.** For all six roles, every page in the menu loaded in a real browser with no script errors, no failing API calls and no error panels (**47/47 pages**). The full backend suite passes (**413 tests**). The money flow was run end to end through the real API, and the figures agree everywhere:
  - area × rate dues, and the 4% penalty with water excluded;
  - partial payment, refusal of a double submission, and overpayment carried to the next month as an advance and applied once;
  - receipt void with reversal, and closed-period protection;
  - the same totals on the office SOA, resident SOA, billing list and PDF.
- **The SOA PDF works.** Server-generated A4 PDFs download for authorized office and resident users and are refused to others. The figures match the SOA. A long statement paginates with repeated table headers and "Page N of N". The browser Print output is clean.
- **SOA email works end to end against a local SMTP server, with two security/robustness defects.** The mail server's TLS certificate is **not verified**, and a second request **sends the SOA again**. Delivery to Gmail is **not verified**: it needs your test recipient and permission (§6).
- **The most urgent risk is operational, not code.**
  - The client's PC runs the **SQLite demo setup**, where the **scheduled daily backup does not work**.
  - That PC still had the **old version**, whose unrestricted Excel import **overwrote** 1,019 units, 1,019 tenants and 255 payments by id (reported 2026-10-08).
  - The import lock exists in source but isn't committed, pushed or installed there yet.
- **Migration from the client's old data is not possible yet.** Only a check-only dry run exists, and both business answers and the legacy data source are missing.
- **Client-parity gaps:** discounts, water advances, a resident-visible remaining credit, and keeping original receipt numbers are not implemented. Each needs a business decision first.

---

## 2. Role and module status matrix

Roles in the system: Superadmin, Admin, Accounting, Manager (HR & Payroll), Staff, Resident.
**There is no separate "Maintenance" role:** maintenance tickets are handled by Superadmin, Admin and Staff (`backend/app/core/permissions.py`).

| Area / workflow | Roles | Status | Evidence / notes |
|---|---|---|---|
| Sign-in, sign-out, CSRF, throttling | all | ✅ | `test_api_auth`, `test_security` (login throttle, CSRF on API and classic forms); crawl signed in all 6 roles |
| Password change, forced first change, policy | all | ✅ | `test_security::test_password_policy…`, `test_own_password_change_keeps_this_session_and_ends_others`; activation flow E2E (2026-10-06) |
| Session idle/absolute expiry, forged session refused | all | ✅ | `test_idle_and_absolute_expiry`, `test_forged_session_version_is_refused` |
| Deactivation ends sessions | staff, resident | ✅ | `test_deactivated_account_loses_its_open_sessions`, `test_deactivated_resident_account_loses_access` |
| Users & Access (create, role, deactivate, reset, delete; last Superadmin protected) | Superadmin | ✅ | `test_users_api` (17); page loads |
| "Developer-only" Superadmin | Superadmin | ⚖ ◐ | `create_admin.py` exists, but any Superadmin can create another in Users & Access (`users.py:39`, `STAFF_ROLES` includes `super_admin`). A procedure, not a lock. |
| Resident account generation, activation code, multi-unit links | Superadmin, Resident | ✅ | `test_resident_provisioning` (16); browser E2E 26/26 (2026-10-06) |
| Resident access isolation (own unit only) | Resident | ✅ | This review: another resident refused the SOA, PDF and cross-unit bill id; `test_idor_resident_cannot_read_another_units_data` |
| Units, owners, tenants, parking/storage assignment | Admin, Superadmin | ✅ | `test_units_api`, `test_phase_b3_parking`; crawl |
| Area-based dues (condo, parking, storage) | Admin, Accounting | ✅ | This review: 39 sqm × 51.28 = 1,999.92; 12.5 × 100 = 1,250.00; storage 4 × 50 shown but **not added before the configured cut-off** |
| Bill generation, SOA view, edit with reason, recalculate | Admin, Accounting | ✅ | `test_billing_api`, `test_phase_b1_billing`; this review generated 31 months |
| Penalty 4%, condo only, water excluded | — | ✅ (rate/base) / ⚖ (frequency) | This review: July penalty 80.00 = 4% × 1,999.92. **Frequency is a decision:** 4% is charged again every month on all unpaid condo dues, so it grows (50, 100 … 1,600 over 32 months in the long sample) |
| Payments, partial payments, official receipts | Admin, Accounting | ✅ | This review: balance fell by exactly 1,000; receipts `OR-2026-000001…`; `test_payment_integrity` (22) |
| Duplicate submission | Admin, Accounting | ✅ | This review: the same form token re-posted gives 409, one receipt only |
| Overpayment → advance (next month), applied once | Accounting | ✅ | This review: excess 500 → one advance starting 2026-08, applied 500 once; `test_overpayment_reconciliation` 5/5 |
| Receipt void (reason required) and reversal | Superadmin, Accounting | ✅ | This review: void without reason → 400; with reason → balance restored by 1,000; audited |
| Closed-period controls | Admin, Accounting | ✅ | This review: recalculate June, pay in July and regenerate July all refused (400) once closed through 2026-07 |
| Issued SOA preservation | — | ✅ | `test_soa_issue_snapshot` (rates/settings/water changes don't alter issued bills); bills before 0010 follow current rules (documented limitation) |
| Water readings and water payments | Admin | ✅ | This review: 10 m³ × 50 = 500.00; `test_advances_water_api` |
| Advance payments (prepaid dues) | Admin, Accounting | ✅ / ◐ | Works for **condo dues only**; no water advances (`record_advance_payment` docstring) |
| SOA PDF (office, resident) | Admin, Accounting, Resident | ✅ | §5 |
| Browser print of SOA | Resident (and office) | ✅ | This review: print-to-PDF of the resident SOA page is one clean page, app chrome hidden |
| SOA email | Superadmin, Admin | ✅ / ❌ | §6: works against a local SMTP server; certificate not verified; resend on repeat |
| Reports (monthly billed/collected/outstanding, export) | Admin, Accounting | ✅ | This review: July billed 4,629.92 = current charges 4,499.92 + penalties 130.00; outstanding 3,550.00 matches the open balances; `test_reports_api` (9) |
| Audit logs | Superadmin, Accounting | ✅ | `test_audit_logs_api`; this review: payments, void and generation recorded |
| Rates & Rules (penalty rate, eligibility, SMTP, closed month) | Superadmin | ✅ | `test_rates_api` (water can never be penalty-eligible; SMTP password encrypted and never returned) |
| Excel export | Superadmin | ✅ | `test_smoke`, round-trip test |
| Excel import (classic) | Superadmin | ◐ | Locked to safe round trips of CITYLAND9's own export (`test_import_check`, 17). **Uncommitted, not on the client PC.** |
| Check only (dry run for migration workbooks) | Superadmin | ✅ | `test_import_check`; browser 11/11 + 9/9 (2026-10-08) |
| Migration of another system's data | Superadmin | ⛔ / ⚖ | §7 |
| Settings, backup status | Superadmin | ✅ / ❌ (SQLite) | Page works; daily backup doesn't run on SQLite installs (§8) |
| Role home pages (dashboards) | non-resident roles | ◐ | Live mode shows a module launcher (`WorkspaceHome`), not figures; `liveApi.dashboards.*` are `notConnected` |
| Resident home, SOA list, payments, water, notices, profile | Resident | ✅ | Crawl; `test_resident_features` (9) |
| Resident maintenance requests, gate pass and moving | Resident | ✅ | `test_resident_features`, `test_operations_community_api`; crawl |
| Operations: move certificates, gate passes, expenses | Admin, Staff | ✅ | `test_operations` (9), `test_operations_community_api` (12); crawl |
| Community: maintenance tickets, announcements, vendors, documents | Admin, Staff, Manager | ✅ | Same tests; crawl |
| HR: employees, attendance, leave, payroll, tax rules | Manager | ✅ (load/API) / ⚪ (payroll figures) | `test_hr_api` (12); crawl. Payroll amounts were not re-verified against official tables in this review |
| Remaining credit visible to residents | Resident | ◐ | The statement shows "Advance applied"; there is no running credit balance in the portal (office Advances screen shows remaining) |
| Discounts | — | ⛔ / ⚖ | No discount concept anywhere in the backend |
| Original (legacy) receipt numbers | — | ⛔ / ⚖ | Receipt numbers are always generated `OR-YYYY-NNNNNN` (`legacy_app.py:1538`); no field for an original number |

---

## 3. Defects (reproduced or confirmed)

| # | Severity | Defect | Trigger | Code | Evidence |
|---|---|---|---|---|---|
| D1 | **High (ops)** | Client PC has no automatic daily backup | Any installation on the SQLite setup (`DB_ENGINE=sqlite`), which is what the client PC runs (backup files named `*.db`) | `database/backup.py` uses `local_mysql.start()` + `mysqldump`; `backend/run.py`: "a SQLite test/demo PC has none" | Source; Settings shows "Needs attention" on SQLite. Only before-import copies exist |
| D2 | **High (data)** | The unrestricted classic import is still installed on the client PC | Import workbook in the old version | old `run_excel_import` path | Client screenshot (2026-10-08): 1,019 units/tenants and 255 payments overwritten by id. Fixed in source (lock); **not deployed** |
| D3 | **Medium (security)** | SOA email does **not verify the mail server's TLS certificate** | Every SOA email | `legacy_app.py:2436` `smtp.starttls()` without a context; Python 3.14 default: `verify_mode=0`, `check_hostname=False` | This review: the client completed STARTTLS and **sent the SMTP password** to a local server with a **self-signed certificate** for a different host name |
| D4 | **Medium** | No server-side duplicate guard on SOA email | Two requests for the same bill (refresh, second admin, retry) | `routes/billing.py` `email_one` / `email_send` | This review: the second request delivered the SOA again. The UI disables the button while sending, so a single double-click is covered |
| D5 | **Medium** | Bulk email is sent synchronously, one SMTP connection per recipient, inside one web request | Emailing a whole month (≈500–700 recipients) | `legacy_app.py::email_soas` | Source; with a 20 s timeout per failing recipient a large run can exceed browser/proxy timeouts. Consumer Gmail allows ~500 emails/day (§6) |
| D6 | **Low** | Failed emails are not in the audit log (server log only); error text shows raw Python/SMTP messages | A refused or unreachable recipient | `email_soas` | This review: a 550-refused recipient was reported in the response, absent from audit |
| D7 | **Low** | Port 465 (implicit TLS) isn't supported | SMTP providers that only offer 465 | `smtplib.SMTP` + `starttls()` always | Source (Gmail 587 + STARTTLS works with the current code) |
| D8 | **Low** | Labels: the report's "billed" includes penalties, while the SOA's "current charges" excludes them | Comparing the report with SOAs | `routes/reports.py` vs `services/soa.py` | This review: 4,629.92 vs 4,499.92 (+130.00 penalties). Totals are correct; only the definitions differ |
| D9 | **Low (ops)** | `STOP_WINDOWS.bat` stops **every** CityLand server process and the "CityLand9 Server" task on the PC | Two installations on one PC (e.g. a test copy beside the real one) | `STOP_WINDOWS.bat` (matches `*backend*run.py*`) | Source (found during the 2026-10-07 rehearsal) |
| D10 | **Data-import (contained)** | Ten classic-importer defects (id overwrite, duplicates, blank resets, closed/issued bills, silent skips, unvalidated values) | Importing anything other than a safe round trip | `run_excel_import` | Reproduced 2026-10-08; now **gated** by the import lock (refuses with 409 unless safe). Fixed in code paths only by the lock, which is not deployed |

**Re-verified items from earlier reports (2026-10-05 review) that are now resolved:**
- **Overpayment double allocation:** fixed (excess goes to the next month; 5 tests pass; reproduced correctly here).
- **Default penalty:** 4% (`test_default_penalty_rate_for_new_installations_is_4`).
- **Water:** can never be penalty-eligible (`test_water_can_never_be_penalty_eligible`).
- **Issued SOAs:** frozen by the 0010 snapshot.
- **PDF content:** now shows the issue date and area/rate bases (seen in the rendered PDFs).
- **Still open from that review:** role dashboards (◐); HTTPS / backup encryption (deployment, unverified); the stale `README_V10.txt`; and the manual-dues mode, which is allowed (⚖).

---

## 4. Financial integrity results (synthetic, isolated)

Run through the real API on a throwaway SQLite database (`review_money.py`, 36 checks): **36 passed**,
plus 2 checks that failed because of my own test definitions, as explained below.

| Check | Result |
|---|---|
| Condo 39.00 × 51.28 | 1,999.92 ✅ |
| Parking 12.50 × 100 | 1,250.00 ✅ |
| Storage before the cut-off | shown, not added ✅ |
| Water 10 m³ × 50 | 500.00 ✅ |
| July penalty | 80.00 = 4% × unpaid June condo; water and parking excluded ✅ |
| Previous balance | equals June's total (3,749.92) ✅ |
| Office SOA = resident SOA = billing list = PDF | 7,079.84 everywhere ✅ |
| Partial payment | balance −1,000.00 exactly ✅ |
| Duplicate form submit | 409, one receipt ✅ |
| Overpayment | bill balance 0.00 (not negative); one advance of 500.00 from 2026-08 ✅ |
| August | advance applied 500.00 once, no penalty, no carried balance ✅ |
| Void | no reason → refused; with reason → balance +1,000.00; audited ✅ |
| Closed period through 2026-07 | recalculate, pay and regenerate refused ✅ |
| Monthly report | outstanding 3,550.00 = open balances ✅. "Billed" includes penalties (see D8). My check compared against current charges, so it failed by 130.00 = the two penalties |
| Long statement | 32 earlier unpaid months listed inline; this fit on **one** page. My check expected two pages, so it "failed"; the 70-payment sample below is 3 pages ✅ |

**Current implementation versus open policy (not invented here):**
- Penalty frequency is monthly and recurring.
- Payments settle the oldest charges first.
- Partial-payment penalty base: the unpaid eligible charges.
- Grace period: none.
- Penalty start: the due date has passed, by the server date.
- Manual dues: allowed.
- Credit carry-over: negative balances are not carried.

All of these remain **decisions for management** (`docs/module-migration-checklist.md` S5; mapping report §9).

---

## 5. SOA PDF

| Check | Result |
|---|---|
| Office download (Admin, Accounting) | ✅ 200, `application/pdf`, starts with `%PDF`, A4 (595×842 pt) |
| Resident download, own unit | ✅ |
| Another resident, staff | ✅ refused (403/404) |
| Figures | ✅ total, condo dues and penalty in the PDF text equal the SOA API |
| One-page layout | ✅ rendered and inspected: header, account, charges with area × rate bases, penalty base sentence, totals box, payments, how to pay, footer "Page 1 of 1"; nothing clipped |
| Multi-page | ✅ 70 payments → **3 pages**; table header repeated on page 2; all 70 rows present; balance printed; "Page 3 of 3" |
| Error feedback | ✅ `test_generation_failure_is_an_error_not_a_file` (a JSON error, not a broken file); the UI shows "Preparing PDF…" and errors through the shared download helper |
| Browser Print | ✅ one clean page |

Receipts (official receipt PDF) are produced in the browser with html2pdf (`Ledger.tsx:109`), not by the server. They were not re-verified in this review.

---

## 6. SOA email and Gmail

### Workflow traced
- **Who can send:** Superadmin and Admin (`billing_email`, `email_bill`, `send_billing_emails`). Accounting is refused (verified).
- **Single bill:** `POST /api/billing/<id>/email` → 409 if no owner/tenant opted in.
- **Bulk:** `GET /api/billing/email?month=` (overview, `smtpConfigured`) → `POST /api/billing/email/send`.
- **Recipients:** current owners/tenants with an email **and** `receive_soa_email` on, de-duplicated. Units without one are counted as "skipped".
- **SMTP:**
  - host, port (default 587), sender and username come from Rates & Rules;
  - the password is stored **encrypted** (`enc:v1:` Fernet derived from `SECRET_KEY`) and never returned;
  - 20 s timeout; `EHLO → STARTTLS → EHLO → AUTH`;
  - **no certificate verification (D3)**; no port-465 mode (D7).
- **Content:** subject "Statement of Account - Unit X - YYYY-MM"; plain text plus HTML body from the same statement data; **PDF attached**.
- **Feedback:** sent/skipped/failed counts and the first error shown in a toast.
- **Audit:** each success is audited, failures are not (D6).
- **Repeat:** the UI blocks a double click while sending; the server re-sends on a second request (D4). There is no delivery tracking beyond "accepted by the SMTP server".
- **Test installations** (`CL9_TEST_INSTALL=1` / `OUTBOUND_EMAIL=disabled`): refused before any connection (`test_test_pc`).

### Results

| Level | Result |
|---|---|
| Mocked/captured workflow | ✅ against a **real local SMTP server** (127.0.0.1, STARTTLS, synthetic recipients): single send accepted; message has To/Subject/HTML/PDF; bulk with one refused recipient → sent 1, failed 1; unreachable server → clean failure; each success audited. 13/14 checks; the 14th was a setup issue (that unit had no bill that month) |
| SMTP acceptance by Gmail | **Not tested.** No authorized SMTP configuration or test recipient was available. Nothing was sent to the internet |
| Actual Gmail inbox receipt | **Unverified** |

### To test real delivery
1. You enter the sender account in Rates & Rules → Email yourself (never in chat). For Gmail as the **sender**: `smtp.gmail.com`, port **587**, the full address as username, and an **App Password**, which requires 2-Step Verification ([App passwords](https://support.google.com/mail/answer/185833?hl=en); [sending from an app](https://knowledge.workspace.google.com/admin/gmail/send-email-from-a-printer-scanner-or-app)).
2. You give me **one test recipient address** (e.g. your own Gmail) and permission to send **one** synthetic SOA.
3. I send it once, without retrying, then report three things separately: generated, accepted by SMTP, and received in the inbox (your confirmation).

Sending **to** a Gmail recipient works with any SMTP sender. Using Gmail **as the sender** is subject to Google's limit of about **500 emails per day** for consumer accounts ([Gmail sending limits](https://support.google.com/mail/answer/22839?hl=en)); some sources cite lower limits for SMTP clients, which is unverified. With ~500–700 opted-in units a month, plan a Google Workspace account or a transactional mail provider, and fix D5 first.

**Email needs internet access.** Everything else (billing, payments, PDFs, portal on the LAN) works offline; only sending SOAs fails without internet, and it fails cleanly.

---

## 7. Migration readiness and client parity

- **Ready:**
  - stable source identity (migration 0012, crosswalk);
  - Check-only validator: blank vs missing vs zero vs clear; invalid areas and months; unresolved relationships; closed periods; issued and hand-corrected bills; no writes;
  - import lock.

  All tested (2026-10-08); **uncommitted**. The live database is at 0011 and needs 0012.
- **Not ready:**
  - no importer exists for another system's data;
  - the client's property list: 0 of 1,019 rows ready (141 with zero size; PARKING meaning, people rules and rate not decided);
  - financial history: no source, cutoff or mode decided.
  - CITYLAND9 recalculates carried balances and penalties from history, so **opening balances need their own design**.
- **Legacy dump and screenshots:** not available. Not inspected; no cutoff can be inferred.

| Client workflow | CITYLAND9 today | Need |
|---|---|---|
| Property and owner/tenant records | ✅ data model; import ⛔ | Answers on PARKING, multi-name cells, zero sizes; then the properties importer |
| Dues | ✅ area × rate | The rate amount |
| Water advances | ⛔ (advances are condo-only) | Decide whether the old system's water advances must carry over |
| Discounts | ⛔ | Decide whether discounts exist, and their rule |
| Remaining credit | ◐ office only | Show the residents' credit balance (essential if the old system did) |
| Payment allocations | ✅ oldest first (⚖ policy) | Confirm it matches the client's practice |
| Original receipt references | ⛔ | A field for the legacy OR number, so imported payments keep their original numbers |
| Historical balances and SOAs | ⛔ | Source + cutoff + mode + opening-balance design |
| Excel exports | ✅ | — |
| Migration validation | ✅ Check only | — |

---

## 8. Windows and operations

| Item | Status |
|---|---|
| Live vs mock | ✅ live API; built assets contain no mock-mode marker; `frontend_build.py status` = current |
| Frontend build | ✅ `tsc --noEmit` clean; `vite build` into a scratch folder succeeded (the committed `dist` was not touched) |
| Startup/shutdown/ports | ⚪ `START_WINDOWS.bat` ran on a disposable copy (2026-10-07); port-in-use detection in source. `STOP_WINDOWS.bat` affects every installation on the PC (D9). Not re-run here |
| Backups (MariaDB) | ⚪ the daily task (`register_tasks.ps1`, `-Daily -At $BackupTime`), retention, `last_backup.json` and `/readyz` warnings exist; restore drills passed on disposable MariaDB (2026-10-07/08). The scheduled task itself was never run on Windows |
| Backups (SQLite, the client PC) | ❌ D1 |
| Database privileges | ✅ app account data-only, separate migration account (rehearsed 2026-10-07) |
| Logging, `/healthz`, `/readyz` | ⚪ source and earlier checks; not re-run |
| LAN security | ⚪ HTTP on the LAN by default (`SESSION_COOKIE_SECURE=0`); HTTPS via a reverse proxy is documented, not set up |
| Safe updates to the client PC | ◐ the documented rule is to keep `.env` and `database\` (`docs/run-guide/07-OPERATIONS.md:143`). A GitHub ZIP extracted to a new folder leaves the SQLite file and `.env` in the old folder; there's no written procedure for that case |
| Live DB schema | needs `db_migrate.py upgrade` (0011 → 0012) before the next start with this code |

Not run in this review: no script that starts, initializes or migrates a real database.

---

## 9. Essential before client testing / migration

1. **Deploy the import lock and stop data loss:** commit → push → update the client PC. Check the earlier `…before_import_….db` copies on that PC for overwritten data.
2. **Backups on the client PC (D1):** move it to CITYLAND's MariaDB (as in `CITYLAND9_TEST_PC_MIGRATION.md`), or add a SQLite backup to the scheduled task. Then restore-test.
3. **Verify the email TLS certificate (D3)**; add a server-side duplicate guard and audited failures (D4, D6).
4. **Client answers:** rate, PARKING billing, multi-name cells, zero sizes, penalty frequency, discounts, water advances, past-bill source and cutoff.
5. **One authorized Gmail delivery test** (§6).

## 10. Essential missing client features (decide first, then build)

- Properties importer (units → owners/tenants) on top of Check only, writing the crosswalk.
- Historical bills and opening balances: needs a design, because carried balances are recalculated.
- Legacy receipt number field.
- Resident credit balance in the portal.
- Water advances and discounts: only if the client confirms they exist.
- Bulk email as a background job with progress and per-recipient status (D5).

## 11. Optional, can wait

- Role dashboards with figures.
- Server-side receipt PDF.
- Port 465 support.
- Report label alignment (D8).
- HTTPS reverse proxy.
- Retiring `README_V10.txt`.
- Payroll figure re-verification.
- `STOP_WINDOWS.bat` scoping (D9).

## 12. Tests actually run (this review)

| Run | Result |
|---|---|
| Full backend suite (`pytest`, isolated SQLite from `tests/conftest.py`) | **413 passed** (137 s) |
| `npx tsc --noEmit` | clean |
| `npx vite build --outDir <scratch>` | built; committed `dist` untouched and current |
| Role crawl, headless Edge, isolated scratch server (6 roles) | **47/47** pages: no exceptions, no failed API calls, no error panels |
| End-to-end money script (throwaway SQLite, synthetic) | 36/38 (the 2 explained in §4) |
| Multi-page PDF (70 payments) | 3 pages; all rows, balance and page numbers present; rendered and inspected |
| Rendered PDFs inspected | office one-page, resident long statement, resident 3-page, browser print |
| SOA email against a local STARTTLS SMTP server with a self-signed certificate | 13/14 (1 setup); D3, D4, D6 reproduced |
| `tests/test_overpayment_reconciliation.py` | included in the suite (5 passed) |
| Gmail delivery | **not run** (no authorization, recipient or configuration) |

## 13. Ordered backlog (small steps)

1. Commit the current work, deploy it to the client PC, and upgrade that database to 0012. Confirm the import lock there.
2. Backups for the client PC: MariaDB move, or a SQLite backup in the daily task, plus a restore test.
3. Email hardening: verified TLS context, a duplicate guard per bill and month, audited failures, sanitized error text. Add tests with a local TLS server.
4. One authorized Gmail test send.
5. Collect the client's answers, then build the properties importer (crosswalk, rerun-safe) and stage it.
6. Decide the opening-balance and history design, add a legacy OR number field, then plan the financial import.
7. Show residents their credit balance; add discounts and water advances only if confirmed.
8. Move bulk email to a background job.

---

## Top three next tasks and how to verify locally

1. **Deploy the safeguards** (commit, push, update the client PC, upgrade the database):
   ```powershell
   .venv\Scripts\python.exe -m pytest -q
   .venv\Scripts\python.exe scripts\frontend_build.py status
   .venv\Scripts\python.exe database\db_migrate.py status
   ```
2. **Give the client PC working backups** (after moving it to MariaDB):
   ```powershell
   .venv\Scripts\python.exe database\backup.py
   .venv\Scripts\python.exe database\restore.py <newest database\backups\cityland9_auto_*.sql.gz> --into cityland9_restore_test --drop-after
   ```
3. **Harden SOA email, then one Gmail test:**
   ```powershell
   .venv\Scripts\python.exe -m pytest -q tests\test_soa_pdf.py tests\test_billing_api.py tests\test_rates_api.py
   ```
   Then a single authorized send from Billing → open a bill → **Email**, with SMTP set in Rates & Rules → Email.
