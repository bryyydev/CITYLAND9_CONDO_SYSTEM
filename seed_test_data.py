"""
CityLand 9 - sample test data loader.

Run from this folder:
    python seed_test_data.py

The script only creates records whose TEST-* unit numbers do not already exist.
It does NOT delete existing records.
"""
import os
import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))
from legacy_app import app, db, Unit, Owner, Tenant, ParkingLot, ParkingBilling, Billing, Payment, WaterReading  # noqa: E402

def add_if_missing():
    with app.app_context():
        # Create asset units first so residential units can point to them.
        assets = {}
        for no, floor, typ, area, rate in [
            ("P-TEST-01","P1","PARKING",10,100),
            ("P-TEST-02","P1","PARKING",10,100),
            ("S-TEST-01","S1","STORAGE",5,50),
            ("S-TEST-02","S1","STORAGE",5,50),
        ]:
            u = Unit.query.filter_by(unit_no=no).first()
            if not u:
                u = Unit(unit_no=no, floor=floor, unit_type=typ, area_sqm=area,
                         unit_rate_per_sqm=rate, dues_mode="per_sqm",
                         status="Vacant", active=True)
                db.session.add(u)
                db.session.flush()
            assets[no] = u

        residential = {}
        specs = [
            ("TEST-501","5F","STUDIO TYPE",40,50,assets["P-TEST-01"],assets["S-TEST-01"],"Owner","Juan Dela Cruz","09170000001","juan.test@example.com"),
            ("TEST-502","5F","1 BEDROOM",50,75,assets["P-TEST-02"],assets["S-TEST-02"],"Tenant","Maria Santos","09170000002","maria.owner@example.com"),
            ("TEST-503","6F","2 BEDROOM",60,100,None,None,"Owner","Pedro Reyes","09170000003","pedro.test@example.com"),
            ("TEST-504","6F","3 BEDROOM",80,125,None,None,"Owner","Ana Garcia","09170000004","ana.test@example.com"),
        ]
        for no,floor,typ,area,rate,parking,storage,occ,owner_name,contact,email in specs:
            u = Unit.query.filter_by(unit_no=no).first()
            if not u:
                u = Unit(unit_no=no, floor=floor, unit_type=typ, area_sqm=area,
                         unit_rate_per_sqm=rate, dues_mode="per_sqm",
                         include_parking=bool(parking), include_storage=bool(storage),
                         assigned_parking_unit_id=parking.id if parking else None,
                         assigned_storage_unit_id=storage.id if storage else None,
                         occupancy_type=occ, owner_name=owner_name, contact_no=contact,
                         email=email, status="Occupied", active=True)
                db.session.add(u)
                db.session.flush()
            residential[no] = u

        # Owners and tenant
        for no,name,contact,email in [
            ("TEST-501","Juan Dela Cruz","09170000001","juan.test@example.com"),
            ("TEST-502","Maria Santos","09170000002","maria.owner@example.com"),
            ("TEST-503","Pedro Reyes","09170000003","pedro.test@example.com"),
            ("TEST-504","Ana Garcia","09170000004","ana.test@example.com"),
        ]:
            u=residential[no]
            if not Owner.query.filter_by(unit_id=u.id, owner_name=name).first():
                db.session.add(Owner(unit_id=u.id, owner_name=name, contact_no=contact, email=email, status="Current", notes="TEST DATA"))
        u=residential["TEST-502"]
        if not Tenant.query.filter_by(unit_id=u.id, tenant_name="Carlos Santos").first():
            db.session.add(Tenant(unit_id=u.id, tenant_name="Carlos Santos", contact_no="09170000005",
                                  email="carlos.tenant@example.com", move_in=date(2026,2,1),
                                  status="Current", representative=False, notes="TEST DATA"))

        # Parking lot records for parking-page testing.
        for no,slot,assigned_name in [
            ("TEST-501","P-TEST-01","Juan Dela Cruz"),
            ("TEST-502","P-TEST-02","Carlos Santos"),
        ]:
            u=residential[no]
            if not ParkingLot.query.filter_by(slot_no=slot).first():
                db.session.add(ParkingLot(unit_id=u.id, slot_no=slot, area_sqm=10, rate_per_sqm=100,
                                          status="Assigned", active=True,
                                          assigned_to_type="Tenant" if no=="TEST-502" else "Owner",
                                          assigned_to_name=assigned_name, notes="TEST DATA"))

        db.session.flush()

        # Water readings: paid, overdue, and unpaid-not-overdue examples.
        water_specs = [
            ("TEST-501","2026-09",20,26,50,date(2026,9,5),True,date(2026,9,6)),
            ("TEST-502","2026-08",30,45,50,date(2026,8,5),False,None),
            ("TEST-503","2026-08",40,50,50,date(2026,8,5),False,None),
            ("TEST-504","2026-09",60,68,50,date(2026,9,5),False,None),
        ]
        for no,month,prev,curr,rate,rdate,paid,pdate in water_specs:
            u=residential[no]
            if not WaterReading.query.filter_by(unit_id=u.id, reading_month=month).first():
                db.session.add(WaterReading(unit_id=u.id, reading_month=month, previous_reading=prev,
                                            current_reading=curr, rate=rate, reading_date=rdate,
                                            paid=paid, paid_date=pdate))

        db.session.flush()

        # Billing examples requested by the user.
        bill_specs = [
            ("TEST-501","2026-09",2000,1000,250,300,3550,date(2026,9,8),"Paid",date(2026,9,6)),
            ("TEST-502","2026-08",3750,1000,250,750,0,date(2026,8,8),"Overdue",None),
            ("TEST-503","2026-08",6000,0,0,500,0,date(2026,8,8),"Overdue",None),
            ("TEST-504","2026-09",10000,0,0,400,0,date(2026,9,15),"Unpaid",None),
        ]
        for no,month,assessment,parking,storage,water_amt,paid,due,status,paid_date in bill_specs:
            u=residential[no]
            if not Billing.query.filter_by(unit_id=u.id, billing_month=month).first():
                db.session.add(Billing(unit_id=u.id, billing_month=month, assessment=assessment,
                                       parking_dues=parking, storage_dues=storage, water=water_amt,
                                       other=0, penalty=0, adjustment=0, previous_balance=0,
                                       amount_paid=paid, due_date=due, status=status, paid_date=paid_date))
        db.session.flush()

        # Payment for the fully-paid TEST-501 bill.
        b=Billing.query.filter_by(unit_id=residential["TEST-501"].id, billing_month="2026-09").first()
        if b and not Payment.query.filter_by(billing_id=b.id, reference="TEST-PAY-001").first():
            db.session.add(Payment(billing_id=b.id, amount=3550, payment_date=date(2026,9,6),
                                   reference="TEST-PAY-001", remarks="Sample fully paid bill"))

        db.session.commit()
        print("Sample data loaded successfully.")
        print("TEST-501: parking + storage + paid")
        print("TEST-502: overdue + parking + storage + water")
        print("TEST-503: overdue + water + condo due")
        print("TEST-504: unpaid but not overdue")

if __name__ == "__main__":
    add_if_missing()
