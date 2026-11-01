"""Find payments the old code counted twice (overpayment / same-month advance). READ-ONLY.

    python database/tools/detect_overpayment_allocations.py --sqlite PATH    (a SQLite file)
    python database/tools/detect_overpayment_allocations.py --mysql          (the MySQL/MariaDB in .env)
    add --json for machine-readable output

Before the 2026-10-06 fix, a payment above a bill's balance was recorded as a bill payment for the
balance plus an "Automatic advance from excess payment for YYYY-MM" that started in the SAME month
and was immediately applied to that same, already settled bill. The excess was then counted twice:
the bill's raw balance went negative (shown as ₱0.00 / Paid) and the credit was used up.

This lists:
  A  definite: an automatic excess advance applied to the bill of the month it came from
  B  possible: any other bill whose computed balance is below zero while advances were applied to it
     (e.g. a prepaid advance recorded after the bill was already paid in cash)

Nothing is changed. Proposed repair, for each A row (only after review and approval, as an audited
correction in an open period): remove that advance application, so the bill's balance returns to 0
and the advance's amount becomes available credit again (it will apply to the next month's bill).
B rows need individual review.
"""
import json
import os
import sys
import warnings
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings("ignore")
MARK = "Automatic advance from excess payment for "


def find_affected(m):
    """Rows (dicts) of affected advance applications / bills. Read-only: nothing is written."""
    m.reset_financial_caches()
    rows, seen_bills = [], set()
    for app_row in m.AdvanceApplication.query.order_by(m.AdvanceApplication.id).all():
        adv, bill = app_row.advance, app_row.billing
        if not adv or not bill or adv.reversed_at:
            continue
        remark = adv.remarks or ""
        origin = remark.split(MARK, 1)[1][:7] if MARK in remark else None
        if origin and origin == bill.billing_month:
            calc = m._bill_calc(bill)
            rows.append({"kind": "A", "unit": bill.unit.unit_no if bill.unit else bill.unit_id, "billing_month": bill.billing_month,
                         "bill_id": bill.id, "advance_id": adv.id, "application_id": app_row.id,
                         "applied": str(m.money(app_row.amount)), "raw_balance": str(calc["balance"]),
                         "advance_date": adv.payment_date.isoformat() if adv.payment_date else None})
            seen_bills.add(bill.id)
    for bill in m.Billing.query.order_by(m.Billing.id).all():
        if bill.id in seen_bills:
            continue
        calc = m._bill_calc(bill)
        if calc["balance"] < 0 and calc["advance"] > 0:
            rows.append({"kind": "B", "unit": bill.unit.unit_no if bill.unit else bill.unit_id, "billing_month": bill.billing_month,
                         "bill_id": bill.id, "advance_id": None, "application_id": None,
                         "applied": str(m.money(calc["advance"])), "raw_balance": str(calc["balance"]), "advance_date": None})
    m.db.session.rollback()
    return rows


def main():
    path = None
    if "--sqlite" in sys.argv:
        path = os.path.abspath(sys.argv[sys.argv.index("--sqlite") + 1])
        os.environ["DATABASE_URL"] = "sqlite:///" + path.replace("\\", "/")
    elif "--mysql" in sys.argv:
        os.environ.pop("DATABASE_URL", None)
        os.environ["DB_ENGINE"] = "mysql"
    else:
        sys.exit(__doc__)
    sys.path.insert(0, os.path.join(ROOT, "backend"))
    sys.path.insert(0, os.path.join(ROOT, "database"))
    sys.path.insert(0, os.path.join(ROOT, "database", "tools"))
    from schema_compat import flag_old_schema
    flag_old_schema(path if "--sqlite" in sys.argv else None)   # database not upgraded yet (e.g. before a migration)
    import legacy_app as m

    with m.app.test_request_context("/"):
        rows = find_affected(m)
    if "--json" in sys.argv:
        json.dump(rows, sys.stdout, indent=1)
        return
    if not rows:
        print("No affected records found. Nothing was changed.")
        return
    print(f"{'kind':4} {'unit':10} {'month':7} {'bill':>6} {'advance':>7} {'applied':>12} {'raw balance':>12}")
    for r in rows:
        print(f"{r['kind']:4} {str(r['unit']):10} {r['billing_month']:7} {r['bill_id']:>6} {str(r['advance_id'] or '-'):>7} "
              f"{r['applied']:>12} {r['raw_balance']:>12}")
    total = sum((Decimal(r["applied"]) for r in rows if r["kind"] == "A"), Decimal("0"))
    print(f"\n{sum(r['kind'] == 'A' for r in rows)} definite (A), {sum(r['kind'] == 'B' for r in rows)} to review (B); "
          f"credit counted twice in A rows: {total}. Nothing was changed.")


if __name__ == "__main__":
    main()
