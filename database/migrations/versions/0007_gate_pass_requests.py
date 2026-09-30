"""Resident Portal: residents request gate passes and move-in / move-out passes online.

Adds to `gate_pass`: the pass type, a link to the unit, who requested it and when, and
who approved / rejected it with a note. All new columns allow NULL, so passes recorded
before this revision stay exactly as they are (shown as type "Visitor").

Backfill: an existing pass whose free-text unit number matches a unit exactly gets that
unit's id in the new `unit_id` column. No existing value is changed.

Downgrade drops the new columns again (the passes themselves are kept).

Revision ID: 0007_gate_pass_requests
Revises: 0006_unit_copies
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0007_gate_pass_requests"
down_revision = "0006_unit_copies"
branch_labels = None
depends_on = None

NEW_COLUMNS = ["pass_type", "unit_id", "requested_by", "requested_at", "reviewed_by", "reviewed_at", "review_note"]


def upgrade():
    op.add_column("gate_pass", sa.Column("pass_type", sa.String(20)))
    op.add_column("gate_pass", sa.Column("unit_id", sa.Integer))
    op.add_column("gate_pass", sa.Column("requested_by", sa.String(80)))
    op.add_column("gate_pass", sa.Column("requested_at", mysql.DATETIME(fsp=6)))
    op.add_column("gate_pass", sa.Column("reviewed_by", sa.String(80)))
    op.add_column("gate_pass", sa.Column("reviewed_at", mysql.DATETIME(fsp=6)))
    op.add_column("gate_pass", sa.Column("review_note", sa.String(300)))
    op.create_index("ix_gate_pass_unit_id", "gate_pass", ["unit_id"])
    op.create_foreign_key("fk_gate_pass_unit_id", "gate_pass", "unit", ["unit_id"], ["id"])
    op.execute("UPDATE gate_pass g JOIN unit u ON u.unit_no = g.unit_no SET g.unit_id = u.id WHERE g.unit_id IS NULL")


def downgrade():
    op.drop_constraint("fk_gate_pass_unit_id", "gate_pass", type_="foreignkey")
    op.drop_index("ix_gate_pass_unit_id", "gate_pass")
    for name in NEW_COLUMNS:
        op.drop_column("gate_pass", name)
