"""Keeps the CityLand 9 server running on the server PC (started by Windows Task Scheduler at boot).

    .venv\\Scripts\\python.exe backend\\service.py

Registered by scripts\\windows\\register_tasks.ps1 as the task "CityLand9 Server". It:
  * waits for the database schema check (database/db_migrate.py check). If the schema is older
    than the code it does NOT start the server; it logs the reason and checks again every 5 minutes.
  * runs backend/run.py --lan (Waitress) and restarts it whenever it stops: after 5 s, doubling up
    to 2 minutes while it keeps failing; back to 5 s once it has run for 10 minutes.
  * logs every start/stop/restart to logs/service.log.
To stop it: STOP_WINDOWS.bat (ends the scheduled task first, so nothing restarts the server).
No internet access is needed at any point.
"""
import logging
import os
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


def _logger():
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    log = logging.getLogger("cityland9.service")
    handler = RotatingFileHandler(os.path.join(ROOT, "logs", "service.log"), maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    log.addHandler(handler)
    log.addHandler(logging.StreamHandler())
    log.setLevel(logging.INFO)
    return log


def main(max_restarts=None):
    log = _logger()
    log.info("Service starting (pid %s)", os.getpid())
    delay, restarts = 5, 0
    while max_restarts is None or restarts <= max_restarts:
        check = subprocess.run([PY, os.path.join("database", "db_migrate.py"), "check"], cwd=ROOT, capture_output=True, text=True)
        if check.returncode != 0:
            log.error("Not starting the server: %s", (check.stdout + check.stderr).strip()[-600:])
            restarts += 1
            time.sleep(300)
            continue
        started = time.monotonic()
        log.info("Starting the web server (backend/run.py --lan)")
        with open(os.path.join(ROOT, "logs", "server-console.log"), "a", encoding="utf-8") as console:
            code = subprocess.call([PY, os.path.join("backend", "run.py"), "--lan"], cwd=ROOT, stdout=console, stderr=subprocess.STDOUT)
        ran = time.monotonic() - started
        delay = 5 if ran > 600 else min(delay * 2, 120)
        restarts += 1
        log.error("The web server stopped (exit code %s after %d s). Restarting in %d s.", code, ran, delay)
        time.sleep(delay)
    log.error("Giving up after %d restarts.", restarts)
    return 1


if __name__ == "__main__":
    sys.exit(main())
