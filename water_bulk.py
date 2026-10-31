"""Template and reader for atomic bulk water-reading imports."""

from datetime import date, datetime
from io import BytesIO


WATER_BULK_MAX_ROWS = 3000
UNIT_FIELDS = ("unit_no", "reading_month", "previous_reading", "current_reading", "rate", "reading_from", "reading_to")
COMMERCIAL_FIELDS = ("commercial_type", "reading_month", "previous_reading", "current_reading", "rate", "reading_from", "reading_to")
LEGACY_UNIT_FIELDS = ("unit_no", "reading_month", "previous_reading", "current_reading", "rate", "reading_date")
LEGACY_COMMERCIAL_FIELDS = ("commercial_type", "reading_month", "previous_reading", "current_reading", "rate", "reading_date")


def build_water_template():
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    workbook = Workbook()
    guide = workbook.active
    guide.title = "Read Me"
    guidance = [
        ("CITYLAND9 bulk water-reading upload", "Fill in the WaterReadings sheet for individual units. Use CommercialWaterReadings for shared commercial meters."),
        ("Existing records only", "This upload creates missing readings only. Existing readings are never overwritten. Duplicate rows, invalid data, or closed periods block the whole upload."),
        ("WaterReadings", "One row per residential unit. Use the exact unit number shown in CITYLAND9."),
        ("CommercialWaterReadings", "One row per commercial_type (2F, UG, or LG); do not add one row per commercial unit."),
        ("Required fields", "reading_month must be YYYY-MM; current_reading must be a number; reading_from and reading_to are the inclusive start and end of the meter-reading period, each in YYYY-MM-DD format. previous_reading and rate may be blank."),
        ("Defaults", "A blank previous_reading uses the latest earlier saved reading for that unit/type (or 0 if none). A blank rate uses the water rate in Rates & Rules."),
        ("Reading period", "For September 16 to October 15, use reading_month 2026-10, reading_from 2026-09-16, and reading_to 2026-10-15."),
        ("No payments", "This upload records meter readings only. It does not record water payments or create receipts."),
        ("Safety", "Check the workbook preview first. If any row has an issue, no readings are saved. Maximum 3,000 rows across both sheets."),
    ]
    guide.append(["Topic", "Instructions"])
    for item in guidance:
        guide.append(list(item))
    guide.column_dimensions["A"].width = 28
    guide.column_dimensions["B"].width = 110
    guide.freeze_panes = "A2"
    guide.sheet_view.showGridLines = False
    for cell in guide[1]:
        cell.fill = PatternFill("solid", fgColor="0F2A3D")
        cell.font = Font(bold=True, color="FFFFFF")
    for row in guide.iter_rows(min_row=2):
        row[0].font = Font(bold=True, color="0F2A3D")
        row[1].alignment = Alignment(wrap_text=True, vertical="top")
        guide.row_dimensions[row[0].row].height = 34

    for sheet_name, fields in (("WaterReadings", UNIT_FIELDS), ("CommercialWaterReadings", COMMERCIAL_FIELDS)):
        sheet = workbook.create_sheet(sheet_name)
        sheet.append(fields)
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = f"A1:{sheet.cell(row=1, column=len(fields)).column_letter}{WATER_BULK_MAX_ROWS + 1}"
        sheet.sheet_view.showGridLines = False
        for cell in sheet[1]:
            cell.fill = PatternFill("solid", fgColor="0F2A3D")
            cell.font = Font(bold=True, color="FFFFFF")
            cell.alignment = Alignment(wrap_text=True, vertical="center")
            sheet.column_dimensions[cell.column_letter].width = max(18, len(cell.value) + 4)
        sheet.row_dimensions[1].height = 30
        sheet.cell(row=1, column=fields.index("reading_month") + 1).number_format = "@"
        for date_field in ("reading_from", "reading_to"):
            sheet.cell(row=1, column=fields.index(date_field) + 1).number_format = "yyyy-mm-dd"

    sheet = workbook["CommercialWaterReadings"]
    validation = DataValidation(type="list", formula1='"2F,UG,LG"', allow_blank=True)
    validation.error = "Select 2F, UG, or LG."
    validation.errorTitle = "Invalid commercial type"
    validation.showErrorMessage = True
    sheet.add_data_validation(validation)
    validation.add(f"A2:A{WATER_BULK_MAX_ROWS + 1}")

    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output


def read_water_rows(stream):
    """Return typed workbook rows as (sheet, row number, field dictionary)."""
    from openpyxl import load_workbook

    stream.seek(0)
    workbook = load_workbook(stream, read_only=True, data_only=True)
    try:
        rows = []
        found_sheet = False
        for sheet_name, fields in (("WaterReadings", UNIT_FIELDS), ("CommercialWaterReadings", COMMERCIAL_FIELDS)):
            if sheet_name not in workbook.sheetnames:
                continue
            found_sheet = True
            sheet = workbook[sheet_name]
            iterator = sheet.iter_rows(values_only=True)
            headers = next(iterator, None)
            legacy_fields = LEGACY_UNIT_FIELDS if sheet_name == "WaterReadings" else LEGACY_COMMERCIAL_FIELDS
            selected_fields = tuple(headers[:len(fields)]) if headers else ()
            if not headers or selected_fields not in (fields, legacy_fields):
                raise ValueError(f"The {sheet_name} sheet columns don't match the CITYLAND9 bulk water-reading template.")
            if len(headers) > len(fields) and any(value not in (None, "") for value in headers[len(fields):]):
                raise ValueError(f"The {sheet_name} sheet has unexpected columns.")
            for row_number, values in enumerate(iterator, start=2):
                values = tuple(values[:len(fields)])
                if all(value in (None, "") for value in values):
                    continue
                rows.append((sheet_name, row_number, dict(zip(selected_fields, values))))
                if len(rows) > WATER_BULK_MAX_ROWS:
                    raise ValueError(f"The workbook exceeds the {WATER_BULK_MAX_ROWS:,}-row limit.")
        if not found_sheet:
            raise ValueError("The workbook must contain a WaterReadings or CommercialWaterReadings sheet from the CITYLAND9 template.")
        return rows
    finally:
        workbook.close()


def cell_text(value):
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value).strip()
