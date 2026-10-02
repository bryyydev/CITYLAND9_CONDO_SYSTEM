# 06 — Security (server PC and office network)

What the application enforces by itself, and what the person installing the server must set up.
Everything stays on the office LAN; nothing here needs a cloud service or internet access.

## 1. Required configuration (the server refuses to start otherwise)

| Setting | Rule |
|---|---|
| `SECRET_KEY` in `.env` | At least 32 random characters, not a placeholder. Generate: `.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"`. Changing it signs everyone out and makes the saved SMTP password unreadable (re-enter it in the app). |
| `APP_ENV` | `production` (default). `development` must be set explicitly and is never used on the client's server. |
| Flask debugger | Only with `APP_ENV=development` **and** `FLASK_DEBUG=1` **and** `FLASK_HOST=127.0.0.1`; refused on a network address. `START_WINDOWS.bat` and the service always use Waitress. |

## 2. What the application enforces

| Area | Behaviour |
|---|---|
| **Forms (CSRF)** | Every form that changes data, sign-in and sign-out carries a per-session token; the API requires the `X-CSRFToken` header. A missing/wrong token gets *"This form has expired"* and nothing is changed. |
| **Repeated submissions** | Payment forms carry a one-time token: a double click or a re-sent form records the payment once. |
| **Sessions** | Signed out after `SESSION_IDLE_MINUTES` (60) without activity and after `SESSION_MAX_HOURS` (12) in any case. Deactivating an account, resetting its password, or the person changing their own password **ends their other sessions immediately**. |
| **Sign-in throttling** | After `LOGIN_MAX_FAILURES` (5) wrong passwords for one account from one PC, that PC waits `LOGIN_LOCKOUT_MINUTES` (15) for that account; 30 failures from one PC block that PC. Messages never reveal whether a username exists. Failures are logged with the PC's address. |
| **Responses** | `X-Content-Type-Options`, `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`; signed-in pages are never cached; HSTS when served over HTTPS. |
| **Uploads** | Excel imports are limited in size (`MAX_UPLOAD_MB`), rows, columns and uncompressed size; non-workbooks are rejected. |
| **Errors** | Users see a plain message with a reference number; details go only to `logs\cityland9.log`. |
| **Database rights** | The web application's database account can only read and write data, not change tables ([02](02-DATABASE_SETUP.md)). The database listens on 127.0.0.1 only. |

## 3. Passwords and accounts

- **One policy everywhere** (create, reset, change, `create_admin.py`): at least
  `PASSWORD_MIN_LENGTH` (10) characters, at most 128, not a common password, not repetitive, not
  containing the username.
- **No default account.** The first Superadmin is created on the server PC with
  `database\create_admin.py` ([02 Step 3](02-DATABASE_SETUP.md#step-3--create-the-first-superadmin)).
- **Temporary passwords.** Passwords set by an administrator (new account, reset) are temporary:
  the person must choose their own before doing anything else.
- **Upgrade from an older version:** any account still using the old shipped password `admin123`
  is flagged and must change its password at the next sign-in.
- Each person gets their own account; never share the Superadmin account. Deactivate accounts of
  people who leave (this signs them out at once).

## 4. SOA email (SMTP) password

Entered in *Rates & Rules → Email Delivery*. It is stored **encrypted** (key derived from
`SECRET_KEY`), never shown again or sent to the browser; the page only says whether one is saved.
Leave the field empty to keep it, tick *Clear* to remove it. Prefer an app-specific password or a
LAN mail relay over a personal mailbox password.

## 5. HTTPS on the LAN (recommended)

Plain `http://` on the LAN means passwords and data cross the office network unencrypted. To use
HTTPS **without internet**, put a small reverse proxy on the server PC with its own local
certificate authority. Example with **Caddy** (one `caddy.exe`, no installer):

1. Download `caddy.exe` (Windows amd64) once from caddyserver.com on any PC and copy it to
   `C:\caddy\`.
2. `C:\caddy\Caddyfile` (replace the name/IP with the server's):

   ```
   https://cityland9.local, https://192.168.100.63 {
       tls internal
       reverse_proxy 127.0.0.1:5000
   }
   ```

3. In `.env`:

   ```
   FLASK_HOST=127.0.0.1          # only the proxy can reach Waitress now
   TRUSTED_PROXY=127.0.0.1       # trust the proxy's X-Forwarded-Proto / X-Forwarded-For only
   SESSION_COOKIE_SECURE=1       # session cookie is sent over HTTPS only
   ```

4. Start Caddy (`C:\caddy\caddy.exe run --config C:\caddy\Caddyfile`; register it in Task
   Scheduler *At startup* like the server). Caddy's local CA root certificate is at
   `%AppData%\Caddy\pki\authorities\local\root.crt`: install it on each office PC
   (*Trusted Root Certification Authorities*) so browsers trust the site.
5. Firewall: open **443** instead of 5000 (below).

An existing company CA or IIS/ARR with a company certificate works the same way: the proxy
terminates HTTPS and forwards to `127.0.0.1:5000`; set the three `.env` values above.

> Status: the Waitress proxy settings were verified (forwarded headers are ignored unless they
> come from `TRUSTED_PROXY`). A full Caddy deployment has **not** been tested on the client's
> network.

## 6. Windows Firewall

Allow only the office network to reach the system (PowerShell **as Administrator**):

```powershell
# HTTP (no proxy):
New-NetFirewallRule -DisplayName "CityLand 9" -Direction Inbound -Protocol TCP -LocalPort 5000 `
  -Profile Private,Domain -RemoteAddress LocalSubnet -Action Allow
# HTTPS proxy instead: use -LocalPort 443 (and do not open 5000)
```

- The network must be set to **Private** (Settings → Network → the connection → *Private*), not
  Public. Never forward ports on the router: the system is not meant to be reachable from the
  internet.
- Port **3307** (database) must **not** be opened; it listens on 127.0.0.1 only.

## 7. The server PC itself

- Windows updates on; a supported Windows version; antivirus on (exclude `database\mysql-data`
  from real-time scanning only if it slows the database down).
- Only administrators can sign in to the server PC. `.env`, `database\mysql-data`,
  `database\backups` and `logs` must not be in a shared folder.
- `.env` holds every secret: never email, commit or copy it to a USB drive together with backups.
- Lock the screen when leaving the server PC.

## 8. Checklist

- [ ] `SECRET_KEY` generated; `APP_ENV=production`; `FLASK_DEBUG=0`
- [ ] First Superadmin created with `create_admin.py`; no shared accounts
- [ ] Firewall rule limited to the local subnet; network profile is *Private*
- [ ] HTTPS proxy with `SESSION_COOKIE_SECURE=1` and `TRUSTED_PROXY` (recommended)
- [ ] SMTP password entered in the app (encrypted), not left in `.env`
