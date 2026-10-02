# 05 — Troubleshooting

Start with the message `START_WINDOWS.bat` prints: each step (`[1/5]` … `[5/5]`) stops with a
`[X]` line that says what failed. Run all commands from the **project root** unless stated.

| Symptom | Go to |
|---|---|
| `Port 5000 is already in use` | [1](#1-port-5000-is-already-in-use) |
| Database won't start / port 3306 or 3307 busy | [2](#2-database-port-conflicts-3306--3307) |
| Works on the server PC, not from other PCs | [3](#3-other-pcs-cant-open-the-system-windows-firewall) |
| "CORS" errors, signed out unexpectedly | [4](#4-cors-errors-and-unexpected-sign-outs) |
| `Can't connect to MySQL server`, `Access denied`, timeouts | [5](#5-database-connection-errors) |
| `Table ... doesn't exist`, `Unknown column` | [6](#6-missing-tables-or-columns) |
| Start-up, sign-in, "form expired", backup warnings | [6b](#6b-start-up-sign-in-and-backup-messages) |
| Blank page, "frontend has not been built", old design | [7](#7-frontend-problems) |
| Python / npm / pip errors | [8](#8-installation-errors) |

---

## 1. Port 5000 is already in use

Usually CityLand is **already running** (another window, or a server started earlier).

```powershell
netstat -ano | findstr ":5000"            # last column = process id (PID)
tasklist /FI "PID eq <PID>"               # which program is it?
```

- If it's `python.exe`: run **`STOP_WINDOWS.bat --keep-db`** (stops only CityLand's server), then
  start again.
- If it's another program: stop it, or move CityLand to another port in `.env`:
  ```ini
  FLASK_PORT=5050
  ```
  Then everyone uses `http://<server IP>:5050`, and the firewall rule (section 3) must allow 5050.

## 2. Database port conflicts (3306 / 3307)

CityLand's MariaDB uses **3307**, on purpose: **3306** belongs to XAMPP's (or another) MySQL,
which CityLand never uses. A conflict on 3306 doesn't affect CityLand.

Check who uses 3307:

```powershell
netstat -ano | findstr ":3307"
.venv\Scripts\python.exe database\local_mysql.py status
```

- `Running on 127.0.0.1:3307` → fine, it's CityLand's own database.
- Another program holds 3307 → choose a free port (e.g. 3308) and change it in **both** places:
  1. `.env` → `MYSQL_PORT=3308`
  2. `database\mysql-data\my.ini` → `port=3308` under `[mysqld]` **and** `[client]`

  Then `local_mysql.py stop` (if running) and start the system again.
- `The database server did not start` → read the MariaDB log:
  `database\mysql-data\<PC-NAME>.err` (e.g. `DESKTOP-4INA0R7.err`), last lines.
  Common causes: the disk is full, the data folder was copied while the server was running, or
  `MYSQL_BIN_DIR` points to a folder without `mysqld.exe`.

## 3. Other PCs can't open the system (Windows Firewall)

The server works at `http://127.0.0.1:5000` on the server PC itself, but other PCs time out.

**On the server PC**, in an **Administrator** PowerShell:

```powershell
# 1. The office network must be "Private" (Public blocks incoming connections)
Get-NetConnectionProfile
Set-NetConnectionProfile -InterfaceAlias "<Wi-Fi or Ethernet name>" -NetworkCategory Private

# 2. Allow CityLand's web port on private networks only
New-NetFirewallRule -DisplayName "CityLand 9 (TCP 5000)" -Direction Inbound -Protocol TCP -LocalPort 5000 -Action Allow -Profile Private
```

Command Prompt equivalent (Administrator):

```bat
netsh advfirewall firewall add rule name="CityLand 9 (TCP 5000)" dir=in action=allow protocol=TCP localport=5000 profile=private
```

When Windows shows *"Allow python.exe to communicate on these networks?"* the first time, tick
**Private networks** only.

**From another PC**, test the path:

```powershell
Test-NetConnection <server IP> -Port 5000      # TcpTestSucceeded : True
```

Still failing?

- Both PCs must be on the **same network** (guest Wi-Fi is often isolated: "client isolation").
- The server's IP may have changed → `ipconfig` on the server; reserve a fixed IP in the router.
- `.env` must have `FLASK_HOST=0.0.0.0` (not `127.0.0.1`).
- Third-party antivirus firewalls need the same exception for port 5000.

**Never open port 3307 (database)**. Only the server PC talks to the database.

## 4. CORS errors and unexpected sign-outs

The React app and the API are served from the **same origin**, so CORS isn't configured and
shouldn't appear. If the browser console shows a CORS error, something calls the API on a
*different* address:

| Cause | Fix |
|---|---|
| A `VITE_API_BASE_URL` or absolute API URL was added | Remove it; API calls must stay relative (`/api/...`) |
| Opening the dev server (5173) while the backend (5000) isn't running | Start `backend\run.py` first; the proxy needs it |
| Opening the built `dist\index.html` as a file, or with `npm run preview` | Use `http://<server>:5000/app/` (served by Flask) |

**Signed out unexpectedly / "Missing or invalid CSRF token":**

- Use **one** address per browser session. `localhost`, `127.0.0.1` and the LAN IP are different
  sites with separate cookies.
- `SECRET_KEY` changed in `.env` → everyone must sign in again (expected).
- Automatic sign-out after `SESSION_IDLE_MINUTES` (60) without activity or `SESSION_MAX_HOURS`
  (12) in total (expected).
- The account was deactivated, its password was reset, or the person changed their password on
  another PC: other sessions end immediately (expected).
- `SESSION_COOKIE_SECURE=1` while using plain `http://` → the browser never sends the session
  cookie; set it to `0`, or use the HTTPS address ([06 §5](06-SECURITY.md#5-https-on-the-lan-recommended)).
- Reload the page; the app fetches a new CSRF token.

**"Your resident portal access has ended…"** — not an error: the resident's owner/tenant record is
no longer *Current* (moved out) or the account was deactivated. An admin can check the unit's
owners/tenants and *Resident Accounts*.

## 5. Database connection errors

| Message | Meaning | Fix |
|---|---|---|
| `(2003) Can't connect to MySQL server on '127.0.0.1' ... actively refused it` | The database isn't running | Start with `START_WINDOWS.bat` (it starts the DB first), or `local_mysql.py start`, then see section 2 if it won't start |
| `(1045) Access denied for user 'cityland9_app'` | Password in `.env` doesn't match the database | Check `MYSQL_USER`/`MYSQL_PASSWORD` in `.env`; to re-apply the `.env` password run `local_mysql.py init` |
| `(1049) Unknown database 'cityland9'` | Database not created | [02 · Step 1](02-DATABASE_SETUP.md#step-1--create-the-database-server-database-and-accounts) |
| `Lost connection` / `MySQL server has gone away` after idle hours | The server closed an idle connection | Retry: connections are recycled automatically; if it repeats, check the `.err` log (section 2) |
| Timeout on start | Database still starting, or a stuck `mysqld.exe` | `local_mysql.py status`; if stuck: `local_mysql.py stop`, wait 10 s, start again |
| `The CityLand database is not set up yet` | No data folder | Fresh install: [02](02-DATABASE_SETUP.md) |

Quick health check:

```powershell
.venv\Scripts\python.exe database\local_mysql.py status
.venv\Scripts\python.exe database\db_migrate.py status
```

## 6. Missing tables or columns

| Message | Cause | Fix |
|---|---|---|
| `The database is missing table(s) ...` / `Table 'cityland9.xxx' doesn't exist` on a new install | Tables not created | `db_migrate.py install` ([02 · Step 2](02-DATABASE_SETUP.md#step-2--create-the-tables)) |
| `The database schema (...) is older than this version` at start | An update was installed but not migrated | `db_migrate.py upgrade` ([07 §6](07-OPERATIONS.md#6-upgrading-an-existing-installation)) |
| `Unknown column ...` after an update | A pending migration | `db_migrate.py status`, then `db_migrate.py upgrade` ([02 §3](02-DATABASE_SETUP.md#3-schema-changes-updates-that-change-tables)) |
| `(1142) ... command denied to user 'cityland9_app'` during a migration | Migration account not set up | `local_mysql.py secure` (adds `MYSQL_MIGRATE_USER`), then upgrade again |
| `db_migrate.py upgrade` reports *bill figures changed* | A migration changed amounts | Stop. Restore the backup it printed ([07 §3b](07-OPERATIONS.md#3b-replacing-the-live-database-disaster-recovery)) and report it |
| `db_migrate.py`: `No module named 'alembic'` | Packages not installed | `.venv\Scripts\python.exe -m pip install -r requirements.txt` |

## 6b. Start-up, sign-in and backup messages

| Message | Cause | Fix |
|---|---|---|
| `Configuration error: SECRET_KEY ...` | Missing, short or placeholder key | Generate one ([06 §1](06-SECURITY.md#1-required-configuration-the-server-refuses-to-start-otherwise)) |
| `No active Superadmin account yet` | Fresh install, or all Superadmins deactivated | `database\create_admin.py` (or `--reset <username>`) |
| *"This form has expired"* | The page was open from before a restart/sign-out, or opened in another session | Reload the page and submit again |
| *"Too many failed sign-in attempts"* | Sign-in throttling | Wait the minutes shown, or an administrator resets the password |
| Asked to choose a new password at sign-in | Temporary password (new account / reset / old `admin123`) | Choose a password that meets the policy |
| `/readyz` shows `warnings: ["backup"]` | Last backup failed or is older than `BACKUP_MAX_AGE_HOURS` | Read `logs\backup.log`; run `database\backup.py` by hand ([07 §2](07-OPERATIONS.md#2-backups)) |
| The server keeps restarting (`logs\service.log`) | It crashes at start | Read `logs\server-console.log` and `logs\cityland9.log` |
| *"Something went wrong ... this reference: ..."* | Unexpected error (nothing was saved) | Find the reference in `logs\cityland9.log` |

## 7. Frontend problems

| Symptom | Fix |
|---|---|
| `/app/` says *"The React frontend has not been built yet"* | `START_WINDOWS.bat --rebuild`, or `cd frontend` → `npm ci` → `npm run build` |
| Old design or old behaviour after an update | Rebuild (`--rebuild`), then hard-refresh the browser: **Ctrl+F5** |
| Blank white page | Press F12 → Console. A 404 for `/app/assets/...` means `dist` is out of date → rebuild |
| A module page says *"still runs on the classic screen"* | Expected: that module isn't rebuilt yet; use **Open …** |
| Prototype shows "Prototype mode" banner on the server | You opened the dev server (port 5173). Users must use port 5000 |

## 8. Installation errors

| Message | Fix |
|---|---|
| `[X] Python is not installed` / `'py' is not recognized` | Install Python 3.11+ with "Add python.exe to PATH", open a **new** terminal |
| `npm error enoent Could not read package.json` | Run npm inside `frontend\`: `cd frontend` |
| `[X] ... Node.js is not installed` | Install Node.js 20.19+ LTS, open a new terminal |
| `npm ci` fails with `EBADENGINE` / Vite needs newer Node | Upgrade Node.js to 20.19+ |
| `[X] Package installation failed` | Needs internet the first time; behind a proxy set `HTTPS_PROXY`. Afterwards no internet is needed |
| `Activate.ps1 cannot be loaded because running scripts is disabled` | Use `.venv\Scripts\python.exe` directly, or `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `pyodbc` fails to install | Not needed: it is only in `requirements-optional.txt` (old SQL Server option) |

## Collecting information for support

```powershell
py --version; node --version
.venv\Scripts\python.exe database\local_mysql.py status
.venv\Scripts\python.exe database\db_migrate.py status
netstat -ano | findstr ":5000 :3307"
git log --oneline -1
```

Plus the last 30 lines of `database\mysql-data\<PC-NAME>.err` and a screenshot of the
`START_WINDOWS.bat` window. **Never** send the `.env` file: it contains the passwords.
