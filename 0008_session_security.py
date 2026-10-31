"""Session security: session revocation, forced password change, encrypted SMTP password.

Schema (all additive, existing rows keep working):
  user.session_version       INT NOT NULL DEFAULT 0   raising it ends every existing session
  user.must_change_password  BOOL NOT NULL DEFAULT 0  new password required at next sign-in
  user.password_changed_at   DATETIME(6) NULL

Data (nothing is deleted; no password or secret is printed or logged):
  * accounts whose password is still the old automatic default ("admin123") are flagged
    must_change_password = 1 (their password itself is not changed)
  * a plain-text SMTP password stored in `setting` is encrypted with a key derived from
    SECRET_KEY (prefix "enc:v1:"). Requires SECRET_KEY in .env, which db_migrate.py loads.

Downgrade drops the three columns and decrypts the SMTP password back to plain text.

Revision ID: 0008_session_security
Revises: 0007_gate_pass_requests
"""
import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0008_session_security"
down_revision = "0007_gate_pass_requests"
branch_labels = None
depends_on = None


def _security():
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"))
    from app.core import security
    return security


def upgrade():
    op.add_column("user", sa.Column("session_version", sa.Integer, nullable=False, server_default="0"))
    op.add_column("user", sa.Column("must_change_password", sa.Boolean, nullable=False, server_default="0"))
    op.add_column("user", sa.Column("password_changed_at", mysql.DATETIME(fsp=6), nullable=True))

    from werkzeug.security import check_password_hash
    sec = _security()
    bind = op.get_bind()
    flagged = 0
    for uid, pw_hash in bind.execute(sa.text("SELECT id, password_hash FROM `user`")).fetchall():
        if any(check_password_hash(pw_hash, default) for default in sec.SHIPPED_DEFAULT_PASSWORDS):
            bind.execute(sa.text("UPDATE `user` SET must_change_password = 1 WHERE id = :id"), {"id": uid})
            flagged += 1
    if flagged:
        bind.execute(sa.text("INSERT INTO audit_log (username, action, created_at) VALUES ('system', :a, UTC_TIMESTAMP(6))"),
                     {"a": f"Migration 0008: {flagged} account(s) still using the old default password must change it at next sign-in"})

    row = bind.execute(sa.text("SELECT id, value FROM setting WHERE `key` = 'smtp_password'")).fetchone()
    if row and row.value and not row.value.startswith(sec.ENCRYPTED_PREFIX):
        key = os.getenv("SECRET_KEY", "")
        sec.check_secret_key(key)  # refuses to encrypt with a weak/placeholder key in production
        bind.execute(sa.text("UPDATE setting SET value = :v WHERE id = :id"), {"v": sec.encrypt_secret(row.value, key), "id": row.id})
        bind.execute(sa.text("INSERT INTO audit_log (username, action, created_at) VALUES ('system', :a, UTC_TIMESTAMP(6))"),
                     {"a": "Migration 0008: stored SMTP password encrypted"})


def downgrade():
    sec = _security()
    bind = op.get_bind()
    row = bind.execute(sa.text("SELECT id, value FROM setting WHERE `key` = 'smtp_password'")).fetchone()
    if row and row.value and row.value.startswith(sec.ENCRYPTED_PREFIX):
        plain = sec.decrypt_secret(row.value, os.getenv("SECRET_KEY", ""))
        bind.execute(sa.text("UPDATE setting SET value = :v WHERE id = :id"), {"v": plain, "id": row.id})
    op.drop_column("user", "password_changed_at")
    op.drop_column("user", "must_change_password")
    op.drop_column("user", "session_version")
