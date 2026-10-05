"""Resident portal provisioning: activation-code expiry and one account for several units.

Schema (additive):
  user.temp_password_expires_at   expiry of a system-generated activation code (a one-time temporary
                                  password: the existing mechanism). NULL = no expiry (every
                                  existing account, and staff temporary passwords).
  resident_unit_link              one row per (resident account, unit) the account may open:
                                    user_id, unit_id, person_type, person_id   the owner/tenant record
                                    active, active_key                          active_key = "Owner:12"
                                      while active (NULL otherwise); UNIQUE, so one owner/tenant
                                      record can have only one active portal account, even under
                                      concurrent requests
                                    created_at/by, ended_at/by, end_reason      history (never deleted)

Data: every existing resident profile becomes its account's first link (same unit, same owner/
tenant record, same active state). Nothing else changes: profiles, passwords and sessions are kept.
When two ACTIVE profiles point at the same owner/tenant record (the old screens could allow that),
the older keeps the unique key and the others are still copied, active, without it (reported).

Downgrade drops the table and the column (links added to a second unit would be lost).

Revision ID: 0011_resident_provisioning
Revises: 0010_soa_issue_snapshot
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0011_resident_provisioning"
down_revision = "0010_soa_issue_snapshot"
branch_labels = None
depends_on = None

OPTS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_bin"}
DT = mysql.DATETIME(fsp=6)


def upgrade():
    op.add_column("user", sa.Column("temp_password_expires_at", DT, nullable=True))
    op.create_table(
        "resident_unit_link",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column("unit_id", sa.Integer, sa.ForeignKey("unit.id"), nullable=False),
        sa.Column("person_type", sa.String(20), nullable=False),
        sa.Column("person_id", sa.Integer),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("1")),
        sa.Column("active_key", sa.String(40)),
        sa.Column("created_at", DT),
        sa.Column("created_by", sa.String(80)),
        sa.Column("ended_at", DT),
        sa.Column("ended_by", sa.String(80)),
        sa.Column("end_reason", sa.String(300)),
        sa.UniqueConstraint("active_key", name="uq_resident_unit_link_active_key"),
        **OPTS,
    )
    op.create_index("ix_resident_unit_link_user", "resident_unit_link", ["user_id"])
    op.create_index("ix_resident_unit_link_unit", "resident_unit_link", ["unit_id"])

    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT p.user_id, p.unit_id, p.person_type, p.person_id, p.active, u.active, p.created_at "
        "FROM resident_profile p JOIN `user` u ON u.id = p.user_id ORDER BY p.created_at, p.id")).fetchall()
    keys, shared = set(), 0
    for user_id, unit_id, person_type, person_id, p_active, u_active, created in rows:
        active = bool(p_active)
        key = f"{person_type or 'Owner'}:{person_id}" if (active and person_id) else None
        if key in keys:
            key, shared = None, shared + 1
        if key:
            keys.add(key)
        bind.execute(sa.text(
            "INSERT INTO resident_unit_link (user_id, unit_id, person_type, person_id, active, active_key, created_at, created_by) "
            "VALUES (:u, :unit, :pt, :pid, :a, :k, :c, 'migration 0011')"),
            {"u": user_id, "unit": unit_id, "pt": person_type or "Owner", "pid": person_id, "a": active, "k": key, "c": created})
    print(f"   copied {len(rows)} resident profile link(s)" + (f"; {shared} shared an owner/tenant record (kept, review them)" if shared else ""))


def downgrade():
    op.drop_index("ix_resident_unit_link_unit", table_name="resident_unit_link")
    op.drop_index("ix_resident_unit_link_user", table_name="resident_unit_link")
    op.drop_table("resident_unit_link")
    op.drop_column("user", "temp_password_expires_at")
