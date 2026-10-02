# 02 — Database setup

CityLand 9 runs its **own MariaDB instance**: its own data folder (`database\mysql-data`),
port **3307**, listening on **127.0.0.1 only**. It uses XAMPP's MariaDB programs but never
XAMPP's data or port 3306. The server starts it automatically (`backend\run.py`), so you
normally never start it by hand.

**Before you start:** `.env` exists ([01](01-PREREQUISITES_AND_ENV.md)) and the Python environment
is installed ([03 §1–2](03-BACKEND_SETUP.md)). Run every command from the **project root**.

Three database accounts, each with only what it needs:

| Account | Used by | Rights |
|---|---|---|
| `cityland9_app` (`MYSQL_USER`) | the running application | read and write **data** only (`SELECT, INSERT, UPDATE, DELETE`) on `cityland9` |
| `cityland9_app_migrate` (`MYSQL_MIGRATE_USER`) | `db_migrate.py` (schema changes) | data + `CREATE, ALTER, INDEX, REFERENCES, DROP` on `cityland9` |
| `root` (`MYSQL_ROOT_PASSWORD`) | setup, backup, restore tools | everything; never used by the web application |

The application **never changes tables at startup**. If the tables are missing or older than the
code, it refuses to start and says which command to run.

## 1. Fresh installation (empty server)

### Step 1 — Create the database server, database and accounts

```powershell
.venv\Scripts\python.exe database\local_mysql.py init
```

This:

- creates the data folder `database\mysql-data` and its `my.ini` (port 3307, 127.0.0.1 only,
  `utf8mb4`, strict SQL mode)
- sets a random **root** password and starts the server
- creates the database **`cityland9`** (`utf8mb4_bin`), the app account and the migration account
  (rights on that database only, as in the table above)
- **adds** all `MYSQL_*` values with random passwords to `.env` (never overwrites existing ones)

Expected ending: `` `cityland9_app`: SELECT, INSERT, UPDATE, DELETE only.  `cityland9_app_migrate`: schema changes (migrations). ``

### Step 2 — Create the tables

```powershell
.venv\Scripts\python.exe database\db_migrate.py install
```

`install` loads `database\schema.sql` (every table, key, index and the role `ENUM`) with the
migration account and records the schema version. It refuses a database that already has tables.
Expected ending: `` Tables created in `cityland9`; schema version 0009_payment_integrity. Matches the models: yes ``

The `user.role` column is an `ENUM` of the six roles. The stored values are lowercase; the labels
are what people see:

| Stored value | Shown as | Workspace |
|---|---|---|
| `super_admin` | Superadmin | `/app/superadmin` |
| `admin` | Admin | `/app/admin` |
| `manager` | HR & Payroll | `/app/hr` |
| `staff` | Staff | `/app/staff` |
| `accounting` | Accounting | `/app/accounting` |
| `resident` | Resident | `/app/resident` |

### Step 3 — Create the first Superadmin

There is **no default account** (the old `superadmin` / `admin123` is gone). On the server PC:

```powershell
.venv\Scripts\python.exe database\create_admin.py
```

It asks for a username and a password (typed twice, not shown). The password must meet the policy
([06 §3](06-SECURITY.md#3-passwords-and-accounts)). Until an active Superadmin exists, the server
prints a reminder at every start.

Then create the real accounts in the app:

- **Staff accounts:** Superadmin → *Users & Access Management* (choose one of the five staff roles).
  The password you give is temporary: the person must choose their own at first sign-in.
- **Resident accounts:** Superadmin → *Resident Accounts*, linked to the owner/tenant record, so
  access ends automatically when that person moves out.

Lost every Superadmin password? On the server PC: `database\create_admin.py --reset <username>`.

### Step 4 — Start, then schedule backups

Run `START_WINDOWS.bat` (or install the scheduled tasks, [07 §1](07-OPERATIONS.md#1-run-as-a-windows-service)),
and do a first backup + restore test ([07 §2–3](07-OPERATIONS.md#2-backups)).

### Step 5 (optional, never on the client's live data) — Demo data

For a test or training PC only:

```powershell
.venv\Scripts\python.exe seed_test_data.py
```

It adds units `TEST-501` to `TEST-504` with owners, tenants, bills and water readings. It never
deletes anything, but don't run it on the client's real database.

### Verified sequence

Steps 1–3 are rehearsed automatically on a throwaway database by
`database\tools\mariadb_integration.py fresh_install`: the startup check refuses the empty database,
`install` creates the tables and they match the models, a second `install` is refused, the
application starts without creating any account, and `create_admin.py` creates the first
Superadmin (and rejects `admin123`).

## 2. Existing installation (normal days)

Nothing to do. `START_WINDOWS.bat` (or the *CityLand9 Server* task) starts the database and checks
that the schema matches the code.

Manual control (project root):

```powershell
.venv\Scripts\python.exe database\local_mysql.py status   # Running / Not running
.venv\Scripts\python.exe database\local_mysql.py start
.venv\Scripts\python.exe database\local_mysql.py stop
.venv\Scripts\python.exe database\db_migrate.py status    # schema version: current and latest
```

## 3. Schema changes (updates that change tables)

Tables are **only** changed through versioned migrations in `database\migrations\versions\`.
After installing an update, the server refuses to start until you run (server stopped):

```powershell
.venv\Scripts\python.exe database\db_migrate.py status    # shows pending revisions
.venv\Scripts\python.exe database\db_migrate.py upgrade
```

`upgrade` always: (1) makes a full backup to `database\backups\`, (2) records every bill's
figures, (3) applies the migration with the migration account, (4) checks the schema matches the
code, (5) checks no bill amount changed. Any difference is reported loudly. Undo one step with
`db_migrate.py downgrade <previous-revision>` (some downgrades refuse when they would lose
history, e.g. voided receipts).

The full upgrade procedure for this release (0007 → 0009) is in
[07 §6](07-OPERATIONS.md#6-upgrading-an-existing-installation).

Never edit `database\schema.sql` by hand: it's generated from the models
(`python database\tools\generate_schema.py`), and a test checks it's current.

## 4. Connecting with a database tool (optional)

| Setting | Value |
|---|---|
| Host / Port | `127.0.0.1` / `3307` (only from the server PC itself) |
| Database | `cityland9` |
| User / Password | `cityland9_app` / `MYSQL_PASSWORD` from `.env` |

Use read-only queries. Change data through the application so the audit log stays complete.

## 5. Moving back to SQLite (emergency rollback only)

Set `DB_ENGINE=sqlite` in `.env` and restart. The app then uses the old file
`cityland_condo_web.db` in the project root. Data entered in MariaDB is **not** copied back.

## 6. Backups

See [07-OPERATIONS.md](07-OPERATIONS.md): daily automatic backups, retention, a second copy off
the PC, failure alerts, and the tested restore procedure.

## 7. Checklist

- [ ] `local_mysql.py status` → `Running on 127.0.0.1:3307`
- [ ] `db_migrate.py status` → current = latest
- [ ] First Superadmin created with `create_admin.py`; nobody shares that account
- [ ] Named staff accounts created
- [ ] Backup task registered, a first backup exists, and a restore test passed ([07](07-OPERATIONS.md))
