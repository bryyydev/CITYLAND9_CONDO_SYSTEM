"""Schema Phase A (docs/schema-design.md section 4): integrity only, no behaviour change.

  * UNIQUE: one bill / water reading / parking bill per unit(lot) per month, one attendance
    row per employee per day, one application per advance per bill            (S6)
  * CHECK: 'YYYY-MM' months, non-negative amounts, coverage >= 1 month, leave dates in order
  * updated_at / updated_by on 16 financial & master tables, stamped by the app   (S12)
  * drop the never-used employee_payslip_item and employee_holiday tables         (S13)
  * delete the unused hr_* copies in `setting` (payroll reads employee_hr_setting) (S9)

Revision ID: 0002_phase_a
Revises: 0001_baseline
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0002_phase_a"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

TRACKED = ["user", "unit", "owner", "tenant", "parking_lot", "billing", "payment", "advance_payment",
           "water_reading", "employee", "expense", "setting", "resident_profile", "employee_payroll",
           "employee_hr_loan", "employee_hr_setting"]

UNIQUES = [
    ("uq_billing_unit_month", "billing", ["unit_id", "billing_month"]),
    ("uq_water_unit_month", "water_reading", ["unit_id", "reading_month"]),
    ("uq_parking_billing_lot_month", "parking_billing", ["parking_lot_id", "billing_month"]),
    ("uq_employee_attendance_day", "employee_attendance", ["employee_id", "attendance_date"]),
    ("uq_advance_application_advance_billing", "advance_application", ["advance_payment_id", "billing_id"]),
]


def month(col):
    return (f"{col} LIKE '____-__' AND SUBSTR({col}, 1, 4) BETWEEN '1900' AND '2999' "
            f"AND SUBSTR({col}, 6, 2) BETWEEN '01' AND '12'")


CHECKS = [
    ("ck_billing_month_format", "billing", month("billing_month")),
    ("ck_billing_amount_paid_nonneg", "billing", "amount_paid >= 0"),
    ("ck_billing_previous_balance_nonneg", "billing", "previous_balance >= 0"),
    ("ck_water_month_format", "water_reading", month("reading_month")),
    ("ck_parking_billing_month_format", "parking_billing", month("billing_month")),
    ("ck_advance_application_month_format", "advance_application", month("billing_month")),
    ("ck_advance_application_amount_nonneg", "advance_application", "amount >= 0"),
    ("ck_payment_amount_nonneg", "payment", "amount >= 0"),
    ("ck_advance_payment_month_format", "advance_payment", month("start_month")),
    ("ck_advance_payment_amount_nonneg", "advance_payment", "amount >= 0"),
    ("ck_advance_payment_coverage_min", "advance_payment", "coverage_months >= 1"),
    ("ck_employee_leave_dates", "employee_leave", "end_date >= start_date"),
]

TABLE_OPTS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_bin"}


def upgrade():
    for table in TRACKED:
        op.add_column(table, sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=True))
        op.add_column(table, sa.Column("updated_by", sa.String(80), nullable=True))
    for name, table, cols in UNIQUES:
        op.create_unique_constraint(name, table, cols)
    for name, table, condition in CHECKS:
        op.create_check_constraint(name, table, condition)
    op.drop_table("employee_payslip_item")
    op.drop_table("employee_holiday")
    op.execute(sa.text("DELETE FROM setting WHERE `key` LIKE :pattern").bindparams(pattern="hr\\_%"))


def downgrade():
    # The deleted hr_* copies are not restored: nothing reads them, and Rates & Rules
    # recreated them on every save before this revision.
    op.create_table(
        "employee_holiday",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("holiday_date", sa.Date, nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("holiday_type", sa.String(30)),
        sa.Column("active", sa.Boolean),
        **TABLE_OPTS,
    )
    op.create_table(
        "employee_payslip_item",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("payroll_id", sa.Integer, sa.ForeignKey("employee_payroll.id"), nullable=False),
        sa.Column("item_type", sa.String(30), nullable=False),
        sa.Column("description", sa.String(150), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2)),
        **TABLE_OPTS,
    )
    op.create_index("ix_employee_payslip_item_payroll_id", "employee_payslip_item", ["payroll_id"])
    for name, table, _ in reversed(CHECKS):
        op.drop_constraint(name, table, type_="check")
    for name, table, _ in reversed(UNIQUES):
        op.drop_constraint(name, table, type_="unique")
    for table in reversed(TRACKED):
        op.drop_column(table, "updated_by")
        op.drop_column(table, "updated_at")
