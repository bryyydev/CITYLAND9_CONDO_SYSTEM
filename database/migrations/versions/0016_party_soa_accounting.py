"""Add payer-specific SOA responsibility and party ledger data.

Revision ID: 0016_party_soa_accounting
Revises: 0015_water_reading_date_range
"""
import sqlalchemy as sa
from alembic import op

revision = "0016_party_soa_accounting"
down_revision = "0015_water_reading_date_range"
branch_labels = None
depends_on = None

OPTS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_bin"}


def upgrade():
    for column in (
        "condo_charge_payer",
        "water_charge_payer",
        "parking_charge_payer",
        "storage_charge_payer",
        "other_charge_payer",
    ):
        op.add_column("unit", sa.Column(column, sa.String(10), nullable=False, server_default="Owner"))
    op.execute(
        "UPDATE unit SET condo_charge_payer = CASE WHEN occupancy_type = 'Tenant' THEN 'Tenant' ELSE 'Owner' END, "
        "water_charge_payer = CASE WHEN occupancy_type = 'Tenant' THEN 'Tenant' ELSE 'Owner' END, "
        "parking_charge_payer = CASE WHEN occupancy_type = 'Tenant' THEN 'Tenant' ELSE 'Owner' END, "
        "storage_charge_payer = CASE WHEN occupancy_type = 'Tenant' THEN 'Tenant' ELSE 'Owner' END, "
        "other_charge_payer = CASE WHEN occupancy_type = 'Tenant' THEN 'Tenant' ELSE 'Owner' END"
    )
    for column in (
        "condo_charge_payer",
        "water_charge_payer",
        "parking_charge_payer",
        "storage_charge_payer",
        "other_charge_payer",
    ):
        op.add_column("billing", sa.Column(column, sa.String(10), nullable=True))
    for table, column in (
        ("payment", "payer_type"),
        ("advance_payment", "payer_type"),
        ("water_reading", "charge_payer"),
        ("receipt_allocation", "payer_type"),
    ):
        op.add_column(table, sa.Column(column, sa.String(10), nullable=True))
    for column in (
        "condo_charge_payer",
        "water_charge_payer",
        "parking_charge_payer",
        "storage_charge_payer",
        "other_charge_payer",
    ):
        op.execute(
            f"UPDATE billing SET {column} = "
            f"(SELECT unit.{column} FROM unit WHERE unit.id = billing.unit_id)"
        )
    op.execute(
        "UPDATE advance_payment SET payer_type = 'Tenant' "
        "WHERE payment_date < '2026-10-01' AND reversed_at IS NULL"
    )
    op.execute(
        "UPDATE water_reading SET charge_payer = "
        "(SELECT unit.water_charge_payer FROM unit WHERE unit.id = water_reading.unit_id)"
    )
    for kind, table, foreign_key, column in (
        ("bill", "payment", "payment_id", "payer_type"),
        ("water", "water_reading", "water_reading_id", "charge_payer"),
        ("advance", "advance_payment", "advance_payment_id", "payer_type"),
    ):
        op.execute(
            f"UPDATE receipt_allocation SET payer_type = "
            f"(SELECT {column} FROM {table} WHERE {table}.id = receipt_allocation.{foreign_key}) "
            f"WHERE kind = '{kind}' AND payer_type IS NULL"
        )

    op.create_table(
        "billing_extra_charge",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("billing_id", sa.Integer(), sa.ForeignKey("billing.id"), nullable=False),
        sa.Column("description", sa.String(100), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("payer_type", sa.String(10), nullable=False),
        sa.CheckConstraint("amount >= 0", name="ck_billing_extra_charge_amount_nonneg"),
        sa.CheckConstraint("payer_type IN ('Owner', 'Tenant')", name="ck_billing_extra_charge_payer"),
        **OPTS,
    )
    op.create_index("ix_billing_extra_charge_billing_id", "billing_extra_charge", ["billing_id"])
    op.create_table(
        "party_opening_balance",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("unit_id", sa.Integer(), sa.ForeignKey("unit.id"), nullable=False),
        sa.Column("payer_type", sa.String(10), nullable=False),
        sa.Column("as_of_month", sa.String(7), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.CheckConstraint("payer_type IN ('Owner', 'Tenant')", name="ck_party_opening_balance_payer"),
        sa.UniqueConstraint("unit_id", "payer_type", "as_of_month", name="uq_party_opening_balance"),
        **OPTS,
    )
    op.create_index("ix_party_opening_balance_unit_id", "party_opening_balance", ["unit_id"])


def downgrade():
    op.drop_table("party_opening_balance")
    op.drop_table("billing_extra_charge")
    for table, column in (
        ("receipt_allocation", "payer_type"),
        ("water_reading", "charge_payer"),
        ("advance_payment", "payer_type"),
        ("payment", "payer_type"),
        ("billing", "other_charge_payer"),
        ("billing", "storage_charge_payer"),
        ("billing", "parking_charge_payer"),
        ("billing", "water_charge_payer"),
        ("billing", "condo_charge_payer"),
        ("unit", "other_charge_payer"),
        ("unit", "storage_charge_payer"),
        ("unit", "parking_charge_payer"),
        ("unit", "water_charge_payer"),
        ("unit", "condo_charge_payer"),
    ):
        op.drop_column(table, column)
