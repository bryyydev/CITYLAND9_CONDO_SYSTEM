"""Payment integrity: receipt counter, duplicate-submission guard, voids/reversals, structured audit.

Schema (additive; existing rows unchanged):
  receipt_counter(year PK, last_seq)        one row per year, locked while issuing a receipt number.
                                            Backfilled from the existing receipts (no number changes).
  form_submission(token PK, action, user_id, created_at)
                                            one row per submitted payment form; a second submission of
                                            the same form is refused (double click, refresh, retry).
  receipt.voided_at / voided_by / void_reason      a voided receipt keeps its number and its row
  payment.reversed_at                       the original payment row is kept, marked reversed
  advance_payment.reversed_at               likewise for advance payments
  audit_log.entity_type / entity_id / reason / details    structured audit for financial events
                                            (+ index on entity_type, entity_id)

Downgrade drops the added tables/columns (voids recorded in the meantime would be lost; the
downgrade refuses to run while any receipt is voided).

Revision ID: 0009_payment_integrity
Revises: 0008_session_security
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0009_payment_integrity"
down_revision = "0008_session_security"
branch_labels = None
depends_on = None

OPTS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_bin"}
DT = mysql.DATETIME(fsp=6)


def upgrade():
    op.create_table(
        "receipt_counter",
        sa.Column("year", sa.Integer, primary_key=True, autoincrement=False),
        sa.Column("last_seq", sa.Integer, nullable=False),
        **OPTS,
    )
    op.execute("INSERT INTO receipt_counter (year, last_seq) SELECT receipt_year, MAX(receipt_seq) FROM receipt GROUP BY receipt_year")
    op.create_table(
        "form_submission",
        sa.Column("token", sa.String(64), primary_key=True),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("user_id", sa.Integer),
        sa.Column("created_at", DT),
        **OPTS,
    )
    op.add_column("receipt", sa.Column("voided_at", DT))
    op.add_column("receipt", sa.Column("voided_by", sa.String(80)))
    op.add_column("receipt", sa.Column("void_reason", sa.String(500)))
    op.add_column("payment", sa.Column("reversed_at", DT))
    op.add_column("advance_payment", sa.Column("reversed_at", DT))
    op.add_column("audit_log", sa.Column("entity_type", sa.String(40)))
    op.add_column("audit_log", sa.Column("entity_id", sa.Integer))
    op.add_column("audit_log", sa.Column("reason", sa.String(500)))
    op.add_column("audit_log", sa.Column("details", sa.Text))
    op.create_index("ix_audit_log_entity", "audit_log", ["entity_type", "entity_id"])


def downgrade():
    voided = op.get_bind().execute(sa.text("SELECT COUNT(*) FROM receipt WHERE voided_at IS NOT NULL")).scalar()
    if voided:
        raise RuntimeError(f"{voided} receipt(s) are voided; downgrading would lose that history. Restore a backup instead.")
    op.drop_index("ix_audit_log_entity", "audit_log")
    for table, column in (("audit_log", "details"), ("audit_log", "reason"), ("audit_log", "entity_id"), ("audit_log", "entity_type"),
                          ("advance_payment", "reversed_at"), ("payment", "reversed_at"),
                          ("receipt", "void_reason"), ("receipt", "voided_by"), ("receipt", "voided_at")):
        op.drop_column(table, column)
    op.drop_table("form_submission")
    op.drop_table("receipt_counter")
