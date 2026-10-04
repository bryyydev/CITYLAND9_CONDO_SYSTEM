"""First-run setup on a new PC (scripts/first_run_setup.py): the generated .env, and that an existing
configuration is never overwritten. (The full launcher was verified on Windows with a fresh copy.)"""
import importlib.util
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("first_run_setup", os.path.join(ROOT, "scripts", "first_run_setup.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "ENV", str(tmp_path / ".env"))
    return mod


def test_env_has_a_fresh_secret_and_sqlite(tmp_path, monkeypatch):
    mod = load(tmp_path, monkeypatch)
    mod.write_env()
    text = (tmp_path / ".env").read_text(encoding="utf-8")
    secret = re.search(r"^SECRET_KEY=(.*)$", text, re.M).group(1)
    assert re.fullmatch(r"[0-9a-f]{64}", secret)                       # not the placeholder
    assert re.search(r"^DB_ENGINE=sqlite$", text, re.M) and "APP_ENV=production" in text
    mod.write_env()
    assert re.search(r"^SECRET_KEY=(.*)$", (tmp_path / ".env").read_text(encoding="utf-8"), re.M).group(1) != secret   # new per PC


def test_existing_configuration_is_kept(tmp_path, monkeypatch, capsys):
    mod = load(tmp_path, monkeypatch)
    (tmp_path / ".env").write_text("SECRET_KEY=keep-me\n", encoding="utf-8")
    assert mod.main() == 0
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "SECRET_KEY=keep-me\n"
    assert "already set up" in capsys.readouterr().out


def test_demo_password_meets_the_policy():
    from app.core import security
    assert security.password_problem("CityLand9-Demo", "demo_admin") is None
