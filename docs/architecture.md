# CityLand 9 — Target Architecture (local / self-hosted)

**Status:** approved direction, not yet implemented. Today the system runs as the legacy Flask app (`backend/legacy_app.py` + `legacy_flask_ui/`) on SQLite.
**Client requirement:** the system is **not cloud-based**. Every component runs on the client's own server PC and LAN, and every core function works **without internet access**.

---

## 1. Deployment topology

```
 Office PCs / phones on the LAN                 Server PC (client-owned)
 ┌──────────────────────────┐   HTTP :5000   ┌──────────────────────────────────────────┐
 │ Browser                  │ ─────────────▶ │ waitress (Flask)  0.0.0.0:5000            │
 │  React app (static files │ ◀───────────── │  ├─ /            → React build (static)   │
 │  served by Flask)        │   JSON /api/*  │  ├─ /api/*       → Flask blueprints       │
 └──────────────────────────┘                │  └─ legacy pages → Jinja (until retired)  │
                                             │              │ PyMySQL                    │
                                             │              ▼                            │
                                             │ MySQL 8 / MariaDB   127.0.0.1:3306 only   │
                                             │ Local folders: uploads/, database/backups │
                                             └──────────────────────────────────────────┘
 Optional, outside the core: SMTP server (LAN or ISP) for SOA email only
```

- **One origin.** In production Flask serves the built React files *and* the API on the same address (`http://192.168.x.x:5000`). There are no cross-origin requests, so **no CORS configuration** is needed, and none is opened.
- **Development:** the Vite dev server (`localhost:5173`) proxies `/api` to Flask on `127.0.0.1:5000`. That's still the same origin from the browser's point of view.
- **MySQL is never reachable from the browser or the LAN.** It listens on `127.0.0.1`, and port 3306 stays closed in the firewall. Only Flask holds the credentials, in the local `.env` file.
- **Offline audit (2026-09-29):** the current templates and static files load **no** CDN scripts, web fonts or external resources, and the backend makes **no** outbound calls except the optional SMTP email. The React build must keep that: every dependency is bundled locally, with no CDN links and no Google Fonts URLs.

### Not used (client requirement)
Firebase, Supabase, AWS/GCP/Azure databases or storage, MongoDB Atlas, PlanetScale, Railway, Render, Neon, any cloud-hosted MySQL/PostgreSQL, cloud authentication, cloud file storage (S3, GCS, Blob, Cloudinary, Firebase/Supabase Storage), and any backend-as-a-service.

---

## 2. Backend layout (target)

```
backend/
├── app/
│   ├── __init__.py      create_app(): config, extensions, blueprints, error handlers, React static route
│   ├── config.py        reads .env: SECRET_KEY, MYSQL_* (builds mysql+pymysql:// URL), FLASK_DEBUG
│   ├── extensions.py    db (SQLAlchemy), migrate (Flask-Migrate), csrf
│   ├── core/            auth (session login, permission matrix), errors (JSON), audit
│   ├── models/          the 31 existing models, moved without changes
│   ├── services/        billing_engine, payroll_engine, email, excel_io, storage
│   ├── routes/          one blueprint per module group (see docs/module-migration-checklist.md §1)
│   └── utils/
├── legacy_app.py        the working system, kept until every module is VERIFIED
└── run.py               `--lan` = waitress
```

The legacy app and the new API share the same models and services during the migration, so the business logic exists **once**.

---

## 3. Authentication and CSRF (plan)

**Decision: keep server-side session cookies. No JWT.** This preserves the existing accounts, Werkzeug password hashes, six roles and login behavior.

| Topic | Plan | Why |
|---|---|---|
| Login | `POST /api/auth/login` checks the existing `user` table with `check_password_hash`. On success: `session.clear()`, set `user_id`, audit "Login" (same as today). | Existing accounts and hashes keep working with no password resets |
| Session cookie | `HttpOnly`, `SameSite=Lax`, signed with `SECRET_KEY` from `.env`. `Secure` only when served over HTTPS. 8-hour idle timeout. | JavaScript can never read the session, so XSS can't steal it. JWT in localStorage would be readable by any injected script. |
| CSRF | Synchronizer token via Flask-WTF `CSRFProtect`. `GET /api/auth/csrf` returns a token; React sends it in an `X-CSRFToken` header on every POST/PUT/PATCH/DELETE. The legacy Jinja forms get a hidden `csrf_token` field in the same release. | Cookie authentication needs CSRF protection. Today **no form is protected**. |
| Who am I | `GET /api/auth/me` returns username, role and the role's home page (`ROLE_HOME`) | The React menu and routing |
| Authorization | **One permission matrix** in `backend/app/core/permissions.py`, used by the API decorators *and* returned to React for menu visibility. It replaces today's two drifting sources (`ENDPOINT_ROLES` and the `@roles` decorators). | Hiding a menu item is not authorization. The API refuses with **403** whatever the UI shows. |
| Login throttling | After 5 failed logins per username+IP, wait 5 minutes. Counters kept in the database or process memory (no Redis or cloud service). | There's no rate limiting today |
| Transport | Plain HTTP on the LAN is common for this kind of office system. Optional upgrade: a locally generated TLS certificate (e.g. `mkcert`) so the session cookie can be `Secure`. No public certificate authority or cloud proxy needed. | Client decision |
| Defaults | Stop showing `superadmin / admin123` on the login page. Force a password change on the first login of the auto-created account. | Security finding (CRITICAL) |

Required packages when implemented: `Flask-WTF` (CSRF). Nothing else. Rate limiting is simple enough to write in-house without a new dependency.

---

## 4. Email (SOA Email) — how it works today

Inspection of `send_soa_email_to_contact()` (`backend/legacy_app.py` ~line 2190):

| Aspect | Current behavior |
|---|---|
| What is emailed | **Only the SOA.** One email per opted-in current owner/tenant (`receive_soa_email`). There are no password-reset, announcement or notification emails. |
| Transport | Python `smtplib` to **any SMTP server** (a LAN mail server, the ISP's SMTP, or the company's mail provider). It always uses `STARTTLS` on the configured port (default 587). |
| Settings | Host, port and sender are stored in `setting` (Rates & Rules → Email Delivery). Username and password also come from `setting`, **falling back to the `SMTP_USERNAME`/`SMTP_PASSWORD` environment variables**. |
| If email is unavailable | Sending raises an error for that recipient. The error is shown and logged, and the other recipients are still attempted. **Billing, SOA printing and every other function keep working.** Email is already an optional add-on, not a dependency. |
| Limitations found | STARTTLS is mandatory: a LAN relay without TLS, or an SMTPS server on port 465, **can't be used**. The SMTP password is stored in plain text and is rendered back into the Rates & Rules page's HTML. |

Plan:
- Move the logic into `services/email.py` unchanged.
- SMTP credentials come from `.env` only and are never sent to React. The settings API accepts a new password but never returns one.
- Add a "TLS mode" option (STARTTLS / SSL / none-for-LAN-relay). This is a new option; the current default stays.
- Keep sending synchronous (one office, small volumes). A job queue would add infrastructure for no real benefit.

---

## 5. File storage (local only)

- **Today there are no real uploads of documents.** The Documents module stores only a typed `file_name`/`file_path` text. The **only** upload in the system is the Excel database import (`.xlsx/.xlsm`, super admin and manager only), which is read in memory and never saved to disk.
- If document upload is added later (a new feature, needing approval), the rules are:
  - Files go under `backend/instance/uploads/` on the server PC. That's outside the web root and git-ignored.
  - Files are saved under a random name (UUID); the original name is kept only in the database.
  - Allowed types: PDF, JPG, PNG, XLSX, DOCX, checked by extension **and** file signature. Executables and scripts are rejected.
  - Maximum size: `MAX_CONTENT_LENGTH` = 10 MB (configurable).
  - Downloads go through `GET /api/documents/<id>/file`, which checks the same audience rules as the list (resident → own unit or residents-wide only). Directory paths are never exposed.
  - Uploads and downloads of sensitive files are written to the audit log.
  - These files are included in the local backup job next to the `mysqldump`.

---

## 6. Other security settings for the local deployment

| Setting | Plan |
|---|---|
| `FLASK_DEBUG` | Always `0` on the server. `run.py` refuses `FLASK_DEBUG=1` when the host is `0.0.0.0`. Today the dev mode defaults to debug on `0.0.0.0` (a HIGH finding). |
| `SECRET_KEY` | Required from `.env`; the app refuses to start with the placeholder `change-me`. Today there's a hard-coded fallback. |
| Database account | `cityland9_app`, limited to the `cityland9` database, never root (docs/migration.md §9.1) |
| Logs | Local rotating log files (`backend/instance/logs/`), no external log service |
| Backups | Windows Task Scheduler: a daily `mysqldump` plus the uploads folder, copied to a local or external drive |
