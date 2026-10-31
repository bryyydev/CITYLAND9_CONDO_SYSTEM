"""Phase B1 (data): freeze issued bills and start charging storage from next month.

Client decisions 2026-09-30:
  * Q1  issued bills are frozen: condo dues / parking come from the amounts stored on the
        bill, no longer from today's rates.
  * D13 storage is added to SOA totals from the next billing month on; earlier bills keep
        their totals.

Until now the engine showed condo dues and parking computed from the unit's CURRENT rates
(stored amounts were ignored unless the SOA had been edited by hand). So that freezing
changes nothing anyone has already seen, this step writes exactly those currently shown
amounts into every bill that was not edited by hand. It also records the storage start month.

Downgrade removes the storage setting. The frozen amounts stay: they are the amounts that
were being displayed, so nothing changes by keeping them.

Revision ID: 0003_freeze_bills
Revises: 0002_phase_a
"""
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

revision = "0003_freeze_bills"
down_revision = "0002_phase_a"
branch_labels = None
depends_on = None


def next_month(today=None):
    today = today or date.today()
    return f"{today.year + (today.month == 12)}-{today.month % 12 + 1:02d}"


def upgrade():
    import legacy_app as m  # the app's own pricing functions, unchanged

    bind = op.get_bind()
    session = Session(bind=bind)
    frozen = changed = 0
    bills = session.query(m.Billing).filter(sa.or_(m.Billing.soa_manual_override.is_(False),
                                                   m.Billing.soa_manual_override.is_(None))).all()
    for bill in bills:
        condo = Decimal(str(m.unit_dues(bill.unit))).quantize(Decimal("0.01"))
        parking = Decimal(str(m.parking_dues(bill.unit))).quantize(Decimal("0.01"))
        if m.money(bill.assessment) != condo or m.money(bill.parking_dues) != parking:
            changed += 1
        bind.execute(sa.text("UPDATE billing SET assessment = :c, parking_dues = :p WHERE id = :id"),
                     {"c": condo, "p": parking, "id": bill.id})
        frozen += 1
    session.close()
    print(f"   froze {frozen} bill(s); {changed} had stored amounts that differed from what was displayed")

    exists = bind.execute(sa.text("SELECT COUNT(*) FROM setting WHERE `key` = 'storage_in_total_from'")).scalar()
    if not exists:
        bind.execute(sa.text("INSERT INTO setting (`key`, value) VALUES ('storage_in_total_from', :v)"), {"v": next_month()})
    start = bind.execute(sa.text("SELECT value FROM setting WHERE `key` = 'storage_in_total_from'")).scalar()
    print(f"   storage is included in SOA totals from {start}")


def downgrade():
    op.execute(sa.text("DELETE FROM setting WHERE `key` = 'storage_in_total_from'"))
