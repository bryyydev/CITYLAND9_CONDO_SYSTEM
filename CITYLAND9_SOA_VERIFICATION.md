# CITYLAND9 SOA Verification

**Verified:** 2026-10-06  
**Scope:** Current local source, committed frontend build, targeted isolated tests, and the shared local browser page. Verification only; no fixes, settings changes, migrations, startup scripts, or live-data operations were performed.

## Summary

- **The SOA calculation defects are not all fixed.** The confirmed same-month overpayment/advance double allocation remains reproducible in an isolated synthetic database. The source default penalty remains 10%, while the effective configured rate for the running/production system is unverified.
- **The current template has structured improvements in source, but an enhancement comparison cannot be verified.** The original screenshot is unavailable in the current attachment context. The current source has a shared React SOA document, a separate email SOA template, and A4 PDF/print actions, but several requested details are absent.
- **The local production frontend build is current and live-mode.** The build-status checker returned 0, the source hash matched, and the mock marker was absent. The running local Flask app opened to the React login page. This does not establish what a separate production server serves.

## Status key

- **Verified fixed/enhanced** — direct current-source/test/browser evidence confirms the requirement.
- **Partially fixed/enhanced** — some parts are present, but at least one material gap remains.
- **Not fixed/enhanced** — current evidence shows the defect/gap remains.
- **Unverified** — cannot be established from accessible source or safe checks.
- **Requires business-policy confirmation** — code currently chooses a behavior but management has not approved it.

## 1. SOA calculation verification

| Item | Status | Current behavior and evidence |
|---|---|---|
| Condo dues = area × applicable rate | **Partially fixed** | `backend/legacy_app.py::unit_dues` calculates `area_sqm × unit_rate_per_sqm` (or the unit-type rate) using Decimal and the project’s money-rounding helper. A positive `manual_monthly_dues` overrides this in manual mode. Whether that exception is allowed requires business-policy confirmation. |
| Parking dues = parking area × applicable rate | **Verified fixed/enhanced, with policy caveat** | `backend/legacy_app.py::parking_dues` uses the assigned parking unit’s area and rate, with a configured rate fallback. `tests/test_phase_b3_parking.py` covers the parking-unit model. Whether parking is penalty-eligible is unresolved; area pricing does not settle that policy. |
| Storage dues = storage area × applicable rate | **Partially fixed** | `storage_dues` delegates to `asset_unit_charge`, which computes area × rate unless the storage asset is in manual mode. Inclusion in a statement also depends on the configurable `storage_in_total_from` cutoff (`storage_charged`). Manual pricing and the cutoff’s financial meaning need confirmation. |
| Penalty default is 4% | **Not fixed** | `backend/app/routes/rates.py::DEFAULTS` uses `"penalty_rate": "10"`; `backend/legacy_app.py::_prepare_bill_calculation_cache` also falls back to 10. The current persisted setting of the running/production system was not queried, so its effective rate is **Unverified**. |
| Safely determine effective configured penalty rate without resident data | **Unverified; safe read path exists** | An authorized Superadmin can use the read-only Rates & Rules screen or `GET /api/admin/rates`, which requires the `settings` permission. This does not require opening resident records. The endpoint returns other configuration fields as well, so inspect only `penaltyRate` and `penaltyIncludes`; this review did not authenticate to it or read production settings. |
| Water/unrelated charges excluded from condo penalty base | **Partially fixed** | Default inclusion flags enable condo and disable parking, storage, and water. The calculator builds the penalty base from prior unpaid charge buckets selected by these flags, not arbitrary `other`/adjustment charges. However, Rates & Rules accepts a water-inclusion flag; water exclusion is not enforced as an invariant. Do not change inclusion eligibility without approval. |
| Excess not allocated both to bill and same-month advance | **Not fixed — confirmed synthetic defect** | `record_bill_payment` caps `amount_to_bill` at the bill balance, creates an excess advance with `start_month=b.billing_month`, then calls `allocate_advances_for_month` for that same month. In the isolated reproduction below, that advance was applied to the already-paid bill. The comment says it should remain for next month, but the allocator call contradicts it. |
| Previous unpaid balances, payments, credits, advances counted exactly once | **Partially fixed** | `_prepare_bill_calculation_cache` carries one running unit balance; applies payments oldest-charge-first; tracks unpaid typed buckets for penalties; and subtracts advance applications. `tests/test_balance_carryover.py` covers carry-forward and oldest-month partial payments. However, the overpayment reproduction double-applies a credit. Running balances are floored at zero, so general negative credits do not carry forward. “Exactly once” is therefore not established for all combinations. Allocation priority and credit treatment require business-policy confirmation. |
| Historical SOAs protected from current settings/reading edits | **Not fixed** | Condo/parking amounts are normally stored at bill generation, but `_prepare_bill_calculation_cache` reads current penalty rate/eligibility; storage inclusion uses the current global cutoff; and water uses the latest row for the bill’s reading month. Editing these settings/readings can change prior SOA results without explicit bill recalculation. `tests/test_phase_b1_billing.py` explicitly tests the storage cutoff changing an existing bill. The effects of intentional “Recalculate” are separately confirmed in the UI as repricing from current rates. |
| Admin/resident API figures agree | **Verified fixed/enhanced for shared SOA serialization** | `backend/app/routes/billing.py` and `backend/app/routes/resident.py` both use `backend/app/services/soa.py`; that serializer calls the same `legacy._bill_calc`. Resident SOAs are restricted to the resident’s unit and matching bill. This is source verification, not a full authenticated browser comparison. |
| Screen, print, and PDF figures agree | **Partially fixed** | The React office drawer and resident SOA render the same server-returned `SoaDetail` through `SoaDocument`; totals in the PDF are generated from that document node. The separate email template renders stored component columns and `bill_total(bill)` for its total, so component rows can disagree with dynamically calculated amounts (e.g., changed penalty/storage/water behavior). No rendered PDF was inspected. |

### Isolated synthetic overpayment reproduction

The test fixture forces `DATABASE_URL` to a temporary SQLite file before importing the application (`tests/conftest.py`), initializes that database, and uses synthetic test units. No live database was involved.

| Value | Synthetic result |
|---|---:|
| Bill total/balance before payment | ₱12,500.00 |
| Cash received | ₱13,000.00 |
| Recorded as bill payment | ₱12,500.00 |
| Recorded as excess advance | ₱500.00 |
| Same-month advance applied to the bill | ₱500.00 |
| Recalculated SOA total | ₱12,000.00 |
| Bill `amount_paid` | ₱12,500.00 |
| Raw calculated balance | -₱500.00 |
| Serialized balance / status | ₱0.00 / Paid |

This reproduces the prior review’s finding in current code. `tests/test_billing_api.py::test_payment_result_and_detail` asserts the initial payment/excess split and Paid status, but not that the advance remains unapplied to that same month or that post-allocation totals reconcile.

### Rules still requiring approval

This review does not prescribe or approve any of the following:

- One-time versus recurring 4% penalty.
- Whether parking or storage is penalty-eligible.
- Penalty base after partial payment.
- Payment allocation priority across months and charge categories.
- Grace period, cutoff boundary, or governing timezone.
- Whether manual condo/storage dues remain valid exceptions.
- Historical correction/restatement policy and whether water edits may revise issued statements.
- Whether and how an excess credit may offset the selected statement/current month.

Current implementation choices are described above only as code behavior.

## 2. SOA templates and views

### Located current paths

1. **Office billing SOA:** React billing detail drawer in `frontend/src/pages/property/Billing.tsx`, rendered through `SoaDocument` from `frontend/src/components/feature/Documents.tsx`.
2. **Resident SOA:** `ResidentSoaDetail` in `frontend/src/pages/resident/ResidentPages.tsx`, using the same `SoaDocument`.
3. **SOA PDF/print:** both React views print or create an A4 PDF from the `SoaDocument` element. `frontend/src/styles/app.css` hides `.no-print` items and removes the print-sheet border/shadow.
4. **SOA email:** `legacy_flask_ui/templates/soa_email_body.html`, rendered by `backend/legacy_app.py::send_soa_email_to_contact`. It is a separate template and uses raw stored bill component fields while its total uses `bill_total(bill)`.
5. **Classic billing URL:** legacy `/billing` and `/billing/<id>` routes redirect into the React app; they no longer render an independent classic SOA screen. `backend/legacy_app.py::_billing_moved` documents this.

### Screenshot comparison

**Unverified:** the original screenshot is not available in the attachment context. The only shared browser page is the local login screen, and the repository image search found only the legacy login background—not an SOA screenshot. No screenshot-to-current visual comparison is claimed.

| Requested design aspect | Status | Current source evidence / limit |
|---|---|---|
| Font readability, contrast, spacing, alignment | **Partially enhanced; comparison unverified** | `SoaDocument` uses a white document, dark ink text, tabular amounts, row spacing, and a branded header. Main table text is 13.5px; metadata labels are 10.5px and section labels 11.5px. Pixel contrast, actual print size, and improvement over the original cannot be verified without the screenshot/browser SOA. |
| Header/property/account details | **Partially enhanced** | React document shows corporation, unit, billing period, due date, balance, and status. It does not show issue date, property address, or resident/owner name inside the printable SOA. The office drawer shows payer/contact separately, outside the PDF document node. |
| Charge breakdown with area, rate, amount | **Not enhanced** | The SOA does not show condo, parking, or storage area/rate. Water can show usage × rate. No basis is supplied to independently verify the dues amount. |
| Current charges vs previous unpaid balances | **Partially enhanced** | Current charge lines and one “Previous balance” line are separate. The printable document does not subtotal current charges separately or itemize the prior balance by month. Office UI separately lists earlier unpaid months in a notice, not in the printed document. |
| Penalty explanation and total readability | **Partially enhanced** | A positive penalty appears with only “Late payment” detail; rate, eligible base, period, and assessment date are absent. “TOTAL AMOUNT DUE” is bold, and balance is repeated in metadata and the final row. The screenshot comparison remains unavailable. |
| Payment history and account-wide payments | **Partially enhanced** | The document lists payments attached to that statement, including date, receipt no., reference, method/type, amount, and reversal marker. It is not the account-wide payment history, which is a separate resident page/receipt flow. |
| Working payment / QR actions | **Partially enhanced; effectiveness unverified** | Resident UI presents a “Pay dues” preview with office-payment instructions; it does not submit an online payment. The office drawer displays one QR image when `qrUrl` is present, outside the document/PDF element. The backend QR route encodes a configured URL if one exists, otherwise a plain `CITYLAND9|...` payload; no actual payment provider/configuration was tested. Two QR actions are not evidenced in current React source. |
| Internal staff instructions removed | **Partially enhanced; comparison unverified** | The current React SOA document contains resident-facing payment guidance and no explicit staff-only instruction. The email template contains a generic automated-message footer. Whether any internal text was removed relative to the screenshot cannot be established. |
| Mobile layout and modal/drawer scrolling | **Partially enhanced; visual behavior unverified** | The shared `Drawer` uses full-height flex layout with `overflow-y-auto` content and a separate footer; it handles Escape. The SOA payment table has five columns with no explicit mobile overflow wrapper. No mobile viewport was inspected. |
| A4 output, page breaks, unclipped totals, hidden controls | **Partially enhanced; output unverified** | PDF options specify A4, and print CSS hides `.no-print`; page header actions are no-print. No explicit page-break rules or row keep-together rules were found. Long statement pagination, clipped totals, and browser print output were not tested. The QR/contact cards are outside the exported article. |

## 3. Live build verification

| Item | Status | Evidence |
|---|---|---|
| `frontend/dist` matches current source | **Verified fixed/enhanced** | Read-only `scripts/frontend_build.py status` returned status code 0; its computed source hash matched `frontend/dist/.source-hash`. No build or stamp write was performed. |
| Production build uses live source | **Verified fixed/enhanced** | `frontend/vite.config.ts` aliases to `source.live.ts` for every mode except explicit `mock`; `npm run build` does not set mock mode. The checker found no mock banner marker in the built assets. |
| Current React SPA is served by local Flask | **Verified for local instance; production deployment unverified** | The shared browser page at `http://127.0.0.1:5000/app/login` displayed the React login form. The existing server’s unauthenticated `/api/auth/me` request returned 401. This review did not authenticate or inspect protected SOA content. |
| Windows startup behavior | **Verified by source only** | `START_WINDOWS.bat` runs `first_run_setup.py` when `.env` is missing, checks the build status, rebuilds `frontend/dist` when required and npm is present, then starts the database and Waitress. It was not run because first-run setup/startup has database side effects. |
| Rebuild needed now? | **No** | The current build is up to date. If it becomes stale, the rebuild command is `cd frontend` then `npm run build`, or `START_WINDOWS.bat --rebuild` on the server PC. Neither was run. |

## 4. Tests and checks actually run

- Confirmed `tests/conftest.py` sets a temporary SQLite `DATABASE_URL` before importing the app, creates a random test secret, initializes the disposable database, and seeds synthetic data. It also redirects import-backup paths into the temp folder.
- Ran the six targeted test files: `test_billing_api.py`, `test_balance_carryover.py`, `test_phase_b1_billing.py`, `test_phase_b3_parking.py`, `test_rates_api.py`, and `test_resident_features.py`. **All six selected files passed** (`runTests` summary: passed=6, failed=0).
- Ran an additional isolated synthetic payment reproduction, which **confirmed** the same-month double allocation as shown above.
- Ran the read-only frontend build status/hash check: **status 0, hash match true, mock build false**.
- Read the shared browser page: React login UI visible. No SOA view was available without authentication; no user credentials were used.
- **Not run:** build/rebuild, `START_WINDOWS.bat`, migrations, live settings query, live database read, authenticated role flows, SOA screenshot comparison, browser print dialog, generated PDF inspection, mobile viewport check, or payment QR scan.

## 5. Final answers

1. **Are the calculation defects fixed?** No. The same-month overpayment/advance double allocation is still present and reproduced. Historical SOAs remain sensitive to some current settings and water-reading edits. The penalty default is still 10%; actual configured rate is unverified.
2. **Is the live SOA template enhanced?** The current source has a shared React office/resident SOA document and A4/print controls, but the original screenshot is unavailable, so comparative enhancement is unverified. Source review finds material omissions (area/rate, issue date, penalty explanation, monthly arrears itemization) and an email template that can use different component values.
3. **Remaining defects and unresolved policies:** overpayment reconciliation; historical statement instability; 10% fallback versus required 4%; water penalty eligibility is configurable; manual pricing exceptions; incomplete charge disclosure; email-versus-API component mismatch; uncertain QR functionality and PDF pagination. Penalty recurrence, parking/storage eligibility, partial-payment basis, allocation priority, grace period/timezone, manual-dues policy, and historical restatement policy still require approval.
4. **Tests actually run:** six targeted isolated test files passed; the synthetic payment reproduction confirmed a defect; the read-only build status/hash check passed. No live data or settings were accessed.
5. **Exact commands to view the current version:**

   - If the local Flask server is already running, open: `http://127.0.0.1:5000/app/login` (the current shared browser is already there).
   - To view React in live development mode from PowerShell: `Set-Location C:\Users\BOGZ\Documents\CITYLAND9_SYSTEM\frontend; npm run dev` and open `http://127.0.0.1:5173/app/`. The Flask API must separately be available on `127.0.0.1:5000`; ensure it is connected only to an approved development/test database before using it.
   - Do not use `npm run dev:mock` to verify calculations; that mode uses mock data. Do not run `START_WINDOWS.bat` as part of verification because it can initialize/setup a database on a first-run PC.
   - If the build later becomes stale, rebuild with `Set-Location C:\Users\BOGZ\Documents\CITYLAND9_SYSTEM\frontend; npm run build` or `START_WINDOWS.bat --rebuild` when intentionally updating the server. No rebuild is currently needed.
