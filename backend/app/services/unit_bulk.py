"""Workbook layout for the guarded bulk unit-create workflow."""

from io import BytesIO


UNIT_BULK_FIELDS = [
    "unit_no", "category", "residential_type", "floor", "commercial_type", "area_sqm", "rate_per_sqm",
    "auto_rate", "dues_mode", "manual_monthly_dues", "occupancy", "status", "parking_unit_no",
    "storage_unit_no",
    "owner_name", "owner_contact_no", "owner_email", "owner_move_in",
    "tenant_name", "tenant_contact_no", "tenant_email", "tenant_move_in",
    "condo_payer", "water_payer", "parking_payer", "storage_payer", "other_payer",
]
PREVIOUS_UNIT_BULK_FIELDS = UNIT_BULK_FIELDS[:-5]
LEGACY_UNIT_BULK_FIELDS = [
    "unit_no", "unit_type", "floor", "area_sqm", "rate_per_sqm", "auto_rate", "dues_mode",
    "manual_monthly_dues", "occupancy", "status", "parking_unit_no", "storage_unit_no",
    "owner_name", "owner_contact_no", "owner_email", "owner_move_in",
    "tenant_name", "tenant_contact_no", "tenant_email", "tenant_move_in",
]
RESIDENTIAL_TYPES = ("STUDIO TYPE", "1 BEDROOM", "2 BEDROOM", "3 BEDROOM")
UNIT_CATEGORIES = ("RESIDENTIAL", "COMMERCIAL", "DRYING AREA", "STORAGE", "PARKING")
COMMERCIAL_TYPES = ("2F", "UG", "LG")
UNIT_BULK_MAX_ROWS = 3000


def build_units_template():
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    workbook = Workbook()
    guide = workbook.active
    guide.title = "Read Me"
    guidance = [
        ("CITYLAND9 bulk unit upload", "Fill in up to 3,000 unit rows on the Units sheet. This upload creates units; it does not update existing units."),
        ("Required fields", "unit_no, category, and area_sqm are required. Repeated unit numbers and unit numbers already in CITYLAND9 are skipped; the first occurrence is used."),
        ("Categories", "Choose RESIDENTIAL, COMMERCIAL, DRYING AREA, STORAGE, or PARKING. DRYING AREA is billable with its own Rates & Rules rate."),
        ("Residential type", "For RESIDENTIAL, choose STUDIO TYPE, 1 BEDROOM, 2 BEDROOM, or 3 BEDROOM. Blank defaults to STUDIO TYPE; other categories ignore this field."),
        ("Commercial type", "For COMMERCIAL, choose 2F, UG, or LG in commercial_type. It is used to group shared commercial water readings. Other units ignore this field."),
        ("Floor", "Use floor for the unit's regular floor value. For Commercial units, use commercial_type instead."),
        ("Dues", "auto_rate: Yes/No. dues_mode: per_sqm/manual. Enter rate_per_sqm when auto_rate is No. Enter manual_monthly_dues when dues_mode is manual."),
        ("Charge responsibility", "Choose Owner or Tenant for each charge. Blank values use the unit's Occupied by selection."),
        ("People", "Owner and tenant columns are optional. Each name is stored as one whole name exactly as entered, including punctuation. Add additional owner/tenant records in Units Directory after upload."),
        ("Assets", "parking_unit_no and storage_unit_no may refer only to existing, active, unassigned parking/storage units. New asset units must be uploaded first."),
        ("Safety", "The system validates every row before creating anything. If any row has an issue, no rows are created. Review the preview before confirming."),
        ("Dates", "Enter dates as YYYY-MM-DD. Do not add IDs; the system assigns IDs to newly created records."),
    ]
    guide.append(["Topic", "Instructions"])
    for item in guidance:
        guide.append(list(item))
    guide.column_dimensions["A"].width = 24
    guide.column_dimensions["B"].width = 110
    guide.freeze_panes = "A2"
    guide.sheet_view.showGridLines = False
    for cell in guide[1]:
        cell.fill = PatternFill("solid", fgColor="0F2A3D")
        cell.font = Font(bold=True, color="FFFFFF")
    for row in guide.iter_rows(min_row=2):
        row[0].font = Font(bold=True, color="0F2A3D")
        row[1].alignment = Alignment(wrap_text=True, vertical="top")
        guide.row_dimensions[row[0].row].height = 32

    sheet = workbook.create_sheet("Units")
    sheet.append(UNIT_BULK_FIELDS)
    sheet.freeze_panes = "A2"
    last_data_row = UNIT_BULK_MAX_ROWS + 1
    sheet.auto_filter.ref = f"A1:AA{last_data_row}"
    sheet.sheet_view.showGridLines = False
    for cell in sheet[1]:
        cell.fill = PatternFill("solid", fgColor="0F2A3D")
        cell.font = Font(bold=True, color="FFFFFF")
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        sheet.column_dimensions[cell.column_letter].width = max(16, min(25, len(cell.value) + 4))
    sheet.row_dimensions[1].height = 32
    from openpyxl.comments import Comment
    sheet.cell(row=1, column=UNIT_BULK_FIELDS.index("category") + 1).comment = Comment(
        "Choose the same unit category as Add unit in Units Directory.",
        "CITYLAND9",
    )
    sheet.cell(row=1, column=UNIT_BULK_FIELDS.index("commercial_type") + 1).comment = Comment(
        "For COMMERCIAL units, choose 2F, UG, or LG. This value is stored in the unit's floor field.",
        "CITYLAND9",
    )
    validations = {
        "category": '"' + ",".join(UNIT_CATEGORIES) + '"',
        "residential_type": '"' + ",".join(RESIDENTIAL_TYPES) + '"',
        "commercial_type": '"' + ",".join(COMMERCIAL_TYPES) + '"',
        "auto_rate": '"Yes,No"',
        "dues_mode": '"per_sqm,manual"',
        "occupancy": '"Owner,Tenant"',
        "status": '"Occupied,Vacant"',
        "condo_payer": '"Owner,Tenant"',
        "water_payer": '"Owner,Tenant"',
        "parking_payer": '"Owner,Tenant"',
        "storage_payer": '"Owner,Tenant"',
        "other_payer": '"Owner,Tenant"',
    }
    for field, formula in validations.items():
        column = UNIT_BULK_FIELDS.index(field) + 1
        letter = sheet.cell(row=1, column=column).column_letter
        validation = DataValidation(type="list", formula1=formula, allow_blank=True)
        validation.error = "Select one of the listed values."
        validation.errorTitle = "Invalid value"
        validation.showErrorMessage = True
        sheet.add_data_validation(validation)
        validation.add(f"{letter}2:{letter}{last_data_row}")
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output


def read_units_rows(stream, *, max_rows):
    """Read a template-shaped Units sheet and return (row_number, {header: value}) rows."""
    from openpyxl import load_workbook

    stream.seek(0)
    workbook = load_workbook(stream, read_only=True, data_only=True)
    try:
        if "Units" not in workbook.sheetnames:
            raise ValueError("The workbook must contain a Units sheet from the CITYLAND9 bulk unit template.")
        sheet = workbook["Units"]
        iterator = sheet.iter_rows(values_only=True)
        headers = next(iterator, None)
        if not headers:
            raise ValueError("The Units sheet columns don't match the CITYLAND9 bulk unit template.")
        header_values = tuple(headers)
        if header_values[:len(UNIT_BULK_FIELDS)] == tuple(UNIT_BULK_FIELDS):
            fields, old_layout = UNIT_BULK_FIELDS, False
        elif header_values[:len(PREVIOUS_UNIT_BULK_FIELDS)] == tuple(PREVIOUS_UNIT_BULK_FIELDS):
            fields, old_layout = PREVIOUS_UNIT_BULK_FIELDS, False
        elif header_values[:len(LEGACY_UNIT_BULK_FIELDS)] == tuple(LEGACY_UNIT_BULK_FIELDS):
            fields, old_layout = LEGACY_UNIT_BULK_FIELDS, True
        else:
            raise ValueError("The Units sheet columns don't match the CITYLAND9 bulk unit template.")
        if len(headers) > len(fields) and any(value not in (None, "") for value in headers[len(fields):]):
            raise ValueError("The Units sheet has unexpected columns.")

        rows = []
        for row_number, values in enumerate(iterator, start=2):
            row = dict(zip(fields, values))
            if old_layout:
                old_type = str(row.pop("unit_type") or "").strip().upper()
                old_floor = row.pop("floor", "")
                if old_type in RESIDENTIAL_TYPES:
                    row["category"] = "RESIDENTIAL"
                    row["residential_type"] = old_type
                    row["floor"] = old_floor
                    row["commercial_type"] = ""
                elif old_type == "COMMERCIAL":
                    row["category"] = "COMMERCIAL"
                    row["residential_type"] = ""
                    row["floor"] = ""
                    row["commercial_type"] = old_floor
                elif not old_type:
                    row["category"] = "RESIDENTIAL"
                    row["residential_type"] = ""
                    row["floor"] = old_floor
                    row["commercial_type"] = ""
                else:
                    row["category"] = old_type
                    row["residential_type"] = ""
                    row["floor"] = old_floor
                    row["commercial_type"] = ""
            if any(value not in (None, "") for value in row.values()):
                if len(rows) >= max_rows:
                    raise ValueError(f"The workbook exceeds the limit of {max_rows} unit rows.")
                rows.append((row_number, row))
        if not rows:
            raise ValueError("Add at least one unit row to the Units sheet.")
        return rows
    finally:
        workbook.close()
