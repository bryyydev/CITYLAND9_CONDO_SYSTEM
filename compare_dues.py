"""Read-only report: would the Decimal pricing rule change any NEW bill?

    .venv\\Scripts\\python.exe database\\tools\\compare_dues.py

Issued bills are stored and never recomputed, so they are not affected. This compares, for every
active unit and every water reading, the OLD calculation (float math + Python round(), which can
round a half-centavo down) with the NEW one (Decimal, rounded once to the centavo, half up).
Differences are at most 0.01 and only appear when a product ends exactly on half a centavo.
Nothing is written to the database.
"""
import os
import sys
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "database"))
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    pass
if os.getenv("DB_ENGINE", "").strip().lower() == "mysql" and os.getenv("MYSQL_DATA_DIR") and not os.getenv("DATABASE_URL"):
    import local_mysql
    local_mysql.start()

import legacy_app as m  # noqa: E402


def old_area_charge(area, rate):
    return Decimal(str(round(float(area or 0) * float(rate or 0), 2))).quantize(Decimal("0.01"))


def main():
    diffs = 0
    with m.app.test_request_context():
        units = m.Unit.query.filter_by(active=True).all()
        for u in units:
            rate = u.unit_rate_per_sqm or m.rate_for_type(u.unit_type)
            old, new = old_area_charge(u.area_sqm, rate), m.unit_dues(u)
            if old != new:
                diffs += 1
                print(f"unit {u.unit_no}: condo dues {old} -> {new}  (area {u.area_sqm} x rate {rate})")
        readings = m.WaterReading.query.all()
        for r in readings:
            usage = max(float(r.current_reading or 0) - float(r.previous_reading or 0), 0)
            old = Decimal(str(round(usage * float(r.rate or 0), 2))).quantize(Decimal("0.01"))
            if old != r.bill_amount:
                diffs += 1
                print(f"water {r.unit.unit_no} {r.reading_month}: {old} -> {r.bill_amount}")
        print(f"Checked {len(units)} unit(s) and {len(readings)} water reading(s): {diffs} difference(s). "
              "Issued bills are stored and unchanged either way.")


if __name__ == "__main__":
    main()
