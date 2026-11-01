"""SOA as a real PDF (A4, selectable text), generated on the server with ReportLab.

The figures are exactly those of services/soa.py (soa_detail), the same data the office and
resident screens display. Works offline: no CDN, no remote fonts. The text font is the PC's own
Arial (it has the peso sign); without it, ReportLab's bundled font is used and amounts are written
"PHP 1,234.00".

Layout: compact branded header; statement facts; account; charges with area × rate; previous unpaid
months; penalty explanation; totals kept together; payment history; payment instructions (+ the
payment QR only when a payment link is configured). Long tables continue on the next page with their
header repeated; every page has a footer "statement no. · page X of Y".
"""
import os
import re
from decimal import Decimal
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import BaseDocTemplate, Frame, KeepTogether, PageTemplate, Paragraph, Spacer, Table, TableStyle

NAVY = colors.HexColor("#0f2a3d")
INK = colors.HexColor("#1f2933")
MUTED = colors.HexColor("#5b6773")
RULE = colors.HexColor("#d5dbe0")
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]

_FONTS = None


def fonts():
    """(regular, bold, peso prefix). Arial from Windows when present, else ReportLab's Vera."""
    global _FONTS
    if _FONTS:
        return _FONTS
    win = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    try:
        pdfmetrics.registerFont(TTFont("SOA-Regular", os.path.join(win, "arial.ttf")))
        pdfmetrics.registerFont(TTFont("SOA-Bold", os.path.join(win, "arialbd.ttf")))
        _FONTS = ("SOA-Regular", "SOA-Bold", "₱")
    except Exception:
        import reportlab
        base = os.path.join(os.path.dirname(reportlab.__file__), "fonts")
        pdfmetrics.registerFont(TTFont("SOA-Regular", os.path.join(base, "Vera.ttf")))
        pdfmetrics.registerFont(TTFont("SOA-Bold", os.path.join(base, "VeraBd.ttf")))
        _FONTS = ("SOA-Regular", "SOA-Bold", "PHP ")
    return _FONTS


def peso(value, sign=""):
    peso_sign = fonts()[2]
    d = Decimal(str(value or 0))
    neg = d < 0
    text = f"{peso_sign}{abs(d):,.2f}"
    return f"−{text}" if (neg or sign == "-") and d != 0 else text


def month_label(month):
    y, m = month.split("-")
    return f"{MONTHS[int(m) - 1]} {y}"


def date_label(iso):
    if not iso:
        return "—"
    y, m, d = iso.split("-")
    return f"{MONTHS[int(m) - 1][:3]} {int(d)}, {y}"


def long_date_label(iso):
    if not iso:
        return ""
    y, m, d = iso.split("-")
    return f"{MONTHS[int(m) - 1]} {int(d)}, {y}"


def esc(text):
    return (str(text or "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class NumberedCanvas(rl_canvas.Canvas):
    """Adds "page X of Y" (total known only at the end) and the running footer."""

    def __init__(self, *args, footer="", **kwargs):
        super().__init__(*args, **kwargs)
        self._saved = []
        self._footer = footer

    def showPage(self):
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            regular = fonts()[0]
            self.setFont(regular, 8)
            self.setFillColor(MUTED)
            self.setStrokeColor(RULE)
            self.line(16 * mm, 12 * mm, A4[0] - 16 * mm, 12 * mm)
            self.drawString(16 * mm, 8 * mm, self._footer)
            self.drawRightString(A4[0] - 16 * mm, 8 * mm, f"Page {self._pageNumber} of {total}")
            super().showPage()
        super().save()


def build_soa_pdf(s, qr_payload=None):
    """PDF bytes for one statement `s` (services.soa.soa_detail)."""
    regular, bold, _ = fonts()
    blue = colors.HexColor("#2d4055")
    blue_light = colors.HexColor("#e8eff3")
    green = colors.HexColor("#139b81")
    st = {
        "corp": ParagraphStyle("corp", fontName=bold, fontSize=13, leading=15, textColor=blue),
        "addr": ParagraphStyle("addr", fontName=regular, fontSize=7.5, leading=9, textColor=MUTED),
        "doc": ParagraphStyle("doc", fontName=bold, fontSize=11, leading=13, textColor=green, alignment=TA_RIGHT),
        "docno": ParagraphStyle("docno", fontName=regular, fontSize=8, leading=10, textColor=INK, alignment=TA_RIGHT),
        "label": ParagraphStyle("label", fontName=bold, fontSize=7, leading=8.5, textColor=MUTED),
        "value": ParagraphStyle("value", fontName=bold, fontSize=8.5, leading=10.5, textColor=INK),
        "h": ParagraphStyle("h", fontName=bold, fontSize=9, leading=11, textColor=blue),
        "cell": ParagraphStyle("cell", fontName=regular, fontSize=8, leading=10, textColor=INK),
        "cellb": ParagraphStyle("cellb", fontName=bold, fontSize=8, leading=10, textColor=INK),
        "sub": ParagraphStyle("sub", fontName=regular, fontSize=7, leading=8.5, textColor=MUTED),
        "num": ParagraphStyle("num", fontName=regular, fontSize=8, leading=10, textColor=INK, alignment=TA_RIGHT),
        "numb": ParagraphStyle("numb", fontName=bold, fontSize=8, leading=10, textColor=INK, alignment=TA_RIGHT),
        "body": ParagraphStyle("body", fontName=regular, fontSize=8, leading=10.5, textColor=INK),
        "small": ParagraphStyle("small", fontName=regular, fontSize=7, leading=9, textColor=MUTED),
        "reminder_title": ParagraphStyle("reminder_title", fontName=bold, fontSize=8.5, leading=10, textColor=blue),
        "reminder": ParagraphStyle("reminder", fontName=regular, fontSize=7, leading=8.5, textColor=INK),
        "totalwhite": ParagraphStyle("totalwhite", fontName=bold, fontSize=10, leading=12, textColor=colors.white),
        "totalwhitenum": ParagraphStyle("totalwhitenum", fontName=bold, fontSize=11, leading=13, textColor=colors.white, alignment=TA_RIGHT),
    }
    P = Paragraph
    width = A4[0] - 32 * mm
    acc, ch, pen = s["account"], s["charges"], s["penaltyInfo"]
    story = []

    # ---- invoice header and account block
    period = month_label(s["billingMonth"])
    bill_period = f"{date_label(s['periodStart'])} – {date_label(s['periodEnd'])}"
    blue = colors.HexColor("#2d4055")
    blue_light = colors.HexColor("#e8eff3")
    green = colors.HexColor("#139b81")
    head = Table([[
        [P(esc(s["property"]["name"]), st["corp"]), P(esc(s["property"]["address"]), st["addr"])],
        [P("BILLING INVOICE", st["doc"]), P("ASSOCIATION DUES AND WATER", st["docno"])],
    ]], colWidths=[width * 0.68, width * 0.32])
    head.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("LINEBELOW", (0, 0), (-1, 0), 1.4, blue),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story += [head, Spacer(1, 5)]

    billed_to = Table([
        [P("BILLED TO:", st["cellb"])],
        [P(esc(acc["billTo"] or "—"), st["cellb"])],
        [P(f"UNIT: {esc(acc['unitNo'] or '—')}", st["cell"])],
        [P(f"OWNER: {esc(acc['ownerName'] or '—')}", st["sub"])],
        [P(f"TENANT: {esc(acc['tenantName'] or '—')}", st["sub"])],
        [P(esc(s["property"]["name"]), st["cell"])],
        [P(esc(s["property"]["address"]), st["sub"])],
    ], colWidths=[width * 0.54])
    billed_to.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), blue), ("TEXTCOLOR", (0, 0), (0, 0), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.6, RULE), ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#f7f8f9")),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    bases = s["bases"]
    unit_area = (f"{Decimal(str(bases['condo']['area'])):,.2f} sqm"
                 if bases.get("condo") and bases["condo"].get("area") is not None else (acc["unitType"] or "—"))
    meta_rows = [
        [P(esc(label), st["cellb"]), P(esc(value), st["cell"])]
        for label, value in (
            ("Invoice No.", s["statementNo"]),
            ("Statement Date", date_label(s["issueDate"])),
            ("Billing Period", period),
            ("Unit Area", unit_area),
            ("Payment Due", date_label(s["dueDate"])),
        )
    ]
    meta = Table(meta_rows, colWidths=[width * 0.14, width * 0.30])
    meta.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, RULE), ("INNERGRID", (0, 0), (-1, -1), 0.3, RULE),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f7f8f9")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    account_block = Table([[billed_to, meta]], colWidths=[width * 0.55, width * 0.45])
    account_block.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4), ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story += [account_block, Spacer(1, 10)]

    def section_bar(title, background=blue, foreground=colors.white):
        heading = ParagraphStyle(f"section-{title}", parent=st["h"], textColor=foreground)
        bar = Table([[P(title, heading)]], colWidths=[width])
        bar.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), background), ("BOX", (0, 0), (-1, -1), 0.5, background),
            ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        return bar

    def detail_row(label, detail, amount, *, bold_row=False, negative=False):
        amount_text = peso(amount)
        if negative and Decimal(str(amount or 0)) > 0:
            amount_text = f"−{amount_text}"
        return [
            P(esc(label), st["cellb"] if bold_row else st["cell"]),
            P(esc(detail), st["sub"]),
            P(amount_text, st["numb"] if bold_row else st["num"]),
        ]

    def make_charges_table(title, rows, total_label, total_amount, header_labels, header_color=blue):
        data = [[P(esc(label), ParagraphStyle(f"{title}-{i}", parent=st["label"], textColor=colors.white))
                 for i, label in enumerate(header_labels)]]
        data.extend(rows)
        data.append([P(esc(total_label), st["cellb"]), P("", st["cell"]), P(peso(total_amount), st["numb"])])
        table = Table(data, colWidths=[width * 0.43, width * 0.37, width * 0.20], repeatRows=1, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), header_color), ("BACKGROUND", (0, -1), (-1, -1), blue_light),
            ("BOX", (0, 0), (-1, -1), 0.6, blue), ("LINEBELOW", (0, 0), (-1, 0), 0.7, blue),
            ("LINEABOVE", (0, -1), (-1, -1), 0.6, blue), ("INNERGRID", (0, 1), (-1, -2), 0.25, RULE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        return [section_bar(title, header_color), table]

    # ---- current period charges
    def basis_text(kind):
        basis = bases.get(kind)
        if not basis:
            return "Basis not recorded for this statement" if not bases.get("recorded") else ""
        where = f"{basis['unitNo']}: " if basis.get("unitNo") else ""
        if basis["basis"] == "manual":
            return f"{where}fixed monthly amount"
        return f"{where}{Decimal(basis['area']):,.2f} sqm × {fonts()[2]}{basis['rate']}/sqm"

    reading_month = ch.get("waterReadingMonth")
    water_period = month_label(reading_month) if reading_month else period
    reading_from, reading_to = ch.get("waterReadingFrom"), ch.get("waterReadingTo")
    water_date_range = (f"Water reading: {long_date_label(reading_from)} to {long_date_label(reading_to)}"
                        if reading_from and reading_to else f"Water reading: {water_period}")
    water_usage = format(Decimal(str(ch["waterUsage"])).normalize(), "f") if ch["waterUsage"] is not None else ""
    allocation = ch.get("waterAllocation")
    if allocation:
        water_detail = (f"Shared {allocation['commercialType']} · {water_date_range} · {water_usage} m³ × {peso(ch['waterRate'])}/m³"
                        f" · {allocation['unitArea']} of {allocation['totalArea']} sqm · "
                        f"{peso(allocation['unitAmount'])} allocated from {peso(allocation['sharedAmount'])}")
    else:
        water_detail = water_date_range
        if water_usage and ch["waterRate"] is not None:
            water_detail += f" · Usage: {water_usage} m³ × {peso(ch['waterRate'])}/m³"
    if ch["waterPaidSeparately"]:
        water_detail += " · paid separately"

    current_rows = [detail_row("Association Dues", f"{bill_period} · {basis_text('condo')}", ch["condoDues"])]
    if Decimal(ch["parking"]) != 0 or bases.get("parking"):
        current_rows.append(detail_row("Parking", basis_text("parking"), ch["parking"]))
    if Decimal(ch["storage"]) != 0 or bases.get("storage"):
        storage_detail = basis_text("storage")
        if not ch["storageIncluded"]:
            storage_detail = (storage_detail + "; " if storage_detail else "") + f"not included in total; charged from {month_label(ch['storageFrom'])}"
        current_rows.append(detail_row("Storage", storage_detail, ch["storage"] if ch["storageIncluded"] else "0"))
    current_rows.append(detail_row("Water Consumption", water_detail if reading_month else "No reading for this period",
                                   "0" if ch["waterPaidSeparately"] else ch["water"]))
    if ch.get("otherItems"):
        for item in ch["otherItems"]:
            current_rows.append(detail_row(item["description"], "Other charge", item["amount"]))
    elif Decimal(ch["other"]) != 0:
        current_rows.append(detail_row("Other charges", "", ch["other"]))
    if Decimal(ch["adjustment"]) != 0:
        current_rows.append(detail_row("Adjustment (credit)" if Decimal(ch["adjustment"]) < 0 else "Adjustment", "", ch["adjustment"]))
    if Decimal(ch["vat"]) != 0:
        vat_detail = f"{ch['vatRate']}% on {', '.join(ch['vatEligible'])}; taxable amount {peso(ch['vatBase'])}"
        current_rows.append(detail_row(f"VAT ({ch['vatRate']}%)", vat_detail, ch["vat"]))
    if Decimal(ch["advanceApplied"]) != 0:
        current_rows.append(detail_row("Advance payment applied", "Prepaid condo dues", ch["advanceApplied"], negative=True))
    current_total = Decimal(ch["currentCharges"]) - Decimal(ch["advanceApplied"])
    story.extend(make_charges_table(
        "CURRENT PERIOD CHARGES", current_rows, "Total Current Charges", current_total,
        ("Particulars / Description", "Meter / Details", "Amount (PHP)"),
    ))
    story.append(Spacer(1, 8))

    # ---- carried balances and penalties
    previous_rows = [
        detail_row("Prior SOA balance",
                   f"{month_label(item['month'])} · {item['statementNo']} · due {date_label(item['dueDate'])}",
                   item["balance"])
        for item in s["previousUnpaid"]
    ]
    listed_previous = sum((Decimal(item["balance"]) for item in s["previousUnpaid"]), Decimal("0"))
    previous_balance = Decimal(ch["previousBalance"])
    if previous_balance != listed_previous:
        previous_rows.append(detail_row(
            "Unitemized carried balance",
            "No matching earlier SOA is available in the records to break down this amount.",
            previous_balance - listed_previous,
        ))
    if Decimal(ch["penalty"]) != 0:
        previous_rows.append(detail_row("Account Penalty", f"Late payment charge ({pen['rate']}%)", ch["penalty"]))
    overdue_total = previous_balance + Decimal(ch["penalty"])
    if overdue_total != 0:
        story.extend(make_charges_table(
            "OVERDUE BALANCES & ARREARS", previous_rows, "Total Overdue Balance", overdue_total,
            ("Prior Account Particulars", "Arrears Period", "Amount (PHP)"),
            header_color=colors.HexColor("#7c8b8e"),
        ))
        story.append(Spacer(1, 8))

    # ---- invoice total summary
    summary_rows = [[P("Total Current Charges:", st["cell"]), P(peso(current_total), st["num"])]]
    if overdue_total != 0:
        summary_rows.append([P("Total Overdue Balance & Penalties:", st["cell"]), P(peso(overdue_total), st["num"])])
    current_summary = Table(summary_rows, colWidths=[width * 0.34, width * 0.20], hAlign="RIGHT")
    current_summary.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, green), ("INNERGRID", (0, 0), (-1, -1), 0.3, RULE),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f6f8f8")),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    total = Table([[
        P("TOTAL AMOUNT DUE:", st["totalwhite"]),
        P(peso(s["total"]), st["totalwhitenum"]),
    ]], colWidths=[width * 0.55, width * 0.45])
    total.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), green), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story += [current_summary, Spacer(1, 3), total]
    if s.get("correctedByHand"):
        story.append(P("This statement was corrected by the Admin Office.", st["small"]))
    if s.get("note"):
        story.append(P(f"<b>Note.</b> {esc(s['note'])}", st["small"]))
    story.append(Spacer(1, 8))

    reminders = [
        Spacer(1, 8),
        section_bar("REMINDERS & PAYMENT POLICIES"),
        P("1. Payments shall be accepted at the <b>Administration Office</b> located at the UG28, CITYLAND DELA ROSA CONDOMINIUM INCORPORATED.", st["reminder"]),
        P("2. Full payment must be made <b>on or before every 8th of the month</b> to avoid a further <b>4% penalty per month</b>, wherein a fraction thereof shall be deemed equivalent to one month.", st["reminder"]),
        P("3. Issue your check in favor of: <b>CITYLAND DELA ROSA CONDOMINIUM INCORPORATED.</b>", st["reminder"]),
        P("4. For cash payment, we encourage exact amount to expedite O.R. issuance and facilitate deposit. Please bring this statement of account when paying.", st["reminder"]),
        P("5. Penalty will be collected for non-payment on due dates even if the reason is non-receipt or delayed receipt of the bill. If you fail to receive your bill on or before the <b>8th of every month</b>, call the <b>Admin Office at 02-8559-9048</b>.", st["reminder"]),
        P("6. The Association shall not be liable for cash/check payments given to the guards.", st["reminder"]),
        P("<b>Any and all disconnection notice(s) received prior to this Statement of Account shall remain valid and effective unless and until the arrears indicated in said notice(s) are fully settled.</b>", st["reminder"]),
    ]
    story.append(KeepTogether(reminders))

    out = BytesIO()
    footer = f"{s['property']['name']} · Statement {s['statementNo']} · Unit {acc['unitNo']}"
    doc = BaseDocTemplate(out, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm, bottomMargin=18 * mm,
                          title=f"Statement of Account {s['statementNo']}", author=s["property"]["name"],
                          subject=f"Unit {acc['unitNo']} · {month_label(s['billingMonth'])}")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="soa",
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates(PageTemplate(id="soa", frames=frame))
    doc.build(story, canvasmaker=lambda *a, **k: NumberedCanvas(*a, footer=footer, **k))
    return out.getvalue()


def build_combined_soa_pdf(statements):
    """Combine already-rendered SOAs while preserving each statement's own page footer."""
    from pypdf import PdfReader, PdfWriter

    combined = PdfWriter()
    for statement in statements:
        reader = PdfReader(BytesIO(build_soa_pdf(statement)))
        for page in reader.pages:
            combined.add_page(page)
    output = BytesIO()
    combined.write(output)
    return output.getvalue()


def pdf_response(legacy, bill, *, inline=False, payer_type=None):
    """The SOA PDF as a download (application/pdf, attachment). A generation failure answers a JSON
    error (never an HTML page saved as .pdf) and is logged."""
    from flask import current_app, make_response

    from ..utils.auth import json_error
    from .soa import soa_detail
    try:
        data = build_soa_pdf(soa_detail(legacy, bill, payer_type=payer_type),
                             legacy.soa_qr_payload(bill, configured_only=True, payer_type=payer_type))
    except ValueError as exc:
        return json_error(409, str(exc))
    except Exception:
        current_app.logger.exception("SOA PDF generation failed for bill %s", bill.id)
        return json_error(500, "The SOA PDF could not be generated. Nothing was changed; please try again or contact the administrator.")
    unit = re.sub(r"[^A-Za-z0-9-]+", "-", bill.unit.unit_no if bill.unit else str(bill.unit_id)).strip("-") or "unit"
    resp = make_response(data)
    resp.headers["Content-Type"] = "application/pdf"
    disposition = "inline" if inline else "attachment"
    payer_suffix = f"_{payer_type}" if payer_type else ""
    resp.headers["Content-Disposition"] = f'{disposition}; filename="SOA_{unit}_{bill.billing_month}{payer_suffix}.pdf"'
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp
