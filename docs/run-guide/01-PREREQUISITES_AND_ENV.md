# 01 — Prerequisites & environment configuration

## 1. Software

| Software | Version | Needed for | Check |
|---|---|---|---|
| **Windows** | 10 / 11 | The server PC (scripts are `.bat`) | — |
| **Python** | **3.11 or newer** (tested on 3.14) | The backend | `py --version` |
| **XAMPP** (for its **MariaDB** programs) | MariaDB 10.4+ | The database server | `C:\xampp\mysql\bin\mysqld.exe --version` |
| **Node.js** (LTS) | **20.19 or newer** (Vite 8 requires it) | Building the React app; development | `node --version` |
| **Git** | any | Getting and updating the code | `git --version` |

Notes:

- **Python:** during installation tick **"Add python.exe to PATH"**.
- **XAMPP:** only its MariaDB *programs* are used. CityLand runs its **own** MariaDB instance with
  its own data folder (`database\mysql-data`) on port **3307**. XAMPP's own MySQL (port 3306) and
  its databases are never touched; you don't need to start XAMPP.
- **MySQL 8.0+** instead of MariaDB: possible as an *existing* server (set the `MYSQL_*` values to
  it and leave `MYSQL_DATA_DIR` empty, so nothing is auto-started). The automatic setup in
  `database\local_mysql.py` needs MariaDB's `mysql_install_db`. The system is tested on MariaDB 10.4.
- **Node.js** is only needed on the server to **build** the React app (first install and after
  updates). The running system doesn't use Node.
- **Internet** is needed only during installation (Python packages, npm packages). Afterwards the
  system runs fully offline on the LAN.

## 2. Get the code

```powershell
cd C:\Users\<you>\Documents
git clone https://github.com/bryyydev/CITYLAND9_CONDO_SYSTEM.git CITYLAND9_SYSTEM
cd CITYLAND9_SYSTEM
```

## 3. The `.env` file (project root)

All configuration and secrets are in **one file: `.env` in the project root**
(`CITYLAND9_SYSTEM\.env`). It is excluded from git; never share or commit it.
There is **no** `backend\.env` and **no** `frontend\.env` (see section 4).

### Fresh installation: start with a short `.env`

Create `.env` in the project root with **only** these lines (Notepad → Save as → `.env`,
"All files"):

```ini
SECRET_KEY=paste-a-generated-value-here
DB_ENGINE=mysql
FLASK_HOST=0.0.0.0
FLASK_PORT=5000
FLASK_DEBUG=0
MYSQL_BIN_DIR=C:\xampp\mysql\bin
```

Generate the secret key:

```powershell
py -c "import secrets; print(secrets.token_hex(32))"
```

`python database\local_mysql.py init` ([02](02-DATABASE_SETUP.md)) then **adds** the database
settings with strong random passwords: `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_DATABASE`,
`MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_ROOT_PASSWORD` and `MYSQL_DATA_DIR`.

> ⚠️ **Don't copy `.env.example` as-is before running `init`.** `init` never overwrites a value
> that already exists, so the template's placeholders (`MYSQL_PASSWORD=your_password`,
> `MYSQL_ROOT_PASSWORD=...`, `MYSQL_DATA_DIR=C:\path\to\project\...`) would be kept: the app
> user would get a weak password, the generated root password would be lost, and the data
> folder would be created in the wrong place. `.env.example` is a **reference** of all options.

### Complete `.env` reference (after `init`)

`.env.example` lists every option with an explanation; copy single lines from it as needed
(never the whole file before `init`, see above).

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `SECRET_KEY` | **yes** | — | Signs session cookies, encrypts the saved SMTP password. At least 32 random characters, or the server refuses to start. Changing it signs everyone out. |
| `APP_ENV` | no | `production` | `development` only on a developer PC ([03 §4](03-BACKEND_SETUP.md#4-development-server)) |
| `DB_ENGINE` | yes | `sqlite` | `mysql` for the normal setup |
| `MYSQL_HOST` / `MYSQL_PORT` | yes | `127.0.0.1` / `3306` | CityLand's MariaDB is on **3307** (added by `init`) |
| `MYSQL_DATABASE` / `MYSQL_USER` / `MYSQL_PASSWORD` | yes | — | App database and its data-only account (added by `init`) |
| `MYSQL_MIGRATE_USER` / `MYSQL_MIGRATE_PASSWORD` | yes | — | Account for schema changes (added by `init` / `local_mysql.py secure`) |
| `MYSQL_ROOT_PASSWORD` | yes | — | Used only by setup, backup and restore tools, never by the web application |
| `MYSQL_BIN_DIR` | yes | `C:\xampp\mysql\bin` | Where `mysqld.exe` / `mysqldump.exe` are |
| `MYSQL_DATA_DIR` | yes* | — | CityLand's own data folder. *When set, the server **auto-starts** MariaDB. |
| `FLASK_HOST` / `FLASK_PORT` | no | `0.0.0.0` / `5000` | Where Waitress listens (`127.0.0.1` behind an HTTPS proxy) |
| `WAITRESS_THREADS` | no | `8` | Worker threads |
| `FLASK_DEBUG` | no | `0` | Debugger, development only ([06 §1](06-SECURITY.md#1-required-configuration-the-server-refuses-to-start-otherwise)) |
| `SESSION_IDLE_MINUTES` / `SESSION_MAX_HOURS` | no | `60` / `12` | Automatic sign-out |
| `SESSION_COOKIE_SECURE` | no | `0` | `1` when served over HTTPS |
| `TRUSTED_PROXY` | no | — | Address of the HTTPS reverse proxy on this PC, e.g. `127.0.0.1` ([06 §5](06-SECURITY.md#5-https-on-the-lan-recommended)) |
| `PASSWORD_MIN_LENGTH` | no | `10` | Password policy minimum (8–64) |
| `LOGIN_MAX_FAILURES` / `LOGIN_LOCKOUT_MINUTES` | no | `5` / `15` | Sign-in throttling |
| `MAX_UPLOAD_MB`, `IMPORT_MAX_ROWS`, `IMPORT_MAX_COLUMNS`, `IMPORT_MAX_UNCOMPRESSED_MB` | no | `20`, `50000`, `200`, `200` | Excel import limits |
| `LOG_MAX_MB` / `LOG_BACKUPS` | no | `10` / `10` | Rotation of `logs\cityland9.log` |
| `DISK_MIN_FREE_GB` | no | `2` | `/readyz` reports *not ready* below this |
| `BACKUP_RETENTION_DAYS` / `BACKUP_KEEP_MIN` | no | `30` / `7` | Automatic backup retention ([07 §2](07-OPERATIONS.md#2-backups)) |
| `BACKUP_MIN_FREE_GB` / `BACKUP_MAX_AGE_HOURS` | no | `2` / `26` | Backup refuses below this free space / warning when the last good backup is older |
| `BACKUP_COPY_DIR` | no (recommended) | — | Second copy of each backup on another disk or PC |
| `DATABASE_URL` | no | — | Advanced override (disables the automatic database start) |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | no | — | Fallback email login; prefer entering it in the app (stored encrypted) |
| `CL9_NO_BROWSER` | no | — | `1` = `START_WINDOWS.bat` doesn't open the browser |

> **SMTP settings** (host, port, sender) are stored in the database through
> *Rates & Rules*, not in `.env`. The SMTP password is write-only in the new interface.

## 4. Frontend configuration — none needed

The React app calls the API with **relative URLs** (`/api/...`) on the same server that serves
it, so it needs **no `frontend\.env`** and no `VITE_API_BASE_URL`:

- **Production:** the browser loads `http://SERVER-IP:5000/app/` and calls
  `http://SERVER-IP:5000/api/...` — same origin, works from any PC on the LAN without
  configuring an IP address anywhere.
- **Development:** Vite (port 5173) forwards `/api` to `http://127.0.0.1:5000`
  (`frontend\vite.config.ts`, `server.proxy`).
- **Prototype mode** is chosen by `npm run dev:mock` (`vite --mode mock`), not by an env file.

Hard-coding a server IP in the frontend would break when the server's IP changes and would
introduce CORS problems, so don't add one.

## 5. Checklist

- [ ] `py --version` shows 3.11+
- [ ] `node --version` shows v20.19+
- [ ] `C:\xampp\mysql\bin\mysqld.exe` exists (or `MYSQL_BIN_DIR` points to MariaDB's `bin`)
- [ ] `.env` exists in the project root with a real `SECRET_KEY` and `DB_ENGINE=mysql`
- [ ] Before `init`: `.env` has **no** `MYSQL_PASSWORD`, `MYSQL_ROOT_PASSWORD` or `MYSQL_DATA_DIR` lines
- [ ] `.env` is **not** listed by `git status` (it's ignored)
