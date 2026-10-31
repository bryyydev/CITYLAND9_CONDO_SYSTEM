"""S4: the unit no longer stores copies of owner / tenant data.

`unit.owner_name`, `contact_no`, `email` were copies of the current owner record and
`unit.tenant_name` of the last current tenant, kept in sync by hand. They drifted (Edit Unit
blanked them; the demo tenant was never copied), and Unit/Billing search read them.
`unit.monthly_rate` was written but never read (0.00 everywhere), and `unit.parking_rate_per_sqm`
was never used. The app now reads names, contact and email from the owner/tenant records.

Before dropping anything, every unit's owner copy is compared with its current owner record:
  * copy filled, no owner record at all -> an owner record (Current) is created from the copy,
    so no name is lost;
  * copy differs from the current owner record -> the step stops with a report, nothing changed.
Tenant copies are only reported (the tenant records are what the SOA already uses).
db_migrate.py takes a full backup first.

Downgrade re-adds the columns and refills the copies from the current owner / tenant.

Revision ID: 0006_unit_copies
Revises: 0005_one_parking
"""
import sqlalchemy as sa
from alembic import op

revision = "0006_unit_copies"
down_revision = "0005_one_parking"
branch_labels = None
depends_on = None

COPIES = ["owner_name", "contact_no", "email", "tenant_name", "monthly_rate", "parking_rate_per_sqm"]


def norm(v):
    return (v or "").strip()


def rows(bind, sql, **params):
    return [dict(r) for r in bind.execute(sa.text(sql), params).mappings()]


def current_owner(owners):
    current = [o for o in owners if o["status"] == "Current"]
    return max(current, key=lambda o: o["id"]) if current else None


def current_tenant(tenants):
    current = [t for t in tenants if t["status"] == "Current"]
    if not current:
        return None
    return next((t for t in current if t["representative"]), None) or max(current, key=lambda t: t["id"])


def upgrade():
    bind = op.get_bind()
    units = rows(bind, "SELECT id, unit_no, owner_name, contact_no, email, tenant_name FROM unit ORDER BY unit_no")
    owners = rows(bind, "SELECT id, unit_id, owner_name, contact_no, email, status FROM owner")
    tenants = rows(bind, "SELECT id, unit_id, tenant_name, status, representative FROM tenant")
    problems, create, notes = [], [], []
    for u in units:
        mine = [o for o in owners if o["unit_id"] == u["id"]]
        owner = current_owner(mine)
        copy = (norm(u["owner_name"]), norm(u["contact_no"]), norm(u["email"]))
        if copy[0] and not mine:
            create.append(u)
        elif any(copy):
            record = (norm(owner["owner_name"]), norm(owner["contact_no"]), norm(owner["email"])) if owner else ("", "", "")
            if copy[0] and copy[0] != record[0]:
                problems.append(f"{u['unit_no']}: unit says owner {copy[0]!r}, current owner record is {record[0] or 'none'!r}")
            elif copy[0] and copy != record:
                notes.append(f"{u['unit_no']}: owner contact/email copy {copy[1:]!r} was stale; record has {record[1:]!r}")
        tenant = current_tenant([t for t in tenants if t["unit_id"] == u["id"]])
        if norm(u["tenant_name"]) != (norm(tenant["tenant_name"]) if tenant else ""):
            notes.append(f"{u['unit_no']}: tenant copy {norm(u['tenant_name']) or 'empty'!r} was stale; "
                         f"current tenant record is {norm(tenant['tenant_name']) if tenant else 'none'!r}")
    if problems:
        raise RuntimeError("Unit owner copies disagree with the owner records, stopped (nothing changed):\n   - "
                           + "\n   - ".join(problems))
    for u in create:
        bind.execute(sa.text("INSERT INTO owner (unit_id, owner_name, contact_no, email, status, receive_soa_email, "
                             "include_in_soa, notes, updated_by) VALUES (:u, :n, :c, :e, 'Current', 0, 1, "
                             "'Created from the unit record (migration 0006)', 'migration 0006')"),
                     {"u": u["id"], "n": norm(u["owner_name"]), "c": norm(u["contact_no"]) or None, "e": norm(u["email"]) or None})
        print(f"   {u['unit_no']}: owner record created from the unit copy ({norm(u['owner_name'])})")
    for n in notes:
        print("   " + n)
    print(f"   {len(units)} unit(s) checked; {len(create)} owner record(s) created; dropping {', '.join(COPIES)}")
    for col in COPIES:
        op.drop_column("unit", col)


def downgrade():
    op.add_column("unit", sa.Column("owner_name", sa.String(200)))
    op.add_column("unit", sa.Column("contact_no", sa.String(80)))
    op.add_column("unit", sa.Column("email", sa.String(160)))
    op.add_column("unit", sa.Column("tenant_name", sa.String(200)))
    op.add_column("unit", sa.Column("parking_rate_per_sqm", sa.Float))
    op.add_column("unit", sa.Column("monthly_rate", sa.Numeric(12, 2)))
    bind = op.get_bind()
    owners = rows(bind, "SELECT id, unit_id, owner_name, contact_no, email, status FROM owner")
    tenants = rows(bind, "SELECT id, unit_id, tenant_name, status, representative FROM tenant")
    for u in rows(bind, "SELECT id FROM unit"):
        o = current_owner([x for x in owners if x["unit_id"] == u["id"]])
        t = current_tenant([x for x in tenants if x["unit_id"] == u["id"]])
        bind.execute(sa.text("UPDATE unit SET owner_name = :n, contact_no = :c, email = :e, tenant_name = :t, "
                             "parking_rate_per_sqm = 0, monthly_rate = 0 WHERE id = :id"),
                     {"n": o and o["owner_name"], "c": o and o["contact_no"], "e": o and o["email"],
                      "t": t and t["tenant_name"], "id": u["id"]})
