CityLand9 Condo Web System — V10.12

CITYLAND 9 CONDO WEB SYSTEM — V10.3 FEATURE UPDATE

Windows startup:
1. Run START_WINDOWS.bat.
2. Open http://127.0.0.1:5000
3. Default account: superadmin / admin123 (change the password in production).

Major features:
- Units with Owner/Tenant occupancy selection.
- Multiple tenant history per unit.
- Optional owner/tenant emails.
- Per-sqm condominium dues.
- Per-sqm parking fees with Owner/Tenant assignment.
- Dedicated Parking management tab.
- Monthly Billing and printable Statement of Account (SOA).
- SOA shows unit and parking overdue months plus payment history.
- Water readings with historical months and Paid/Unpaid/Partially Paid filtering.
- Excel database export/import.

Database:
- SQLite database is stored beside app.py unless DATABASE_URL is set.
- On schema upgrade, a .before_v10_cleanup.bak backup is created when possible.

Note:
- Existing databases are migrated in place. Keep a separate backup before first production use.

SYSTEM NAME (V10.28): CITYLAND 9 CONDO SYSTEM 2026
Units now support PARKING and STORAGE asset units and assignment to residential units.
