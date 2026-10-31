"""SOA issue snapshot: keep what was issued on each statement.

Until now an issued statement still depended on settings read when it was displayed: the penalty
rate, which charge types are penalty-eligible, and the storage cut-off month; and its water amount
followed the month's reading row. Changing those settings could change an old SOA without anyone
recalculating it.

Schema (additive; existing rows unchanged):
  billing.issued_at          when the bill was generated (marks bills that have a snapshot)
  billing.issued_amount      amount due as first issued (after advances), kept apart from later
                             payments and explicit restatements
  billing.penalty_rate       % applied to this statement
  billing.penalty_basis      eligible charge types, e.g. "condo"
  billing.storage_included   storage counted in this statement's total
  billing.dues_basis, condo_area, condo_rate,
  billing.parking_unit_no, parking_area, parking_rate,
  billing.storage_unit_no, storage_basis, storage_area, storage_rate
                             how the dues were priced (area x rate per sqm, or a manual amount)

No backfill: bills issued before this migration keep NULL. Their issue-time rules were never
recorded and are not invented; they keep following the current settings (documented limitation in
docs/module-migration-checklist.md, S2). Downgrade drops the columns.

Revision ID: 0010_soa_issue_snapshot
Revises: 0009_payment_integrity
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0010_soa_issue_snapshot"
down_revision = "0009_payment_integrity"
branch_labels = None
depends_on = None

COLUMNS = [
    ("issued_at", mysql.DATETIME(fsp=6)),
    ("issued_amount", sa.Numeric(12, 2)),
    ("penalty_rate", sa.Numeric(7, 4)),
    ("penalty_basis", sa.String(60)),
    ("storage_included", sa.Boolean),
    ("dues_basis", sa.String(10)),
    ("condo_area", sa.Numeric(10, 2)),
    ("condo_rate", sa.Numeric(12, 4)),
    ("parking_unit_no", sa.String(50)),
    ("parking_area", sa.Numeric(10, 2)),
    ("parking_rate", sa.Numeric(12, 4)),
    ("storage_unit_no", sa.String(50)),
    ("storage_basis", sa.String(10)),
    ("storage_area", sa.Numeric(10, 2)),
    ("storage_rate", sa.Numeric(12, 4)),
]


def upgrade():
    for name, type_ in COLUMNS:
        op.add_column("billing", sa.Column(name, type_, nullable=True))


def downgrade():
    for name, _type in reversed(COLUMNS):
        op.drop_column("billing", name)
