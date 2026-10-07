# CityLand 9 — moving a copy to a test PC

How to set up an **independent test installation** of CityLand 9 on another Windows laptop or PC, from
a backup made on the office server ("source PC").

The test PC gets **its own** MariaDB database. Nothing it does (new bills, deleted accounts, wiped data)
can reach the source PC: there is no network connection between the two databases and no
synchronisation. The only thing that travels is a file you copy by hand (USB drive).

All commands run in **PowerShell, in the project folder** (`C:\...\CITYLAND9_SYSTEM`).

---

## 1. What the system uses (checked 2026-10-07)

| Item | Finding |
|---|---|
| Database | **MariaDB 10.4.32** (XAMPP's programs in `C:\xampp\mysql\bin`), run as CityLand's **own instance**: data folder `database\mysql-data`, port **3307**, listening on **127.0.0.1 only**. XAMPP's own MySQL (port 3306) is not used. |
| Started by | `START_WINDOWS.bat` → `backend\run.py`, which starts that MariaDB, then the web server (Waitress, port 5000). |
| Configuration | `.env` in the project folder (secrets; never copied). Template: `.env.example`. |
| Database accounts | `cityland9_app` (the app: data only), `cityland9_app_migrate` (schema changes), `root` (setup, backup, restore). Passwords live only in `.env`. |
| Schema | Alembic migrations in `database\migrations\versions` (current: `0011_resident_provisioning`). The app **never changes tables at startup**; it refuses to start on an older schema. |
| Existing tools reused | `database\backup.py` / `local_mysql.py` (mysqldump with a private options file), `database\restore.py` (restore into a separate database + verification), `database\db_migrate.py` (upgrade with backup and bill check), `database\create_admin.py` (Superadmin from the console), `seed_test_data.py` (synthetic units, people, bills). |
| In-app export | *Superadmin → Database Export* (Excel) is a partial data export, not a restorable backup. Not used here. |
| Payment integrations | None (the SOA QR code is a static link/text). The online payment link setting is removed from test packages. |
| Outbound email | SOA email over SMTP (`Rates & Rules`). Disabled on a test installation (§7). |

---

## 2. Which data goes to the test PC

**Recommended: synthetic data only.** `export-synthetic` builds the sample data set (units
TEST-501…504 with parking/storage, owners, a tenant, bills in different states — paid, overdue,
unpaid, with water and penalty — and a payment) in a **throwaway** database. The real database is
not even opened.

Check what the source database holds first (counts only, nothing personal is printed):

```powershell
.venv\Scripts\python.exe database\test_pc.py profile
```

It ends with a VERDICT line. If it says the database **may hold real records**, do not transfer them
unless the owner decides so (next paragraph).

### If your database holds only dummy data: copy it as it is

When **every** owner, tenant, employee and other record in the database is dummy/test data (as on
the development PC), `export-copy` takes your records **as they are**, names included, through the
same staging database. It asks you to type `DUMMY DATA`. Accounts, passwords, activation codes,
audit history, form tokens, SMTP settings and the payment link are still removed (below), and it
reports that the live database's table checksums are unchanged.

Don't use it once real residents have been entered: use `export-sanitized` then.

### If real data is needed (owner's approval required)

`export-sanitized` makes an **anonymized copy**. It reads the live database with a read-only snapshot,
does all the work in a separate **staging** database, and drops that afterwards. The live database is
not modified; the tool compares its table checksums before and after and reports the result.

| Kept as is | Replaced | Left out unless `--include` names it |
|---|---|---|
| units, rates, settings, bills, payments, receipts, advances, water readings, amounts, dates, statuses | owner/tenant names (`Owner 0001`), emails (`owner1@example.invalid`), phones, notes; payment references, remarks; expense descriptions; staff usernames in "updated by" fields | `hr` (employees, payroll, leave, loans), `vendors`, `community` (announcements, tickets, gate passes, move certificates) — anonymized when included |

Before writing the package it checks that **no original name, email or phone** is left in any text
column, and refuses otherwise.

**Questions for the owner before running it:**

1. May anonymized billing history (real amounts and dates, fake names) leave the office?
2. Should HR/payroll, vendor or community records be included (anonymized), or left out (default)?
3. Who keeps the test PC, and is it deleted after testing?

### Always removed from every package

User accounts and password hashes (including activation codes and temporary passwords); resident
portal profiles and unit links; the audit history; one-time form tokens; SMTP settings and the stored
SMTP password; the online payment link; uploaded-document records (the files themselves are never
copied). Sessions are not stored in the database: they depend on `SECRET_KEY`, which every PC
generates for itself, so no session can be reused.

---

## 3. Source PC: export (exact commands)

```powershell
# 1. What does the database hold? (read-only, counts only)
.venv\Scripts\python.exe database\test_pc.py profile

# 2a. RECOMMENDED: synthetic test package (the real database is not opened)
.venv\Scripts\python.exe database\test_pc.py export-synthetic

# 2b. Database holds ONLY dummy data: your records as they are (asks you to type DUMMY DATA)
.venv\Scripts\python.exe database\test_pc.py export-copy

# 2c. ONLY with the owner's approval: anonymized copy (asks you to type ANONYMIZE)
#     add  --include hr,vendors,community  only for the categories the owner approved
.venv\Scripts\python.exe database\test_pc.py export-sanitized

# 3. The application files (no .env, databases, backups, logs, .venv, node_modules, git history)
.venv\Scripts\python.exe database\test_pc.py package-app
```

Everything is written to `database\test_packages\` (excluded from git):

```
cityland9_app_<timestamp>.zip                       the application
cityland9_testpkg_synthetic_<timestamp>.sql.gz      the test database
cityland9_testpkg_synthetic_<timestamp>.manifest.json   checksum, schema version, row counts
```

Copy **all three** to a USB drive. The `.sql.gz` and `.manifest.json` must stay together, with the
same name.

> `package-app` refuses to run if a file looks like it contains a password or secret key, and lists
> it. It includes uncommitted changes (it packs the files as they are now); commit first if you want
> the package to match a known version.

---

## 4. Transfer checklist

**Copy:**

- [ ] `cityland9_app_<timestamp>.zip` — application source, built React app (`frontend\dist`), migrations, `schema.sql`, `.env.example`
- [ ] `cityland9_testpkg_<kind>_<timestamp>.sql.gz` **and** its `.manifest.json`
- [ ] This guide

**Never copy:**

- [ ] `.env` (passwords, `SECRET_KEY`), `database\mysql-data` (the live data files — never copy a running database's data folder), `database\backups\` (production backups contain accounts and real data), `cityland_condo_web.db*`, `logs\`, `.venv\`, `node_modules\`, `.git\`

**Target PC needs (install once, internet needed only for this):**

- [ ] **Python 3.11+** from python.org, with "Add python.exe to PATH" ticked (tested with 3.14.7)
- [ ] **XAMPP** (only for its MariaDB programs; tested with MariaDB 10.4.32). You don't need to start XAMPP.
- [ ] Node.js is **not** needed (the built app is in the package).

---

## 5. Target PC: one double-click setup (recommended)

1. Install **Python** (tick "Add python.exe to PATH") and **XAMPP** into `C:\xampp` (section 4). You don't need to start XAMPP.
2. Get the application: on GitHub, **Code → Download ZIP** (or the `cityland9_app_*.zip` from `package-app`).
   Before extracting a downloaded ZIP: right-click it → **Properties** → tick **Unblock** → OK.
   Extract it to a short path, e.g. `C:\CITYLAND9\`.
3. Put the two data files you received (`cityland9_testpkg_<kind>_<date>.sql.gz` and its `.manifest.json`)
   **in the extracted project folder** (next to `SETUP_TEST_PC.bat`), or in the folder above it.
4. Double-click **`SETUP_TEST_PC.bat`**. Keep the internet on for the first run. It:
   - creates the Python environment and installs the packages;
   - creates `.env` for a test installation (new secret key, database `cityland9_test`, email disabled);
   - creates this PC's own MariaDB database (port 3307, this PC only) with its own random passwords;
   - finds the data file and restores it (checksum checked, no accounts in it);
   - asks you to create **this PC's Superadmin** (username + password);
   - creates the tester accounts and shows their temporary passwords **once**: write them down;
   - checks the installation is independent and local, then offers to start CityLand 9.
5. From then on: **`START_WINDOWS.bat`** to start, **`STOP_WINDOWS.bat`** to stop. Open `http://127.0.0.1:5000`.

If something stops it (no internet, data file missing, XAMPP not found), fix that and double-click it
again: steps already done are skipped.

Don't double-click `START_WINDOWS.bat` **before** the setup. On a copy without `.env` it sets up a demo
(SQLite file with demo accounts). If that happened anyway, just run `SETUP_TEST_PC.bat`: it sets the demo
configuration aside as `.env.demo-backup` and continues.

## 5b. Target PC: the same steps by hand

Unzip `cityland9_app_<timestamp>.zip` to a **short path**, for example `C:\CityLand9Test\`, so the
project folder is `C:\CityLand9Test\CITYLAND9_SYSTEM`. Copy the two package files to, for example,
`C:\CityLand9Test\`. Then, in PowerShell:

```powershell
cd C:\CityLand9Test\CITYLAND9_SYSTEM

# 1. Configuration FIRST. Without a .env, START_WINDOWS.bat would run the demo setup instead
#    (SQLite file with demo accounts), which is not this test installation.
Copy-Item .env.example .env
notepad .env        # make the changes in section 6, save, close

# 2. Python environment (internet needed this one time)
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

# 3. This PC's own MariaDB: data folder, random root/app/migration passwords (written to .env only),
#    database cityland9_test
.venv\Scripts\python.exe database\local_mysql.py init

# 4. Restore the test package into cityland9_test (checksum checked first)
.venv\Scripts\python.exe database\test_pc.py restore C:\CityLand9Test\cityland9_testpkg_synthetic_<timestamp>.sql.gz

# 5. The developer's own Superadmin (typed on this PC; not copied from anywhere)
.venv\Scripts\python.exe database\create_admin.py

# 6. Tester accounts (section 8)
.venv\Scripts\python.exe database\test_pc.py create-testers --residents 2

# 7. Check this is an independent local test installation
.venv\Scripts\python.exe database\test_pc.py check-target --package C:\CityLand9Test\cityland9_testpkg_synthetic_<timestamp>.sql.gz

# 8. Start (first start while still online: it records the installed packages)
.\START_WINDOWS.bat
```

After step 8 the test PC works **offline**. Open `http://127.0.0.1:5000`.

What `restore` does:

1. Refuses unless `.env` has `CL9_TEST_INSTALL=1`, a local `MYSQL_HOST`, no `DATABASE_URL`, and a test database name (`cityland9_test…`).
2. Refuses files without a matching manifest (so a **production backup can't be restored** here), and files whose checksum doesn't match (damaged copy).
3. Restores only into a database that is **new or empty**. An existing database with tables is refused unless you pass `--overwrite cityland9_test` **and** type the name; a safety backup is taken first (`database\backups\cityland9_before_test_restore_*.sql`).
4. Re-applies the account rights: `cityland9_app` = data only, `cityland9_app_migrate` = schema changes.
5. If the package is from an **older** schema, it upgrades it with `db_migrate.py upgrade` (backup first, bill figures compared). A package from a **newer** version is refused: copy the newer application first.
6. Verifies every table exists, the schema version, every bill's computed figures, row counts against the manifest, and that **no account, token, SMTP setting or audit entry** came along.

---

## 6. Safe configuration (`.env` on the test PC)

Change only these lines of the copied `.env.example`; leave the `MYSQL_*` passwords to `local_mysql.py init`.

```ini
APP_ENV=production
# Generate:  .venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=<paste-a-new-64-character-random-value>

# 127.0.0.1 = only this PC can open CityLand. Use 0.0.0.0 only if testers on other PCs must reach it.
FLASK_HOST=127.0.0.1
FLASK_PORT=5000

DB_ENGINE=mysql
MYSQL_BIN_DIR=C:\xampp\mysql\bin
MYSQL_DATABASE=cityland9_test

# Marks this as a TEST installation: test_pc.py target commands are allowed, the server refuses a
# remote database, and no email is ever sent.
CL9_TEST_INSTALL=1
OUTBOUND_EMAIL=disabled

# Never set on a test PC:
# DATABASE_URL=...        (would bypass the local database)
# MYSQL_HOST=<other PC>   (refused when CL9_TEST_INSTALL=1)
```

`local_mysql.py init` adds `MYSQL_HOST=127.0.0.1`, `MYSQL_PORT=3307`, the account names and their
random passwords to `.env`. They are this PC's own and are not related to the source PC's passwords.
Keep `.env` private on the test PC too: whoever has it can sign in to that PC's database as root.

---

## 7. Email and other outbound connections on the test PC

- With `CL9_TEST_INSTALL=1` (or `OUTBOUND_EMAIL=disabled`), the server refuses before any SMTP connection is opened: *"Email sending is disabled on this test installation."* The Billing email screen shows SMTP as not configured. A tester who enters SMTP settings in Rates & Rules still cannot send.
- Test packages contain no SMTP host, sender, username or password, and no online payment link.
- There are no payment-gateway integrations to disable.
- At startup the server prints `TEST INSTALLATION: database cityland9_test on this PC; outbound email disabled.`

---

## 8. Tester and developer access

### Who can manage accounts (unchanged application permissions)

| Action | Superadmin | Admin | Accounting | Staff | HR & Payroll | Resident |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| Create staff accounts (Users & Access) | ✔ | – | – | – | – | – |
| Change role / deactivate / reactivate staff accounts | ✔ | – | – | – | – | – |
| Reset another account's password | ✔ (not another Superadmin's) | – | – | – | – | – |
| **Permanently delete** a staff account | ✔ | – | – | – | – | – |
| Create resident portal accounts / new activation code | ✔ | – | – | – | – | – |
| Deactivate a resident account / end its access to a unit | ✔ | – | – | – | – | – |
| Change own password | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |

- **Deletion:** staff accounts can be deleted permanently (Superadmin only; never the account you're signed in with, never the last active Superadmin). Resident portal accounts **cannot be deleted**, only deactivated (their history is kept).
- So on the test PC, **only a Superadmin** can create, deactivate, reset or delete accounts. The tester accounts created in step 6 (Admin, Accounting, Staff, HR & Payroll, Residents) cannot.

### Developer-only Superadmin

- The developer creates their Superadmin **on the test PC** with `database\create_admin.py` (asks for a username and a password, typed twice, not shown). No account or password hash is copied from the source PC; packages contain no accounts at all.
- `test_pc.py create-testers` refuses to create a Superadmin.
- "Developer-only" is a **procedure**, not a technical lock: the application lets any Superadmin create another Superadmin in Users & Access. Don't give testers a Superadmin account or the Superadmin password if they shouldn't have it.

### Tester accounts

```powershell
.venv\Scripts\python.exe database\test_pc.py create-testers                         # tester.admin, tester.accounting, tester.staff, tester.manager
.venv\Scripts\python.exe database\test_pc.py create-testers --roles admin,staff     # only some roles
.venv\Scripts\python.exe database\test_pc.py create-testers --residents 2           # + portal accounts for 2 current owners/tenants
```

- Each staff tester gets a random **temporary password**, shown **once** in the console (not saved or logged). At first sign-in they must choose their own; until then everything else is blocked.
- Each resident tester gets a username and a one-time **activation code** (valid 7 days), the same mechanism as Resident Accounts → Generate account.
- Lost? The developer (Superadmin) issues a new one: Users & Access → Reset password, or Resident Accounts → New activation code.

---

## 9. Application permissions vs direct database access

Application roles control what people can do **through CityLand 9** (the web pages and the API).
They do **not** protect the database from someone who controls the test PC:

- Anyone with Windows administrator access to the test PC, or with the passwords in its `.env` (MariaDB `root`, the app or migration account), can open the database directly (`mysql.exe`, HeidiSQL, a Python script) and read, change or delete anything, create a Superadmin row, or replace the whole database.
- The developer-only Superadmin rule, the forced password change and the audit log all apply only to actions **inside the application**. Direct database changes are not audited by CityLand 9.
- What protects the **source** data: the test PC never has a connection to the source database (it binds to 127.0.0.1 and has its own passwords), and the package contains only synthetic or anonymized data without accounts. Treat the test PC and its `.env` as **fully controlled by whoever has it**.

---

## 10. Starting, stopping, ports

| Task | Command |
|---|---|
| Start | `.\START_WINDOWS.bat` (keep the window open; closing it stops the server) |
| Stop server and database | `.\STOP_WINDOWS.bat` |
| Stop only the server | `.\STOP_WINDOWS.bat --keep-db` |
| Database status / start / stop | `.venv\Scripts\python.exe database\local_mysql.py status` (or `start`, `stop`) |
| Schema version | `.venv\Scripts\python.exe database\db_migrate.py status` |
| Is it the local test database? | `.venv\Scripts\python.exe database\test_pc.py check-target` |

**"Port 5000 is already in use"** — another CityLand window is open, or another program uses the port:

```powershell
Get-NetTCPConnection -LocalPort 5000 -State Listen | Select-Object OwningProcess
Get-Process -Id <OwningProcess>
```

Close that program, or set `FLASK_PORT=5001` in `.env` and open `http://127.0.0.1:5001`.

**Database port 3307 in use** (e.g. another MariaDB): set `MYSQL_PORT=3308` in `.env` **before**
running `local_mysql.py init`. After init, the port is also written in `database\mysql-data\my.ini`;
change both, then restart.

**Other PCs can't connect** (only if `FLASK_HOST=0.0.0.0`): allow `python.exe` in Windows Defender
Firewall for **Private** networks, and use `http://<test-PC-IP>:5000`.

**Prove the test PC uses its own database:** `check-target` must show PASS for *database server is this
computer*, *database files are this installation's* and *MariaDB listens on this PC only*; with
`--package`, it also confirms this is not the PC that exported the package.

---

## 11. If the restore fails

| Message | What to do |
|---|---|
| `Refused: CL9_TEST_INSTALL=1 is not set` / `not a test name` / `MYSQL_HOST is not 127.0.0.1` | Fix `.env` as in section 6. Nothing was changed. |
| `No manifest next to the package` | Copy the `.manifest.json` with the same name next to the `.sql.gz`. A production backup can't be restored here, by design. |
| `Checksum mismatch` | The copy is damaged. Copy both files again from the source PC. |
| `cityland9_test already has N table(s)` | Use another database: set `MYSQL_DATABASE=cityland9_test2` in `.env`, run `local_mysql.py init` again, then `restore`. Or replace it: `restore <file> --overwrite cityland9_test` and type the name (a safety backup is taken first). |
| `The package's schema (…) is newer than this code` | Copy the newer application (`package-app`) from the source PC first. |
| `Schema upgrade failed` | The data is restored but not upgraded. Fix the error shown and run `.venv\Scripts\python.exe database\db_migrate.py upgrade`. |
| `restore failed: …` / `RESTORE NOT VERIFIED` | The test database may be half-filled. Run the restore again with `--overwrite cityland9_test`. |
| `The CityLand database is not set up yet` | Run `.venv\Scripts\python.exe database\local_mysql.py init` first. |
| `…mysqld.exe not found` | Install XAMPP, or set `MYSQL_BIN_DIR` in `.env` to the folder containing `mysqld.exe`. |
| MariaDB won't start | See the `.err` log in `database\mysql-data`; usually the port is taken (section 10). |

To start completely over on the test PC: `.\STOP_WINDOWS.bat`, delete the project folder (including
its `database\mysql-data`), and repeat section 5. This never affects the source PC.

To put back the test database from before an `--overwrite`:
`.venv\Scripts\python.exe database\restore.py database\backups\cityland9_before_test_restore_<timestamp>.sql --into cityland9_test_previous`
then set `MYSQL_DATABASE=cityland9_test_previous` in `.env` and give the app account its normal rights
on it: `.venv\Scripts\python.exe database\local_mysql.py secure` (restore.py grants read-only access
for its verification).

---

## 12. Verification performed (2026-10-07) and what was not tested

### Rehearsal on disposable databases

Done on the development PC with **two separate disposable MariaDB 10.4.32 instances** (own data folders
in a temporary directory, ports 3317 and 3327), each in its own copy of the application extracted from
`package-app`. The real database (port 3307) was **never contacted**. Only synthetic, invented records
were used, and everything was deleted afterwards.

| Check | Result |
|---|---|
| `package-app`: 315 files, no `.env`, databases, backups, `.git`, `.venv`, `node_modules`; built app included | PASS |
| `profile` on a source with an invented non-TEST unit, HR and vendor record → "may be REAL" (exit code 2) | PASS |
| `export-synthetic`: throwaway database built, scrubbed, dumped, dropped | PASS |
| `export-sanitized` (all categories): no original name/email/phone left (32 values checked); source table checksums identical before and after | PASS |
| `export-copy`: wrong confirmation refused; records kept as they are (names, emails, HR); accounts, resident profiles, SMTP, payment link and audit removed; source checksums unchanged; restored and verified on the target | PASS |
| Packages scanned: no source names, accounts, password hashes, SMTP data, payment link, audit or token rows | PASS |
| Restore into a new `cityland9_test`: 33 tables, schema 0011, 4 bills computed, row counts = manifest, no accounts | PASS |
| Refusals: database with tables, wrong confirmation, no manifest (production backup), damaged file, live-style name `cityland9`, not marked as test, remote `MYSQL_HOST`, server start with remote host, Superadmin as tester role | PASS (9/9) |
| `--overwrite` with typed name: safety backup written, restored, verified | PASS |
| Anonymized package restored into a second test database: placeholder names, `example.invalid` emails, 0 accounts | PASS |
| Package from an older schema (0010): upgraded to 0011 during restore (backup, bills unchanged) | PASS |
| `create_admin.py` (developer Superadmin) and `create-testers --residents 2` | PASS |
| `check-target --package`: all PASS; "same PC as the source" shown as WARN (expected in a one-PC rehearsal) | PASS |
| Running test server (port 5022), API: Superadmin sees only fresh accounts; tester forced to change the temporary password; Admin/Staff refused Users & Access and Resident Accounts; Admin can't create a Superadmin; resident activates with the code, sees only TEST-501, SOA detail and **PDF download** work, another unit's SOA and summary refused; email screen shows SMTP not configured; sending refused | 21/21 PASS |

Defects found and fixed during the rehearsal:

- Migration `0011` **downgrade** failed on MariaDB (an index needed by a foreign key was dropped first). Fixed; the downgrade and the re-upgrade were then run successfully. (The live database only upgrades, so it was not affected.)
- Restoring into a database other than the one `local_mysql.py init` set up failed at the grant step. Fixed in `test_pc.py`.

Automated tests: `tests/test_test_pc.py` (11 tests, no database server needed) covers the email block,
the configuration refusals, the manifest/checksum refusals, the server's remote-database refusal and
the application package contents.

### Not tested

- A **second physical Windows PC** (clean Windows, fresh Python and XAMPP installs, `pip install` from the internet, then offline use). The rehearsal used two app copies and two MariaDB instances on the same PC and the existing Python environment.
- `STOP_WINDOWS.bat` on the test copy. It stops every CityLand server process on a PC, so on the development PC it would also have stopped the real server; it was not run there. Tested instead: `SETUP_TEST_PC.bat` on a fresh copy (.venv creation, package install from the internet, .env, database, restore, Superadmin, testers, check; then a second run that skipped every finished step), and `START_WINDOWS.bat` on the test copy (schema check, "TEST INSTALLATION" line, app answering).
- Downloading the ZIP from GitHub itself. The rehearsal used `package-app`, which packs the same files.
- Browser screens on the test copy. The checks used the same API the screens use.
- `export-sanitized` on **real** data (it was rehearsed on invented data only, by design). Its leak check covers names, emails and phones stored in the person, vendor, employee, visitor, account and resident-profile records. Free text typed elsewhere (for example a name inside an SOA note or a remark) is cleared or replaced, but review the categories with the owner.
- Restoring into a **different MariaDB/MySQL version** than 10.4.32 (dumps from 10.4 restore into the same or newer MariaDB; MySQL 8 was not tried).
- Packages larger than the synthetic/test data (a few KB here).

---

## 13. Files

| File | Change |
|---|---|
| `database/test_pc.py` | **New.** profile, export-synthetic, export-copy, export-sanitized, package-app, make-env, setup, check-target, restore, create-testers |
| `SETUP_TEST_PC.bat` | **New.** One double-click setup of a test PC (section 5) |
| `backend/legacy_app.py` | `outbound_email_disabled()`; SOA email refused before any SMTP connection on a test installation |
| `backend/app/routes/billing.py` | Email endpoints report disabled / SMTP not configured on a test installation |
| `backend/run.py` | `CL9_TEST_INSTALL=1`: refuses a remote `MYSQL_HOST` or `DATABASE_URL`; prints the test-installation line |
| `database/migrations/versions/0011_resident_provisioning.py` | Downgrade fixed for MariaDB |
| `.gitignore` | `database/test_packages/` |
| `tests/test_test_pc.py` | **New.** 11 tests |
