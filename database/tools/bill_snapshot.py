"""Golden master: every bill's figures as computed by the existing billing engine.

    python database/tools/bill_snapshot.py --sqlite PATH     (read a SQLite file)
    python database/tools/bill_snapshot.py --mysql           (read the local MySQL from .env)

Prints JSON {bill_id: {condo, parking, water, penalty, previous, advance, current,
total, balance, status}}. Running it on the old and the new database and comparing
the two outputs proves the migration did not change a single amount. Read-only.
"""
import json
import os
import sys
import warnings

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings("ignore")


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

    out = {}
    with m.app.test_request_context("/"):
        for bill in m.Billing.query.order_by(m.Billing.id).all():
            calc = m._bill_calc(bill)
            out[str(bill.id)] = {k: str(calc[k]) for k in
                                 ("condo", "parking", "water", "penalty", "previous", "advance", "current", "total", "balance")}
            out[str(bill.id)]["status"] = m.bill_status(bill)
    json.dump(out, sys.stdout, sort_keys=True)


if __name__ == "__main__":
    main()
