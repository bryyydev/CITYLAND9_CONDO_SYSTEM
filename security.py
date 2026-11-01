"""Security policy shared by the classic screens, the API and the command-line tools.

    environment()             "production" (default) or "development" (APP_ENV=development)
    check_secret_key()        refuses missing / short / placeholder SECRET_KEY in production
    password_problem()        THE password policy (create, reset, change, CLI)
    LoginThrottle             bounded in-memory sign-in throttling
    session timing            idle and absolute expiry (SESSION_IDLE_MINUTES, SESSION_MAX_HOURS)
    encrypt_secret/decrypt_secret   settings secrets (SMTP password) encrypted at rest

Nothing here prints or logs a password or a secret value.
"""
import base64
import hashlib
import os
import threading
import time
from collections import OrderedDict

# ------------------------------------------------------------------ environment
DEVELOPMENT = "development"
PRODUCTION = "production"


def environment():
    """APP_ENV=development must be set explicitly; anything else is production."""
    return DEVELOPMENT if os.getenv("APP_ENV", "").strip().lower() == DEVELOPMENT else PRODUCTION


def is_development():
    return environment() == DEVELOPMENT


# ------------------------------------------------------------------ secret key
PLACEHOLDER_SECRETS = {
    "", "change-me", "changeme", "secret", "cityland9-v10-change-this-secret", "paste-a-generated-value-here",
    "paste-the-generated-value-here", "your-secret-key", "dev", "development",
}
MIN_SECRET_LENGTH = 32
DEV_FALLBACK_SECRET = "development-only-not-for-production-" + "0" * 32


class ConfigurationError(RuntimeError):
    """Raised at startup for unsafe configuration. The message never contains secret values."""


def check_secret_key(value):
    """Return the key to use. Production: refuse missing, short or placeholder keys."""
    key = (value or "").strip()
    weak = key.lower() in PLACEHOLDER_SECRETS or len(key) < MIN_SECRET_LENGTH or len(set(key)) < 8
    if not weak:
        return key
    if is_development():
        return key or DEV_FALLBACK_SECRET
    raise ConfigurationError(
        "SECRET_KEY in .env is missing, too short or a placeholder. Set a long random value "
        f"(at least {MIN_SECRET_LENGTH} characters), for example from:  "
        'python -c "import secrets; print(secrets.token_hex(32))"')


# ------------------------------------------------------------------ password policy
def _int_env(name, default, low, high):
    try:
        return max(low, min(high, int(os.getenv(name, default))))
    except ValueError:
        return default


PASSWORD_MIN_LENGTH = _int_env("PASSWORD_MIN_LENGTH", 10, 8, 64)
PASSWORD_MAX_LENGTH = 128
# Values that must never be (re)used: shipped defaults and the most common choices.
KNOWN_WEAK_PASSWORDS = {
    "admin123", "password", "password1", "password123", "passw0rd", "12345678", "123456789", "1234567890",
    "qwerty123", "iloveyou", "welcome1", "welcome123", "letmein123", "changeme", "change-me", "cityland9",
    "cityland9!", "cityland123", "demo@1234", "admin@123", "superadmin", "p@ssw0rd", "p@ssword1",
}
# Created automatically by earlier versions (superadmin / admin123). An account still using it
# must change its password at the next sign-in.
SHIPPED_DEFAULT_PASSWORDS = ("admin123",)


def password_policy():
    return {"minLength": PASSWORD_MIN_LENGTH, "maxLength": PASSWORD_MAX_LENGTH}


def password_problem(password, username=""):
    """The one password policy (create, reset, change, command line). None when acceptable."""
    password = password or ""
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"Use at least {PASSWORD_MIN_LENGTH} characters."
    if len(password) > PASSWORD_MAX_LENGTH:
        return f"Use at most {PASSWORD_MAX_LENGTH} characters."
    if password.lower() in KNOWN_WEAK_PASSWORDS:
        return "That password is too common. Choose a less predictable one."
    if len(set(password)) < 4:
        return "Use a less repetitive password."
    if username and len(username) >= 3 and username.lower() in password.lower():
        return "The password must not contain the username."
    return None


# ------------------------------------------------------------------ sign-in throttling
class LoginThrottle:
    """Sliding-window throttling of failed sign-ins, per (username, client) and per client.

    In memory and bounded (oldest keys are dropped beyond max_keys), which suits the
    deployment: one Waitress process on the office server. A restart clears it.
    """

    def __init__(self, per_account=5, per_client=30, window_seconds=900, max_keys=10_000):
        self.per_account, self.per_client, self.window = per_account, per_client, window_seconds
        self.max_keys = max_keys
        self._failures = OrderedDict()   # key -> [timestamps]
        self._lock = threading.Lock()

    def _recent(self, key, now):
        stamps = [t for t in self._failures.get(key, ()) if now - t < self.window]
        if stamps:
            self._failures[key] = stamps
            self._failures.move_to_end(key)
        else:
            self._failures.pop(key, None)
        return stamps

    def _keys(self, username, client):
        return (f"a:{(username or '').strip().lower()}|{client}", f"c:{client}")

    def retry_after(self, username, client, now=None):
        """Seconds until another attempt is allowed, or 0."""
        now = now or time.monotonic()
        account_key, client_key = self._keys(username, client)
        with self._lock:
            waits = []
            for key, limit in ((account_key, self.per_account), (client_key, self.per_client)):
                stamps = self._recent(key, now)
                if len(stamps) >= limit:
                    waits.append(self.window - (now - stamps[-limit]))
            return int(max(waits)) + 1 if waits else 0

    def failure(self, username, client, now=None):
        now = now or time.monotonic()
        with self._lock:
            for key in self._keys(username, client):
                self._failures.setdefault(key, []).append(now)
                self._failures.move_to_end(key)
            while len(self._failures) > self.max_keys:
                self._failures.popitem(last=False)

    def success(self, username, client):
        with self._lock:
            self._failures.pop(self._keys(username, client)[0], None)

    def clear(self):
        with self._lock:
            self._failures.clear()


login_throttle = LoginThrottle(
    per_account=_int_env("LOGIN_MAX_FAILURES", 5, 3, 50),
    window_seconds=_int_env("LOGIN_LOCKOUT_MINUTES", 15, 1, 1440) * 60,
)

# ------------------------------------------------------------------ session timing
SESSION_IDLE_SECONDS = _int_env("SESSION_IDLE_MINUTES", 60, 5, 24 * 60) * 60
SESSION_MAX_SECONDS = _int_env("SESSION_MAX_HOURS", 12, 1, 24 * 14) * 3600


def session_expired(issued_at, last_seen, now=None):
    """True when the session is past its absolute lifetime or has been idle too long."""
    now = now or time.time()
    try:
        issued_at, last_seen = float(issued_at), float(last_seen)
    except (TypeError, ValueError):
        return True
    return now - issued_at > SESSION_MAX_SECONDS or now - last_seen > SESSION_IDLE_SECONDS


# ------------------------------------------------------------------ settings secrets (SMTP password)
ENCRYPTED_PREFIX = "enc:v1:"


def _fernet(secret_key):
    from cryptography.fernet import Fernet
    digest = hashlib.sha256(b"cityland9/settings-secret/v1|" + secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext, secret_key):
    if not plaintext:
        return ""
    return ENCRYPTED_PREFIX + _fernet(secret_key).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(stored, secret_key):
    """Plaintext, or "" when empty / unreadable (e.g. SECRET_KEY changed). Legacy plaintext is returned as-is."""
    if not stored:
        return ""
    if not stored.startswith(ENCRYPTED_PREFIX):
        return stored
    from cryptography.fernet import InvalidToken
    try:
        return _fernet(secret_key).decrypt(stored[len(ENCRYPTED_PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""
