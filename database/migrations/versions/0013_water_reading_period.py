"""Record which month-end water reading was billed on each SOA.

Revision ID: 0013_water_reading_period
Revises: 0012_import_crosswalk
"""
import sqlalchemy as sa
from alembic import op

revision = "0013_water_reading_period"
down_revision = "0012_import_crosswalk"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("billing", sa.Column("water_reading_month", sa.String(7), nullable=True))
    # Existing bills used same-month readings; preserve their sources and amounts.
    op.execute("""
        UPDATE billing AS b
        JOIN water_reading AS w ON w.unit_id = b.unit_id AND w.reading_month = b.billing_month
        SET b.water_reading_month = b.billing_month
        WHERE b.water_reading_month IS NULL
    """)


def downgrade():
    op.drop_column("billing", "water_reading_month")