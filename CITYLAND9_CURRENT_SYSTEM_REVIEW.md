# CITYLAND9 Current System Review

## Scope and evidence limits

This is a review of the current local source and committed frontend build. It is review-only: no application code, settings, migrations, templates, or existing business data were changed. No production database or `.env` values were read. The worktree was clean at the start of review.

Evidence is labeled as:

- **Observed** — directly seen in the local browser or command output.
- **Source-confirmed** — established by current code, tests, or project documentation.
- **Unverified** — depends on a deployment setting, actual account, screenshot, or environment not available during review.

The requested SOA screenshot was not present in the available attachment/browser context. Consequently, this report assesses the SOA’s source-defined document and styles, not the screenshot’s actual pixels. The resident and staff workflows were not clicked through: the local browser was unauthenticated, and `/api/auth/me` returned the expected `401`. Do not treat inferred UI behavior below as an authenticated end-to-end observation.

## Executive summary

1. **High — confirmed synthetic financial defect:** an overpayment can be allocated both to the selected bill and to an advance that is immediately applied to that same billing month. In a synthetic isolated test, a ₱13,000 receipt on a ₱12,500 bill resulted in ₱12,500 recorded as bill-paid and ₱500 of advance applied to the same bill. The recalculated total fell to ₱12,000 while `amount_paid` stayed ₱12,500; the raw balance was -₱500 and the SOA status was Paid. The displayed balance is clamped to zero, obscuring that over-allocation.
2. **High — confirmed non-frozen historical figures:** issued condo and parking dues are normally stored, but historical SOAs also depend on current penalty settings, the current storage cutoff, and the water reading for that month. A settings/readings change can therefore change a prior SOA without recalculating that bill. An existing test explicitly demonstrates a storage cutoff changing an already-issued bill.
3. **Medium — confirmed policy mismatch in defaults:** the code default penalty rate is 10%, not the confirmed 4%. The deployed setting was not read. Water is excluded by default, but Rates & Rules permits enabling it in the penalty base, which conflicts with the confirmed water exclusion unless that control is constrained.
4. **Medium — confirmed pricing exception:** condo dues and storage support manual monthly amounts, despite the confirmed area × rate requirements. Parking uses area × rate. Decide whether manual exceptions are still authorized.
5. **Medium — SOA document omissions:** the printable React SOA does not disclose the condo/parking/storage area and rate, issue date, penalty percentage/base, or configured payment instructions. A QR appears in the office bill drawer, outside the printable SOA element; the resident SOA has no QR.
6. **Medium — deployment protection is not verified:** the documented default LAN path is HTTP, and backup artifacts are compressed SQL rather than demonstrably encrypted backups. HTTPS, disk encryption, access control, and recovery readiness depend on deployment and were not verified.

No Critical finding was established. The main questions to settle before implementation are listed at the end; the report does not infer management approval for unresolved policies.

## 1. Current status and architecture

### Working tree and recent work

- **Observed:** `git status --short` was empty before review; there were no staged, unstaged, or untracked changes to preserve.
- **Source-confirmed:** the recent commit series includes the new Flask domain API route modules, the React role workspaces, and the Windows startup/build checks. Current code has both the older Flask/Jinja pages and the React application/API; the former has not been removed.
- The recent billing work includes an oldest-charge-first running balance intended to prevent repeated carry-forward of the same arrears and to stop paid dues accruing penalties. `tests/test_balance_carryover.py` covers those cases. The separate overpayment/advance interaction described below is not covered by its current result assertions.
- The code and docs support a portable first-run SQLite demo installation and a MariaDB server installation. The actual database engine and settings on the production server were not inspected.

### Runtime modes

| Entry point | What it does | Review note |
|---|---|---|
| `START_WINDOWS.bat` | Starts the Windows server workflow. With no `.env`, it runs first-run setup, which can configure a database and load demo data. Otherwise it checks the Python environment, checks the React build stamp, rebuilds when needed (or with `--rebuild`) if Node is available, validates, then starts Waitress on port 5000. | **Not run:** first-run startup can create/seed a database and a rebuild can replace `frontend/dist`. Current source says the React build is served by Flask at `/app/`. |
| `cd frontend; npm run dev` | Starts the Vite development frontend on port 5173; API requests are proxied to the Flask server on port 5000. It uses the live data source and requires the backend separately. | The user’s two listed `npm run dev` entries are literally identical; they do not describe two modes. |
| `cd frontend; npm run dev:mock` | Starts the prototype with in-memory mock data and a role/persona switcher. It does not write to the application database and resets on reload. | Use this only to explore prototype flows, not to validate live persistence or permission enforcement. |
| `npm run build` | Runs the TypeScript check and Vite production build using the live source alias. | Build was not rerun because it writes to the committed `dist`; the current build was checked without replacing it. |

**Live-build verification:**

- **Observed:** opening `http://127.0.0.1:5000/app/` displayed the React sign-in page. The unauthenticated auth check returned 401.
- **Source/build-confirmed:** `frontend/dist/.source-hash` matches the current frontend source hash. The compiled assets were checked for prototype/demo marker strings and no mock banner, demo persona names, or demo credentials were found.
- This verifies the local served build is current and live-mode, not that a separate production server is running the same revision or has the same environment configuration.

### Architecture and service boundaries

- `backend/legacy_app.py` remains the central model, legacy route, shared authentication, and financial calculation module.
- Domain API blueprints under `backend/app/routes/` cover authentication, billing, receipts, advances, rates, units, residents, water, community, operations, HR, reports, users, resident accounts, settings, and audit logs. They reuse the legacy model/calculation layer rather than maintaining a second SOA calculator.
- React routes and role workspaces are in `frontend/src/App.tsx`; module metadata and access menus are in `frontend/src/config/modules.ts` and `roles.ts`. The live service is `frontend/src/services/liveApi.ts`.
- The React SOA detail and resident portal call the same service serializer, `backend/app/services/soa.py`, which delegates amounts to `legacy._bill_calc`. React displays the returned figures; its correction-form total is a client-side preview, not the authoritative calculation.
- Classic Flask routes remain accessible as legacy screens and compatibility paths. Current module metadata has no `live: false` entries, so the React router’s classic-screen bridge is not presently the normal route for any listed module.

## 2. Role and module status

The following is based on the current role menus, backend permission matrix, React route map, and live API service. “Live API” means the source has a corresponding Flask endpoint/service call; it is not a claim that each workflow was exercised in an authenticated browser session.

| Role | Permitted modules in current role menu | Interface/API status and material limitations |
|---|---|---|
| **Superadmin** | Dashboard; Users & Access; Resident Accounts; Rates & Rules; Audit Logs; Settings; All Property Modules launcher. The launcher includes property/billing, operations, community, and HR modules, subject to each module’s backend permission. | React pages and live APIs for staff/resident accounts, rates, audit, system settings, and the module pages. The live system dashboard API is explicitly unconnected; the live `/app` dashboard renders the generic module launcher rather than dashboard data. |
| **Admin** | Units (owners, tenants, parking/storage assignments); Billing & SOA; Payments & ORs; Advances; Water; Certificates; Gate Passes; Expenses; Maintenance; Announcements; Vendors; Documents; Property Reports. | React screens with live Flask APIs in the source. Admin is not permitted to create staff/resident portal accounts, change system rates/settings, or view audit logs under the backend matrix. The property dashboard API is not connected; the live `/app` dashboard is the generic workspace launcher. |
| **HR & Payroll Manager** | Employees; Attendance & Overtime; Leave; Payroll; Tax Rules; Announcements. | React screens and corresponding HR/announcements endpoints are present. Employee edit, attendance, leave/overtime decisions, payroll generation/statutory inputs, loans, and HR rules have API methods. The live HR dashboard is not connected; the live `/app` dashboard is generic. |
| **Staff** | Gate Passes; Move In/Out Certificates; Maintenance; Expenses; Daily Attendance Entry. | React screens and corresponding operations/HR endpoints are present. The permission matrix limits staff to these data-entry/front-desk areas; no financial reports or audit logs. The live staff dashboard API is not connected; the live `/app` dashboard is generic. |
| **Accounting** | Financial Reports; Billing & Advance Payments; Collections & ORs; Audit Logs. | React screens and live billing, advance, receipt, report, and audit APIs are present. The backend matrix gives accounting full billing/payment/advance access and receipt void access, but not Rates & Rules or water-reading administration. The live accounting dashboard is generic rather than API-backed. |
| **Resident** | Home; SOA; Payment History; Water Usage; Maintenance request; Gate Pass & Moving; Announcements/Notices; Profile & Password. | React resident portal and unit-scoped APIs are present. Residents can view statements/receipts/water, update contact details, file maintenance and gate-pass requests, and cancel an unhandled pass. There is no resident payment-submission gateway in the exposed service; the printed document only gives office payment guidance. The dashboard is connected to resident summary data. |

### Module actions and incomplete areas

| Module group | Source-defined actions | Gaps, caveats, or access boundary |
|---|---|---|
| Users & Access / Resident Accounts | Staff account creation/editing/deactivation and password reset; resident account creation/linking, edits, and reset. | Staff accounts and resident accounts are separately permissioned; account creation is Superadmin-only. No authenticated form interaction was performed. |
| Units / Owners / Tenants / Parking / Storage | Unit directory and detail; owner/tenant add/edit; parking and storage are modeled as assigned area-bearing units. | `liveApi.units.list` and `.people` are explicit `NotConnectedError` placeholders, although the current React unit page uses paginated/detail/person methods. No production unit records were inspected. |
| Billing / SOAs / Payments / Advances / Receipts | Preview and generate bills; view/correct SOA; optionally recalculate; record full/partial payment; auto-record excess as advance; record advance; receipt ledger/detail/void; email; PDF; office QR. | Recalculation deliberately reprices selected bills from current rates. Automatic excess allocation has the same-month over-application issue below. Receipt void is limited to Superadmin/Accounting. |
| Water | Enter/correct readings, mark water paid, and record water receipts. | Reading changes flow into historical SOA calculations. Water eligibility for penalties is configurable in Rates & Rules even though the confirmed requirement excludes water from the condo-dues penalty base. |
| Rates & Rules / Settings / Audit Logs | Rates, penalty flags, cutoff/closed-book settings, payment/email configuration; system database export/import; audit search/export. | Audit is read-only; Settings/backup import/export and rate controls are Superadmin-only. Actual live values, online-payment configuration, SMTP configuration, backups, and closed-book date were not read. |
| Maintenance / Gate Passes / Announcements / Documents / Vendors / Expenses / Certificates | Create/update or review workflows are represented by React pages and corresponding APIs; residents can request maintenance and gate passes and view notices. | Resident unit scoping applies to resident endpoints. The review did not submit any form; deployment data, attachment configuration, and notifications were not tested. |
| HR | Employee roster, attendance entry/history, leave/overtime requests and decisions, payroll preview/generation, statutory settings, payslips/reports, and loans. | HR pages are permission-gated to Manager/Superadmin except staff attendance entry. Dashboard data is not connected in the live build. Payroll correctness was outside this SOA review. |

Navigation and guards: the React router sends unauthenticated users to `/login`, redirects users entering another role’s workspace to their own, requires forced password change when flagged, and checks the backend-supplied permission before rendering a module. Compatibility redirects preserve query strings for selected old paths. React forms use their page-level validation, confirmation, toast, and error-state components; actual interaction, scrolling, and return navigation remain unverified because no authenticated browser session was available.

## 3. SOA computation traced end to end

### Calculation path

1. **Bill creation and due date:** `generate_bills(month)` validates `YYYY-MM`, skips an existing bill for a unit/month, calculates the configured due day (clamped to the month’s last day), snapshots condo dues, parking dues, storage dues, and water amount, determines prior balance/penalty, allocates scheduled advances, audits, and commits. See `backend/legacy_app.py` (`due_date_for`, `generate_bills`).
2. **Condo dues:** normally `area_sqm × unit-specific or type-based rate`, rounded by the Decimal money helper. A unit in `dues_mode == "manual"` with a positive manual monthly amount overrides this formula.
3. **Parking:** assigned parking-unit area × assigned rate, falling back to the configured parking rate. This is area-based in the current calculator.
4. **Storage:** when the residential unit is configured to include storage, its assigned storage asset is charged by area × rate, unless that asset uses manual dues. Whether the amount is included in a bill also depends on the global `storage_in_total_from` cutoff.
5. **Water and current charges:** water is calculated from the monthly reading and rate. If marked paid separately, it is shown but removed from the bill total. Other charges and adjustments are included. Current charges plus prior unpaid balance, penalty, and advance application produce the SOA total.
6. **Prior balances and payments:** `_prepare_bill_calculation_cache` traverses a unit’s bills chronologically and carries one running balance forward rather than summing already-cumulative balances. Payments are applied oldest-charge-first; inside the charge buckets the current source order is condo, parking, storage, water, then penalty/other/adjustment. Advances apply to condo dues only. This is current behavior, not a confirmed management allocation policy.
7. **SOA display:** billing and resident APIs use `backend/app/services/soa.py`, which serializes `_bill_calc` values. Both React screens render the shared `SoaDocument`. The frontend does not independently compute authoritative dues/penalties. The mock prototype has its own mock calculator and must not be used to validate production amounts.

### Confirmed requirements versus current behavior

| Rule | Confirmed requirement | Current source behavior / open issue |
|---|---|---|
| Condo dues | Area in sqm × applicable rate per sqm. | Formula is used in per-sqm mode; manual mode can override it. |
| Parking/storage dues | Also area-based. | Parking is area-based. Storage is area-based except for manual-mode storage assets. Inclusion in SOA additionally depends on a global cutoff. |
| Penalty rate/base | Eligible overdue condo dues have a 4% penalty. Water and unrelated charges must not increase this condo-dues base. | Rates default to 10%; default inclusion is condo on, parking/storage/water off. The settings API permits changing the rate and enabling water. The running server’s actual settings are unverified. |
| Penalty timing/frequency | Not yet decided. | Penalty is recalculated using current settings and is gated by `date.today() > bill.due_date` for the bill being calculated. A prior unpaid amount can appear without penalty while the current SOA’s own due date has not passed. Each overdue billing month can add another assessment against the then-unpaid eligible base; this is effectively recurring, not a single stored event. |
| Partial payment | Not yet decided. | Payment is allocated oldest month first, then by the calculator’s charge-bucket order. That reduces eligible unpaid buckets; a previously paid condo balance stops contributing to later penalty bases. This allocation order is an implementation choice, not an approved policy. |
| Advances/credits | Not yet decided. | Advances offset condo dues and are automatically applied to months within their coverage. An overpayment is made into a one-month advance starting in the selected bill’s own month; the same-month allocation causes the confirmed issue below. Credits/negative balances are not carried forward as a general balance. |
| Due date/grace period | Not yet decided. | The configured due day is clamped to month end. Penalty begins only when the server’s local `date.today()` is strictly later than the bill due date. No additional grace period or explicit timezone policy is applied here. |
| Rounding/history | Not yet decided beyond the supplied ₱80 example. | Core amount helpers use Decimal and round-half-up, while direct `Decimal.quantize("0.01")` calls in penalty/SOA serialization use the Decimal context’s default rounding (normally half-even). Historical statement inputs are not wholly frozen. |

### Confirmed high-priority reconciliation defect

`record_bill_payment` caps the bill payment at the pre-payment balance, turns any excess into an advance whose `start_month` is the same bill month, then calls `allocate_advances_for_month` for that same month. The adjacent comment says the advance “remains available for the next month,” but the call allocates it to the current bill.

**Safe reproduction:** ran the existing isolated fixture against a synthetic future bill for `TEST-503`, not the live database. Before payment: total/balance ₱12,500.00. Recorded cash: ₱13,000.00. The function reported ₱12,500.00 applied to the bill and ₱500.00 excess; same-month advance application was ₱500.00. Afterward, total was ₱12,000.00, `amount_paid` remained ₱12,500.00, raw balance was -₱500.00, and status was Paid. The SOA serializer clamps displayed balance to zero.

`tests/test_billing_api.py::test_payment_result_and_detail` checks the receipt number, initial applied amount, excess amount, paid status, and receipt link, but does not assert the final advance application or financial reconciliation. The synthetic run demonstrates the gap. Before SOA/payment work proceeds, define how an excess received for one statement is allocated and ensure cash, bill payment, advance applications, receipt allocations, ledger totals, and subsequent SOAs reconcile exactly once.

### Historical statement stability

The code comment says issued bills are frozen, and generated condo/parking amounts are normally stored. However:

- Penalties in `_prepare_bill_calculation_cache` use current `penalty_rate` and current inclusion flags when rendering prior bills.
- `storage_charged(bill)` compares the bill month to the current global storage cutoff, so changing the cutoff changes whether an existing bill includes storage.
- The test `tests/test_phase_b1_billing.py::test_storage_is_added_only_from_the_cutoff_month` changes the cutoff and verifies an already-created bill’s total changes.
- A bill’s water amount is derived from the selected reading for that month, so correcting that reading can change a historical SOA.
- An explicit “Recalculate” action reprices condo/parking/storage from current rates; the React UI warns and confirms this and records the change.

The first three are current, source-confirmed behaviors; whether water corrections should restate old statements is a business/audit policy question. At minimum, distinguish original-issued values from current restated values and record who/why changed them.

## 4. SOA document and screenshot-specific review

### Screenshot limitation and arithmetic

The screenshot itself was unavailable, so its layout, visual defects, QR controls, and source record cannot be verified. The following are arithmetic checks, not validation of the screenshot’s database contents:

- `39.0 × ₱51.28 = ₱1,999.92` exactly.
- `₱1,999.92 + ₱1,999.92 + ₱550.00 = ₱4,549.84` before a penalty, payment, or advance. This addition is correct.
- `₱1,999.92 × 4% = ₱79.9968`, which rounds half-up to ₱80.00.
- With the code default of 10%, the same base would be ₱199.992, normally displayed as ₱199.99. The source does not establish the deployed system’s actual rate or the screenshot’s underlying records.
- The ₱550 water amount could result from a reading/rate combination, but no such real reading was queried. No parking/storage omission is inferred from an absent line in an unavailable screenshot.

The apparent “overdue condo dues but ₱0 penalty” can be explained by the source’s timing gate: penalty is checked against the due date of the bill being calculated, not simply the presence of any older unpaid balance. If that statement’s own due date has not passed, its penalty can be zero while earlier unpaid months are listed. Other possibilities include no eligible unpaid base, different stored/configured behavior, or a manually corrected statement. The unavailable screenshot and unqueried records prevent choosing among them.

### Current document source assessment (not pixel inspection)

- **Hierarchy/readability:** white-paper styling, a property header/status, a four-column metadata grid, charge table, totals, and payment table are defined in `frontend/src/components/feature/Documents.tsx`. Text is generally 13–16 px in the body, with some labels at 10.5–11.5 px; real-world print readability/contrast was not browser-measured.
- **Identity/period:** the document displays corporation, unit number, billing period, due date, status, and balance. It does not display an issue/statement date or payer/contact identity in the printable SOA itself. Contact details appear separately in the office drawer and are not part of the printed article.
- **Charge explanation:** the SOA labels condo dues by month but omits condo area and rate and omits parking/storage area and rate. Water can show usage × rate. Previous balance is a single amount rather than an itemized aging schedule in the printable document. The office drawer separately lists earlier unpaid months.
- **Penalty:** only a positive penalty is rendered, with the generic detail “Late payment.” The printed SOA does not show penalty rate, eligible base, period/frequency, or assessment date.
- **Totals/payment history:** total amount due, amount paid, and balance are shown, with those balance figures intentionally repeated in the metadata and totals section. Its payment rows are payments linked to that bill, not a complete account-wide ledger; resident payment history is a separate screen.
- **Payment instructions and QR:** the printable article contains fixed “Pay at the Admin Office by cash, check or online transfer” text rather than the configured payment instructions returned by the API. The office billing drawer can show one QR image outside the `SoaDocument` element, so the PDF created from the document reference excludes that QR. The resident SOA has no QR. Two QR buttons were not found in the current React SOA source; whether a live QR endpoint has a configured, usable destination was not tested.
- **Printing/PDF/mobile:** print and `html2pdf.js` actions use an A4 PDF configuration. A print stylesheet hides navigation and retains the print sheet. The drawer has independently scrolling content and fixed actions. No browser test verified long-history pagination, A4 page breaks, mobile overflow, QR readability, or exact screenshot appearance; the PDF configuration does not itself prove that multi-page statements paginate cleanly.
- **Empty/internal content:** water can show “No reading for this period”; payment history can be empty. No internal staff instructions are in the React SOA article source. This is a source observation, not an assessment of the screenshot.

## 5. Security and data protection

### Protections evidenced in source

- **Role/access control:** one backend permission matrix is used by legacy routes and APIs; unknown permission keys fail closed. React module guards also check the permissions returned by auth. Resident unit-scoped routes require ownership, and an SOA detail route verifies the bill belongs to the requested unit.
- **Authentication:** Werkzeug password hash/check helpers are used. Auth accepts active accounts only. Current-user validation checks account activity and session version; password changes/resets/deactivation increment or invalidate session state. Idle and absolute session expiry and login throttling are implemented.
- **CSRF and replay:** unsafe classic requests check the session CSRF token; protected API blueprints require the `X-CSRFToken` header. Payment forms use one-time submission tokens. Payment/receipt paths use row locking and transactional persistence, and audit entries accompany key financial actions.
- **Secrets:** the security helper enforces a strong production Flask secret key and encrypts the SMTP password using a key derived from it. The repository docs say SMTP credentials are not returned to the browser. No secret values were read or included in this report.
- **Receipts:** the payment code creates receipt allocations and audit details. Existing documentation and source describe unique receipt sequencing and reversal/void support. Concurrency behavior was code-reviewed, not stress-tested against the deployed database.

### Deployment/data-protection items not verified

- The security guide explicitly says plain LAN HTTP transmits passwords and data unencrypted; HTTPS via a reverse proxy is recommended but the full proxy deployment is untested. No production proxy/TLS/session-cookie configuration or office-network observation was available.
- The backup tool creates compressed `.sql.gz` database dumps. The operations guide warns that backups include resident/financial data and password hashes and relies on restricted storage; it does not establish encryption of backup artifacts. Disk/volume encryption and backup-share ACLs are deployment questions.
- Backup/restore, production MariaDB rights, network firewall rules, account deactivation, and operational recovery were not exercised in this review. Documentation is not proof that deployment settings match it.
- `README_V10.txt` still says `superadmin / admin123`, while the current run/security guide says no default account and the current password policy rejects that weak password. This is stale documentation, not evidence that such an account exists.

## 6. Findings by priority

### High

| Finding | Status and evidence | Why it matters |
|---|---|---|
| Excess payment and same-month advance can over-allocate a bill | **Confirmed synthetic defect.** `backend/legacy_app.py::record_bill_payment` creates the excess advance for `b.billing_month` and immediately calls `allocate_advances_for_month` for that month. Reproduction and output are documented above. `tests/test_billing_api.py::test_payment_result_and_detail` checks the initial split but not the post-allocation balance. | A surplus credit can be consumed against a statement already paid up to its pre-advance balance; the recalculated statement goes negative while the UI shows zero. This can make ledger and future-credit reconciliation wrong. |

### Medium

| Finding | Status and evidence | Why it matters |
|---|---|---|
| Existing SOAs are not fully immutable | **Confirmed source behavior.** Current penalty settings, storage cutoff, and monthly reading participate in `_bill_calc`/`storage_charged`. The storage cutoff behavior is directly asserted by `test_phase_b1_billing.py`. | Rate/rule or reading edits can alter prior account statements without an explicit bill recalculation. Decide whether the system should preserve issue-time snapshots or create an audited restatement/version. |
| The current default is 10%, not the confirmed 4%; water can be made penalty-eligible | **Confirmed source defaults/control; deployed values unverified.** `backend/app/routes/rates.py::DEFAULTS` uses 10%; current default inclusion is condo on and parking/storage/water off. The settings API supports toggling water. | A new or reset configuration can produce a rate inconsistent with the confirmed requirement, and a later settings change can make water contribute to the base. Require an effective-date/configuration decision and validation that enforces the approved base. |
| Manual dues modes contradict strict area × rate requirements | **Confirmed source behavior.** `unit_dues` permits manual condo dues; `asset_unit_charge` permits manual storage dues. | Financial results can violate the confirmed formula even when the displayed area/rate would imply another amount. Decide whether to remove the mode, migrate existing manual values, or explicitly document a narrow exception. |
| Penalty timing/frequency is implementation-defined, not a settled policy | **Confirmed source behavior; policy unresolved.** The calculation requires the current statement’s due date to be strictly earlier than `date.today()` and dynamically reassesses each overdue bill/month. | A zero penalty beside older overdue balances can be timing-dependent; recurring assessments and partial-payment behavior are not yet management-approved. Specify exact delinquency event and period rules before changing calculations. |
| Printable SOA lacks key verification information and configured instructions | **Confirmed source behavior.** The shared document omits issue date, area/rate for dues, penalty base/rate, and configured payment instructions. The office QR sits outside the PDF article; resident SOA has no QR. | Residents cannot independently verify dues/penalty math from the statement, and on-screen/configured payment paths can disagree with print content. The unavailable screenshot prevents pixel-level conclusions. |
| Live workspaces do not have role-specific dashboard data | **Confirmed source behavior.** `App.tsx::Dashboard` sends every nonresident live role to `WorkspaceHome`; `liveApi.dashboards` methods explicitly reject with `NotConnectedError`. | The dashboard tile may look like a functional module but does not provide role dashboard metrics. This is incomplete, not a defect in billing data. |
| HTTPS and backup encryption are deployment-dependent and unverified | **Documented risk, actual deployment unverified.** The security guide describes HTTP as unencrypted and HTTPS as recommended; backup artifacts are compressed SQL dumps and docs do not establish encryption at rest. | Credentials, session cookies, personal/financial records, and password hashes need protected transport and storage. Verify actual TLS, secure-cookie, disk, backup, and ACL controls before relying on the system for production data. |

### Low

| Finding | Status and evidence | Why it matters |
|---|---|---|
| Legacy README gives obsolete default credentials and SQLite setup guidance | **Confirmed documentation drift.** `README_V10.txt` says `admin123` and describes a SQLite default; current run/security guides say no default account and describe both the isolated demo setup and MariaDB server. | Testers may follow incorrect login/setup instructions. Current guidance should replace or clearly archive the stale README. |
| Decimal rounding is not explicit at every calculation/serialization boundary | **Confirmed source inconsistency; no failing amount established.** Core `round_money` uses half-up; direct `quantize("0.01")` in penalty and SOA serialization uses the default Decimal rounding context. | Edge values at half-cent boundaries can differ across components or legacy inputs. Standardize rounding and add explicit half-cent tests as part of SOA work. |

## 7. Verification performed

| Check | Result |
|---|---|
| Repository instructions and Git working tree | No `AGENTS.md` found in the reviewed project context. Worktree was clean before review; no existing changes were overwritten. |
| Focused backend tests | **182 passed, 0 failed.** `tests/conftest.py` sets `DATABASE_URL` to a temporary SQLite file before importing the application; tests initialize/use that disposable database. No live database was used. |
| Synthetic overpayment/advance reproduction | **Confirmed defect** using the same isolated fixture and a synthetic future bill; exact before/after values are in the finding above. |
| TypeScript check | `tsc --noEmit --incremental false` completed successfully. |
| Production frontend freshness | `frontend/dist/.source-hash` matched current frontend source; built assets were checked for mock/prototype/demo markers and none were found. No rebuild was run. |
| Browser | Local `/app/` sign-in page observed; unauthenticated auth check returned 401. No privileged credentials were used and no data-changing browser action was performed. |
| Screenshot, production account/configuration, deployed TLS, live DB/migrations, backup restore, full role click-through, and PDF pagination | **Unverified.** Screenshot was unavailable; no live database, migrations, seed scripts, server startup script, or production settings were touched. |

## 8. Recommended implementation order

1. **Resolve business policy first:** penalty recurrence/base/eligibility, partial-payment behavior, payment waterfall, grace/cutoff/timezone, manual-dues exception, effective date, and treatment of historical statements.
2. **Correct and add regression coverage for overpayment accounting:** define whether excess starts in the next billing month or can offset the current statement; make the receipt, bill payment, advance application, remaining credit, and ledger add up exactly once. Assert both raw and displayed balance after allocation.
3. **Define statement immutability/versioning:** snapshot all charged inputs and the applied rule version at issue time, or create explicit audited restatements. Do not let a general settings edit silently re-price old SOAs.
4. **Implement a single Decimal backend calculator:** calculate by charge category; enforce condo-only penalty base per the confirmed rule; use explicit half-up quantization at agreed boundaries; avoid frontend/mock calculators as production authorities.
5. **Add measurable calculation tests:** supplied examples; water exclusion; condo-only 4%; parking/storage policy; due-date boundary; single/recurring assessment; partial payment; old balance and payment allocation; advance/excess; historical settings/reading changes; rounding ties; both resident and office API equality.
6. **Update the statement contract and presentation:** add issue date, area/rate breakdowns, previous-balance aging, penalty base/rate/period, correct configured payment instructions, and only verified payment actions/QRs. Ensure resident/office views use the same statement data.
7. **Validate non-destructively first:** isolated fixture/tests, API output assertions, live-source TypeScript/build into temporary output, and print/PDF browser checks using synthetic records. Then perform a separately backed-up, approved migration/deployment plan; none of that is authorized by this review.

## 9. Corrections to the proposed SOA implementation prompt

Before implementation, the prompt should explicitly require:

- A single authoritative server-side calculator shared by billing, resident SOA, receipts/reports, and exports; the frontend must render server values rather than independently recalculate policy.
- Condo, parking, and storage area/rate inputs in the returned SOA contract, while preserving the confirmed water exclusion from the condo penalty base.
- An explicit decision about manual dues modes; otherwise “area × rate” is not enforceable.
- Exact policy inputs: penalty effective date, one-time/recurring cadence, eligible categories, base for partial payments, assessment event/date, grace period, timezone, and rounding mode.
- A defined payment allocation waterfall for prior balances, current charges, water, penalties, advances, and overpayments. Include the reproduced same-month overpayment scenario and require no double allocation, lost credit, or negative hidden balance.
- Historical statement rules for changing rates, penalty settings, storage cutoff, and water readings: either immutable issue-time snapshots or audited, versioned restatements.
- API and print requirements: issue date; itemized prior balances; area × rate for each applicable asset; penalty rate/base/period; configured instructions; verified QR behavior; account-wide versus statement-specific payments; A4 and long-history pagination; mobile drawer behavior.
- Acceptance tests using synthetic data for the exact provided arithmetic: 39.0 sqm × ₱51.28 = ₱1,999.92; ₱1,999.92 + ₱1,999.92 + ₱550 = ₱4,549.84 before penalty; eligible 4% assessment rounds to ₱80.00; water never raises that penalty base. Also state expected outputs for the still-unresolved policies rather than guessing them.
- A migration/backfill/rollback and audit plan if schemas or historical SOA values change, plus explicit separation of unit-test data from the live database.

## Questions that must be answered before financial changes

1. Is the 4% penalty one-time per delinquent charge or recurring each billing period?
2. Are parking and storage dues penalty-eligible?
3. After partial payment, which charge buckets are paid first, and on what remaining balance is penalty assessed?
4. What is the payment priority across oldest dues, current dues, water, other charges, penalty, and advances?
5. Is there a grace period, and what exact local date/time/timezone makes a bill overdue?
6. Must every historical SOA remain exactly as issued after rate, penalty, cutoff, or water-reading changes? If not, how should a restatement be versioned and communicated?
7. Are manual condo/storage dues still permitted exceptions to area × rate? If so, who can set them and how must an SOA disclose them?
8. What rounding rule applies at every calculation boundary, and should stored financial amounts use a fixed-decimal database type?
9. Which actual payment instructions and QR destination(s) are approved for residents, and should they appear in the printable/PDF SOA?
