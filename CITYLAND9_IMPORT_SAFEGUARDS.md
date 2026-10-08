# CITYLAND9 — data-migration safeguards and "Check only" (dry run)

**Status: 2026-10-08.** This stage adds a stable source identity (migration 0012) and a dry-run
validator. **No client record was imported, no importer defect was fixed, and no financial policy
changed.** The live database was not opened or upgraded. Companion report:
`CITYLAND9_IMPORT_MAPPING.md` (workbook mapping and client answers).

---

## 1. What changed

| File | Change |
|---|---|
| `database/migrations/versions/0012_import_crosswalk.py` | **New, additive migration:** tables `import_source`, `import_batch`, `import_crosswalk` (§3). Empty after upgrade. |
| `backend/legacy_app.py` | Models `ImportSource`, `ImportBatch`, `ImportCrosswalk`. No other behaviour changed. |
| `database/schema.sql` | Regenerated (35 tables). |
| `backend/app/services/import_check.py` | **New:** the dry-run validator, with no-write guard, codes and report (§5). |
| `backend/app/routes/system_settings.py` | **New endpoint** `POST /api/admin/system/import/check` (Superadmin `database_import`, CSRF-protected blueprint, upload limits unchanged, `Cache-Control: no-store`). `POST /api/admin/system/import` is **locked** behind the same check (§6). |
| `database/import_check.py` | **New:** the same check from the command line (for a staging copy). |
| `database/import_config.example.json` | **New:** template of the decisions file (placeholders only). |
| `frontend/src/components/feature/ImportCheckReport.tsx`, `frontend/src/pages/system/SettingsPage.tsx`, `frontend/src/services/{types,api,liveApi,mockApi}.ts` | Settings → *Import from Excel* gets a **Check only** button and a report. The import button stays, now secondary, and the notice states its limits (§6). |
| `frontend/dist/` | Production build, rebuilt and stamped. |
| `database/test_pc.py` | Test-PC packages now also strip the three `import_*` tables. |
| `tests/test_import_check.py` | **New:** 17 tests (synthetic workbooks only), including the import lock. |
| `migration_data/import_config.json` | **Local only (git-ignored).** The client's 2026-10-08 answers. Every financial decision is empty. |

---

## 2. The ten reported importer issues (re-verified, none fixed)

**Evidence:** code reading, plus reproductions on throwaway SQLite databases with synthetic data.
Each scenario got a fresh copy of the schema and sample data, then one crafted workbook was run
through the existing `run_excel_import` (`backend/legacy_app.py:2943`). These were re-run after the
0012 models were added, with identical results. Line numbers refer to the current file.

| # | Issue | Status | Code evidence | Reproduction result |
|---|---|---|---|---|
| 1 | Units matched by external id before unit number | **Confirmed** | `unit_by_id.get(old_id)` first, then `unit_by_no` (line 3111); `obj.id = old_id` on insert | A row with id 1 and a new unit number **renamed** existing unit 1 instead of adding a unit |
| 2 | Child records matched by external id; duplicates without ids | **Confirmed** | owners/tenants/water/billing/payments look up `*_by_id` only (lines 3171, 3277) | An Owners row with an existing owner's id **moved that owner to another unit** and renamed it; the same id-less sheet imported twice made **2 owners** |
| 3 | Unit/month collisions (bills, readings) | **Confirmed** | bills/readings are not matched on `(unit_id, month)` | A bill without an id for an existing unit + month made the whole import fail with a raw `UNIQUE constraint failed` message and no row number (nothing saved) |
| 4 | Blank cells reset values | **Confirmed** | `default=0`, `True`, `False`, `date.today()` fallbacks (lines 3128, 3137, 3250–3254) | manual dues 1234.00 → 0.00; deactivated unit → active; paid water → unpaid; readings → 0/0 |
| 5 | Import bypasses closed periods | **Confirmed** | no call to `check_open_period` (line 1463) | A bill in a closed month was changed |
| 6 | Issued / hand-corrected bills changed; payments vs status | **Confirmed** | no check of `soa_manual_override` / `issued_at` (compare `recalculate_bill`, line 2524); `amount_paid`/`status` copied as given | A hand-corrected bill's assessment was overwritten; a bill with no payments stored amount paid 999.00 and status `Whatever` |
| 7 | Unresolved relationships silently skipped | **Confirmed** | `if unit_obj is None: continue` / `if billing_obj is None: continue` | An orphan owner and payment → success, "0 new / 0 updated", no warning |
| 8 | Invalid month parsing | **Confirmed (corrected earlier)** | `normalize_month` falls back to `text[:7]`; the CHECK constraints `ck_*_month_format` then reject it | `Jan 26`, `2026-13`, `13/45/2026` → whole import fails with a raw constraint message. **No bad month is stored.** |
| 9 | Missing numeric/date/status/type/dues-mode validation | **Confirmed** | `money()` turns unreadable values into 0 (line 149); text fields copied as given | `"abc"` amount → stored as **0.00** and reported as success; negative assessment stored; unit with negative area, type `ANYTHING`, dues mode `weird`, status `???` stored |
| 10 | Employee number not used for matching | **Confirmed (narrow)** | `employee_by_id.get(old_id) if old_id is not None else employee_by_no.get(eno)` (line 3350) | An unknown `id` with an existing employee number → whole import fails (`UNIQUE`); id-less rows match by number correctly |

**Overpayment / advance double allocation:** `tests/test_overpayment_reconciliation.py` was **run** on its
isolated test database: **5 passed**. These tests cover the fix in `record_bill_payment` (excess goes to
the **next** month) and the cap in `allocate_advances_for_month`. Live data was not checked for old double
allocations: `database/tools/detect_overpayment_allocations.py` is the read-only tool for that.

**New finding relevant to historical balances:** for ordinary bills, CITYLAND9 does **not** use the stored
`previous_balance`. It recalculates arrears as a running balance over the unit's earlier bills
(`_prepare_bill_calculation_cache`, legacy_app.py line 621; carry-over rule from line 671). Penalties are recalculated too. Only
hand-corrected SOAs keep their typed amounts. As a result:
- an opening balance stored as `previous_balance` on the first imported bill **would disappear**;
- an incomplete history **would understate** arrears.

The dry run flags both (§5).

---

## 3. Stable source identity

```
import_source   (code UNIQUE)                                  one row per SOURCE SYSTEM
     │  e.g. code "legacy-condo-system" (proposed; confirm before the first real import)
     ├── import_batch     file_name, file_sha256, sheet_summary, status, who/when     PROVENANCE ONLY
     └── import_crosswalk (source_id, entity_type, external_id) → target_id
              UNIQUE (source_id, entity_type, external_id)   one external record → one CITYLAND9 record
              UNIQUE (source_id, entity_type, target_id)     one CITYLAND9 record ← one external record
              first_batch_id / last_batch_id, source_sheet, source_row                 provenance
```

- **Record identity = source system + entity type + external id.** File names, sheet names, row numbers and file hashes are **only** batch provenance. A re-export with a new file name or hash maps to the same records.
- **External ids never become CITYLAND9 primary keys.** They are stored as text (`1.0` in Excel → `"1"`).
- **Conflicts the dry run detects:**
  - the crosswalk points to a missing record;
  - the crosswalk points to a unit whose number differs from the workbook;
  - the unit with that number is already mapped to a **different** external id;
  - more than one existing unit matches after ignoring case and spaces (ambiguous);
  - an employee number and the crosswalk disagree.
- **Matching rules in use:** unit number = `Name` (confirmed by the client). Bills and readings match on `(unit, month)`, the database's own unique keys. Employees match on employee number. **People are never matched or merged by name.**
- **Not applied anywhere.** Migration 0012 was tested only on disposable MariaDB instances (§8). The live database is still at 0011.

> **Operational consequence:** once this code is on a PC, `START_WINDOWS.bat` refuses to start until
> `.venv\Scripts\python.exe database\db_migrate.py upgrade` has applied 0012 (backup first, as always).
> This applies to the office server and to any test PC.

---

## 4. Decisions the dry run requires (decisions file)

Location: `migration_data/import_config.json` (git-ignored). Template: `database/import_config.example.json`.
Override the path with the `IMPORT_CONFIG_PATH` environment variable.

| Setting | Current value (2026-10-08) | Effect while missing or unapproved |
|---|---|---|
| `source_system.code` | `legacy-condo-system` (**proposed**, to be confirmed) | No proposals at all; `SOURCE_SYSTEM_NOT_CONFIGURED` |
| `properties.unit_no_confirmed` | `true` (client: Name = SOA unit number) | Every row `UNIT_NO_UNCONFIRMED` |
| `properties.group_mapping` | RESIDENTIAL, 2F, LG, UG approved (2F/LG/UG → floor); **PARKING not approved** | Rows `GROUP_UNCONFIRMED` / `GROUP_UNMAPPED` |
| `properties.people.one_person_per_cell_confirmed` | `false` | Every Owner/Tenant cell unresolved; people are never proposed |
| `properties.people.owner_equals_tenant_rule` | `null` | `OWNER_EQUALS_TENANT` |
| `financial.approved`, `approved_by`, `approved_on` | empty | `FINANCIAL_NOT_APPROVED` |
| `financial.cutoff_date` | empty | `FINANCIAL_CUTOFF_MISSING` |
| `financial.mode` (`full_history` / `opening_balances`) | empty | `FINANCIAL_MODE_MISSING` |
| `financial.scope` (first/last month, entity types) | empty | `FINANCIAL_SCOPE_MISSING` |
| `financial.control_totals` (bill rows, bill charges total, payment rows, payment total) | empty | `CONTROL_TOTALS_MISSING`; a mismatch gives `CONTROL_TOTALS_MISMATCH` |

**Financial readiness is never reported as ready by this stage,** even when all of the above is approved:
- no supported source has an **advance/credit ledger** (`ADVANCE_LEDGER_MISSING`);
- official **receipts** are never invented (`RECEIPTS_NOT_IN_SOURCE`);
- **opening-balance mode** has no approved representation in CITYLAND9 yet (`OPENING_BALANCE_UNSUPPORTED`, see §2's finding);
- the property list contains **no bills at all** (`FINANCIAL_SOURCE_MISSING`).

Double-counting risks flagged per row (export format):
- `OPENING_ARREARS_NOT_CARRIED`: first bill of a unit has a previous balance;
- `PREVIOUS_BALANCE_WITH_HISTORY`: a carried balance plus earlier bills;
- `PAYMENT_DETAIL_MISSING`: amount paid without payment rows;
- `PAYMENT_TOTAL_MISMATCH`: payment rows ≠ amount paid;
- `OVERPAYMENT_WITHOUT_ADVANCE_LEDGER`;
- `PENALTY_RECALCULATED`;
- `HISTORY_IN_OPENING_BALANCE_MODE`, `BILL_AFTER_CUTOFF`, `BILL_OUTSIDE_SCOPE`.

---

## 5. Check only (dry run)

### How to use
- **In the app:** Superadmin → **Settings** → *Import from Excel*. Choose the workbook, then click **Check only**. The report shows readiness, rows per sheet, proposals, issues by type, and the issue list (sheet / row / column / code), with a CSV download made in the browser.
- **Command line** (staging copy recommended):
  ```powershell
  .venv\Scripts\python.exe database\restore.py <backup.sql.gz> --into cityland9_stage      # staging copy
  .venv\Scripts\python.exe database\import_check.py "migration_data\master_file (2).xlsx" --database cityland9_stage --details migration_data\check_details.csv
  ```
  Exit code: 0 = no blocking issue (importing is still not enabled), 1 = blocked / needs decisions, 2 = unreadable file.

### What it guarantees
- **No writes:**
  - `no_writes()` refuses every non-read SQL statement on the connection while the check runs, refuses ORM flushes, and rolls back at the end;
  - no backup, audit entry, account or file is created (the CLI writes the `--details` CSV only when asked).
  - **Tested:** a digest of every row of every table is identical before and after (API and service); forced writes inside the guard raise `DryRunWriteError`.
- **Privacy:** issues carry sheet, row, column and a code with a fixed message. No names, contacts, unit numbers, ids or amounts from the workbook. Tested on the API response.
- **Every row is checked** (no stop at the first error). Row results: *valid*, *blocked*, *conflict*, *unresolved*.
- **Cell states are distinguished:** missing column, blank, explicit zero, explicit clear (`#CLEAR`), value. A blank is never treated as zero. Clearing a required field is blocked. Ending an owner or tenancy through a clear is unresolved.
- **New records need their required fields:** unit number, area > 0, approved group; owner/tenant name and a resolved unit; bill unit + valid month; payment bill + amount > 0 + date; employee number + name; expense date + category + amount.
- **Rejected values:**
  - invalid months and dates;
  - non-finite numbers or text in number cells;
  - more than 2 decimals;
  - negative values except a bill `adjustment`;
  - unknown unit type, dues mode or status;
  - non-yes/no booleans;
  - a current reading below the previous one;
  - text longer than the field allows.
- **Relationships:**
  - an unresolved unit, bill or parking/storage reference → **blocked**;
  - a child of an invalid unit or bill → `PARENT_RECORD_NOT_VALID`;
  - duplicates of an external id, unit number, unit/month or employee number in a sheet → **blocked**.
- **Financial protections:**
  - closed periods (`books_closed_through`), issued bills and hand-corrected bills → **blocked** by default;
  - existing unit/month records not mapped to the row → **conflict**.
- **Size is treated as confirmed sqm.** Values are reported, never corrected (0 stays blocked).
- **Proposals** (insert / link / update / unchanged) appear **only** where the matching rule is confirmed and the record itself is valid. People are not proposed until their rules are approved.
- **`readyForImport` is always `false`** in this stage (`IMPORTER_NOT_ENABLED`).

### Results on the real files (local staging run, aggregates only)
The run used a **disposable MariaDB 10.4.32 instance** with synthetic sample data, not the live database. Its table checksums were identical before and after, and both workbooks' hashes were unchanged.

**`master_file (2).xlsx`** (1,019 rows):
- 0 valid, 141 blocked, 878 unresolved.
- `AREA_ZERO` 141 (blocked).
- `GROUP_UNCONFIRMED` 342 (PARKING).
- `PERSON_MULTIPLE_NAMES` 422 cells; `PERSON_RELATIONSHIP_UNCONFIRMED` 1,323 cells; `OWNER_EQUALS_TENANT` 96.
- `PERSON_NAME_REPEATED` 585 (information).
- Proposed unit inserts: **670** (= 1,019 − 342 parking − 7 zero-size residential).
- Readiness: units **not ready** (AREA_ZERO, GROUP_UNCONFIRMED); people not ready; financial not ready (no bills in the file, nothing approved); importing not enabled.

**`cityland9_database_export.xlsx`** (CITYLAND9's own sample data):
- Units: 8 linked by unit number.
- Billing and water readings: `UNIT_MONTH_EXISTS` (the sample already exists in that database).
- Owners/tenants: `POSSIBLE_DUPLICATE_PERSON`.
- The payment row now waits for its bill (`PARENT_RECORD_NOT_VALID`; this check was added after that run surfaced the gap).
- Financial: not ready.

---

## 6. The classic import is now LOCKED (2026-10-08, after a client-PC incident)

**Why:** a screenshot from the client's PC showed **Import workbook** reporting "Units 0 new / 1,019 updated, Tenants 0 new / 1,019 updated, Payments 0 new / 255 updated":
- every row matched an existing record **by id** and was **overwritten** (issues 1–2);
- that PC was still on the previous version (no Check only);
- its backup was named `…before_import_….db`, which means it runs the **SQLite** demo setup, not CITYLAND's MariaDB.

**The lock:** `POST /api/admin/system/import` now checks the uploaded file itself first (the same dry run, `roundtrip=True`, no writes) and runs `run_excel_import` **only when every row is a safe update of THIS database's own export**. Otherwise it answers **409** and changes nothing (no backup, no audit entry).

| Refused (gate blocker) | Why |
|---|---|
| Any layout other than CITYLAND9's export (`LAYOUT_NOT_IMPORTABLE`) | Data from another system is check-only until the migration importer exists |
| A row whose id is not a record of this database (`NEW_RECORD_NOT_ALLOWED`), including an owner typed on the Units sheet for a unit without one | The classic import would create it with the file's id as primary key |
| An id that names another unit number, unit or month (`CROSSWALK_UNIT_NO_MISMATCH`, `UNIT_MAPPED_TO_OTHER_EXTERNAL_ID`, `ID_POINTS_TO_OTHER_RECORD`) | It would rename or move an unrelated record (issues 1–2) |
| A blank cell the import would replace with a default (`BLANK_WOULD_RESET`): manual dues, active, parking/storage assignment, water readings/rate/date/paid, expense fields | Issue 4 |
| A change in a closed month, or to an issued or hand-corrected bill (`PERIOD_CLOSED`, `BILL_ISSUED`, `BILL_CORRECTED_BY_HAND`) | Issues 5–6. **Unchanged** rows are not blocked (the import wouldn't alter them) |
| Changed bills whose payments/status don't reconcile (`PAYMENT_TOTAL_MISMATCH`, `STATUS_INCONSISTENT`) | Issue 6 |
| Invalid values, months, dates, types, statuses, dues modes, duplicates, unresolved references, children of invalid rows | Issues 3, 7–10 |

**On the screen:**
- **Import workbook** stays disabled until **Check only** has passed **for the same file**. Choosing another file resets it.
- The result shows **"Import workbook: Allowed / Locked for this file"** with the reasons.
- The server re-checks the file anyway, so the lock doesn't depend on the screen.
- File-size and row/column limits keep their plain 400 messages.

**Still true:**
- Importing data **from another system** (e.g. the client's master file) is **not possible** in this version. It is check only.
- The classic importer's code is unchanged; it simply can't run unless the gate proves none of its unsafe behaviours would trigger.
- Allowed imports still take a database backup first and write the audit entry.

**For the client's PC:**
1. Update it to this version: commit → push → download → `db_migrate.py upgrade` on MariaDB; SQLite creates the new tables at start.
2. Decide whether it should run CITYLAND's MariaDB as described in `CITYLAND9_TEST_PC_MIGRATION.md`; it currently runs the SQLite demo setup.
3. Review the data the earlier imports overwrote: the `…before_import_….db` backups on that PC hold the earlier state.

---

## 7. Tests actually run

| Run | Result |
|---|---|
| `tests/test_import_check.py` (new) | **17 passed** |
| `tests/test_overpayment_reconciliation.py` (isolated test DB) | **5 passed** |
| `tests/test_test_pc.py` after the package-scrub change | **11 passed** |
| Full backend suite (after the lock) | **413 passed** |
| `npx tsc --noEmit` + `npm run build` + `scripts/frontend_build.py stamp/status` | clean; build current |
| Ten importer reproductions (throwaway SQLite, synthetic) | as in §2; none fixed |
| Migration 0012 on a disposable MariaDB 10.4.32 (separate data folder, port 3337) | `install` at 0012 matches models; `downgrade 0011` and `upgrade` back: schema matches, bill figures unchanged |
| CLI dry run of both real workbooks on that disposable MariaDB | completed; database checksums identical before/after |
| Browser (Edge headless, isolated SQLite server, synthetic workbook): Settings → Check only | **11/11**: report shown, "nothing was changed", codes not values, no audit entry and no unit added, no horizontal scroll at 390 px |
| Browser: the lock | **9/9**: Import disabled before a check; a property list stays locked; this database's own export, unchanged, is "Allowed", imports, and adds nothing; choosing another file resets the check |

## 8. Not verified

- **Live database:** not opened, not upgraded to 0012. The upgrade must be run by the user (§3).
- **MariaDB:**
  - the no-write guard and the API endpoint ran against SQLite in tests; on MariaDB only the CLI check ran (disposable instance);
  - no concurrent requests were tested.
- **Windows service / scheduled-task restarts** after the 0012 upgrade.
- **Browsers other than headless Edge.** Large workbooks (the 1,019-row file took about a second via the CLI; the 1,000-issue list cap in the API was not stress-tested in a browser).
- The proposed `source_system.code` and every unanswered client decision.

## 9. Next stage required before any staging import

1. Client answers to the open questions in `CITYLAND9_IMPORT_MAPPING.md` §9.1:
   - rate per sqm;
   - parking: billed with the unit or on its own SOA, plus the slot-to-unit list;
   - several names in one cell;
   - the 141 zero sizes;
   - the past-bill source and period;
   - Owner = Tenant.

   Record them in `import_config.json` and **confirm the source-system code**.
2. **Fix importer defects 1–10** in a new, separate import path built on this validator: crosswalk-based matching, blank-cell rules, closed/issued protection, no silent skips, strict parsing. Each fix gets a regression test that fails today.
3. **Staging database:** `restore.py --into cityland9_stage` from a fresh backup. Run Check only until it reports no blocked/conflict rows for the units in scope.
4. **Properties-only import into staging** (units, then approved owners/tenants), writing crosswalk rows and a batch. Re-run the same file and confirm nothing changes; reconcile counts and area totals per group.
5. **Historical bills: separate plan.** Approved source, cutoff, mode, control totals, and a decision on how opening balances and advances are represented, since CITYLAND9 recalculates carried balances. Then a reconciliation of unit balances against the source.
6. **Client sign-off on the staging result.** Then a verified backup, the live run, post-checks and the documented restore path.
