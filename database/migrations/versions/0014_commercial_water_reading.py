"""Shared monthly water readings for commercial types.

Revision ID: 0014_commercial_water_reading
Revises: 0013_water_reading_period
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0014_commercial_water_reading"
down_revision = "0013_water_reading_period"
branch_labels = None
depends_on = None

OPTS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_bin"}
DT = mysql.DATETIME(fsp=6)


def upgrade():
    op.create_table(
        "commercial_water_reading",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("commercial_type", sa.String(8), nullable=False),
        sa.Column("reading_month", sa.String(7), nullable=False),
        sa.Column("previous_reading", sa.Float, nullable=True),
        sa.Column("current_reading", sa.Float, nullable=True),
        sa.Column("rate", sa.Float, nullable=True),
        sa.Column("reading_date", sa.Date, nullable=True),
        sa.Column("updated_at", DT, nullable=True),
        sa.Column("updated_by", sa.String(80), nullable=True),
        sa.UniqueConstraint("commercial_type", "reading_month", name="uq_commercial_water_type_month"),
        sa.CheckConstraint("commercial_type IN ('2F', 'UG', 'LG')", name="ck_commercial_water_type"),
        sa.CheckConstraint(
            "reading_month LIKE '____-__' AND SUBSTRING(reading_month, 1, 4) BETWEEN '1900' AND '2999' "
            "AND SUBSTRING(reading_month, 6, 2) BETWEEN '01' AND '12'",
            name="ck_commercial_water_month_format",
        ),
        **OPTS,
    )
    op.create_index("ix_commercial_water_month", "commercial_water_reading", ["reading_month"])


def downgrade():
    op.drop_table("commercial_water_reading")
