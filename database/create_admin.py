"""Create (or recover) a Superadmin account, on the server PC itself.

    .venv\\Scripts\\python.exe database\\create_admin.py

Replaces the old automatic "superadmin / admin123" account. Asks for a username and a password
(typed twice, never shown or printed), applies the same password policy as the application and
writes an audit-log entry. Use it:
  * once after a fresh installation (no account exists yet), or
  * to regain access when no Superadmin can sign in.
An existing username is never overwritten; to reset an existing Superadmin's password, pass
--reset <username> (its other sessions end and the account is re-activated).
"""
import getpass
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "database"))
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    pass

if os.getenv("DB_ENGINE", "").strip().lower() == "mysql" and os.getenv("MYSQL_DATA_DIR") and not os.getenv("DATABASE_URL"):
    import local_mysql
    local_mysql.start()

from app.core import security  # noqa: E402

try:
    import legacy_app as m  # noqa: E402
except security.ConfigurationError as exc:
    sys.exit(f"Configuration error: {exc}")


def ask_password(username):
    if not sys.stdin.isatty():   # piped input (scripted installation): one line, checked the same way
        password = sys.stdin.readline().rstrip("\r\n")
        problem = security.password_problem(password, username)
        if problem:
            sys.exit(f"{problem} No account was changed.")
        return password
    for _ in range(3):
        first = getpass.getpass("Password (not shown): ")
        problem = security.password_problem(first, username)
        if problem:
            print(f"  {problem}")
            continue
        if getpass.getpass("Type it again: ") != first:
            print("  The passwords don't match.")
            continue
        return first
    sys.exit("No account was changed.")


def main():
    reset = None
    if len(sys.argv) == 3 and sys.argv[1] == "--reset":
        reset = sys.argv[2]
    elif len(sys.argv) != 1:
        sys.exit(__doc__)
    with m.app.app_context():
        if reset:
            user = m.User.query.filter_by(username=reset, role="super_admin").first()
            if not user:
                sys.exit(f"No Superadmin account named {reset!r}.")
            m.set_password(user, ask_password(user.username), temporary=False)
            user.active = True
            m.db.session.add(m.AuditLog(username="system", action=f"Superadmin password reset from the server console for {user.username}"))
            m.db.session.commit()
            print(f"Password set for Superadmin {user.username}. Their other sessions were ended.")
            return
        existing = m.User.query.filter_by(role="super_admin", active=True).count()
        if existing:
            print(f"Note: {existing} active Superadmin account(s) already exist. Creating another one.")
        username = input("New Superadmin username: ").strip()
        if not m.USERNAME_PATTERN.match(username):
            sys.exit("Use 3-80 letters, numbers, dots, dashes or underscores. No account was created.")
        if m.User.query.filter(m.func.lower(m.User.username) == username.lower()).first():
            sys.exit("That username already exists. No account was changed.")
        user = m.User(username=username, role="super_admin", active=True, password_hash="!")
        m.set_password(user, ask_password(username), temporary=False)
        m.db.session.add(user)
        m.db.session.add(m.AuditLog(username="system", action=f"Superadmin {username} created from the server console"))
        m.db.session.commit()
        print(f"Superadmin {username} created. Sign in at http://<this PC>:5000")


if __name__ == "__main__":
    main()
