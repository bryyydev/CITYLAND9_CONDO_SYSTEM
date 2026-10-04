"""First-run setup on a new PC (run by START_WINDOWS.bat when there is no .env yet).

    .venv\\Scripts\\python.exe scripts\\first_run_setup.py

Makes a downloaded copy of CityLand 9 runnable without any manual database work:
  1. creates .env from .env.example with a new random SECRET_KEY and DB_ENGINE=sqlite
     (a single database file, cityland_condo_web.db, in this folder: nothing else to install)
  2. creates the database tables and loads the demo data (TEST-* units, owners, tenants, bills,
     water readings: the same sample set as the development PC)
  3. creates one demo account per role (password shown at the end) and links the demo resident
     to unit TEST-502
  4. asks for the Superadmin username and password (typed twice, not shown)
Nothing is overwritten: an existing .env stops the script, and existing accounts/units are kept.
For the client's real server use CityLand's own MariaDB instead (docs/run-guide/02-DATABASE_SETUP.md).
Everything stays on this PC; no cloud service is used.
"""
import getpass
import os
import re
import secrets
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = os.path.join(ROOT, ".env")
EXAMPLE = os.path.join(ROOT, ".env.example")
DEMO_PASSWORD = "CityLand9-Demo"
DEMO_ACCOUNTS = [("demo_admin", "admin"), ("demo_manager", "manager"), ("demo_staff", "staff"),
                 ("demo_accounting", "accounting"), ("demo_resident", "resident")]


def write_env():
    with open(EXAMPLE, encoding="utf-8") as fh:
        text = fh.read()
    text = re.sub(r"^SECRET_KEY=.*$", f"SECRET_KEY={secrets.token_hex(32)}", text, count=1, flags=re.M)
    text = re.sub(r"^DB_ENGINE=.*$", "DB_ENGINE=sqlite", text, count=1, flags=re.M)
    text = ("# Created automatically by scripts/first_run_setup.py for this PC (SQLite database file).\n"
            "# To use CityLand's own MariaDB instead, see docs/run-guide/02-DATABASE_SETUP.md.\n\n" + text)
    with open(ENV, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def ask_superadmin(m):
    later = "  Create the Superadmin later with:  .venv\\Scripts\\python.exe database\\create_admin.py"
    if not sys.stdin.isatty():
        print("  (No keyboard input.)\n" + later)
        return None
    try:
        return _ask_superadmin(m)
    except (EOFError, KeyboardInterrupt):
        # Optional step: the rest of the setup is kept; the demo accounts work already.
        m.db.session.rollback()
        print("\n  Superadmin not created (no input).\n" + later)
        return None


def _ask_superadmin(m):
    print()
    print("  Create the Superadmin account (full access to the system).")
    while True:
        username = input("  Superadmin username [superadmin]: ").strip() or "superadmin"
        if not m.USERNAME_PATTERN.match(username):
            print("  Use 3-80 letters, numbers, dots, dashes or underscores.")
            continue
        if m.User.query.filter(m.func.lower(m.User.username) == username.lower()).first():
            print("  That username already exists; choose another.")
            continue
        break
    while True:
        first = getpass.getpass(f"  Password for {username} (at least {m.security.PASSWORD_MIN_LENGTH} characters, not shown): ")
        problem = m.security.password_problem(first, username)
        if problem:
            print(f"  {problem}")
            continue
        if getpass.getpass("  Type it again: ") != first:
            print("  The two passwords are different. Try again.")
            continue
        break
    user = m.User(username=username, role="super_admin", active=True, password_hash="!")
    m.set_password(user, first, temporary=False)
    m.db.session.add(user)
    m.db.session.add(m.AuditLog(username="system", action=f"Superadmin {username} created by first-run setup"))
    m.db.session.commit()
    return username


def main():
    if os.path.exists(ENV):
        print(".env already exists: this PC is already set up. Nothing was changed.")
        return 0
    if not os.path.exists(EXAMPLE):
        print("[X] .env.example is missing from this folder, so the configuration can't be created.")
        return 1
    print("  First run on this PC: setting up CityLand 9...")
    write_env()
    print("  [1/4] Configuration created (.env, new secret key, SQLite database).")
    try:
        from dotenv import load_dotenv
        load_dotenv(ENV, override=True)
        sys.path.insert(0, os.path.join(ROOT, "backend"))
        sys.path.insert(0, ROOT)
        import legacy_app as m
        m.init_db()
        print("  [2/4] Database created (cityland_condo_web.db).")
        import seed_test_data
        seed_test_data.add_if_missing()
        print("  [3/4] Demo data loaded (TEST-501 to TEST-504, parking and storage units, bills, water readings).")
        with m.app.app_context():
            for username, role in DEMO_ACCOUNTS:
                if m.User.query.filter_by(username=username).first():
                    continue
                user = m.User(username=username, role=role, active=True, password_hash="!")
                m.set_password(user, DEMO_PASSWORD, temporary=False)
                m.db.session.add(user)
                m.db.session.flush()
                if role == "resident":
                    unit = m.Unit.query.filter_by(unit_no="TEST-502").first()
                    tenant = m.Tenant.query.filter_by(unit_id=unit.id, status="Current").first() if unit else None
                    if unit:
                        m.db.session.add(m.ResidentProfile(user_id=user.id, unit_id=unit.id, person_type="Tenant" if tenant else "Owner",
                                                           person_id=tenant.id if tenant else None,
                                                           display_name=tenant.tenant_name if tenant else "Demo Resident"))
            m.db.session.add(m.AuditLog(username="system", action="Demo accounts created by first-run setup"))
            m.db.session.commit()
            print(f"  [4/4] Demo accounts: {', '.join(u for u, _ in DEMO_ACCOUNTS)}  (password: {DEMO_PASSWORD})")
            admin = ask_superadmin(m)
    except Exception as exc:
        # Leave no half-made configuration behind: the next start runs the setup again.
        try:
            os.remove(ENV)
        except OSError:
            pass
        print(f"[X] First-run setup failed: {exc}")
        print("    Nothing needs cleaning up; fix the problem above and run START_WINDOWS.bat again.")
        return 1
    print()
    print("  Setup complete.")
    if admin:
        print(f"  Sign in as {admin} with the password you just chose,")
    print(f"  or with a demo account (e.g. demo_admin / {DEMO_PASSWORD}).")
    print("  Demo accounts are for testing only: change or delete them before real use.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
