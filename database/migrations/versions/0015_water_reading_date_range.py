"""Store the date range covered by each water meter reading.

Revision ID: 0015_water_reading_date_range
Revises: 0014_commercial_water_reading
"""
import sqlalchemy as sa
from alembic import op

revision = "0015_water_reading_date_range"
down_revision = "0014_commercial_water_reading"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("water_reading", sa.Column("reading_period_start", sa.Date(), nullable=True))
    op.add_column("water_reading", sa.Column("reading_period_end", sa.Date(), nullable=True))
    op.add_column("commercial_water_reading", sa.Column("reading_period_start", sa.Date(), nullable=True))
    op.add_column("commercial_water_reading", sa.Column("reading_period_end", sa.Date(), nullable=True))


def downgrade():
    op.drop_column("commercial_water_reading", "reading_period_end")
    op.drop_column("commercial_water_reading", "reading_period_start")
    op.drop_column("water_reading", "reading_period_end")
    op.drop_column("water_reading", "reading_period_start")
