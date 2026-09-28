CITYLAND 9 TEST DATA

Four residential test cases were prepared:

TEST-501
- Studio
- With Parking
- With Storage
- September 2026 bill is fully PAID
- Water reading is paid

TEST-502
- 1 Bedroom
- With Parking
- With Storage
- August 2026 bill is OVERDUE
- Includes water bill

TEST-503
- 2 Bedroom
- August 2026 bill is OVERDUE
- Includes Condo Due + Water Bill

TEST-504
- 3 Bedroom
- September 2026 bill is UNPAID but NOT OVERDUE
- Due date: September 15, 2026

The seed_test_data.py script is safer than importing the Excel file because it creates
the self-referencing parking/storage assignments using the actual database IDs and does
not delete existing records.

IMPORTANT:
- The sample records are deliberately named TEST-*.
- The seed script does not delete existing data.
- Use this only for testing, not as real condo records.
