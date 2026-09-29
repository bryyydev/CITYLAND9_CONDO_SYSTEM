# CityLand 9 — Database Migration Plan: SQLite → Local MySQL

**Status (2026-09-30):** this development PC **runs on local MySQL**: MariaDB 10.4 program files, CityLand's own server on 127.0.0.1:3307, data in `database/mysql-data/`. Only demo/test data existed. It was migrated and verified: 76 rows with every value identical, and the 4 bills' computed figures identical. After the switch, 37/37 account checks and all 42 legacy pages passed. **The client's production data has not been migrated.** That is still step 8 of the runbook (§9), on the client's server.

**How it runs now**
- `.env` holds `DB_ENGINE=mysql`, the `MYSQL_*` settings with generated passwords, and a random `SECRET_KEY`. `backend/run.py` starts the database server automatically.
- Tools: `database/local_mysql.py {init,start,stop,status,backup}`, `database/tools/migrate_sqlite_to_mysql.py --confirm`, `database/tools/bill_snapshot.py`.
- On MySQL, the Excel import takes a `mysqldump` backup first and refuses to import if the backup fails (I6).
- **Rollback:** set `DB_ENGINE=sqlite` in `.env` and restart. The SQLite file hasn't been used since the switch, except for 6 test sign-in audit rows written by a stale server process during verification. Anything entered in MySQL after the switch won't be in SQLite.

The original plan text follows.
**Date:** 2026-09-29 · **Baseline:** git commit `c8c0f94` (V10.64, Stage 1)
**Deployment constraint:** self-hosted only. MySQL runs on the client's own server PC or LAN. No cloud database, cloud backend or backend-as-a-service is used or planned (see `docs/architecture.md`).

> Review gate: **module migration (React/API) does not start until this plan is approved.** The steps in §9 that touch real data each need an explicit go-ahead.

---

## 1. Current database (as-is)

| Item | Current state |
|---|---|
| Engine | **SQLite 3**, one file: `cityland_condo_web.db` in the project root (WAL journal mode, `busy_timeout=30000`) |
| Access layer | SQLAlchemy **1.4.54** through Flask-SQLAlchemy 3.0.5. Models live in `backend/legacy_app.py` |
| Schema management | `init_db()` runs `create_all()`, then **SQLite-only** `ALTER TABLE … ADD COLUMN` upgrades (backup `*.before_v10_cleanup.bak` first), plus 4 indexes created with raw SQL. The HR tables are also created on demand by `__table__.create(checkfirst=True)` inside HR routes. There is **no migration history.** |
| Size | **31 tables, 302 columns, 26 foreign keys, 10 UNIQUE columns, 23 indexes** (19 declared on models, one of them unique, + 4 created at runtime) |
| Data on this development PC | Essentially empty: 1 user, 19 settings, 3 audit rows. **The client's real data is on the client's server.** Every data check in this plan must be re-run against a *copy* of that database. |
| Earlier alternative | `migrate_sqlite_to_sqlserver.py` (SQL Server via pyodbc, V10.16). It's superseded by the MySQL target and kept for reference only. |

### 1.1 Tables by business area

| Area | Tables (rows depend on each other left → right) |
|---|---|
| Condo core | `unit` (self-references for assigned parking/storage units) → `owner`, `tenant`, `parking_lot` → `parking_billing` |
| Billing / SOA | `billing` → `payment`; `advance_payment` → `advance_application` (← `billing`); `water_reading` |
| Operations | `move_certificate`, `gate_pass`, `expense` |
| HR / payroll | `employee` → `employee_attendance`, `employee_leave`, `employee_overtime`, `employee_hr_loan`, `employee_payroll` → `employee_payroll_statutory`, `employee_payslip_item`; `employee_holiday`, `employee_hr_setting` |
| Community | `user` → `resident_profile` → `maintenance_ticket` (← `vendor`, `unit`); `announcement`, `vendor`, `document_record` |
| Administration | `user`, `audit_log`, `setting` |

Full per-column detail (types, nullability, defaults, keys, indexes) is in **Appendix A**. It's generated from the models, so it matches the code exactly.

### 1.2 Relationships and keys that need special care

| Item | Detail |
|---|---|
| Self-referencing FK | `unit.assigned_parking_unit_id` and `unit.assigned_storage_unit_id` → `unit.id`. A residential unit can point to an asset unit with a **higher** id, so a plain load in id order can fail (§6). |
| Polymorphic ids (no FK) | `move_certificate.person_id` and `resident_profile.person_id` refer to `owner.id` *or* `tenant.id`, depending on `person_type`. `gate_pass.unit_no` is free text. None of these can get a real FK. The preflight will check them logically. |
| 1-to-1 | `resident_profile.user_id` UNIQUE; `employee_payroll_statutory.payroll_id` UNIQUE |
| Unique in code only (D12) | One bill per (`unit_id`, `billing_month`); one reading per (`unit_id`, `reading_month`); one parking bill per (`parking_lot_id`, `billing_month`); one attendance row per (`employee_id`, `attendance_date`); one application per (`advance_payment_id`, `billing_id`) |
| Calculated, not stored | `water_reading.usage` and `bill_amount` (model properties). Bill totals, balances, penalties and status are **recomputed live** by the billing engine (checklist §3.1). The migration copies stored columns only. It never copies or "fixes" calculated values. |
| Historical records | `billing`, `payment`, `advance_payment`/`advance_application`, `water_reading`, `employee_payroll`/`statutory`, `audit_log`, `move_certificate`. They're copied as they are, ids included, with no recalculation or cleanup. |

---

## 2. Target database

| Item | Decision | Why |
|---|---|---|
| Server | **MySQL 8.0+ Community** or **MariaDB 10.4+**, installed **on the client's server PC** | Client requirement: local only. XAMPP ships MariaDB 10.4, and the schema was tested on it (§10). |
| Network | `bind-address = 127.0.0.1` when Flask runs on the same PC (recommended). **Port 3306 is never opened in the Windows firewall** | The browser never talks to MySQL. Only Flask does. |
| Account | A dedicated `cityland9_app` user limited to the `cityland9` database. The application **never uses root** | Least privilege |
| Driver | **PyMySQL** (`mysql+pymysql://`) | Pure Python: no DLLs and no MySQL client install, which avoids the kind of DLL trouble fixed in V10.63 |
| Storage engine | InnoDB | Transactions and foreign keys |
| Character set | `utf8mb4` | Full Unicode (ñ, accents, symbols) |
| Collation | **`utf8mb4_bin`** | Same comparison rules as SQLite today: case- and accent-sensitive for `=` and UNIQUE. The general/`ai_ci` collations would, for example, make usernames `Admin` and `admin` collide, or reject existing data. Search is unaffected: the code uses `ilike()`, which SQLAlchemy turns into `LOWER(x) LIKE LOWER(y)`. Available on both MySQL and MariaDB. |
| SQL mode | Strict (`STRICT_TRANS_TABLES`), the MySQL 8 default | Bad data fails loudly instead of being silently truncated |

---

## 3. Generated schema: `database/schema.sql`

- Produced by `python database/tools/generate_schema.py` from the models. **Never edit it by hand.** `--check` fails when it's out of date.
- Contents: 31 `CREATE TABLE` statements in foreign-key order, InnoDB/utf8mb4/utf8mb4_bin, all PKs, FKs and UNIQUE constraints, the 19 model indexes (one unique), and the 4 runtime indexes from `init_db()`.
- It **reproduces today's schema exactly.** The extra constraints proposed in §8.3 aren't in it; they'll be added as later, reviewed migrations.

---

## 4. SQLite → MySQL type mapping

| Model type | SQLite today | MySQL | Notes |
|---|---|---|---|
| `Integer` (PK) | INTEGER | `INT AUTO_INCREMENT` | After loading rows with explicit ids, InnoDB continues from max(id)+1 automatically |
| `Integer` | INTEGER | `INT` | |
| `String(n)` | VARCHAR(n), **length not enforced** | `VARCHAR(n)`, **enforced** | The preflight lists any value longer than *n* (blocking) |
| `Text` | TEXT | `TEXT` (64 KB) | |
| `Numeric(12,2)` / `(8,2)` / `(8,3)` | NUMERIC, **stored as a binary float** (hence the SAWarning at startup) | `DECIMAL(p,s)`, exact | Values with extra decimal places (float residue) are rounded to *s* on load. The preflight reports how many. Money becomes *more* exact, not less. |
| `Float` | REAL (8-byte double) | **`DOUBLE`**, not `FLOAT` | MySQL `FLOAT` is 4-byte single precision: an area of 45.37 would come back as 45.369998…, and `area × rate` dues would change. `DOUBLE` keeps the exact values used today. Affects `unit.area_sqm`, `unit_rate_per_sqm`, `parking_rate_per_sqm`, `parking_lot.area_sqm`/`rate_per_sqm`, `water_reading.previous_reading`/`current_reading`/`rate`. Converting these to DECIMAL is possible later, **after** golden-master checks (§7.4). |
| `Boolean` | 0/1 | `TINYINT(1)` | |
| `Date` | 'YYYY-MM-DD' text | `DATE` | |
| `DateTime` | 'YYYY-MM-DD HH:MM:SS.ffffff' text | **`DATETIME(6)`** | Keeps microseconds. Plain `DATETIME` would truncate them and change the ordering of rows created in the same second. |
| `Time` | text | `TIME` | |
| Months (`billing_month`, …) | VARCHAR(7) 'YYYY-MM' | `VARCHAR(7)` | Kept as text. All month logic compares strings, which works identically under `utf8mb4_bin` |

---

## 5. Incompatibilities found and how each is handled

| # | Severity | Incompatibility | Evidence | Resolution |
|---|---|---|---|---|
| I1 | **BLOCKER** | The app sets `SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 30}}` for every database. **PyMySQL rejects `timeout`**, so the app cannot connect to MySQL at all. | `pymysql.connect(timeout=30)` → `TypeError: unexpected keyword argument 'timeout'` (reproduced) | Config change: pass `timeout` only for SQLite; for MySQL use `connect_timeout` and `pool_recycle=280`. **Needs approval** (it changes `legacy_app.py`, but no business logic). |
| I2 | HIGH | MySQL `FLOAT` would change dues calculations | §4 | Schema uses `DOUBLE`, a generator rule. The **models** still say `Float`, so Alembic must use a `with_variant(DOUBLE, "mysql")` rule or it would recreate FLOAT (§8). |
| I3 | HIGH | **Foreign keys are enforced by MySQL, not by SQLite.** "Delete employee" fails for any employee with attendance, leave, overtime, loans or payroll. "Delete user" fails for resident accounts (their `resident_profile`). Today SQLite deletes the parent and leaves orphan rows. | Reproduced on MariaDB: `ERROR 1451 … employee_attendance_ibfk_1` and `… resident_profile_ibfk_1` | **Decision Q8 (yours):** block the delete with a clear message (safest, keeps history); deactivate instead of delete (`employment_status`/`active` already exist); or cascade (destroys payroll history, **not recommended**). Until decided, the new API returns HTTP 409. |
| I4 | HIGH | Existing orphans (rows whose parent was deleted under SQLite) will be **rejected** on load | Preflight check | The preflight lists every orphan (blocking). You decide per case: re-link, archive to a side table, or delete. Nothing is deleted automatically. |
| I5 | MEDIUM | `init_db()` schema upgrades are SQLite-only. Its raw index SQL (`CREATE INDEX IF NOT EXISTS "name"`) isn't valid MySQL, so it fails with a printed message. | Code lines ~3686–3720 | On MySQL, schema changes are made only by Alembic migrations (§8). `init_db()` keeps running on SQLite until cutover. |
| I6 | MEDIUM | The Excel import takes an automatic **SQLite-file** backup first. On MySQL `backup_sqlite_database()` returns `None`, so the import would run **with no backup**. | Code line ~2777 | Before Excel import is enabled on MySQL: add a local `mysqldump` backup step (to `database/backups/`, git-ignored), and refuse the import if the backup fails. |
| I7 | MEDIUM | Defaults are applied by Python (SQLAlchemy `default=`), not by the database. `schema.sql` therefore has no `DEFAULT` clauses, so rows inserted with raw SQL (not through the app) get NULLs. | Appendix A, "App default" column | Fine for the app itself. A later Alembic revision can add matching `server_default`s (§8.3). |
| I8 | MEDIUM | Concurrency: InnoDB (REPEATABLE READ, row locks) lets two users run "Generate billing" at the same moment, so the check-then-insert could create **duplicate bills**. SQLite's single-writer lock made that unlikely. | Code line 1856 | Short term: nothing changes (single office). After cleanup, add the D12 UNIQUE constraints (§8.3) so the database itself prevents duplicates. |
| I9 | MEDIUM | Self-referencing `unit` FKs can fail when loading in id order | §1.2 | Two-pass load for `unit` (§6) |
| I10 | LOW | `key` is a reserved word in MySQL (`setting.key`, `employee_hr_setting.key`) | Schema | SQLAlchemy quotes it automatically, as `` `key` `` in `schema.sql`. The code has no raw SQL that uses it. |
| I11 | LOW | MySQL closes idle connections after `wait_timeout` (8 h by default). The first request after a night idle would fail. | MySQL behavior | `pool_pre_ping=True` (already set) + `pool_recycle=280` (part of I1) |
| I12 | LOW | MySQL 8 logs in with `caching_sha2_password` by default, and PyMySQL needs the `cryptography` package for that | PyMySQL docs | `cryptography` is in `backend/requirements.txt` |
| I13 | INFO | Tested so far on **MariaDB 10.4** (XAMPP) only. MySQL 8 differs in its default auth plugin, collation names and some SQL modes. | §10 | Re-run the schema test on the client's actual server (MySQL 8 or MariaDB) before step 5 of §9 |

Checked and **not** a problem: `db.extract("year", …)`, `func.coalesce`, `ilike`, `distinct()`, pagination, `ORDER BY` on month strings, boolean filters, index key lengths (the longest indexed column is VARCHAR(255) = 1020 bytes, under InnoDB's 3072-byte limit), and table and index names under 64 characters.

---

## 6. Data migration order

Load in foreign-key dependency order, keeping every original `id`:

```
announcement, audit_log, employee, employee_holiday, employee_hr_setting, expense,
gate_pass, setting, unit*, user, vendor,
advance_payment, billing, document_record, employee_attendance, employee_hr_loan,
employee_leave, employee_overtime, employee_payroll, move_certificate, owner,
parking_lot, resident_profile, tenant, water_reading,
advance_application, employee_payroll_statutory, employee_payslip_item,
maintenance_ticket, parking_billing, payment
```

\* **`unit` uses two passes:** first insert every unit with `assigned_parking_unit_id` and `assigned_storage_unit_id` set to NULL, then `UPDATE` those two columns once all units exist. Foreign-key checks stay **on** the whole time (never `SET FOREIGN_KEY_CHECKS=0`), so MySQL itself proves the references are valid.

The whole load runs in **one transaction**. Any error rolls back everything and leaves the target database empty.

Not migrated (by design): the unused `hr_*` copies in `setting` (checklist D9) are carried over **as they are**. No data is filtered or "cleaned" silently. Every exclusion must be listed here and approved.

---

## 7. Validation strategy

Each gate must pass before moving to the next step of §9. Every tool is local and read-only against the source.

| # | Gate | Tool | Pass condition |
|---|---|---|---|
| 7.1 | **Source preflight** | `database/tools/sqlite_preflight.py <copy-of-client-db>` (works on its own temporary copy, read-only) | 0 blocking items. Warnings reviewed and signed off. |
| 7.2 | **Schema test** | `database/tools/mysql_schema_test.py --create` against a **new, empty** database | Every table, column, type, nullability, PK, FK, UNIQUE and index matches the models. The tool refuses to run on a non-empty database. |
| 7.3 | **Data round-trip** | Same tool with `--roundtrip-sqlite <copy>` for test data; the §9 migration tool for the client's data | Row counts equal per table. **Every value** equal after normalization (decimals to their scale). The DECIMAL rounding count equals the preflight's warning count. |
| 7.4 | **Golden master (business results)** | To be built before §9 step 5 | For every bill, the legacy engine's `condo, parking, water, penalty, previous, advance, current, total, balance, status`, computed on SQLite and then on MySQL, are identical. The same goes for every payroll/statutory row, the dashboard KPIs and the report totals for 3 sample periods. |
| 7.5 | **Application tests** | `python -m pytest` pointed at a MySQL test database | The existing 22 tests plus MySQL-specific ones (FK behavior, collation) pass |
| 7.6 | **User acceptance** | Client office staff | A sample of SOAs, payslips and reports printed from both systems match. Signed off. |

**Evidence so far** (synthetic data only, 2026-09-29):
- The schema applied cleanly to a new MariaDB 10.4.32 database in strict mode. All 31 tables and 302 columns match the models.
- 198 synthetic rows covering all 31 tables round-tripped with **0 mismatches**.
- Negative tests passed: the schema test detected a FLOAT column and a missing index, and refused both a non-empty target and the production file as a source. The preflight caught all 6 injected defects (over-long text, orphan FK, duplicate bill, non-numeric amount, 3-decimal amount, bad month).
- The preflight of this PC's `cityland_condo_web.db` passed (it's almost empty), and the file's timestamp and size were unchanged afterwards.

---

## 8. Schema migrations with Flask-Migrate / Alembic (plan)

### 8.1 Setup (when the application factory `backend/app/__init__.py` exists)
- `Migrate(app, db, directory="database/migrations")`, so migrations live next to `schema.sql`.
- Revision **`0001_baseline`** creates exactly what `schema.sql` creates, and is verified by running the schema test against a database built with `flask db upgrade`.
- The models get MySQL variants so autogenerate doesn't fight the §4 decisions: `db.Float().with_variant(DOUBLE, "mysql")` and `db.DateTime().with_variant(DATETIME(fsp=6), "mysql")`. That's a type annotation only, with no behavior change.
- A test runs `generate_schema.py --check` and compares a `flask db upgrade` database with `schema.sql`, so the two can't drift apart.

### 8.2 Rules
- The legacy **SQLite database is not managed by Alembic.** `init_db()` keeps handling it until cutover. After cutover, `init_db()`'s schema code is disabled for MySQL and Alembic is the only way to change the schema.
- Every revision is reviewed, has a working `downgrade()`, and is tested on a copy of the data first. A `mysqldump` backup runs before `flask db upgrade` on the server.
- Autogenerated revisions are edited by hand before use. Nothing is auto-applied on app start.

### 8.3 Candidate follow-up revisions (each needs its own approval)
1. UNIQUE constraints for the D12 combinations, **after** the preflight shows zero duplicates (or duplicates are resolved by the client).
2. `ON DELETE` behavior once Q8 is decided.
3. `server_default` values matching the Python defaults (I7).
4. Optionally, `Float` → `DECIMAL` for rates and areas, only if the golden master proves identical results.

---

## 9. Migration runbook (steps that touch real data need a separate go-ahead)

| Step | What | Touches real data? |
|---|---|---|
| 1 | Client installs MySQL 8 or MariaDB **on the server PC**, with `bind-address=127.0.0.1` and 3306 closed in the firewall. Create the database and the least-privilege user (§9.1). | No |
| 2 | Copy the client's `cityland_condo_web.db` (with the app stopped, or using the SQLite backup API) to a working folder. Run the preflight on the **copy**. | Reads a copy |
| 3 | Resolve the blocking items: a written decision for each, applied to the copy. | No (copy only) |
| 4 | `mysql_schema_test.py --create` against a new, empty `cityland9_trial` database | No |
| 5 | Trial migration: copy → `cityland9_trial` (tool to be written after this plan is approved), then gates 7.3–7.5 | No |
| 6 | Code change I1 (engine options), the Q8 decision, and the I6 backup for Excel import. Reviewed and committed. | No |
| 7 | Parallel run: the legacy app on a copy pointed at `cityland9_trial`. Staff re-enter one day's work in both systems and compare (gate 7.6). | No |
| 8 | **Cutover** (a planned window, e.g. after month-end billing): stop the app → final SQLite backup → final migration into a new empty `cityland9` → gates 7.3/7.4 → set `DATABASE_URL=mysql+pymysql://…` in `.env` → start → smoke test | **Yes**, after sign-off |

### 9.1 Local MySQL setup (server PC)
```sql
-- run as the MySQL administrator on the server PC
CREATE DATABASE cityland9 CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
CREATE USER 'cityland9_app'@'localhost' IDENTIFIED BY '<strong password, stored only in .env>';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, REFERENCES, DROP
      ON cityland9.* TO 'cityland9_app'@'localhost';   -- DROP is needed by Alembic downgrades only
```
`my.ini` / `my.cnf`: `bind-address=127.0.0.1`, `character-set-server=utf8mb4`, `collation-server=utf8mb4_bin`, `sql_mode=STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION`.
Backups: a daily `mysqldump --single-transaction --routines cityland9` from Windows Task Scheduler to a local or external drive, kept for 30 days. No cloud backup unless the client approves it.

---

## 10. Rollback strategy

| Point in the plan | Rollback |
|---|---|
| Steps 1–7 | Nothing to roll back: production is still the untouched SQLite file. Trial databases can be dropped. |
| Code changes | Every change is a git commit on top of `c8c0f94`, reverted with `git revert`. The Stage 0 ZIP backup (`Documents\CITYLAND9_BACKUP_V10_64_pre_migration_20260929_024843.zip`) is kept as well. |
| Cutover (step 8) | The SQLite file is **never modified or deleted**: after the final backup it's kept read-only as the archive. Rollback = remove `DATABASE_URL` from `.env` and restart, which puts the app straight back on SQLite. |
| After go-live | Any data entered in MySQL after cutover would be lost by a plain rollback. So during an agreed **stabilization period** (proposed: 2 weeks), daily `mysqldump` backups are kept, and a MySQL → SQLite export tool is ready (the same copy tool pointed the other way, tested at step 5). |
| Failed schema revision | `flask db downgrade`, or restore the `mysqldump` taken right before the upgrade |

---

## 11. Decisions needed from you

| ID | Question |
|---|---|
| Q8 | Deleting employees and resident accounts that have history: **block with a message** (recommended), **deactivate instead**, or cascade? (I3) |
| Q10 | Which server will the client use: **MySQL 8 Community** (recommended: long-term support, matches "MySQL") or **XAMPP MariaDB**? The schema test must be re-run on the chosen one. |
| Q11 | Approve code change I1 (engine options) when step 6 comes? |
| Q12 | For the trial migration (step 5), who provides a copy of the client's live database, and when? |

---

## Appendix A — Column reference (generated from the models)

¹ Index created at runtime by `init_db()`, not declared on the model. `schema.sql` includes it.
"App default" is applied by the application (SQLAlchemy), not by the database (see I7).

#### `announcement`
PK `id` · FK — · UNIQUE — · INDEX —

| Column | SQLite (today) | MySQL | NULL | App default |
|---|---|---|---|---|
| `id` | INTEGER | INT AUTO_INCREMENT | PK | — |
| `title` | VARCHAR(200) | VARCHAR(200) | **no** | — |
| `message` | TEXT | TEXT | **no** | — |
| `audience` | VARCHAR(30) | VARCHAR(30) | yes | 'residents' |
| `published` | BOOLEAN | TINYINT(1) | yes | True |
| `publish_date` | DATE | DATE | yes | now/today |
| `created_by` | VARCHAR(80) | VARCHAR(80) | yes | — |
| `created_at` | DATETIME | DATETIME(6) | yes | now/today |

#### `audit_log`
PK `id` · FK — · UNIQUE — · INDEX `ix_audit_log_created_at`¹

| Column | SQLite (today) | MySQL | NULL | App default |
|---|---|---|---|---|
| `id` | INTEGER | INT AUTO_INCREMENT | PK | — |
| `username` | VARCHAR(80) | VARCHAR(80) | yes | — |
| `action` | VARCHAR(255) | VARCHAR(255) | yes | — |
| `created_at` | DATETIME | DATETIME(6) | yes | now/today |

#### `employee`
PK `id` · FK — · UNIQUE `employee_no` · INDEX —

| Column | SQLite (today) | MySQL | NULL | App default |
|---|---|---|---|---|
| `id` | INTEGER | INT AUTO_INCREMENT | PK | — |
| `employee_no` | VARCHAR(50) | VARCHAR(50) | **no** | — |
| `full_name` | VARCHAR(200) | VARCHAR(200) | **no** | — |
| `position` | VARCHAR(120) | VARCHAR(120) | yes | — |
| `department` | VARCHAR(120) | VARCHAR(120) | yes | — |
| `employment_status` | VARCHAR(40) | VARCHAR(40) | yes | 'Active' |
| `contact_no` | VARCHAR(80) | VARCHAR(80) | yes | — |
| `email` | VARCHAR(160) | VARCHAR(160) | yes | — |
| `date_hired` | DATE | DATE | yes | — |
| `monthly_salary` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `notes` | VARCHAR(500) | VARCHAR(500) | yes | — |
| `created_at` | DATETIME | DATETIME(6) | yes | now/today |

#### `employee_holiday`
PK `id` · FK — · UNIQUE `holiday_date` · INDEX —

| Column | SQLite (today) | MySQL | NULL | App default |
|---|---|---|---|---|
| `id` | INTEGER | INT AUTO_INCREMENT | PK | — |
| `holiday_date` | DATE | DATE | **no** | — |
| `name` | VARCHAR(200) | VARCHAR(200) | **no** | — |
| `holiday_type` | VARCHAR(30) | VARCHAR(30) | yes | 'REGULAR' |
| `active` | BOOLEAN | TINYINT(1) | yes | True |

#### `employee_hr_setting`
PK `id` · FK — · UNIQUE `key` · INDEX —

| Column | SQLite (today) | MySQL | NULL | App default |
|---|---|---|---|---|
| `id` | INTEGER | INT AUTO_INCREMENT | PK | — |
| `key` | VARCHAR(80) | VARCHAR(80) | **no** | — |
| `value` | VARCHAR(255) | VARCHAR(255) | yes | — |

#### `expense`
PK `id` · FK — · UNIQUE — · INDEX —

| Column | SQLite (today) | MySQL | NULL | App default |
|---|---|---|---|---|
| `id` | INTEGER | INT AUTO_INCREMENT | PK | — |
| `expense_date` | DATE | DATE | yes | now/today |
| `category` | VARCHAR(100) | VARCHAR(100) | yes | — |
| `description` | VARCHAR(300) | VARCHAR(300) | yes | — |
| `amount` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |

#### `gate_pass`
PK `id` · FK — · UNIQUE — · INDEX —

| Column | SQLite (today) | MySQL | NULL | App default |
|---|---|---|---|---|
| `id` | INTEGER | INT AUTO_INCREMENT | PK | — |
| `pass_date` | DATE | DATE | yes | now/today |
| `unit_no` | VARCHAR(40) | VARCHAR(40) | yes | — |
| `visitor_name` | VARCHAR(200) | VARCHAR(200) | yes | — |
| `purpose` | VARCHAR(300) | VARCHAR(300) | yes | — |
| `status` | VARCHAR(30) | VARCHAR(30) | yes | 'Issued' |

#### `setting`
PK `id` · FK — · UNIQUE `key` · INDEX —

| Column | SQLite (today) | MySQL | NULL | App default |
|---|---|---|---|---|
| `id` | INTEGER | INT AUTO_INCREMENT | PK | — |
| `key` | VARCHAR(80) | VARCHAR(80) | yes | — |
| `value` | VARCHAR(255) | VARCHAR(255) | yes | — |

#### `unit`
PK `id` · FK `assigned_storage_unit_id` → `unit.id`, `assigned_parking_unit_id` → `unit.id` · UNIQUE `unit_no` · INDEX —

| Column | SQLite (today) | MySQL | NULL | App default |
|---|---|---|---|---|
| `id` | INTEGER | INT AUTO_INCREMENT | PK | — |
| `unit_no` | VARCHAR(40) | VARCHAR(40) | **no** | — |
| `floor` | VARCHAR(20) | VARCHAR(20) | yes | — |
| `unit_type` | VARCHAR(50) | VARCHAR(50) | yes | — |
| `area_sqm` | FLOAT | DOUBLE | yes | 0 |
| `unit_rate_per_sqm` | FLOAT | DOUBLE | yes | 0 |
| `include_parking` | BOOLEAN | TINYINT(1) | yes | False |
| `include_storage` | BOOLEAN | TINYINT(1) | yes | False |
| `assigned_parking_unit_id` | INTEGER | INT | yes | — |
| `assigned_storage_unit_id` | INTEGER | INT | yes | — |
| `owner_name` | VARCHAR(200) | VARCHAR(200) | yes | — |
| `contact_no` | VARCHAR(80) | VARCHAR(80) | yes | — |
| `email` | VARCHAR(160) | VARCHAR(160) | yes | — |
| `tenant_name` | VARCHAR(200) | VARCHAR(200) | yes | — |
| `parking_slot` | VARCHAR(80) | VARCHAR(80) | yes | — |
| `parking_slots` | INTEGER | INT | yes | 0 |
| `parking_rate_per_sqm` | FLOAT | DOUBLE | yes | 0 |
| `monthly_rate` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `auto_rate` | BOOLEAN | TINYINT(1) | yes | True |
| `dues_mode` | VARCHAR(20) | VARCHAR(20) | yes | 'per_sqm' |
| `manual_monthly_dues` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `occupancy_type` | VARCHAR(20) | VARCHAR(20) | yes | 'Owner' |
| `status` | VARCHAR(30) | VARCHAR(30) | yes | 'Vacant' |
| `active` | BOOLEAN | TINYINT(1) | yes | True |

#### `user`
PK `id` · FK — · UNIQUE `username` · INDEX —

| Column | SQLite (today) | MySQL | NULL | App default |
|---|---|---|---|---|
| `id` | INTEGER | INT AUTO_INCREMENT | PK | — |
| `username` | VARCHAR(80) | VARCHAR(80) | **no** | — |
| `password_hash` | VARCHAR(255) | VARCHAR(255) | **no** | — |
| `role` | VARCHAR(40) | VARCHAR(40) | yes | 'staff' |
| `active` | BOOLEAN | TINYINT(1) | yes | True |
| `created_at` | DATETIME | DATETIME(6) | yes | now/today |

#### `vendor`
PK `id` · FK — · UNIQUE — · INDEX —

| Column | SQLite (today) | MySQL | NULL | App default |
|---|---|---|---|---|
| `id` | INTEGER | INT AUTO_INCREMENT | PK | — |
| `vendor_name` | VARCHAR(200) | VARCHAR(200) | **no** | — |
| `service_type` | VARCHAR(120) | VARCHAR(120) | yes | — |
| `contact_person` | VARCHAR(160) | VARCHAR(160) | yes | — |
| `contact_no` | VARCHAR(80) | VARCHAR(80) | yes | — |
| `email` | VARCHAR(160) | VARCHAR(160) | yes | — |
| `address` | VARCHAR(300) | VARCHAR(300) | yes | — |
| `status` | VARCHAR(30) | VARCHAR(30) | yes | 'Active' |
| `notes` | TEXT | TEXT | yes | — |
| `created_at` | DATETIME | DATETIME(6) | yes | now/today |

#### `advance_payment`
PK `id` · FK `unit_id` → `unit.id` · UNIQUE — · INDEX —

| Column | SQLite (today) | MySQL | NULL | App default |
|---|---|---|---|---|
| `id` | INTEGER | INT AUTO_INCREMENT | PK | — |
| `unit_id` | INTEGER | INT | **no** | — |
| `payment_date` | DATE | DATE | yes | now/today |
| `amount` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `start_month` | VARCHAR(7) | VARCHAR(7) | **no** | — |
| `coverage_months` | INTEGER | INT | yes | 1 |
| `monthly_amount` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `payment_method` | VARCHAR(20) | VARCHAR(20) | yes | 'CASH' |
| `reference` | VARCHAR(100) | VARCHAR(100) | yes | — |
| `remarks` | VARCHAR(300) | VARCHAR(300) | yes | — |

#### `billing`
PK `id` · FK `unit_id` → `unit.id` · UNIQUE — · INDEX `ix_billing_month`, `ix_billing_unit_month`

| Column | SQLite (today) | MySQL | NULL | App default |
|---|---|---|---|---|
| `id` | INTEGER | INT AUTO_INCREMENT | PK | — |
| `unit_id` | INTEGER | INT | **no** | — |
| `billing_month` | VARCHAR(7) | VARCHAR(7) | **no** | — |
| `assessment` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `parking_dues` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `storage_dues` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `water` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `other` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `penalty` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `adjustment` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `previous_balance` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `amount_paid` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `due_date` | DATE | DATE | yes | — |
| `status` | VARCHAR(30) | VARCHAR(30) | yes | 'Unpaid' |
| `paid_date` | DATE | DATE | yes | — |
| `soa_note` | VARCHAR(1000) | VARCHAR(1000) | yes | '' |
| `soa_manual_override` | BOOLEAN | TINYINT(1) | yes | False |
| `created_at` | DATETIME | DATETIME(6) | yes | now/today |

#### `document_record`
PK `id` · FK `unit_id` → `unit.id` · UNIQUE — · INDEX —

| Column | SQLite (today) | MySQL | NULL | App default |
|---|---|---|---|---|
| `id` | INTEGER | INT AUTO_INCREMENT | PK | — |
| `title` | VARCHAR(200) | VARCHAR(200) | **no** | — |
| `category` | VARCHAR(100) | VARCHAR(100) | yes | 'General' |
| `description` | TEXT | TEXT | yes | — |
| `file_name` | VARCHAR(300) | VARCHAR(300) | yes | — |
| `file_path` | VARCHAR(500) | VARCHAR(500) | yes | — |
| `audience` | VARCHAR(30) | VARCHAR(30) | yes | 'admin' |
| `unit_id` | INTEGER | INT | yes | — |
| `active` | BOOLEAN | TINYINT(1) | yes | True |
| `uploaded_by` | VARCHAR(80) | VARCHAR(80) | yes | — |
| `created_at` | DATETIME | DATETIME(6) | yes | now/today |

#### `employee_attendance`
PK `id` · FK `employee_id` → `employee.id` · UNIQUE — · INDEX `ix_employee_attendance_attendance_date`, `ix_employee_attendance_employee_id`

| Column | SQLite (today) | MySQL | NULL | App default |
|---|---|---|---|---|
| `id` | INTEGER | INT AUTO_INCREMENT | PK | — |
| `employee_id` | INTEGER | INT | **no** | — |
| `attendance_date` | DATE | DATE | **no** | — |
| `time_in` | TIME | TIME | yes | — |
| `time_out` | TIME | TIME | yes | — |
| `status` | VARCHAR(30) | VARCHAR(30) | **no** | 'PRESENT' |
| `remarks` | VARCHAR(255) | VARCHAR(255) | yes | — |
| `created_at` | DATETIME | DATETIME(6) | yes | now/today |

#### `employee_hr_loan`
PK `id` · FK `employee_id` → `employee.id` · UNIQUE — · INDEX `ix_employee_hr_loan_employee_id`

| Column | SQLite (today) | MySQL | NULL | App default |
|---|---|---|---|---|
| `id` | INTEGER | INT AUTO_INCREMENT | PK | — |
| `employee_id` | INTEGER | INT | **no** | — |
| `loan_type` | VARCHAR(60) | VARCHAR(60) | **no** | — |
| `reference_no` | VARCHAR(80) | VARCHAR(80) | yes | — |
| `original_amount` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `balance` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `monthly_deduction` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `status` | VARCHAR(20) | VARCHAR(20) | yes | 'ACTIVE' |
| `notes` | VARCHAR(300) | VARCHAR(300) | yes | — |

#### `employee_leave`
PK `id` · FK `employee_id` → `employee.id` · UNIQUE — · INDEX `ix_employee_leave_employee_id`

| Column | SQLite (today) | MySQL | NULL | App default |
|---|---|---|---|---|
| `id` | INTEGER | INT AUTO_INCREMENT | PK | — |
| `employee_id` | INTEGER | INT | **no** | — |
| `leave_type` | VARCHAR(50) | VARCHAR(50) | **no** | — |
| `start_date` | DATE | DATE | **no** | — |
| `end_date` | DATE | DATE | **no** | — |
| `days` | NUMERIC(8, 2) | DECIMAL(8, 2) | yes | 0 |
| `status` | VARCHAR(20) | VARCHAR(20) | yes | 'PENDING' |
| `reason` | VARCHAR(500) | VARCHAR(500) | yes | — |
| `approved_by` | VARCHAR(100) | VARCHAR(100) | yes | — |
| `created_at` | DATETIME | DATETIME(6) | yes | now/today |

#### `employee_overtime`
PK `id` · FK `employee_id` → `employee.id` · UNIQUE — · INDEX `ix_employee_overtime_employee_id`, `ix_employee_overtime_ot_date`

| Column | SQLite (today) | MySQL | NULL | App default |
|---|---|---|---|---|
| `id` | INTEGER | INT AUTO_INCREMENT | PK | — |
| `employee_id` | INTEGER | INT | **no** | — |
| `ot_date` | DATE | DATE | **no** | — |
| `hours` | NUMERIC(8, 2) | DECIMAL(8, 2) | yes | 0 |
| `rate_multiplier` | NUMERIC(8, 3) | DECIMAL(8, 3) | yes | 1.25 |
| `amount` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `status` | VARCHAR(20) | VARCHAR(20) | yes | 'PENDING' |
| `reason` | VARCHAR(500) | VARCHAR(500) | yes | — |
| `approved_by` | VARCHAR(100) | VARCHAR(100) | yes | — |
| `created_at` | DATETIME | DATETIME(6) | yes | now/today |

#### `employee_payroll`
PK `id` · FK `employee_id` → `employee.id` · UNIQUE — · INDEX `ix_employee_payroll_employee_id`, `ix_employee_payroll_period_end`, `ix_employee_payroll_period_start`

| Column | SQLite (today) | MySQL | NULL | App default |
|---|---|---|---|---|
| `id` | INTEGER | INT AUTO_INCREMENT | PK | — |
| `employee_id` | INTEGER | INT | **no** | — |
| `period_start` | DATE | DATE | **no** | — |
| `period_end` | DATE | DATE | **no** | — |
| `basic_salary` | NUMERIC(12, 2) | DECIMAL(12, 2) | **no** | 0 |
| `overtime_pay` | NUMERIC(12, 2) | DECIMAL(12, 2) | **no** | 0 |
| `allowances` | NUMERIC(12, 2) | DECIMAL(12, 2) | **no** | 0 |
| `deductions` | NUMERIC(12, 2) | DECIMAL(12, 2) | **no** | 0 |
| `absences` | NUMERIC(12, 2) | DECIMAL(12, 2) | **no** | 0 |
| `late_undertime` | NUMERIC(12, 2) | DECIMAL(12, 2) | **no** | 0 |
| `net_pay` | NUMERIC(12, 2) | DECIMAL(12, 2) | **no** | 0 |
| `status` | VARCHAR(20) | VARCHAR(20) | **no** | 'DRAFT' |
| `remarks` | VARCHAR(255) | VARCHAR(255) | yes | — |
| `created_at` | DATETIME | DATETIME(6) | yes | now/today |

#### `move_certificate`
PK `id` · FK `unit_id` → `unit.id` · UNIQUE `certificate_no` · INDEX —

| Column | SQLite (today) | MySQL | NULL | App default |
|---|---|---|---|---|
| `id` | INTEGER | INT AUTO_INCREMENT | PK | — |
| `certificate_no` | VARCHAR(50) | VARCHAR(50) | **no** | — |
| `move_type` | VARCHAR(20) | VARCHAR(20) | **no** | — |
| `person_type` | VARCHAR(20) | VARCHAR(20) | **no** | — |
| `unit_id` | INTEGER | INT | **no** | — |
| `person_id` | INTEGER | INT | **no** | — |
| `person_name` | VARCHAR(200) | VARCHAR(200) | **no** | — |
| `move_date` | DATE | DATE | yes | — |
| `certificate_date` | DATE | DATE | **no** | now/today |
| `issued_by` | VARCHAR(80) | VARCHAR(80) | yes | — |
| `created_at` | DATETIME | DATETIME(6) | yes | now/today |

#### `owner`
PK `id` · FK `unit_id` → `unit.id` · UNIQUE — · INDEX `ix_owner_unit_id`¹

| Column | SQLite (today) | MySQL | NULL | App default |
|---|---|---|---|---|
| `id` | INTEGER | INT AUTO_INCREMENT | PK | — |
| `unit_id` | INTEGER | INT | **no** | — |
| `owner_name` | VARCHAR(200) | VARCHAR(200) | **no** | — |
| `contact_no` | VARCHAR(100) | VARCHAR(100) | yes | — |
| `email` | VARCHAR(200) | VARCHAR(200) | yes | — |
| `move_in` | DATE | DATE | yes | — |
| `move_out` | DATE | DATE | yes | — |
| `notes` | TEXT | TEXT | yes | — |
| `status` | VARCHAR(20) | VARCHAR(20) | yes | 'Current' |
| `receive_soa_email` | BOOLEAN | TINYINT(1) | yes | False |
| `include_in_soa` | BOOLEAN | TINYINT(1) | yes | True |
| `created_at` | DATETIME | DATETIME(6) | yes | now/today |

#### `parking_lot`
PK `id` · FK `unit_id` → `unit.id` · UNIQUE — · INDEX —

| Column | SQLite (today) | MySQL | NULL | App default |
|---|---|---|---|---|
| `id` | INTEGER | INT AUTO_INCREMENT | PK | — |
| `unit_id` | INTEGER | INT | **no** | — |
| `slot_no` | VARCHAR(80) | VARCHAR(80) | **no** | — |
| `area_sqm` | FLOAT | DOUBLE | yes | 0 |
| `rate_per_sqm` | FLOAT | DOUBLE | yes | 0 |
| `status` | VARCHAR(30) | VARCHAR(30) | yes | 'Assigned' |
| `active` | BOOLEAN | TINYINT(1) | yes | True |
| `notes` | VARCHAR(300) | VARCHAR(300) | yes | — |
| `assigned_to_type` | VARCHAR(20) | VARCHAR(20) | yes | 'Owner' |
| `assigned_to_name` | VARCHAR(200) | VARCHAR(200) | yes | — |
| `include_in_soa_owner` | BOOLEAN | TINYINT(1) | yes | False |
| `include_in_soa_tenant` | BOOLEAN | TINYINT(1) | yes | False |
| `created_at` | DATETIME | DATETIME(6) | yes | now/today |

#### `resident_profile`
PK `id` · FK `unit_id` → `unit.id`, `user_id` → `user.id` · UNIQUE `user_id` · INDEX —

| Column | SQLite (today) | MySQL | NULL | App default |
|---|---|---|---|---|
| `id` | INTEGER | INT AUTO_INCREMENT | PK | — |
| `user_id` | INTEGER | INT | **no** | — |
| `unit_id` | INTEGER | INT | **no** | — |
| `person_type` | VARCHAR(20) | VARCHAR(20) | yes | 'Owner' |
| `person_id` | INTEGER | INT | yes | — |
| `display_name` | VARCHAR(200) | VARCHAR(200) | **no** | — |
| `active` | BOOLEAN | TINYINT(1) | yes | True |
| `created_at` | DATETIME | DATETIME(6) | yes | now/today |

#### `tenant`
PK `id` · FK `unit_id` → `unit.id` · UNIQUE — · INDEX `ix_tenant_status`¹, `ix_tenant_unit_id`¹

| Column | SQLite (today) | MySQL | NULL | App default |
|---|---|---|---|---|
| `id` | INTEGER | INT AUTO_INCREMENT | PK | — |
| `unit_id` | INTEGER | INT | **no** | — |
| `tenant_name` | VARCHAR(200) | VARCHAR(200) | **no** | — |
| `contact_no` | VARCHAR(80) | VARCHAR(80) | yes | — |
| `email` | VARCHAR(160) | VARCHAR(160) | yes | — |
| `move_in` | DATE | DATE | yes | — |
| `move_out` | DATE | DATE | yes | — |
| `notes` | VARCHAR(500) | VARCHAR(500) | yes | — |
| `status` | VARCHAR(30) | VARCHAR(30) | yes | 'Current' |
| `representative` | BOOLEAN | TINYINT(1) | yes | False |
| `receive_soa_email` | BOOLEAN | TINYINT(1) | yes | False |
| `include_in_soa` | BOOLEAN | TINYINT(1) | yes | True |
| `created_at` | DATETIME | DATETIME(6) | yes | now/today |

#### `water_reading`
PK `id` · FK `unit_id` → `unit.id` · UNIQUE — · INDEX `ix_water_month`, `ix_water_unit_month`

| Column | SQLite (today) | MySQL | NULL | App default |
|---|---|---|---|---|
| `id` | INTEGER | INT AUTO_INCREMENT | PK | — |
| `unit_id` | INTEGER | INT | **no** | — |
| `reading_month` | VARCHAR(7) | VARCHAR(7) | **no** | — |
| `previous_reading` | FLOAT | DOUBLE | yes | 0 |
| `current_reading` | FLOAT | DOUBLE | yes | 0 |
| `rate` | FLOAT | DOUBLE | yes | 50 |
| `reading_date` | DATE | DATE | yes | now/today |
| `paid` | BOOLEAN | TINYINT(1) | yes | False |
| `paid_amount` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `payment_method` | VARCHAR(20) | VARCHAR(20) | yes | 'CASH' |
| `payment_type` | VARCHAR(20) | VARCHAR(20) | yes | 'FULL' |
| `payment_reference` | VARCHAR(100) | VARCHAR(100) | yes | — |
| `paid_date` | DATE | DATE | yes | — |

#### `advance_application`
PK `id` · FK `advance_payment_id` → `advance_payment.id`, `billing_id` → `billing.id` · UNIQUE — · INDEX `ix_advance_application_advance`, `ix_advance_application_billing`

| Column | SQLite (today) | MySQL | NULL | App default |
|---|---|---|---|---|
| `id` | INTEGER | INT AUTO_INCREMENT | PK | — |
| `advance_payment_id` | INTEGER | INT | **no** | — |
| `billing_id` | INTEGER | INT | **no** | — |
| `billing_month` | VARCHAR(7) | VARCHAR(7) | **no** | — |
| `amount` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `applied_date` | DATE | DATE | yes | now/today |

#### `employee_payroll_statutory`
PK `id` · FK `payroll_id` → `employee_payroll.id` · UNIQUE `payroll_id` · INDEX `ix_employee_payroll_statutory_payroll_id`

| Column | SQLite (today) | MySQL | NULL | App default |
|---|---|---|---|---|
| `id` | INTEGER | INT AUTO_INCREMENT | PK | — |
| `payroll_id` | INTEGER | INT | **no** | — |
| `sss_employee` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `sss_employer` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `sss_ec_employer` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `philhealth_employee` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `philhealth_employer` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `pagibig_employee` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `pagibig_employer` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `withholding_tax` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `thirteenth_month` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `gross_pay` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `total_employee_deductions` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `total_employer_cost` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `net_pay_after_statutory` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |

#### `employee_payslip_item`
PK `id` · FK `payroll_id` → `employee_payroll.id` · UNIQUE — · INDEX `ix_employee_payslip_item_payroll_id`

| Column | SQLite (today) | MySQL | NULL | App default |
|---|---|---|---|---|
| `id` | INTEGER | INT AUTO_INCREMENT | PK | — |
| `payroll_id` | INTEGER | INT | **no** | — |
| `item_type` | VARCHAR(30) | VARCHAR(30) | **no** | — |
| `description` | VARCHAR(150) | VARCHAR(150) | **no** | — |
| `amount` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |

#### `maintenance_ticket`
PK `id` · FK `resident_profile_id` → `resident_profile.id`, `unit_id` → `unit.id`, `vendor_id` → `vendor.id` · UNIQUE `ticket_no` · INDEX —

| Column | SQLite (today) | MySQL | NULL | App default |
|---|---|---|---|---|
| `id` | INTEGER | INT AUTO_INCREMENT | PK | — |
| `ticket_no` | VARCHAR(40) | VARCHAR(40) | **no** | — |
| `unit_id` | INTEGER | INT | yes | — |
| `resident_profile_id` | INTEGER | INT | yes | — |
| `vendor_id` | INTEGER | INT | yes | — |
| `category` | VARCHAR(100) | VARCHAR(100) | yes | 'General' |
| `title` | VARCHAR(200) | VARCHAR(200) | **no** | — |
| `description` | TEXT | TEXT | **no** | — |
| `priority` | VARCHAR(20) | VARCHAR(20) | yes | 'Normal' |
| `status` | VARCHAR(30) | VARCHAR(30) | yes | 'Open' |
| `assigned_to` | VARCHAR(120) | VARCHAR(120) | yes | — |
| `resolution` | TEXT | TEXT | yes | — |
| `requested_at` | DATETIME | DATETIME(6) | yes | now/today |
| `updated_at` | DATETIME | DATETIME(6) | yes | now/today |

#### `parking_billing`
PK `id` · FK `parking_lot_id` → `parking_lot.id` · UNIQUE — · INDEX `ix_parking_billing_lot_month`

| Column | SQLite (today) | MySQL | NULL | App default |
|---|---|---|---|---|
| `id` | INTEGER | INT AUTO_INCREMENT | PK | — |
| `parking_lot_id` | INTEGER | INT | **no** | — |
| `billing_month` | VARCHAR(7) | VARCHAR(7) | **no** | — |
| `amount` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `amount_paid` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `due_date` | DATE | DATE | yes | — |
| `status` | VARCHAR(30) | VARCHAR(30) | yes | 'Unpaid' |
| `created_at` | DATETIME | DATETIME(6) | yes | now/today |

#### `payment`
PK `id` · FK `billing_id` → `billing.id` · UNIQUE — · INDEX `ix_payment_billing`

| Column | SQLite (today) | MySQL | NULL | App default |
|---|---|---|---|---|
| `id` | INTEGER | INT AUTO_INCREMENT | PK | — |
| `billing_id` | INTEGER | INT | **no** | — |
| `amount` | NUMERIC(12, 2) | DECIMAL(12, 2) | yes | 0 |
| `payment_date` | DATE | DATE | yes | now/today |
| `payment_method` | VARCHAR(20) | VARCHAR(20) | yes | 'CASH' |
| `payment_type` | VARCHAR(20) | VARCHAR(20) | yes | 'FULL' |
| `reference` | VARCHAR(100) | VARCHAR(100) | yes | — |
| `remarks` | VARCHAR(300) | VARCHAR(300) | yes | — |

