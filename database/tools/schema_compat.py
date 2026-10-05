"""Let read-only tools load the app against a database that is not upgraded yet.

db_migrate.py takes a "golden master" of every bill BEFORE applying migrations, and restore.py
verifies a restored backup the same way. Both import the current code, whose Billing model already
has the columns added by migration 0010 (SOA issue snapshot); on an older database those columns do
not exist and every bill query would fail.

flag_old_schema() checks the database and, when billing.issued_at is missing, sets
CL9_SCHEMA_BEFORE_0010=1 (and CL9_SCHEMA_BEFORE_0011=1 when user.temp_password_expires_at is
missing) BEFORE legacy_app is imported: the models then leave those columns out.
Bills of such a database have no snapshot anyway (the new code treats them exactly like that), so
the computed figures are the same. The running server never uses this: it refuses to start on an
older schema (db_migrate.py check).
"""
import os
import sqlite3


def _mysql_columns():
    # Built here like legacy_app does (importing mysql_schema_test would switch DATABASE_URL to SQLite).
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env"))
    except ImportError:
        pass
    from sqlalchemy import create_engine, inspect
    from sqlalchemy.engine import URL
    engine = create_engine(URL.create("mysql+pymysql", username=os.getenv("MYSQL_USER", ""), password=os.getenv("MYSQL_PASSWORD", ""),
                                      host=os.getenv("MYSQL_HOST", "127.0.0.1"), port=int(os.getenv("MYSQL_PORT", "3306")),
                                      database=os.getenv("MYSQL_DATABASE", "cityland9"), query={"charset": "utf8mb4"}))
    try:
        insp = inspect(engine)
        return {c["name"] for c in insp.get_columns("billing")}, {c["name"] for c in insp.get_columns("user")}
    finally:
        engine.dispose()


def flag_old_schema(sqlite_path=None):
    """Set CL9_SCHEMA_BEFORE_0010=1 when the database's billing table predates migration 0010."""
    try:
        if sqlite_path:
            with sqlite3.connect(sqlite_path) as con:
                cols = {row[1] for row in con.execute("PRAGMA table_info(billing)")}
                ucols = {row[1] for row in con.execute('PRAGMA table_info("user")')}
        else:
            cols, ucols = _mysql_columns()
    except Exception:
        return False          # no billing table yet / unreachable: let the tool report the real error
    flagged = False
    if cols and "issued_at" not in cols:
        os.environ["CL9_SCHEMA_BEFORE_0010"] = "1"
        flagged = True
    if ucols and "temp_password_expires_at" not in ucols:
        os.environ["CL9_SCHEMA_BEFORE_0011"] = "1"
        flagged = True
    return flagged
