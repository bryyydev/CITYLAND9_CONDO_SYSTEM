"""CityLand 9 server entry point.

Run from the project root:
    python backend/run.py --lan    production server for the LAN (Waitress)   <- START_WINDOWS.bat
    python backend/run.py          same as --lan unless APP_ENV=development

Development (explicit opt-in, never on the client's server):
    APP_ENV=development  python backend/run.py
        Flask development server with auto-reload on 127.0.0.1. The interactive debugger is
        enabled only with FLASK_DEBUG=1 and is refused on any address other than 127.0.0.1,
        because it lets anyone who can reach it run code on this PC.

The application itself lives in backend/legacy_app.py (being split into backend/app/).
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_ROOT, ".env"))
except ImportError:
    pass

# With DB_ENGINE=mysql and CityLand's own local database server (MYSQL_DATA_DIR),
# start that server first: legacy_app connects to the database while it loads.
if os.getenv("DB_ENGINE", "").strip().lower() == "mysql" and os.getenv("MYSQL_DATA_DIR") and not os.getenv("DATABASE_URL"):
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
    log_file = ops.configure_logging(app, _ROOT)
    init_db()
    print(f"Log file: {log_file}")
    minimum = float(os.getenv("DISK_MIN_FREE_GB", "2"))
    for label, status in ops.disk_status({"this drive": _ROOT}, minimum).items():
        if not status["ok"]:
            app.logger.warning("LOW DISK SPACE on %s: %s GB free (minimum %s GB). Backups and the database need room.",
                               label, status["free_gb"], minimum)
    backup = ops.backup_status(_ROOT, float(os.getenv("BACKUP_MAX_AGE_HOURS", "26")))
    if not backup["ok"]:
        app.logger.warning("BACKUP WARNING: %s", backup.get("detail") or f"last backup {backup.get('status')}, "
                           f"{backup.get('age_hours')} hours ago")
    port = int(os.getenv("FLASK_PORT", "5000"))
    if security.is_development() and "--lan" not in sys.argv:
        host = os.getenv("FLASK_HOST", "127.0.0.1")
        debug = os.getenv("FLASK_DEBUG", "0") == "1"
        if debug and host not in ("127.0.0.1", "localhost"):
            sys.exit("Refusing to start the Flask debugger on a network address. "
                     "Use FLASK_HOST=127.0.0.1 for debugging, or run with --lan (Waitress).")
        print(f"DEVELOPMENT server on http://{host}:{port} (debugger {'ON' if debug else 'off'})")
        app.run(host=host, port=port, debug=debug)
        return
    from waitress import serve
    host = os.getenv("FLASK_HOST", "0.0.0.0")
    threads = int(os.getenv("WAITRESS_THREADS", "8"))
    options = {}
    proxy = os.getenv("TRUSTED_PROXY", "").strip()
    if proxy:
        # HTTPS reverse proxy on this PC (docs/run-guide/06-SECURITY.md): trust ONLY its forwarded scheme and
        # client address, so HSTS, secure cookies and sign-in throttling see the real browser.
        options = {"trusted_proxy": proxy, "trusted_proxy_count": 1, "clear_untrusted_proxy_headers": True,
                   "trusted_proxy_headers": {"x-forwarded-proto", "x-forwarded-for"}}
    print(f"CityLand 9 running on http://{host}:{port} (waitress, {threads} threads, {security.environment()}"
          f"{', behind proxy ' + proxy if proxy else ''})")
    serve(app, host=host, port=port, threads=threads, **options)


if __name__ == "__main__":
    main()
