"""Checks for the local-MySQL migration tooling in database/tools/.

These run without a MySQL server. The MySQL schema test itself needs a server and
is run by hand (see docs/migration.md, section 7).
"""
import os
import subprocess
import sys

from conftest import _ROOT, _TMP_DIR

TOOLS = os.path.join(_ROOT, "database", "tools")


def run_tool(*args):
    return subprocess.run([sys.executable, *args], cwd=_ROOT, capture_output=True, text=True, timeout=120)


def test_schema_sql_matches_models():
    result = run_tool(os.path.join(TOOLS, "generate_schema.py"), "--check")
    assert result.returncode == 0, result.stdout + result.stderr


def test_preflight_passes_on_seeded_test_database(app_module):
    result = run_tool(os.path.join(TOOLS, "sqlite_preflight.py"), os.path.join(_TMP_DIR, "test.db"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "BLOCKING (0)" in result.stdout
