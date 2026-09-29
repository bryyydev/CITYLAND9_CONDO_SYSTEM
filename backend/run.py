"""CityLand 9 server entry point.

Run from the project root:
    python backend/run.py          development server (debug + auto-reload)
    python backend/run.py --lan    production server for LAN use (waitress)

The application itself still lives in backend/legacy_app.py until it is split
into the backend/app/ blueprint package (see docs/migration.md).
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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

from legacy_app import app, init_db  # noqa: E402

if __name__ == "__main__":
    init_db()
    host = os.getenv("FLASK_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_PORT", "5000"))
    if "--lan" in sys.argv:
        from waitress import serve
        print(f"CityLand 9 running on http://{host}:{port} (waitress, 8 threads)")
        serve(app, host=host, port=port, threads=8)
    else:
        app.run(host=host, port=port, debug=os.getenv("FLASK_DEBUG", "1") == "1")
