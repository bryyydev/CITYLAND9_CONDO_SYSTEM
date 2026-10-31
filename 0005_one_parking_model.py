"""Phase B3: one parking model - parking is a PARKING unit assigned to a residential unit.

Client decision 2026-09-30: "Assigned PARKING units". Until now there were two models:
  * parking lots (`parking_lot`, with `parking_billing` rows that no screen totals), and
  * PARKING-type units assigned through unit.assigned_parking_unit_id.
The engine charged the lots first and the assigned unit only when there were no lots.

This step converts every active parking lot into the assigned-PARKING-unit model, checks that
every unit's monthly parking charge is exactly the same before and after, and only then drops
`parking_billing`, `parking_lot` and the unused copies unit.parking_slot / unit.parking_slots.

Per active lot (unit U):
  * U already has an assigned PARKING unit that gives the same charge -> the lot was a duplicate.
  * U has no assigned PARKING unit and exactly one lot -> the lot becomes a PARKING unit
    (unit_no = slot no., same area, same rate) assigned to U.
  * anything else (several lots, a different charge, a slot number already used by another unit,
    paid parking_billing rows) -> the step stops with a report and changes nothing.
Inactive lots charged nothing and are dropped. db_migrate.py takes a full backup first.

Downgrade recreates the two tables and the two columns empty (the converted PARKING units stay:
they are real units now).

Revision ID: 0005_one_parking
Revises: 0004_receipts
"""
from decimal import Decimal

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0005_one_parking"
down_revision = "0004_receipts"
branch_labels = None
depends_on = None

OPTS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_bin"}
CENT = Decimal("0.01")


def dec(x):
    return Decimal(str(float(x or 0))).quantize(CENT)


def charge(area, rate, default_rate):
    """The engine's formula: area x (rate or the default parking rate), rounded to centavos."""
    return Decimal(str(round(float(area or 0) * float(rate or default_rate), 2))).quantize(CENT)


def rows(bind, sql, **params):
    return [dict(r) for r in bind.execute(sa.text(sql), params).mappings()]


def parking_charges(bind, default_rate, with_lots):
    """Monthly parking charge per unit: the old rule (lots first) or the new one (assigned unit)."""
    units = {u["id"]: u for u in rows(bind, "SELECT id, unit_no, area_sqm, unit_rate_per_sqm, "
                                           "assigned_parking_unit_id FROM unit")}
    lots = {}
    if with_lots:
        for lot in rows(bind, "SELECT unit_id, area_sqm, rate_per_sqm FROM parking_lot WHERE active = 1"):
            lots[lot["unit_id"]] = lots.get(lot["unit_id"], 0.0) + float(lot["area_sqm"] or 0) * float(
                lot["rate_per_sqm"] or default_rate)
    result = {}
    for uid, u in units.items():
        total = round(lots.get(uid, 0.0), 2)
        assigned = units.get(u["assigned_parking_unit_id"])
        if assigned and not total:
            total = float(charge(assigned["area_sqm"], assigned["unit_rate_per_sqm"], default_rate))
        result[uid] = Decimal(str(total)).quantize(CENT)
    return result


def upgrade():
    bind = op.get_bind()
    default_rate = float(bind.execute(sa.text(
        "SELECT value FROM setting WHERE `key` = 'parking_rate_per_sqm'")).scalar() or 0)
    before = parking_charges(bind, default_rate, with_lots=True)
    units = {u["id"]: u for u in rows(bind, "SELECT id, unit_no, unit_type, area_sqm, unit_rate_per_sqm, "
                                           "assigned_parking_unit_id FROM unit")}
    by_no = {u["unit_no"]: u for u in units.values()}
    taken = {u["assigned_parking_unit_id"] for u in units.values() if u["assigned_parking_unit_id"]}
    active = rows(bind, "SELECT * FROM parking_lot WHERE active = 1 ORDER BY id")
    inactive = bind.execute(sa.text("SELECT COUNT(*) FROM parking_lot WHERE active = 0 OR active IS NULL")).scalar()
    paid = bind.execute(sa.text("SELECT COUNT(*) FROM parking_billing WHERE amount_paid > 0")).scalar()
    billing_rows = bind.execute(sa.text("SELECT COUNT(*) FROM parking_billing")).scalar()

    problems, plan = [], []
    if paid:
        problems.append(f"{paid} parking_billing row(s) have money recorded; they must be reviewed first")
    per_unit = {}
    for lot in active:
        per_unit.setdefault(lot["unit_id"], []).append(lot)
    for uid, unit_lots in sorted(per_unit.items()):
        u = units[uid]
        slots = ", ".join(lot["slot_no"] for lot in unit_lots)
        assigned = units.get(u["assigned_parking_unit_id"])
        if assigned:
            new = charge(assigned["area_sqm"], assigned["unit_rate_per_sqm"], default_rate)
            if new == before[uid]:
                plan.append(f"{u['unit_no']}: lot {slots} duplicates assigned parking unit {assigned['unit_no']} "
                            f"({new}/month) - lot dropped")
            else:
                problems.append(f"{u['unit_no']}: lots {slots} charge {before[uid]} but assigned parking unit "
                                f"{assigned['unit_no']} charges {new}")
            continue
        if len(unit_lots) > 1:
            problems.append(f"{u['unit_no']}: has {len(unit_lots)} active lots ({slots}); one PARKING unit per unit")
            continue
        lot = unit_lots[0]
        existing = by_no.get(lot["slot_no"])
        if existing and (existing["unit_type"] != "PARKING" or existing["id"] in taken or
                         charge(existing["area_sqm"], existing["unit_rate_per_sqm"], default_rate) != before[uid]):
            problems.append(f"{u['unit_no']}: slot {lot['slot_no']} is already used by unit {existing['unit_no']}")
            continue
        plan.append((u, lot, existing))

    if problems:
        raise RuntimeError("Parking conversion stopped, nothing changed:\n   - " + "\n   - ".join(problems))

    for item in plan:
        if isinstance(item, str):
            print("   " + item)
            continue
        u, lot, existing = item
        if existing:
            pid = existing["id"]
            print(f"   {u['unit_no']}: lot {lot['slot_no']} -> existing PARKING unit {existing['unit_no']}")
        else:
            pid = bind.execute(sa.text(
                "INSERT INTO unit (unit_no, unit_type, area_sqm, unit_rate_per_sqm, include_parking, include_storage, "
                "dues_mode, auto_rate, status, active, occupancy_type, updated_by) VALUES "
                "(:no, 'PARKING', :area, :rate, 0, 0, 'per_sqm', 1, 'Vacant', 1, 'Owner', 'migration 0005')"),
                {"no": lot["slot_no"], "area": lot["area_sqm"] or 0, "rate": lot["rate_per_sqm"] or 0}).lastrowid
            print(f"   {u['unit_no']}: lot {lot['slot_no']} -> new PARKING unit {lot['slot_no']}")
        bind.execute(sa.text("UPDATE unit SET assigned_parking_unit_id = :p, include_parking = 1 WHERE id = :u"),
                     {"p": pid, "u": u["id"]})

    after = parking_charges(bind, default_rate, with_lots=False)
    diffs = [f"{units[uid]['unit_no']}: {before[uid]} -> {after.get(uid)}" for uid in before
             if uid in units and before[uid] != after.get(uid)]
    if diffs:
        raise RuntimeError("Parking charges would change, stopped:\n   - " + "\n   - ".join(diffs))
    print(f"   parking charge identical for all {len(before)} unit(s); dropping {len(active)} active + "
          f"{inactive} inactive lot(s) and {billing_rows} parking_billing row(s)")

    op.drop_table("parking_billing")
    op.drop_table("parking_lot")
    op.drop_column("unit", "parking_slot")
    op.drop_column("unit", "parking_slots")


def downgrade():
    op.add_column("unit", sa.Column("parking_slot", sa.String(80)))
    op.add_column("unit", sa.Column("parking_slots", sa.Integer))
    op.create_table(
        "parking_lot",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("unit_id", sa.Integer, sa.ForeignKey("unit.id"), nullable=False),
        sa.Column("slot_no", sa.String(80), nullable=False),
        sa.Column("area_sqm", mysql.DOUBLE),
        sa.Column("rate_per_sqm", mysql.DOUBLE),
        sa.Column("status", sa.String(30)),
        sa.Column("active", sa.Boolean),
        sa.Column("notes", sa.String(300)),
        sa.Column("assigned_to_type", sa.String(20)),
        sa.Column("assigned_to_name", sa.String(200)),
        sa.Column("include_in_soa_owner", sa.Boolean),
        sa.Column("include_in_soa_tenant", sa.Boolean),
        sa.Column("created_at", mysql.DATETIME(fsp=6)),
        sa.Column("updated_at", mysql.DATETIME(fsp=6)),
        sa.Column("updated_by", sa.String(80)),
        **OPTS,
    )
    op.create_table(
        "parking_billing",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("parking_lot_id", sa.Integer, sa.ForeignKey("parking_lot.id"), nullable=False),
        sa.Column("billing_month", sa.String(7), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2)),
        sa.Column("amount_paid", sa.Numeric(12, 2)),
        sa.Column("due_date", sa.Date),
        sa.Column("status", sa.String(30)),
        sa.Column("created_at", mysql.DATETIME(fsp=6)),
        sa.UniqueConstraint("parking_lot_id", "billing_month", name="uq_parking_billing_lot_month"),
        **OPTS,
    )
    op.create_index("ix_parking_billing_lot_month", "parking_billing", ["parking_lot_id", "billing_month"])
