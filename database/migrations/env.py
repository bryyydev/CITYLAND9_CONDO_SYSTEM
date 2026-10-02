"""Alembic environment: migrations run against the MySQL database the app is configured for
(.env: DB_ENGINE=mysql + MYSQL_*). The SQLite rollback database is handled by init_db()."""
import os
import sys
import warnings

from alembic import context

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings("ignore")
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    pass
sys.path.insert(0, os.path.join(ROOT, "backend"))

import legacy_app  # noqa: E402

if context.is_offline_mode():
    raise SystemExit("Offline (SQL script) mode is not used for CityLand 9. Run migrations against the database.")

def migration_engine():
    """Migrations change tables, so they use the migration account (MYSQL_MIGRATE_USER) when it exists;
    the application account only has data access (database/local_mysql.py secure)."""
    app_engine = legacy_app.db.engine
    if not app_engine.url.drivername.startswith("mysql"):
        raise SystemExit("Schema migrations are for the MySQL database only (set DB_ENGINE=mysql in .env).")
    user, password = os.getenv("MYSQL_MIGRATE_USER"), os.getenv("MYSQL_MIGRATE_PASSWORD")
    if not (user and password):
        print("Note: MYSQL_MIGRATE_USER is not set; using the application account. "
              "Run  python database/local_mysql.py secure  to separate them.")
        return app_engine
    from sqlalchemy import create_engine
    return create_engine(app_engine.url.set(username=user, password=password))


with legacy_app.app.app_context():
    engine = migration_engine()
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=legacy_app.db.metadata,
                          compare_type=True, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()
