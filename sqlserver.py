"""Local SQL Server backup and restore helpers."""
import os
import re
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url


def _local_url(database_url):
    url = make_url(database_url)
    if url.get_backend_name() != "mssql":
        raise RuntimeError("DATABASE_URL must use SQL Server (mssql+pyodbc).")
    host = (url.host or "").lower()
    machine = host.split("\\", 1)[0]
    if machine not in ("localhost", "127.0.0.1", "::1", ".", "(local)"):
        raise RuntimeError("SQL Server backup/restore is supported only for a local SQL Server instance.")
    if not url.database:
        raise RuntimeError("DATABASE_URL must name a SQL Server database.")
    return url


def supports_local_database(database_url):
    try:
        _local_url(database_url)
    except (ValueError, RuntimeError):
        return False
    return True


def _quote_identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,128}", value):
        raise ValueError("SQL Server database and restore-target names may contain only letters, digits and underscores.")
    return f"[{value}]"


def _sql_string(value):
    return "N'" + str(value).replace("'", "''") + "'"


def _connect(url, database=None):
    return create_engine(url.set(database=database or url.database), future=True, pool_pre_ping=True)


def _execute_admin_command(conn, statement):
    cursor = conn.connection.connection.cursor()
    try:
        cursor.execute(statement)
        while True:
            if cursor.description is not None:
                cursor.fetchall()
            if not cursor.nextset():
                break
    finally:
        cursor.close()


def backup_database(database_url, backup_path, *, verify=True):
    """Create a copy-only native backup; optionally run VERIFYONLY as an authorized administrator."""
    url = _local_url(database_url)
    quoted_database = _quote_identifier(url.database)
    path = Path(backup_path).resolve()
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing backup: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = _connect(url)
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            _execute_admin_command(
                conn,
                f"BACKUP DATABASE {quoted_database} TO DISK = {_sql_string(path)} "
                "WITH COPY_ONLY, INIT, CHECKSUM"
            )
            if verify:
                _execute_admin_command(
                    conn,
                    f"RESTORE VERIFYONLY FROM DISK = {_sql_string(path)} WITH CHECKSUM"
                )
    except Exception:
        if path.exists():
            path.unlink()
        raise
    finally:
        engine.dispose()
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError("SQL Server reported a successful backup but the backup file is missing or empty.")
    return str(path)


def _database_file_paths(url):
    engine = _connect(url, "master")
    try:
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT CAST(SERVERPROPERTY('InstanceDefaultDataPath') AS nvarchar(4000)), "
                "CAST(SERVERPROPERTY('InstanceDefaultLogPath') AS nvarchar(4000))"
            )).one()
            data_dir, log_dir = row[0], row[1]
            if not data_dir or not log_dir:
                rows = conn.exec_driver_sql(
                    "SELECT type_desc, physical_name FROM sys.master_files "
                    "WHERE database_id = DB_ID('master') AND file_id IN (1, 2)"
                ).all()
                for file_type, physical_path in rows:
                    if file_type == "ROWS" and not data_dir:
                        data_dir = os.path.dirname(physical_path) + os.sep
                    elif file_type == "LOG" and not log_dir:
                        log_dir = os.path.dirname(physical_path) + os.sep
            if not data_dir or not log_dir:
                raise RuntimeError("Could not determine SQL Server's default data and log directories.")
            return data_dir, log_dir
    finally:
        engine.dispose()


def restore_test_database(database_url, backup_path, target_name):
    """Restore a verified backup into a new database and return table row counts."""
    url = _local_url(database_url)
    quoted_source = _quote_identifier(url.database)
    quoted_target = _quote_identifier(target_name)
    if target_name.lower() == url.database.lower():
        raise ValueError("The restore test target must not be the configured database.")
    path = Path(backup_path).resolve()
    if not path.is_file() or path.stat().st_size == 0:
        raise FileNotFoundError(f"SQL Server backup file not found or empty: {path}")
    data_dir, log_dir = _database_file_paths(url)
    engine = _connect(url, "master")
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            if conn.exec_driver_sql("SELECT DB_ID(?)", (target_name,)).scalar():
                raise RuntimeError(f"Restore target {target_name!r} already exists; choose a new empty database name.")
            _execute_admin_command(conn, f"RESTORE VERIFYONLY FROM DISK = {_sql_string(path)} WITH CHECKSUM")
            files = conn.exec_driver_sql(f"RESTORE FILELISTONLY FROM DISK = {_sql_string(path)}").all()
            moves = []
            data_index = log_index = 0
            for file in files:
                logical_name, file_type = file[0], file[2]
                if file_type == "D":
                    data_index += 1
                    suffix = "mdf" if data_index == 1 else "ndf"
                    dest_dir = data_dir
                    dest_name = f"{target_name}_data_{data_index}.{suffix}"
                elif file_type == "L":
                    log_index += 1
                    dest_dir = log_dir
                    dest_name = f"{target_name}_log_{log_index}.ldf"
                else:
                    raise RuntimeError(f"Unsupported SQL Server backup file type: {file_type!r}")
                destination = os.path.join(dest_dir, dest_name)
                moves.append(f"MOVE {_sql_string(logical_name)} TO {_sql_string(destination)}")
            if not moves:
                raise RuntimeError("The SQL Server backup contains no database files.")
            _execute_admin_command(
                conn,
                f"RESTORE DATABASE {quoted_target} FROM DISK = {_sql_string(path)} "
                f"WITH {', '.join(moves)}, RECOVERY, CHECKSUM"
            )
    finally:
        engine.dispose()

    restored_url = url.set(database=target_name)
    restored = _connect(restored_url)
    try:
        inspector = inspect(restored)
        tables = sorted(inspector.get_table_names(schema="dbo"))
        with restored.connect() as conn:
            counts = {
                table: conn.exec_driver_sql(f"SELECT COUNT(*) FROM [dbo].{_quote_identifier(table)}").scalar()
                for table in tables
            }
        return {"database": target_name, "tables": len(tables), "rows": counts}
    finally:
        restored.dispose()


def restore_live_database(database_url, backup_path, safety_backup_path):
    """Replace the configured database, keeping a verified copy of its pre-restore contents."""
    url = _local_url(database_url)
    quoted_database = _quote_identifier(url.database)
    path = Path(backup_path).resolve()
    if not path.is_file() or path.stat().st_size == 0:
        raise FileNotFoundError(f"SQL Server backup file not found or empty: {path}")
    safety_path = backup_database(database_url, safety_backup_path)
    engine = _connect(url, "master")
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            _execute_admin_command(conn, f"RESTORE VERIFYONLY FROM DISK = {_sql_string(path)} WITH CHECKSUM")
            conn.exec_driver_sql(f"ALTER DATABASE {quoted_database} SET SINGLE_USER WITH ROLLBACK IMMEDIATE")
            try:
                _execute_admin_command(
                    conn,
                    f"RESTORE DATABASE {quoted_database} FROM DISK = {_sql_string(path)} "
                    "WITH REPLACE, RECOVERY, CHECKSUM"
                )
            finally:
                conn.exec_driver_sql(f"ALTER DATABASE {quoted_database} SET MULTI_USER")
    finally:
        engine.dispose()
    return safety_path


def drop_database(database_url, target_name):
    """Drop a named restore-test database, refusing the configured database."""
    url = _local_url(database_url)
    if target_name.lower() == url.database.lower():
        raise ValueError("Refusing to drop the configured SQL Server database.")
    quoted_target = _quote_identifier(target_name)
    engine = _connect(url, "master")
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            if conn.exec_driver_sql("SELECT DB_ID(?)", (target_name,)).scalar():
                conn.exec_driver_sql(f"ALTER DATABASE {quoted_target} SET SINGLE_USER WITH ROLLBACK IMMEDIATE")
                conn.exec_driver_sql(f"DROP DATABASE {quoted_target}")
    finally:
        engine.dispose()
