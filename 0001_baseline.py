"""Baseline: the schema created by database/schema.sql as of commit 22ba8ae
(31 tables, 26 foreign keys, role ENUM). Existing databases are stamped with this
revision; nothing is changed.

Revision ID: 0001_baseline
Revises:
"""

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
