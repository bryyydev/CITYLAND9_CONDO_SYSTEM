"""Data migration from another system: stable source identity and an external-ID crosswalk.

Schema (additive, empty after the upgrade; nothing reads or writes these tables during normal use):
  import_source     one row per source SYSTEM (not per file): a stable code chosen once, e.g.
                    "legacy-condo-2026". Re-exports of the same system keep the same code.
  import_batch      provenance of one import run: file name, SHA-256, sheet summary, who/when.
                    Batches never identify records; they only record where a mapping came from.
  import_crosswalk  (source, entity type, external id) -> the CITYLAND9 record it became.
                      UNIQUE (source_id, entity_type, external_id)  one external record -> one target
                      UNIQUE (source_id, entity_type, target_id)    one target <- one external record
                    External ids are text and are NEVER used as CITYLAND9 primary keys.

No importer writes to these tables yet (CITYLAND9_IMPORT_SAFEGUARDS.md). The dry-run validator
(backend/app/services/import_check.py) only reads them to detect conflicting mappings.

Downgrade drops the three tables (any recorded mappings would be lost).

Revision ID: 0012_import_crosswalk
Revises: 0011_resident_provisioning
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0012_import_crosswalk"
down_revision = "0011_resident_provisioning"
branch_labels = None
depends_on = None

OPTS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_bin"}
DT = mysql.DATETIME(fsp=6)


def upgrade():
    op.create_table(
        "import_source",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(500)),
        sa.Column("created_at", DT),
        sa.Column("created_by", sa.String(80)),
        sa.UniqueConstraint("code", name="uq_import_source_code"),
        **OPTS,
    )
    op.create_table(
        "import_batch",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source_id", sa.Integer, sa.ForeignKey("import_source.id"), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("sheet_summary", sa.Text),
        sa.Column("status", sa.String(20), nullable=False, server_default="started"),
        sa.Column("started_at", DT),
        sa.Column("finished_at", DT),
        sa.Column("created_by", sa.String(80)),
        **OPTS,
    )
    op.create_index("ix_import_batch_source", "import_batch", ["source_id"])
    op.create_table(
        "import_crosswalk",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source_id", sa.Integer, sa.ForeignKey("import_source.id"), nullable=False),
        sa.Column("entity_type", sa.String(30), nullable=False),
        sa.Column("external_id", sa.String(100), nullable=False),
        sa.Column("target_id", sa.Integer, nullable=False),
        sa.Column("first_batch_id", sa.Integer, sa.ForeignKey("import_batch.id")),
        sa.Column("last_batch_id", sa.Integer, sa.ForeignKey("import_batch.id")),
        sa.Column("source_sheet", sa.String(60)),
        sa.Column("source_row", sa.Integer),
        sa.Column("created_at", DT),
        sa.Column("updated_at", DT),
        sa.UniqueConstraint("source_id", "entity_type", "external_id", name="uq_import_crosswalk_external"),
        sa.UniqueConstraint("source_id", "entity_type", "target_id", name="uq_import_crosswalk_target"),
        **OPTS,
    )
    op.create_index("ix_import_crosswalk_target", "import_crosswalk", ["entity_type", "target_id"])


def downgrade():
    # Dropping a table removes its indexes and foreign keys (MariaDB refuses to drop an index a
    # foreign key still needs, error 1553).
    op.drop_table("import_crosswalk")
    op.drop_table("import_batch")
    op.drop_table("import_source")
