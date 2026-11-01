"""CityLand 9 server entry point.

Run from the project root:
    python backend/run.py --lan    production server (Waitress); HTTPS proxy required for LAN access
    python backend/run.py          production Waitress, local-only unless HTTPS proxy is configured

Development (explicit opt-in, never on the client's server):
    APP_ENV=development  python backend/run.py
        Flask development server with auto-reload on 127.0.0.1. The interactive debugger is
        enabled only with FLASK_DEBUG=1 and is refused on any address other than 127.0.0.1,
        because it lets anyone who can reach it run code on this PC.

The application itself lives in backend/legacy_app.py (being split into backend/app/).
"""
import ipaddress
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_ROOT, ".env"))
except ImportError:
    pass

# A test installation (CL9_TEST_INSTALL=1, CITYLAND9_TEST_PC_MIGRATION.md) must use its OWN local database,
# never another PC's: refuse a remote MYSQL_HOST or an explicit DATABASE_URL. Its email is disabled.
TEST_INSTALL = os.getenv("CL9_TEST_INSTALL", "").strip() == "1"
if TEST_INSTALL and (os.getenv("DATABASE_URL") or os.getenv("MYSQL_HOST", "127.0.0.1").strip().lower() not in ("127.0.0.1", "localhost", "::1")):
    sys.exit("CL9_TEST_INSTALL=1: a test installation must use its own database on this PC. "
             "Remove DATABASE_URL and set MYSQL_HOST=127.0.0.1 in .env.")

# With DB_ENGINE=mysql and CityLand's own local database server (MYSQL_DATA_DIR),
# start that server first: legacy_app connects to the database while it loads.
if (os.getenv("DB_ENGINE", "").strip().lower() == "mysql"
        and os.getenv("MYSQL_DATA_DIR")
        and os.getenv("MYSQL_EXTERNAL_SERVER", "").strip().lower() not in ("1", "true", "yes")
        and not os.getenv("DATABASE_URL")):
    sys.path.insert(0, os.path.join(_ROOT, "database"))
    import local_mysql  # noqa: E402
    local_mysql.start()

from app.core import security  # noqa: E402

try:
    from legacy_app import app, init_db  # noqa: E402
except security.ConfigurationError as exc:
    sys.exit(f"Configuration error: {exc}")


def main():
    from app.core import ops
    host = os.getenv("FLASK_HOST", "127.0.0.1").strip()
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    lan_mode = "--lan" in sys.argv
    if security.is_development():
        if lan_mode:
            sys.exit("Refusing to start --lan in development mode. Use APP_ENV=production with HTTPS.")
        if debug and not _is_loopback(host):
            sys.exit("Refusing to start the Flask debugger on a network address. "
                     "Use FLASK_HOST=127.0.0.1 for debugging.")
        if not _is_loopback(host):
            sys.exit("Refusing to expose the development server on a network address. "
                     "Use FLASK_HOST=127.0.0.1.")

    proxy = os.getenv("TRUSTED_PROXY", "").strip()
    secure_cookie = os.getenv("SESSION_COOKIE_SECURE", "0") == "1"
    if proxy:
        if not _is_loopback(host):
            sys.exit("When TRUSTED_PROXY is set, FLASK_HOST must be loopback-only (127.0.0.1).")
        if not secure_cookie:
            sys.exit("HTTPS proxy mode requires SESSION_COOKIE_SECURE=1.")
    elif not _is_loopback(host):
        sys.exit("Refusing to expose production CityLand over unencrypted HTTP. "
                 "Bind FLASK_HOST to 127.0.0.1 and configure an HTTPS reverse proxy "
                 "(see docs/run-guide/06-SECURITY.md).")

    log_file = ops.configure_logging(app, _ROOT)
    init_db()
    print(f"Log file: {log_file}")
    if TEST_INSTALL:
        print(f"TEST INSTALLATION: database `{os.getenv('MYSQL_DATABASE', '')}` on this PC; outbound email disabled.", flush=True)
    minimum = float(os.getenv("DISK_MIN_FREE_GB", "2"))
    for label, status in ops.disk_status({"this drive": _ROOT}, minimum).items():
        if not status["ok"]:
            app.logger.warning("LOW DISK SPACE on %s: %s GB free (minimum %s GB). Backups and the database need room.",
                               label, status["free_gb"], minimum)
    # SQLite test/demo installs have no scheduled database-server backup.
    database_uri = app.config["SQLALCHEMY_DATABASE_URI"]
    uses_mariadb = database_uri.startswith("mysql")
    uses_sqlserver = database_uri.startswith("mssql")
    backup = ops.backup_status(_ROOT, float(os.getenv("BACKUP_MAX_AGE_HOURS", "26"))) \
        if uses_mariadb or uses_sqlserver else {"ok": True}
    if not backup["ok"]:
        app.logger.warning("BACKUP WARNING: %s", backup.get("detail") or f"last backup {backup.get('status')}, "
                           f"{backup.get('age_hours')} hours ago")
    port = int(os.getenv("FLASK_PORT", "5000"))
    if security.is_development():
        print(f"DEVELOPMENT server on http://{host}:{port} (debugger {'ON' if debug else 'off'})")
        app.run(host=host, port=port, debug=debug)
        return
    from waitress import serve
    threads = int(os.getenv("WAITRESS_THREADS", "8"))
    options = {}
    if proxy:
        # HTTPS reverse proxy on this PC (docs/run-guide/06-SECURITY.md): trust ONLY its forwarded scheme and
        # client address, so HSTS, secure cookies and sign-in throttling see the real browser.
        options = {"trusted_proxy": proxy, "trusted_proxy_count": 1, "clear_untrusted_proxy_headers": True,
                   "trusted_proxy_headers": {"x-forwarded-proto", "x-forwarded-for"}}
    if proxy:
        print(f"CityLand 9 running behind HTTPS proxy {proxy} "
              f"(Waitress on {host}:{port}, {threads} threads, {security.environment()})")
    else:
        print(f"CityLand 9 running on http://{host}:{port} "
              f"(Waitress, {threads} threads, {security.environment()}, local access only)")
    serve(app, host=host, port=port, threads=threads, **options)


def _is_loopback(host):
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


if __name__ == "__main__":
    main()
