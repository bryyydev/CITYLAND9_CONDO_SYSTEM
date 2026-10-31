import pytest

from database import sqlserver


def test_sqlserver_url_is_restricted_to_local_instances():
    local = r"mssql+pyodbc://@localhost\SQLEXPRESS/CityLand9?driver=ODBC+Driver+18+for+SQL+Server&trusted_connection=yes"
    remote = "mssql+pyodbc://user:password@db.example.com/CityLand9?driver=ODBC+Driver+18+for+SQL+Server"

    assert sqlserver.supports_local_database(local)
    assert not sqlserver.supports_local_database(remote)
    assert not sqlserver.supports_local_database("sqlite:///cityland_condo_web.db")


@pytest.mark.parametrize("value", ["CityLand9", "CityLand9_Restore_Test", "db2026"])
def test_sqlserver_identifier_quotes_safe_names(value):
    assert sqlserver._quote_identifier(value) == f"[{value}]"


@pytest.mark.parametrize("value", ["CityLand9; DROP DATABASE master", "db-name", "", "x" * 129])
def test_sqlserver_identifier_rejects_unsafe_names(value):
    with pytest.raises(ValueError):
        sqlserver._quote_identifier(value)


def test_admin_commands_consume_all_result_sets():
    class Cursor:
        description = None

        def __init__(self):
            self.result_sets = [[("restore progress",)], []]
            self.index = -1
            self.fetched = []
            self.closed = False

        def execute(self, statement):
            self.statement = statement
            self.index = 0
            self.description = ("message",)

        def fetchall(self):
            self.fetched.append(self.index)
            return self.result_sets[self.index]

        def nextset(self):
            self.index += 1
            if self.index == len(self.result_sets):
                self.description = None
                return False
            self.description = ("message",)
            return True

        def close(self):
            self.closed = True

    class DBAPIConnection:
        def __init__(self):
            self.cursor_instance = Cursor()

        def cursor(self):
            return self.cursor_instance

    class Connection:
        def __init__(self):
            self.connection = type("ConnectionFairy", (), {"connection": DBAPIConnection()})()

    connection = Connection()
    sqlserver._execute_admin_command(connection, "RESTORE DATABASE [test]")

    cursor = connection.connection.connection.cursor_instance
    assert cursor.statement == "RESTORE DATABASE [test]"
    assert cursor.fetched == [0, 1]
    assert cursor.closed


def test_backup_can_skip_restore_verify_for_limited_backup_account(tmp_path, monkeypatch):
    backup_file = tmp_path / "cityland9.bak"
    commands = []

    class Connection:
        def execution_options(self, **kwargs):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Engine:
        def connect(self):
            return Connection()

        def dispose(self):
            pass

    def execute(conn, statement):
        commands.append(statement)
        if statement.startswith("BACKUP DATABASE"):
            backup_file.write_bytes(b"backup")

    monkeypatch.setattr(sqlserver, "_connect", lambda url: Engine())
    monkeypatch.setattr(sqlserver, "_execute_admin_command", execute)

    result = sqlserver.backup_database(
        r"mssql+pyodbc://@localhost\SQLEXPRESS/CityLand9?driver=ODBC+Driver+18+for+SQL+Server&trusted_connection=yes",
        backup_file,
        verify=False,
    )

    assert result == str(backup_file.resolve())
    assert len(commands) == 1
    assert "WITH COPY_ONLY, INIT, CHECKSUM" in commands[0]
    assert not any(command.startswith("RESTORE VERIFYONLY") for command in commands)
