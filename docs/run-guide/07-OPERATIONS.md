# 07 — Operations: service, backups, restore, monitoring, upgrades

For the person looking after the server PC. All commands run from the **project root** in
PowerShell; "as Administrator" means PowerShell opened with *Run as administrator*.
Nothing here needs internet access.

## 1. Run as a Windows service

Instead of keeping the `START_WINDOWS.bat` window open, register two scheduled tasks (once, as
Administrator, after the installation in [02](02-DATABASE_SETUP.md) works with `START_WINDOWS.bat`):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\register_tasks.ps1                    # backup at 20:00
powershell -ExecutionPolicy Bypass -File scripts\windows\register_tasks.ps1 -BackupTime 21:30  # other time
```

| Task | When | Runs | Does |
|---|---|---|---|
| **CityLand9 Server** | at startup (30 s delay), as SYSTEM, no sign-in needed | `backend\service.py` | starts the database, checks the schema, runs the Waitress server and **restarts it if it stops** (5 s, then doubling to 2 min while it keeps failing). Log: `logs\service.log`, server output: `logs\server-console.log` |
| **CityLand9 Backup** | daily; runs later if the PC was off | `database\backup.py` | see §2 |

```powershell
Start-ScheduledTask -TaskName "CityLand9 Server"     # start now (or restart the PC)
Get-ScheduledTask -TaskName "CityLand9*" | Get-ScheduledTaskInfo   # last run time / result
```

- **Stop:** `STOP_WINDOWS.bat` ends the task first, so nothing restarts the server, then stops
  the server and the database. Start again with `Start-ScheduledTask` (or restart the PC).
- If the schema is older than the code (an update was installed but not migrated), the service
  does **not** start the server; `logs\service.log` says so and it checks again every 5 minutes.
- Remove the tasks: `register_tasks.ps1 -Remove`.
- Don't run `START_WINDOWS.bat` while the task is running (it will report port 5000 in use).

> Status: `service.py`'s restart behaviour is covered by automated tests and the script's syntax
> is checked, but registering the tasks needs Administrator rights and was **not** run during
> development. Do the checks in §7 after registering.

## 2. Backups

`database\backup.py` (run daily by the task, or by hand at any time):

1. refuses to start with less than `BACKUP_MIN_FREE_GB` free;
2. dumps the database with `mysqldump --single-transaction` (consistent while people keep
   working) into `database\backups\cityland9_auto_<date>_<time>.sql.gz` (compressed);
   the database password goes through a temporary private file, never the command line;
3. checks the dump is complete;
4. copies it to `BACKUP_COPY_DIR` when set (**strongly recommended**: another disk, a USB drive
   that stays connected, or a share on another office PC);
5. deletes **scheduled** backups older than `BACKUP_RETENTION_DAYS` (30), always keeping the newest
   `BACKUP_KEEP_MIN` (7). Manual, before-migration and before-restore backups are never deleted;
6. records the result in `database\backups\last_backup.json` and `logs\backup.log`.

```powershell
.venv\Scripts\python.exe database\backup.py         # exit code 0 = OK, 1 = failed (reason in logs\backup.log)
```

**Failure reporting:** a failed or missing backup shows as a warning on `/readyz` (§4) and in
the server log at every start, once the last good backup is older than `BACKUP_MAX_AGE_HOURS` (26).
Task Scheduler also shows *Last Run Result* ≠ `0x0`.

Other backups the system makes automatically: before every migration
(`cityland9_before_migration_*.sql`), before every Excel import, and before a live restore
(`cityland9_before_restore_*.sql`).

> The backups contain all resident and financial data **and password hashes**. Keep copies only
> on drives/shares that only administrators can open. Never store `.env` next to them.

## 3. Restore

### 3a. Restore test (monthly, and after changing anything about backups)

Restores into a **separate** database, never touching the live one:

```powershell
.venv\Scripts\python.exe database\restore.py database\backups\cityland9_auto_<date>.sql.gz --drop-after
```

It prints the schema version, the row count of every table and the number of bills whose figures
could be computed, then `RESTORE VERIFIED` (and drops the test database because of `--drop-after`).
Leave out `--drop-after` to look at the restored data in `cityland9_restore_test`; drop it later.

### 3b. Replacing the live database (disaster recovery)

Only when the live data is lost or damaged:

```powershell
STOP_WINDOWS.bat --keep-db                                              # nobody can enter data now
.venv\Scripts\python.exe database\restore.py <backup file> --drop-after  # 1. test the file first
.venv\Scripts\python.exe database\restore.py <backup file> --replace-live
```

`--replace-live` refuses while the web server answers, asks you to type the database name, saves
the current (damaged) data as `cityland9_before_restore_*.sql`, restores, and verifies. Then start
the system again. If the backup came from an older version, run `database\db_migrate.py upgrade`
before starting. Everything entered after the backup was taken is lost: re-enter it from the
paper receipts/forms of that period.

### What was verified

`database\tools\mariadb_integration.py backup_restore` runs all of this on throwaway databases
with synthetic data: backup → restore into a separate database → every bill's figures and every
table's row count identical, receipt numbers/amounts and non-English text preserved; refusal of a
non-empty target and of the live database; `--replace-live` refused while the server runs and
without the typed name, then restoring damaged data exactly, keeping a safety backup and the
database account rights; a failed backup exits with an error, leaves no partial file and is
recorded; retention deletes only old scheduled backups.

## 4. Monitoring

| What | Where | Meaning |
|---|---|---|
| `http://127.0.0.1:5000/healthz` | any browser | `{"status": "ok"}` = the server process answers |
| `http://127.0.0.1:5000/readyz` | any browser / monitoring tool | `ready` (HTTP 200) or `not ready` (HTTP 503): database answers, schema version matches the code, free disk ≥ `DISK_MIN_FREE_GB`. `warnings: ["backup"]` = the last backup failed or is too old |
| `logs\cityland9.log` | text file, rotates at `LOG_MAX_MB`, keeps `LOG_BACKUPS` | request errors (with the reference number users see), sign-in failures, low disk / backup warnings at startup |
| `logs\service.log` | text file | service starts, server stops and restarts |
| `logs\backup.log` | text file | every backup, retention deletions, failures |
| `database\mysql-data\*.err` | text file | the database server's own log |

Neither endpoint needs sign-in or reveals data. A free LAN monitoring tool (or a scheduled
`Invoke-WebRequest http://127.0.0.1:5000/readyz`) can alert when it isn't `ready`.

**Disk space:** the database, backups and logs are on the same drive as the project. Watch the
`disk` figure in `/readyz`; keep at least 10 GB free.

## 5. Offline operation

After the installation, the server needs **no internet**: Python packages are installed once
(`START_WINDOWS.bat` reinstalls only when `requirements.txt` changes), the React app is
pre-built into `frontend\dist`, there are no external fonts/scripts, and the database is local.
Only SOA email needs a reachable mail server (a LAN relay works).

## 6. Upgrading an existing installation

General procedure for any update (example: this release, schema 0007 → 0009):

```powershell
# 0. Office closed; stop everything that writes data
STOP_WINDOWS.bat --keep-db

# 1. Keep a copy you can go back to
.venv\Scripts\python.exe database\local_mysql.py backup

# 2. Update the code (git pull, or copy the new release over the folder; keep .env and database\)
# 3. Packages (single runtime file)
.venv\Scripts\python.exe -m pip install -r requirements.txt

# 4. Separate the database accounts (once; idempotent)
.venv\Scripts\python.exe database\local_mysql.py secure

# 5. Migrate (backs up again, checks the schema and that no bill amount changed)
.venv\Scripts\python.exe database\db_migrate.py upgrade

# 6. Optional, read-only: how the new exact (centavo) pricing compares with the old figures
.venv\Scripts\python.exe database\tools\compare_dues.py

# 7. Start with a fresh React build
START_WINDOWS.bat --rebuild
```

Specific to this release:

- Step 5 needs `SECRET_KEY` in `.env` (it encrypts a saved SMTP password).
- Any account still using `admin123` must choose a new password at the next sign-in.
- There is no automatic default account any more; if nobody can sign in as Superadmin, use
  `database\create_admin.py` (or `--reset <username>`).
- Existing bills, payments, due dates and receipt numbers are not changed by the migration;
  step 5 prints `Bill figures unchanged`. If it reports a difference, stop and restore the backup
  from step 1 (§3b).

Going back: `db_migrate.py downgrade 0007_gate_pass_requests` with the previous code (refused once
a receipt has been voided, to keep that history), or restore the backup from step 1.

## 7. After installing / upgrading — checks

- [ ] `/readyz` shows `ready`, schema current, disk OK
- [ ] `Start-ScheduledTask "CityLand9 Backup"`, then `logs\backup.log` ends with `Backup OK` and `/readyz` has no `backup` warning
- [ ] A restore test (§3a) printed `RESTORE VERIFIED`
- [ ] `BACKUP_COPY_DIR` set and the copy appears there
- [ ] Restart the PC: the system comes back without anyone signing in (`logs\service.log`)
- [ ] Stop `python.exe` running `backend\run.py` in Task Manager: it is back within seconds
