"""Shared Excel export columns and an import-layout workbook for CITYLAND9."""

from io import BytesIO


RESIDENTIAL_UNIT_TYPES = ("STUDIO TYPE", "1 BEDROOM", "2 BEDROOM", "3 BEDROOM")
UNIT_CATEGORIES = ("RESIDENTIAL", "COMMERCIAL", "DRYING AREA", "STORAGE", "PARKING")


def normalize_import_unit_type(value, current_type=None):
    """Map the Settings import category to the unit type stored by CITYLAND9."""
    text = str(value or "").strip()
    if text.upper() == "RESIDENTIAL":
        current = str(current_type or "").strip()
        return current if current.upper() in RESIDENTIAL_UNIT_TYPES else "STUDIO TYPE"
    if text.upper() in UNIT_CATEGORIES:
        return text.upper()
    return text


EXPORT_FIELDS = {
    "Units": [
        "id", "unit_no", "floor", "unit_type", "area_sqm", "unit_rate_per_sqm", "dues_mode",
        "auto_rate", "manual_monthly_dues", "include_parking", "include_storage", "assigned_parking_unit_id",
        "assigned_storage_unit_id", "occupancy_type", "owner_name", "contact_no", "email", "status",
        "active", "condo_charge_payer", "water_charge_payer", "parking_charge_payer",
        "storage_charge_payer", "other_charge_payer",
    ],
    "Owners": [
        "id", "unit_id", "owner_name", "contact_no", "email", "move_in", "move_out", "status",
        "receive_soa_email", "include_in_soa", "notes",
    ],
    "Tenants": [
        "id", "unit_id", "tenant_name", "contact_no", "email", "move_in", "move_out", "status",
        "representative", "receive_soa_email", "include_in_soa", "notes",
    ],
    "Billing": [
        "id", "unit_id", "billing_month", "assessment", "parking_dues", "storage_dues", "water", "other",
        "water_reading_month", "penalty", "adjustment", "previous_balance", "amount_paid", "due_date",
        "status", "paid_date",
    ],
    "Payments": [
        "id", "billing_id", "amount", "payment_date", "payment_method", "payment_type", "reference", "remarks",
    ],
    "WaterReadings": [
        "id", "unit_id", "reading_month", "previous_reading", "current_reading", "rate", "reading_date",
        "reading_period_start", "reading_period_end", "paid",
        "paid_amount", "payment_method", "payment_type", "payment_reference", "paid_date",
    ],
    "CommercialWaterReadings": [
        "id", "commercial_type", "reading_month", "previous_reading", "current_reading", "rate",
        "reading_date", "reading_period_start", "reading_period_end",
    ],
    "Employees": [
        "id", "employee_no", "full_name", "position", "department", "employment_status", "contact_no",
        "email", "date_hired", "monthly_salary", "notes",
    ],
    "Expenses": ["id", "expense_date", "category", "description", "amount"],
}

_COMMENTS = {
    "id": "Preserve the existing CITYLAND9 record ID from an export of this database. Imports cannot create records.",
    "unit_id": "Existing CITYLAND9 unit ID. Preserve the ID from an export of this database.",
    "billing_id": "Existing CITYLAND9 billing ID. Preserve the ID from an export of this database.",
    "unit_no": "Unique unit number, exactly as shown in CITYLAND9.",
    "unit_type": "Choose RESIDENTIAL, COMMERCIAL, DRYING AREA, STORAGE, or PARKING. RESIDENTIAL keeps an existing residential subtype, or defaults to STUDIO TYPE.",
    "floor": "For Commercial units, this is the commercial type: 2F, UG, or LG. For other units, use the floor value.",
    "auto_rate": "Yes uses the unit type's configured rate; No uses the entered unit_rate_per_sqm.",
    "dues_mode": "Allowed values: per_sqm, manual.",
    "status": "Use the status values accepted for this sheet. Blank cells keep saved values.",
    "billing_month": "Month in YYYY-MM format.",
    "reading_month": "Month in YYYY-MM format.",
    "water_reading_month": "Month in YYYY-MM format for the water reading included on this bill.",
    "commercial_type": "Commercial type: 2F, UG, or LG.",
    "assigned_parking_unit_id": "Existing unit ID for a PARKING unit.",
    "assigned_storage_unit_id": "Existing unit ID for a STORAGE unit.",
    "condo_charge_payer": "Choose Owner or Tenant for condo dues.",
    "water_charge_payer": "Choose Owner or Tenant for water charges.",
    "parking_charge_payer": "Choose Owner or Tenant for parking dues.",
    "storage_charge_payer": "Choose Owner or Tenant for storage dues.",
    "other_charge_payer": "Choose Owner or Tenant for named other charges.",
}

_VALIDATIONS = {
    "Units": {
        "unit_type": '"' + ",".join(UNIT_CATEGORIES) + '"',
        "dues_mode": '"per_sqm,manual"',
        "status": '"Occupied,Vacant"',
        "include_parking": '"Yes,No"',
        "include_storage": '"Yes,No"',
        "active": '"Yes,No"',
        "auto_rate": '"Yes,No"',
        "condo_charge_payer": '"Owner,Tenant"',
        "water_charge_payer": '"Owner,Tenant"',
        "parking_charge_payer": '"Owner,Tenant"',
        "storage_charge_payer": '"Owner,Tenant"',
        "other_charge_payer": '"Owner,Tenant"',
    },
    "Owners": {
        "status": '"Current,Past"',
        "receive_soa_email": '"Yes,No"',
        "include_in_soa": '"Yes,No"',
    },
    "Tenants": {
        "status": '"Current,Past"',
        "representative": '"Yes,No"',
        "receive_soa_email": '"Yes,No"',
        "include_in_soa": '"Yes,No"',
    },
    "Billing": {"status": '"Unpaid,Partially Paid,Paid,Overdue"'},
    "Payments": {"payment_method": '"CASH,CHECK,ONLINE"', "payment_type": '"FULL,PARTIAL"'},
    "WaterReadings": {
        "paid": '"Yes,No"',
        "payment_method": '"CASH,CHECK,ONLINE"',
        "payment_type": '"FULL,PARTIAL"',
    },
    "CommercialWaterReadings": {"commercial_type": '"2F,UG,LG"'},
}

_README = [
    ("CITYLAND9 Excel import template", "Layout only — this workbook contains no records and is not ready to import."),
    ("For safe updates", "Download the current workbook from Settings > Export to Excel, edit only the intended existing records, and preserve every ID."),
    ("Existing records only", "The import is locked to updates of existing records from this same database. New units, people, bills, payments, readings, employees, and expenses cannot be created through this importer."),
    ("IDs and references", "Do not invent, renumber, remove, or copy IDs from another database. Foreign-key columns such as unit_id and billing_id must reference existing records."),
    ("Blank cells", "Blank cells keep the saved value. Use only the values and formats accepted by the system; do not use formulas."),
    ("Before importing", "Upload the edited export in Settings > Import from Excel and run Check only. Import stays locked unless the system's safety checks pass."),
    ("Creating records", "Create new records in their normal CITYLAND9 screens. Then download a fresh export if you need to edit them in Excel."),
    ("Sheets", "The data sheets contain current importable fields and match the CITYLAND9 database export. Protected issue snapshots and reversal metadata are intentionally excluded. The Read Me sheet is ignored by the importer."),
]


def build_import_template():
    """Return an empty, formatted workbook with the exact headers of the database export."""
    from openpyxl import Workbook
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    workbook = Workbook()
    readme = workbook.active
    readme.title = "Read Me"
    readme.append(["Topic", "Guidance"])
    for entry in _README:
        readme.append(list(entry))
    readme.column_dimensions["A"].width = 27
    readme.column_dimensions["B"].width = 112
    readme.freeze_panes = "A2"
    readme.auto_filter.ref = f"A1:B{readme.max_row}"
    readme.sheet_view.showGridLines = False
    for row in readme.iter_rows(min_row=2):
        row[0].font = Font(bold=True, color="0F2A3D")
        row[1].alignment = Alignment(wrap_text=True, vertical="top")
        readme.row_dimensions[row[0].row].height = 34

    header_fill = PatternFill("solid", fgColor="0F2A3D")
    for sheet_name, headers in EXPORT_FIELDS.items():
        sheet = workbook.create_sheet(sheet_name)
        sheet.append(headers)
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = f"A1:{sheet.cell(row=1, column=len(headers)).column_letter}1"
        sheet.sheet_view.showGridLines = False
        for cell in sheet[1]:
            cell.fill = header_fill
            cell.font = Font(bold=True, color="FFFFFF")
            cell.alignment = Alignment(vertical="center")
            if cell.value in _COMMENTS:
                cell.comment = Comment(_COMMENTS[cell.value], "CITYLAND9")
            sheet.column_dimensions[cell.column_letter].width = max(16, min(30, len(str(cell.value)) + 4))
        sheet.row_dimensions[1].height = 24
        for column, values in _VALIDATIONS.get(sheet_name, {}).items():
            col = headers.index(column) + 1
            validation = DataValidation(type="list", formula1=values, allow_blank=True)
            validation.error = "Select one of the listed values."
            validation.errorTitle = "Invalid value"
            validation.showErrorMessage = True
            sheet.add_data_validation(validation)
            validation.add(f"{sheet.cell(row=2, column=col).column_letter}2:{sheet.cell(row=1001, column=col).column_letter}1001")

    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output
