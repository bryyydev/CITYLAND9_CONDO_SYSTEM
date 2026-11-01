import pytest


@pytest.mark.parametrize(
    ("dialect", "expected"),
    [("sqlite", False), ("mysql", True), ("mssql", True)],
)
def test_schema_managed(app_module, monkeypatch, dialect, expected):
    with app_module.app.app_context():
        monkeypatch.setattr(app_module.db.engine.dialect, "name", dialect)
        assert app_module.schema_managed() is expected
