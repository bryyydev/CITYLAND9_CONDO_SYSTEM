# 03 — Backend setup (Flask + Waitress)

The backend is a Flask 3.1 application (`backend\legacy_app.py` plus the API blueprints in
`backend\app\`), started by **`backend\run.py`**:

| Command | Server | Use |
|---|---|---|
| `backend\run.py --lan` | **Waitress** WSGI, 8 threads, loopback by default (`127.0.0.1:5000`); HTTPS proxy required for LAN access | Production (this is what `START_WINDOWS.bat` runs) |
| `backend\run.py` | Waitress in production; with `APP_ENV=development`, Flask serves loopback only (debugger only if also `FLASK_DEBUG=1`) | Local development only; `--lan` refuses development mode |
| `backend\service.py` | Runs `run.py --lan` and restarts it whenever it stops | Windows service via Task Scheduler ([07 §1](07-OPERATIONS.md#1-run-as-a-windows-service)) |

Before it loads the app, `run.py` reads `.env` and, when `DB_ENGINE=mysql` and `MYSQL_DATA_DIR`
are set, **starts CityLand's MariaDB** if it isn't running. It refuses to start when `SECRET_KEY`
is missing or weak, or when required database tables are missing. Application startup never creates
or alters production tables: apply MariaDB/MySQL migrations or SQL Server schema changes separately
before restarting (see [02](02-DATABASE_SETUP.md)).

All commands below are run from the **project root**.

## 1. Create the virtual environment

```powershell
py -3 -m venv .venv
```

There's no need to "activate" it: the commands in this guide call `.venv\Scripts\python.exe`
directly, which always uses the right environment. If you prefer activating it:

```powershell
.venv\Scripts\Activate.ps1        # PowerShell
.venv\Scripts\activate.bat        # Command Prompt
```

(If PowerShell refuses to run `Activate.ps1`, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or just use the direct paths.)

## 2. Install the packages

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

| File | Contains |
|---|---|
| `requirements.txt` | **Everything the server needs** (the one runtime file): Flask 3.1.3, Flask-SQLAlchemy 3.0.5, SQLAlchemy 1.4.54, Werkzeug 3.1.3, **Waitress 3.0.2**, **PyMySQL 1.2.3**, cryptography (encrypted SMTP password), python-dotenv, **Alembic** (migrations), openpyxl (Excel), qrcode (SOA QR) |
| `backend\requirements.txt` | Points to `requirements.txt` (kept for older instructions) |
| `requirements-sqlserver.txt` | SQL Server runtime: base requirements plus `pyodbc`; selected by `START_WINDOWS.bat` with Python 3.14 when `DATABASE_URL` targets SQL Server |
| `requirements-optional.txt` | Optional database extras; not used by the normal MariaDB/SQLite startup |
| `requirements-dev.txt` | `pytest` for the test suite (development PCs) |

All versions are pinned. `START_WINDOWS.bat` installs `requirements.txt` automatically, and only
again when it changes (so the server starts without internet).

## 3. Start on the LAN (production)

```powershell
START_WINDOWS.bat
```

or by hand:

```powershell
.venv\Scripts\python.exe backend\run.py --lan
```

Expected output for local-only access:

```
Log file: C:\...\CITYLAND9_SYSTEM\logs\cityland9.log
CityLand 9 running on http://127.0.0.1:5000 (Waitress, 8 threads, production, local access only)
```

- **This PC:** `http://127.0.0.1:5000`
- **Other PCs / phones:** configure HTTPS first; do not expose port 5000 directly. Follow [06 §5](06-SECURITY.md#5-https-on-the-lan-required-for-lan-access).

What the server answers:

| Path | What |
|---|---|
| `/` | Redirects to the React app (`/app/login` or the user's workspace) |
| `/app/...` | The React app (`frontend\dist`, see [04](04-FRONTEND_SETUP.md)) |
| `/api/...` | JSON API (sign-in, users, resident portal) |
| `/billing`, `/units`, … | Classic screens, still used by the staff modules not yet rebuilt |
| `/healthz`, `/readyz` | Health checks for monitoring ([07 §4](07-OPERATIONS.md#4-monitoring)) |

### Stop

- Ctrl+C in the server window, or close it.
- `STOP_WINDOWS.bat` stops the server **and** the database (`--keep-db` keeps the database). It
  also ends the *CityLand9 Server* scheduled task when that is installed.

## 4. Development server

```powershell
$env:APP_ENV = "development"; .venv\Scripts\python.exe backend\run.py
```

Development mode must be chosen explicitly (`APP_ENV=development`, in the shell or in a
development PC's `.env`); without it `run.py` always starts Waitress. It listens on 127.0.0.1 and
auto-reloads on code changes. Add `FLASK_DEBUG=1` **on a development PC only** for the interactive
debugger; it is refused on any network address. Run the React dev server next to it
([04 §3](04-FRONTEND_SETUP.md)).

## 5. Tests

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q
```

The tests use a throwaway SQLite database in a temporary folder; they never touch the real
database. All tests should pass (199 at the time of writing).

Checks that need MariaDB itself (migrations on a copy of the old schema, parallel cashiers,
fresh installation, backup and restore) run on **throwaway databases** with synthetic data:

```powershell
.venv\Scripts\python.exe database\tools\mariadb_integration.py               # all scenarios
.venv\Scripts\python.exe database\tools\mariadb_integration.py concurrency   # one scenario
```

They create `cl9_it_<random>` databases and a temporary account, and drop them at the end. The
live `cityland9` database is never opened.

## 6. Running as a service (recommended on the server)

`scripts\windows\register_tasks.ps1` registers the server (at startup, restarted when it stops)
and the daily backup: see [07 §1](07-OPERATIONS.md#1-run-as-a-windows-service).

## 7. Checklist

- [ ] `.venv\Scripts\python.exe --version` → 3.11+
- [ ] `pip install` finished without errors
- [ ] `backend\run.py --lan` binds to `127.0.0.1`; configure HTTPS before enabling LAN access
- [ ] `http://127.0.0.1:5000` opens the sign-in page on the server
- [ ] The configured HTTPS URL opens from another PC; port 5000 is not reachable over the LAN
- [ ] `FLASK_DEBUG=0` and `APP_ENV=production` on the server
