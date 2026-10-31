CityLand9 Condo Web System — V10.12

CITYLAND 9 CONDO WEB SYSTEM — V10.3 FEATURE UPDATE

Windows startup:
1. Run START_WINDOWS.bat.
2. Open http://127.0.0.1:5000
3. There is no default account. Create the first Superadmin on the server PC with `.venv\Scripts\python.exe database\create_admin.py`, then sign in with the username and password chosen there. Accounts are not copied from the developer PC.

Major features:
- Units with Owner/Tenant occupancy selection.
- Multiple tenant history per unit.
- Optional owner/tenant emails.
- Per-sqm condominium dues.
- Per-sqm parking fees with Owner/Tenant assignment.
- Units Directory assigns Condo Dues, Water, Parking, Storage, and Other charges to Owner or Tenant.
- Separate Owner and Tenant SOAs from the configured cutover month; older net balances and credits start in the Tenant ledger.
- New payments, advances, and separately paid water require a payer ledger. Unassigned historical transactions block separate SOAs until reviewed.
- Named Other charges can be itemized on an edited SOA and assigned per line.
- Dedicated Parking management tab.
- Monthly Billing and printable Statement of Account (SOA).
- Superadmins can delete selected or monthly generated SOAs only while the period is open and the bills have no payments or advance allocations; deletion is audited.
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
