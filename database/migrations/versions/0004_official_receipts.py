"""Phase B2: one payments ledger with automatic official-receipt (OR) numbers.

Adds `receipt` and `receipt_allocation`. From now on every payment recorded at the office
(bill payment, water payment, advance payment) gets an OR number. The existing payment
records are kept exactly as they are: balances are computed as before.

Backfill: every payment recorded before this revision gets a receipt (source='backfill'),
numbered per year in date order. A bill payment and the automatic advance created from its
excess share one receipt. Existing rows are only read, never changed. The step checks that
receipts add up exactly to the money already recorded.

Revision ID: 0004_receipts
Revises: 0003_freeze_bills
"""
import re
from decimal import Decimal

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session

revision = "0004_receipts"
down_revision = "0003_freeze_bills"
branch_labels = None
depends_on = None

OPTS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_bin"}


def upgrade():
    op.create_table(
        "receipt",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("receipt_year", sa.Integer, nullable=False),
        sa.Column("receipt_seq", sa.Integer, nullable=False),
        sa.Column("receipt_no", sa.String(20), nullable=False, unique=True),
        sa.Column("unit_id", sa.Integer, sa.ForeignKey("unit.id"), nullable=False),
        sa.Column("received_date", sa.Date, nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("payment_method", sa.String(20), nullable=False),
        sa.Column("reference", sa.String(100)),
        sa.Column("remarks", sa.String(300)),
        sa.Column("received_by", sa.String(80)),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6)),
        sa.UniqueConstraint("receipt_year", "receipt_seq", name="uq_receipt_year_seq"),
        sa.CheckConstraint("amount > 0", name="ck_receipt_amount_positive"),
        **OPTS,
    )
    op.create_index("ix_receipt_unit_id", "receipt", ["unit_id"])
    op.create_table(
        "receipt_allocation",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("receipt_id", sa.Integer, sa.ForeignKey("receipt.id"), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("payment_id", sa.Integer, sa.ForeignKey("payment.id")),
        sa.Column("water_reading_id", sa.Integer, sa.ForeignKey("water_reading.id")),
        sa.Column("advance_payment_id", sa.Integer, sa.ForeignKey("advance_payment.id")),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.CheckConstraint("kind IN ('bill', 'water', 'advance')", name="ck_receipt_allocation_kind"),
        sa.CheckConstraint("amount > 0", name="ck_receipt_allocation_amount_positive"),
        sa.CheckConstraint("(kind = 'bill' AND payment_id IS NOT NULL) OR (kind = 'water' AND water_reading_id IS NOT NULL) "
                           "OR (kind = 'advance' AND advance_payment_id IS NOT NULL)", name="ck_receipt_allocation_target"),
        **OPTS,
    )
    for col in ("receipt_id", "payment_id", "water_reading_id", "advance_payment_id"):
        op.create_index(f"ix_receipt_allocation_{col}", "receipt_allocation", [col])
    backfill()


def backfill():
    import legacy_app as m
    bind = op.get_bind()
    session = Session(bind=bind)   # reads only; inserts go through `bind` (Alembic's transaction)
    money = m.money
    events = []   # (date, unit_id, method, reference, remarks, [(kind, id, amount)])

    for p in session.query(m.Payment).order_by(m.Payment.id):
        if money(p.amount) <= 0:
            continue
        events.append([p.payment_date or p.billing.created_at.date(), p.billing.unit_id, p.payment_method or "CASH",
                       p.reference, p.remarks, [("bill", p.id, money(p.amount))], p.billing.billing_month])

    for a in session.query(m.AdvancePayment).order_by(m.AdvancePayment.id):
        if money(a.amount) <= 0:
            continue
        match = re.search(r"Automatic advance from excess payment for (\d{4}-\d{2})", a.remarks or "")
        joined = False
        if match:   # the excess of a bill payment: same receipt as that payment
            for ev in events:
                if (ev[6] == match.group(1) and ev[1] == a.unit_id and ev[0] == a.payment_date and
                        (ev[2] or "") == (a.payment_method or "") and (ev[3] or "") == (a.reference or "")):
                    ev[5].append(("advance", a.id, money(a.amount)))
                    joined = True
                    break
        if not joined:
            events.append([a.payment_date, a.unit_id, a.payment_method or "CASH", a.reference, a.remarks,
                           [("advance", a.id, money(a.amount))], None])

    for w in session.query(m.WaterReading).order_by(m.WaterReading.id):
        if money(w.paid_amount) <= 0:
            continue
        events.append([w.paid_date or w.reading_date, w.unit_id, w.payment_method or "CASH", w.payment_reference, None,
                       [("water", w.id, money(w.paid_amount))], None])
    session.close()

    kind_order = {"bill": 0, "advance": 1, "water": 2}
    events.sort(key=lambda ev: (ev[0], kind_order[ev[5][0][0]], ev[5][0][1]))
    receipts, allocations = m.Receipt.__table__, m.ReceiptAllocation.__table__
    seq_by_year = {}
    for date_, unit_id, method, reference, remarks, parts, _ in events:
        seq = seq_by_year.get(date_.year, 0) + 1
        seq_by_year[date_.year] = seq
        rid = bind.execute(receipts.insert().values(
            receipt_year=date_.year, receipt_seq=seq, receipt_no=f"OR-{date_.year}-{seq:06d}", unit_id=unit_id,
            received_date=date_, amount=sum(p[2] for p in parts), payment_method=(method or "CASH").upper(),
            reference=reference or None, remarks=(remarks or "")[:300] or None, received_by="backfill",
            source="backfill")).inserted_primary_key[0]
        for kind, record_id, amount in parts:
            bind.execute(allocations.insert().values(
                receipt_id=rid, kind=kind, amount=amount,
                payment_id=record_id if kind == "bill" else None,
                water_reading_id=record_id if kind == "water" else None,
                advance_payment_id=record_id if kind == "advance" else None))

    # reconciliation: receipts must equal the money already recorded
    recorded = Decimal(str(bind.execute(sa.text(
        "SELECT (SELECT COALESCE(SUM(amount),0) FROM payment WHERE amount > 0)"
        " + (SELECT COALESCE(SUM(amount),0) FROM advance_payment WHERE amount > 0)"
        " + (SELECT COALESCE(SUM(paid_amount),0) FROM water_reading WHERE paid_amount > 0)")).scalar()))
    issued = Decimal(str(bind.execute(sa.text("SELECT COALESCE(SUM(amount),0) FROM receipt")).scalar()))
    if recorded != issued:
        raise RuntimeError(f"Receipt backfill does not reconcile: payments {recorded} vs receipts {issued}")
    print(f"   backfilled {len(events)} receipt(s); total {issued} = payments recorded {recorded}")


def downgrade():
    op.drop_table("receipt_allocation")
    op.drop_table("receipt")
