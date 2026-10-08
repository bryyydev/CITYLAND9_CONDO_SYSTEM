# CITYLAND9 — Excel-to-database import mapping (review only)

**Status:** inspection and mapping report, 2026-10-08. **Nothing was imported or fixed.** No application
code, settings, schema, migration or database record was changed. The live database was not opened
and `.env` was not read. Both workbooks were opened read-only; their SHA-256 hashes were identical
before and after the review.

**Data protection:** this report contains no client names, contact details or amounts. Workbook
contents are described by counts, category labels and character *shapes* only (`9` = digit,
`A` = letter, e.g. `9999` = a four-digit label).

**Provenance:** an earlier draft of this file (same task, written shortly before this review) was
found in the working tree. Its findings were re-verified independently and are kept here, corrected
where the evidence differed (§5, defect I).

---

## 1. Working tree and source files

| Check | Result |
|---|---|
| Branch / last commit | `master`, `221e887` |
| Uncommitted changes found | `.gitignore` (adds `/migration_data/`), this report (untracked). Both preserved. |
| `migration_data/master_file (2).xlsx` | ignored by `.gitignore:49` (`/migration_data/`), **not tracked** |
| `migration_data/cityland9_database_export.xlsx` | ignored by `.gitignore:49`, **not tracked** |
| Originals modified | No (hashes unchanged; no save, recalculation or macro execution) |
| Code tree schema head | `0011_resident_provisioning` (live database revision not checked in this review) |

---

## 2. Workbook profiles (each inspected independently)

### 2.1 `master_file (2).xlsx` — sheet `Properties` (the only sheet, visible)

Headers: `ID`, `Name`, `Size`, `Group`, `Owner`, `Tenant`. **1,019 data rows** (worksheet rows 2–1020);
no values beyond the six columns. No formulas. (The file's stored sheet dimension is wrong — a
reader that trusts it sees one column; read it with the dimension reset.)

| Column | Type in file | Blank | Distinct | Findings |
|---|---|---:|---:|---|
| `ID` | number (all integral, stored as float) | 0 | 1,019 | Unique; range 1–1,019; **not** in sheet order; never equal to `Name`. Max 4 digits. |
| `Name` | text | 0 | 1,019 | Unique, also after trimming/upper-casing/removing spaces; no stray spaces. Shapes: `9999` 552, `AA99` 312, `AA999` 89, `A999` 28, `AAA9` 19, `A-99` 13, `9A/A` 3, three word-like labels. Max length 10 (fits `Unit.unit_no` `String(40)`). |
| `Size` | number | 0 | 306 | **Confirmed: square metres.** **141 rows are exactly 0** (none negative); 4 rows are between 0 and 5 sqm (smallest 0.01). Max 111.86. At most 2 decimal places. |
| `Group` | text | 0 | 5 | `RESIDENTIAL` 500, `PARKING` 342, `2F` 63, `LG` 63, `UG` 51. Meanings not confirmed. |
| `Owner` | text | 1 (row 552) | — | 1,018 filled. Possible multi-person markers: `/` 229, `,` 71, `&` 13, ` and ` 4, `(` 76. 191 cells contain organisation-like words. **124 identical texts appear on 409 rows** (one text on up to 49 rows). Max length 67 (fits `String(200)`). |
| `Tenant` | text | 292 | — | 727 filled. `/` 65, `,` 57, `&` 13, ` and ` 29, `(` 18. 115 organisation-like. 48 identical texts on 138 rows. **Owner text = Tenant text on 96 rows.** No row has a tenant without an owner. |

Per `Group` (rows are interleaved in the sheet, not grouped):

| Group | Rows | `Size` = 0 | Owner blank | Tenant blank | `Name` shapes |
|---|---:|---:|---:|---:|---|
| `RESIDENTIAL` | 500 | 7 | 0 | 59 | `9999` 482, `A-99` 13, `9A/A` 3, 2 word labels |
| `PARKING` | 342 | 134 | 1 | 210 | `AA99` 199, `AA999` 89, `A999` 28, `AAA9` 19, `9999` 7 |
| `2F` | 63 | 0 | 0 | 6 | `9999` 63 |
| `LG` | 63 | 0 (one 0.01) | 0 | 12 | `AA99` 62, 1 word label |
| `UG` | 51 | 0 | 0 | 5 | `AA99` 51 |

No property types were inferred from `2F`, `LG` or `UG`.

### 2.2 `cityland9_database_export.xlsx` — CITYLAND's own Excel export format

Produced by `database_export()` (`backend/legacy_app.py:2857`). All sheets visible; ids are integers.

| Sheet | Rows | Columns |
|---|---:|---|
| `Units` | 8 | id, unit_no, floor, unit_type, area_sqm, unit_rate_per_sqm, dues_mode, manual_monthly_dues, include_parking, include_storage, assigned_parking_unit_id, assigned_storage_unit_id, occupancy_type, owner_name, contact_no, email, status, active |
| `Owners` | 4 | id, unit_id, owner_name, contact_no, email, move_in, move_out, status, notes |
| `Tenants` | 1 | id, unit_id, tenant_name, contact_no, email, move_in, move_out, status, representative, notes |
| `Billing` | 4 | id, unit_id, billing_month, assessment, parking_dues, storage_dues, water, other, penalty, adjustment, previous_balance, amount_paid, due_date, status, paid_date |
| `Payments` | 1 | id, billing_id, amount, payment_date, reference, remarks |
| `WaterReadings` | 4 | id, unit_id, reading_month, previous_reading, current_reading, rate, reading_date, paid, paid_date |
| `Employees` | 0 | id, employee_no, full_name, position, department, employment_status, contact_no, email, date_hired, monthly_salary, notes |
| `Expenses` | 0 | id, expense_date, category, description, amount |

- **Internal references all resolve** (owner/tenant/billing/water `unit_id` → `Units.id`; `Payments.billing_id` → `Billing.id`; both parking/storage assignments). No duplicate unit + month. Billing months 2026-08 and 2026-09.
- For the 4 bills, `amount_paid` equals the sum of that bill's `Payments` rows (a limited check: advances and receipts are not in this format).
- `unit_no` shapes are `AAAA-999` (4) and `A-AAAA-99` (4); unit types are the 6 CITYLAND types. **This matches the synthetic `TEST-*` sample set**, not the client's 1,019 properties. No `ID`/`Name` from the master file matches any `unit_no`.
- The format **omits** receipts, advance payments, advance applications, resident accounts/links, settings, audit history and the issue-time SOA snapshot columns. It is not a complete backup or ledger.

**Conclusion:** the two workbooks are unrelated datasets. Do not assume their ids correspond or that
either is complete.

---

## 3. Mapping: `master_file (2).xlsx` → CITYLAND9

Destination models are in `backend/legacy_app.py`: `Unit` (line 1123), `Owner` (1197), `Tenant` (1213).

| Source column | Source meaning | Destination | Transformation and validation | Matching key / duplicates | Unresolved |
|---|---|---|---|---|---|
| `Properties.ID` | Source system's property id (unique here) | **Not** `Unit.id`. Kept as an **external reference** in an import crosswalk (source file + sheet + ID text → CITYLAND `unit.id`). `Unit` has no external-reference column today, so this needs a new staging/crosswalk table (a migration) — a decision, not done here. | Convert the integral number to canonical text (`1.0` → `"1"`); reject non-integral values; keep workbook, sheet and row in the import log. | Crosswalk key `(source, ID)`. A rerun finds the existing crosswalk row and updates the same unit; never creates a second. | Is `ID` stable between exports? Any leading-zero convention lost because Excel stored numbers? |
| `Properties.Name` | Property label (unique) | Probably `Unit.unit_no` (`String(40)`, unique) — **only if the client confirms** it is the unit number shown on SOAs. | Trim; keep case and punctuation as given (`9A/A`, `A-99` and word labels included); reject blank; reject duplicates after trim/upper-case normalisation (none found). | Second check behind the crosswalk: an existing unit with the same normalised `unit_no` but a different crosswalk entry → **blocked as a conflict**, never overwritten. | Is `Name` the unit number? What are the 3 `9A/A` and 3 word-label rows? |
| `Properties.Size` | Area in **square metres** (confirmed) | `Unit.area_sqm` (`Float`); dues = area × rate (`unit_dues`, line 433; parking `asset_unit_charge`, line 459) | Parse as `Decimal`; require > 0 and ≤ 2 decimals; store; read back and compare at 2 decimals. **Block the 141 zero rows** (they would bill ₱0). Flag 4 rows under 5 sqm for review. Never default a bad value to 0. | — (never a key) | Correct sizes for the 141 zero rows (134 parking, 7 residential)? Are the tiny areas real? |
| `Properties.Group` | Category (5 labels) | **No confirmed field.** `Unit.unit_type` drives billing: `PARKING`/`STORAGE` are excluded from residential logic (lines 1764, 2170, 2220); residential rates need `STUDIO TYPE` / `1–3 BEDROOM` (`TYPE_RATE_KEYS`, line 397) or a per-unit rate. | Keep the source label in staging. Map only by an approved table (e.g. `PARKING` → `unit_type = PARKING` **if confirmed**). Do not infer anything from `2F`, `LG`, `UG`. | — | Meaning of each label (§8 Q1). `RESIDENTIAL` has no bedroom type, so **no dues rate can be derived**: per-unit rate, manual dues, or a type list is needed. |
| `Properties.Owner` | Name text of the owner(s) | `Owner` record(s) on the unit: `owner_name`, `unit_id`, `status = Current`. **Not a login.** | Trim. **Do not split** on `/`, `,`, `&`, `and` automatically: ~290 cells may hold several names or an organisation. Rows with a multi-person marker go to review. Blank (1 row) → no owner created, flagged. | Per unit: a rerun matches the existing owner on that unit with the same normalised text, via the crosswalk. **Never matched across units by name**: the 124 texts repeated on 409 rows may be one owner of many units or different people with the same name. | One person per cell? Joint owners? Current only? Organisation owners (191)? |
| `Properties.Tenant` | Name text of the tenant(s) | `Tenant` record(s) on the unit: `tenant_name`, `status = Current`, `representative`. **Not a login.** | Same rules as Owner. 292 blanks = no tenant (confirm). The 96 rows where Tenant = Owner need a decision (owner-occupied vs a real tenancy record). | As Owner. | Is Tenant = Owner meaningful? Who is the representative? Current only? |
| *(none)* | — | `Unit.floor`, `unit_rate_per_sqm`, `dues_mode`, `manual_monthly_dues`, parking/storage assignment, `occupancy_type`, contact, email, move dates | **Left unset**, not invented. Defaults: per-sqm dues, status Vacant, occupancy Owner. | — | Source for floor, rates and parking-to-unit assignment? (No column links a parking slot to a residential unit; it must **not** be guessed from owner names.) |

**Resident portal accounts:** none are created by the import. Accounts are made afterwards by a
Superadmin, one person at a time, with *Generate account* (identity confirmed by the office).

---

## 4. Mapping: `cityland9_database_export.xlsx` → CITYLAND9

The sheet and column names are what the current importer (`run_excel_import`, line 2896) reads.
This workbook holds the synthetic sample set, so **no import of it is proposed**. The table defines
how this format must be handled if the client ever supplies data in it.

| Sheet.column(s) | Destination | Required transformation / validation | Matching key / duplicate rule | Unresolved |
|---|---|---|---|---|
| `Units.id` | crosswalk only (never `Unit.id`) | integer, unique in sheet | crosswalk `(source, id)` | — |
| `Units.unit_no` | `Unit.unit_no` | trim; required; unique | normalised `unit_no`; conflicts block | — |
| `Units.floor, unit_type, area_sqm, unit_rate_per_sqm, dues_mode, manual_monthly_dues, include_parking, include_storage, occupancy_type, status, active` | same-named `Unit` fields | `unit_type` ∈ approved list; area > 0; rates ≥ 0; `dues_mode` ∈ {per_sqm, manual}; booleans strict; **missing column = keep; blank cell = keep; explicit clear marker = clear** | — | allowed `status` values |
| `Units.assigned_parking_unit_id / assigned_storage_unit_id` | `Unit.assigned_*_unit_id` | must resolve **within the workbook** to a unit whose type is PARKING / STORAGE | via crosswalk | — |
| `Units.owner_name, contact_no, email` | ignored (owners come from `Owners`) | warn if they disagree with `Owners` | — | — |
| `Owners.*` / `Tenants.*` | `Owner` / `Tenant` | `unit_id` must resolve (else **row error, import blocked**); name required; dates valid; status ∈ {Current, Past} | crosswalk `(source, id)`; no name-based merging | — |
| `Billing.*` | `Billing` | month `YYYY-MM`; amounts numeric, ≥ 0 (adjustment may be negative if approved); status ∈ {Unpaid, Partial, Paid, Overdue}; `amount_paid` must equal the sum of payments **or** be explained by an approved opening-balance rule | `(unit, billing_month)` — the database's own unique key; crosswalk for the source id | cutoff date; meaning of `previous_balance` |
| `Payments.*` | `Payment` (+ receipt and allocation questions) | `billing_id` must resolve (else blocked); amount > 0; date valid | crosswalk `(source, id)` | do historical payments need official receipts? |
| `WaterReadings.*` | `WaterReading` | month valid; current ≥ previous; rate ≥ 0 | `(unit, reading_month)` (unique key) | — |
| `Employees.*` | `Employee` | `employee_no` and name required | `employee_no` (unique key), then crosswalk | — |
| `Expenses.*` | `Expense` | date, category and amount required; amount > 0 | crosswalk only (no natural key) | duplicates on rerun without a crosswalk |

---

## 5. Reported importer problems: verification against the current code

*Line numbers in this section refer to `backend/legacy_app.py` before the 0012 models were added (+47 lines since). The re-verification with current line numbers is in `CITYLAND9_IMPORT_SAFEGUARDS.md` §2.*

Code: `run_excel_import`, `backend/legacy_app.py:2896–3367`, called from
`backend/app/routes/system_settings.py:46` (Superadmin only).

**Method:** source reading, plus **reproduction on throwaway SQLite databases** in a scratch folder.
Each scenario got a fresh copy of the schema with the synthetic sample data, then one crafted
synthetic workbook was imported. The live database and the client files were not used. These
reproductions are not MariaDB runs: the same CHECK and UNIQUE constraints exist in the MariaDB
schema, but behaviour there was not executed.

| # | Reported problem | Verdict | Evidence |
|---|---|---|---|
| A | External ids overwrite unrelated records | **Confirmed defect** | Units are matched by `id` before `unit_no` (line 3064). Ids are copied into new rows (3070, 3129, 3163, 3194, 3234, 3277, 3307). **Reproduced:** a row `id=1, unit_no=SRC-0001` renamed existing unit 1 (`P-TEST-01`) instead of adding a unit. An Owners row with an existing owner's `id` moved that owner to another unit and replaced the name. |
| B | Duplicates on repeated imports | **Confirmed defect** | Child rows without `id` are always inserted (owner lookup only by id, line 3124). **Reproduced:** the same Owners sheet imported twice → 2 identical owners. Expenses have no natural key at all. |
| C | Unit/month uniqueness conflicts | **Confirmed defect** | Billing and water readings are matched by id only (3230), not by `(unit, month)`. **Reproduced:** a Billing row without id for an existing unit + month → whole import failed with a raw `UNIQUE constraint failed: billing.unit_id, billing.billing_month` message and no row number. Nothing is saved (single transaction). |
| D | Blank cells reset existing values | **Confirmed defect** | `manual_monthly_dues` defaults to 0 (3081), `active` to True (3090), water `paid` to False (3207), readings to 0 (3203–3204), `reading_date` to today (3206), and expense fields reset. **Reproduced:** manual dues 1234.00 → 0.00; deactivated unit → active; paid water → unpaid; readings → 0/0. Missing column, blank cell and intended clearing are not distinguished. |
| E | Changes to closed periods and issued / hand-corrected bills | **Confirmed defect** | No call to `check_open_period` (line 1463) or check of `soa_manual_override` in the importer; compare `recalculate_bill` (2477), which refuses both. **Reproduced:** with books closed through the bill's month and the bill hand-corrected, the import changed its assessment. Issue-time snapshot fields (migration 0010) are not refreshed either, so an overwritten bill no longer matches its stored issue snapshot. |
| F | Payment totals / status not reconciled | **Confirmed defect** | `amount_paid` and `status` are copied from the sheet (3252–3257); imported payments create no receipt or allocation. **Reproduced:** a bill with no payment rows stored amount paid 999.00 and status `Whatever`. |
| G | Silently skipped relationships | **Confirmed defect** | `continue` when the unit or bill doesn't resolve (3122, 3156, 3188, 3228, 3271) or required text is blank (3062, 3301); no count or message. **Reproduced:** an owner and a payment with unknown parents → "0 new / 0 updated", success, no warning. |
| H | Invalid month formats | **Partly confirmed; earlier claim corrected** | `normalize_month` falls back to `text[:7]` (2937–2943). Invalid months are **not stored**: the database CHECK constraints `ck_billing_month_format` / `ck_water_month_format` (`month_check`, line 123) reject them. **Reproduced** (`Jan 26`, `2026-13`, `13/45/2026`): whole import failed with a raw constraint message, no sheet/row given. Defect = error reporting and all-or-nothing on one bad cell, not data corruption. |
| I | Missing amount / status / type validation | **Confirmed defect** | `money()` turns unreadable values into 0 (line 149–155). **Reproduced:** `"abc"` in an amount → stored as 0.00, reported as success; negative assessment stored; unit with negative area, type `ANYTHING`, dues mode `weird`, status `???` stored. Only `amount_paid`, `previous_balance` and payment `amount` are protected by CHECK constraints (≥ 0). |
| J | Employee matching ignores employee numbers | **Confirmed, narrower than reported** | Line 3303: if the row has an `id`, the employee number is not used to match. **Reproduced:** an unknown `id` with an existing `employee_no` → whole import failed (`UNIQUE constraint failed: employee.employee_no`). Rows **without** `id` do match by number correctly. |

**Works as intended:** the file/size/row/column limits (lines 2908–2919, 2960–2966); the backup before
import, where a failed backup means no import (3027–3028); all-or-nothing commit and rollback; the
audit entry; Superadmin-only access.

**Potential concerns (not reproduced as defects):**
- `Unit.area_sqm` is a binary `Float`. Values with 2 decimals survive a round trip, but the importer uses `float(...)` without a decimal check (unlike the Units API).
- The audit entry records the file name only, not per-record before/after values. The backup is the only record of overwritten values.
- One invalid cell rejects the whole workbook without saying where.

---

## 6. Overpayment / advance double-allocation defect: current status

**Fixed in the current code, according to source inspection and isolated tests. Not changed in this task.**

- `record_bill_payment` (line 2265): excess over the bill balance becomes an `AdvancePayment` whose `start_month` is the **next** billing month (lines 2321–2329). It is no longer applied to the same, already-settled bill.
- `allocate_advances_for_month` (line 901): an advance is applied at most up to what the bill still owes (`need`, line 919). Recorded allocations are never rewritten (lines 963–965).
- `tests/test_overpayment_reconciliation.py`: **5 passed** in this review on the isolated test database. They cover excess counted once and carried forward, next month already billed, payment on a paid bill, the cap on scheduled advances, and the read-only detector for old double allocations.
- Minor inconsistency: the docstring of `record_bill_payment` (line 2269) still says the excess starts "from this billing month". The code says next month.
- Not verified here: historical double allocations in the **live** database. `database/tools/detect_overpayment_allocations.py` is the read-only check for that; it was not run (live database out of scope).

---

## 7. Next-stage plan (proposed, not executed)

### Proposed fix rules
- **Updates** distinguish a *missing column* (keep), a *blank cell* (keep), and an *explicit clear* (a documented marker such as `#CLEAR`). This applies to every field.
- **New records** must pass required-field validation (unit number, area > 0, approved type; owner/tenant name and resolved unit; bill unit + month; payment bill + amount > 0).
- **Unresolved financial relationships** (bill → unit, payment → bill, parking/storage → unit) **block the import** with sheet/row/column errors. They are never skipped.
- **Closed periods and issued or hand-corrected bills** are rejected by default; changing them needs a separately approved correction.
- **Historical financial migration** (bills, payments, advances, opening balances) needs its own approved mapping and reconciliation plan before any code is written for it. The `Properties` import does not touch money.

### Stages (in order)
1. **Focused importer fixes + regression tests:** defects A–J, each with a synthetic test that fails today and passes after the fix. Run the existing suite, plus a MariaDB run on a disposable database.
2. **Separate staging database:** a disposable `cityland9_stage_*` on the client's PC (or a test-PC copy). It is refused if the name or host is the live database. The `Properties` load goes there first.
3. **Dry-run validator, no database writes:** reads the workbook and reports, per row, new / update / conflict / blocked, with sheet, row, column and error code. It never echoes names or amounts. It also checks Group mapping coverage, the area rules, and multi-person cells flagged for review.
4. **Rerunnable importer:** crosswalk table (source, sheet, external id → CITYLAND id); matching only on approved keys; no source ids as primary keys; a second run of the same file changes nothing.
5. **Error reporting:** a downloadable error sheet (sheet, row, column, code, required action). The import is blocked until every blocking row is fixed or explicitly excluded by the client.
6. **Reconciliation, source → target:** row counts per Group; units with an owner / tenant; area totals per Group (source vs stored, 2 decimals); unresolved and excluded rows listed. For a later financial stage: bill counts per month, totals per unit, payments vs amount paid, opening balances at the cutoff.
7. **Rollback and approval:**
   - a verified backup (with a restore test) right before the run;
   - the client signs off the dry-run report and the reconciliation;
   - the import runs in one transaction on the staging database first, then on live;
   - post-import checks;
   - a documented restore path (`database\restore.py`).

---

## 8. Questions for the client

*Answers received on 2026-10-08 are recorded in §9; open items are re-asked there in plain wording.*

**Group meanings**
1. What does each Group mean: `RESIDENTIAL`, `PARKING`, `2F`, `LG`, `UG`? For each one, which CITYLAND unit type should it become (e.g. residential unit, parking slot, storage, commercial)? Are any of them not billed?
2. For `RESIDENTIAL` units, what is each unit's dues basis: bedroom type (Studio / 1 / 2 / 3 BR), a rate per sqm per unit, or a fixed monthly amount? The workbook has no bedroom type, so dues can't be computed without this.

**Identity and relationships**
3. Is `Name` the official unit number printed on SOAs? Is `ID` only the old system's internal number? Is it stable if you export again?
4. Does each `Owner` / `Tenant` cell name exactly one person? How are joint owners or several occupants written (`/`, `,`, `&`, "and", parentheses)? Should they be separate records?
5. When the same owner name appears on several properties, is it always the same person? Do you have an owner id, TIN or contact number that tells people apart?
6. On 96 rows, Tenant and Owner are the same text. Does that mean owner-occupied (no tenant record), or a real tenancy?
7. Which residential unit does each parking slot belong to? Is there a list? (It won't be guessed from names.)
8. Are organisation owners/tenants (companies) expected? Who is their contact person for SOAs?

**Completeness and data quality**
9. 141 properties have Size 0 (134 parking, 7 residential), and 4 are under 5 sqm (smallest 0.01). What are the correct areas, or should these rows be excluded for now?
10. Is `master_file (2).xlsx` the complete current list of properties? Are owner/tenant names current only, or do some include past occupants?
11. Is there another export with contact numbers, emails, move-in dates and representatives? None of these are in this file.
12. The second workbook, `cityland9_database_export.xlsx`, contains CITYLAND's own sample data (`TEST-*`). Was it sent as a format example only? Are there client billing or payment records to migrate, and in what format?

**Cutoff and finance**
13. What is the go-live cutoff date? Should CITYLAND start from **opening balances** per unit at that date, or import **historical bills and payments**? If historical, from which month?
14. Are there unpaid balances, advance payments or credits per unit at the cutoff? Who signs off the reconciled totals?
15. Should any month be marked closed (`books_closed_through`) right after migration?

---

## 9. Client answers (received 2026-10-08) and what they change

| Question | Client's answer (as given) | Effect on the mapping |
|---|---|---|
| Group meanings | All are units. `RESIDENTIAL` = lived-in units; `2F` = 2nd floor; `LG` = lower ground; `UG` = upper ground. (`PARKING` not separately explained.) | `2F` / `LG` / `UG` describe **where** the unit is → candidate for `Unit.floor` (stored as given, e.g. `2F`). They are billable units, not parking. `RESIDENTIAL` rows have no floor in the file → `Unit.floor` left blank (not derived from the unit number). `PARKING` → `unit_type = PARKING` still needs a one-line confirmation (§9.1 Q-b). |
| Residential dues | "Same for all, calculated per sqm of the size." | Dues = `Size` × one rate per sqm. In CITYLAND this means setting the **same `unit_rate_per_sqm`** on every billable unit (it overrides the bedroom-type rate, `unit_dues`, line 433), so no bedroom type is needed. **The rate amount is still unknown** (§9.1 Q-a). |
| Is `Name` the unit number on SOAs? | Yes. | **Confirmed:** `Name` → `Unit.unit_no`. `ID` → external reference in the crosswalk only. |
| One person per Owner/Tenant cell? | Not understood ("which cell name?"). | **Still open** — re-asked in plain words (§9.1 Q-c). |
| Correct areas for the 141 zero-size rows | Not understood. | **Still open** — re-asked in plain words (§9.1 Q-d). Rows stay blocked until answered. |
| Cutoff / history | "Bring past bills, as soon as it's in order." | Historical bills are wanted. **The master file contains no bills or payments**, so a source for past bills is required before any financial stage can be planned (§9.1 Q-e, Q-f). The `Properties` load (units, owners, tenants) can still go ahead separately. |

**New finding that depends on these answers:** CITYLAND only generates bills for units whose type is
not `PARKING` or `STORAGE` (`backend/app/routes/billing.py:118`). Parking is charged **through the
residential unit it is assigned to** (`assigned_parking_unit_id`, `parking_dues`). The 342 parking rows
have their own Owner/Tenant text and no link to a residential unit, so unless they are assigned (or
billed as their own units by an approved rule), **parking would not be billed at all**.

### 9.1 Follow-up questions (plain wording for the client)

- **Q-a. Rate:** How much is the dues **per square meter**? Is it the same amount for parking slots and for units on 2F, LG and UG?
- **Q-b. Parking:** Are the `PARKING` rows parking slots? Is a parking slot billed **together with the owner's unit** (on the same SOA) or **on its own SOA**? If together, which unit does each slot belong to (is there a list)?
- **Q-c. Owner/Tenant names:** In the Owner and Tenant columns, some boxes have **two or more names** written together (with `/`, `,`, `&` or "and"). For those, are they **co-owners** (two people who both own the unit), or one person written in a long way? Should each name become a separate owner?
- **Q-d. Zero sizes:** 141 properties have **Size 0** (134 parking, 7 residential). Size 0 means the bill would be ₱0. What is the **real size in square meters** of these? If not known yet, may we leave them out of the first import and add them later?
- **Q-e. Past bills — source:** Where are the past bills and payments kept (another Excel file, the old system, paper)? Please send a sample with a few rows.
- **Q-f. Past bills — period:** From which month should past bills be brought in, and what is the last month in the old system?

---

## 10. Safeguards added (2026-10-08) — see `CITYLAND9_IMPORT_SAFEGUARDS.md`

- **Stable source identity** (migration `0012_import_crosswalk`, not applied to the live database):
  - `import_source` (one row per source system), `import_crosswalk` (source + entity + external id → CITYLAND9 id; unique both ways) and `import_batch` (file name and hash as provenance only).
  - The `Properties.ID` column maps here, never to `Unit.id`.
- **Check only (dry run):** Settings → *Import from Excel* → **Check only**, or `database\import_check.py`.
  - It writes nothing (guarded and tested).
  - It reports sheet / row / column / code without values.
  - It applies the rules of §3–4: Size as confirmed sqm, zero blocked, Group via approved mapping only, people unresolved until approved, unit number = `Name` (confirmed).
- **Decisions file:** `migration_data/import_config.json` (git-ignored) holds the §9 answers. Every financial decision stays empty, so financial readiness stays blocked.
- **Re-verification of the ten importer issues:** all still present (none fixed in this stage). Invalid months are rejected by the database rather than stored (§5 H). The overpayment regression tests were re-run: 5 passed.
- **New finding:** CITYLAND9 recalculates carried balances and penalties from earlier bills and ignores a bill's stored `previous_balance` (except hand-corrected SOAs). An opening balance can't be imported as `previous_balance`, and partial history understates arrears. This needs its own approved design before any financial import.
- **Dry-run result on `master_file (2).xlsx`:**
  - 0 of 1,019 rows ready: 141 blocked (Size 0), 878 needing decisions (PARKING meaning, people rules);
  - 670 unit inserts proposed once those are settled.

