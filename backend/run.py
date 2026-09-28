"""CityLand 9 server entry point.

Run from the project root:
    python backend/run.py          development server (debug + auto-reload)
    python backend/run.py --lan    production server for LAN use (waitress)

The application itself still lives in backend/legacy_app.py until it is split
into the backend/app/ blueprint package (see docs/migration.md).
"""
import os
import sys

from legacy_app import app, init_db

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
