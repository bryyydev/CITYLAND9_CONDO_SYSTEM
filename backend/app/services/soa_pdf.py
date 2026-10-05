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
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

NAVY = colors.HexColor("#0f2a3d")
TEAL = colors.HexColor("#0b6b7a")
TEAL_LIGHT = colors.HexColor("#e6f2f4")
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
    """PDF bytes for one statement `s` (services.soa.soa_detail). qr_payload: text for the payment QR,
    or None (no QR)."""
    regular, bold, _ = fonts()
    st = {
        "corp": ParagraphStyle("corp", fontName=bold, fontSize=13.5, leading=16, textColor=NAVY),
        "addr": ParagraphStyle("addr", fontName=regular, fontSize=8.5, leading=11, textColor=MUTED),
        "doc": ParagraphStyle("doc", fontName=bold, fontSize=11, leading=13, textColor=TEAL, alignment=TA_RIGHT),
        "docno": ParagraphStyle("docno", fontName=regular, fontSize=9, leading=12, textColor=INK, alignment=TA_RIGHT),
        "label": ParagraphStyle("label", fontName=bold, fontSize=7.5, leading=9, textColor=MUTED),
        "value": ParagraphStyle("value", fontName=bold, fontSize=10, leading=12.5, textColor=INK),
        "h": ParagraphStyle("h", fontName=bold, fontSize=9.5, leading=12, textColor=TEAL, spaceBefore=8, spaceAfter=4),
        "cell": ParagraphStyle("cell", fontName=regular, fontSize=9.5, leading=12, textColor=INK),
        "cellb": ParagraphStyle("cellb", fontName=bold, fontSize=9.5, leading=12, textColor=INK),
        "sub": ParagraphStyle("sub", fontName=regular, fontSize=8, leading=10, textColor=MUTED),
        "num": ParagraphStyle("num", fontName=regular, fontSize=9.5, leading=12, textColor=INK, alignment=TA_RIGHT),
        "numb": ParagraphStyle("numb", fontName=bold, fontSize=9.5, leading=12, textColor=INK, alignment=TA_RIGHT),
        "body": ParagraphStyle("body", fontName=regular, fontSize=9, leading=12.5, textColor=INK),
        "small": ParagraphStyle("small", fontName=regular, fontSize=8, leading=10.5, textColor=MUTED),
        "big": ParagraphStyle("big", fontName=bold, fontSize=12.5, leading=15, textColor=NAVY),
        "bignum": ParagraphStyle("bignum", fontName=bold, fontSize=12.5, leading=15, textColor=NAVY, alignment=TA_RIGHT),
    }
    P = Paragraph
    width = A4[0] - 32 * mm
    acc, ch, pen = s["account"], s["charges"], s["penaltyInfo"]
    story = []

    # ---- header
    head = Table([[
        [P(esc(s["property"]["name"]), st["corp"]), P(esc(s["property"]["address"]), st["addr"])],
        [P("STATEMENT OF ACCOUNT", st["doc"]), P(f"No. {esc(s['statementNo'])}", st["docno"])],
    ]], colWidths=[width * 0.62, width * 0.38])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                              ("LINEBELOW", (0, 0), (-1, 0), 1.6, TEAL), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    story += [head, Spacer(1, 7)]

    # ---- statement facts
    def fact(label, value):
        return [P(label, st["label"]), P(esc(value), st["value"])]
    facts = Table([
        [fact("UNIT", acc["unitNo"]), fact("BILLING PERIOD", f"{date_label(s['periodStart'])} – {date_label(s['periodEnd'])}"), fact("STATEMENT DATE", date_label(s["issueDate"]))],
        [fact("DUE DATE", date_label(s["dueDate"])), fact("STATUS", s["status"]), fact("BALANCE" if Decimal(s["balance"]) >= 0 else "CREDIT", peso(abs(Decimal(s["balance"]))))],
    ], colWidths=[width / 3] * 3)
    facts.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f4f7f8")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5), ("LEFTPADDING", (0, 0), (-1, -1), 7),
                               ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.white)]))
    story += [facts, Spacer(1, 6)]

    # ---- account
    rows = [["Bill to", acc["billTo"] or "—"], ["Owner", acc["ownerName"] or "—"]]
    if acc["tenantName"]:
        rows.append(["Tenant", acc["tenantName"]])
    rows.append(["Unit type", " · ".join(x for x in (acc["unitType"], f"Floor {acc['floor']}" if acc["floor"] else "") if x) or "—"])
    account = Table([[P(k, st["sub"]), P(esc(v), st["cell"])] for k, v in rows], colWidths=[28 * mm, width - 28 * mm])
    account.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5)]))
    story += [P("ACCOUNT", st["h"]), account]

    # ---- charges
    bases = s["bases"]

    def basis_text(kind):
        b = bases.get(kind)
        if not b:
            return "Basis not recorded for this statement" if not bases.get("recorded") else ""
        where = f"{b['unitNo']}: " if b.get("unitNo") else ""
        if b["basis"] == "manual":
            return f"{where}fixed monthly amount"
        return f"{where}{Decimal(b['area']):,.2f} sqm × {fonts()[2]}{b['rate']}/sqm"

    lines = [[P("DESCRIPTION", st["label"]), P("BASIS", st["label"]), P("AMOUNT", ParagraphStyle("lr", parent=st["label"], alignment=TA_RIGHT))]]

    def line(desc, basis, amount, bold_row=False, minus=False):
        lines.append([P(esc(desc), st["cellb"] if bold_row else st["cell"]), P(esc(basis), st["sub"]),
                      P(peso(amount, "-" if minus else ""), st["numb"] if bold_row else st["num"])])

    period = month_label(s["billingMonth"])
    line(f"Condo dues · {period}", basis_text("condo"), ch["condoDues"])
    if Decimal(ch["parking"]) != 0 or bases.get("parking"):
        line("Parking", basis_text("parking"), ch["parking"])
    if Decimal(ch["storage"]) != 0 or bases.get("storage"):
        stor = basis_text("storage")
        if not ch["storageIncluded"]:
            stor = (stor + "; " if stor else "") + "not included in this total"
        line("Storage", stor, ch["storage"] if ch["storageIncluded"] else "0")
    if ch["waterPaidSeparately"]:
        wb = "paid separately; not included in this total"
    elif ch["waterUsage"] is not None:
        wb = f"{ch['waterUsage']} m³ × {peso(ch['waterRate'])}/m³"
    else:
        wb = "no reading for this period"
    line(f"Water · {period}", wb, "0" if ch["waterPaidSeparately"] else ch["water"])
    if Decimal(ch["other"]) != 0:
        line("Other charges", "", ch["other"])
    if Decimal(ch["adjustment"]) != 0:
        line("Adjustment" + (" (credit)" if Decimal(ch["adjustment"]) < 0 else ""), "", ch["adjustment"])
    line("Current charges", "", ch["currentCharges"], bold_row=True)
    sub_rows = [len(lines) - 1]
    if Decimal(ch["previousBalance"]) != 0:
        still = "; ".join(f"{month_label(p['month'])} {peso(p['balance'])}" for p in s["previousUnpaid"])
        line("Previous unpaid balance", "carried from earlier statements when issued" + (f"; still unpaid now: {still}" if still else "; since paid"),
             ch["previousBalance"], bold_row=True)
        sub_rows.append(len(lines) - 1)
    if Decimal(ch["penalty"]) != 0:
        line(f"Late payment penalty ({pen['rate']}%)", f"on {peso(pen['base'])} unpaid {', '.join(pen['eligible'])}", ch["penalty"])
    if Decimal(ch["advanceApplied"]) != 0:
        line("Advance payment applied", "prepaid condo dues", ch["advanceApplied"], minus=True)
    charges = Table(lines, colWidths=[width * 0.40, width * 0.38, width * 0.22], repeatRows=1)
    style = [("LINEBELOW", (0, 0), (-1, 0), 0.8, NAVY), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
             ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
             ("LINEBELOW", (0, 1), (-1, -1), 0.4, RULE)]
    for r in sub_rows:
        style.append(("BACKGROUND", (0, r), (-1, r), TEAL_LIGHT))
    charges.setStyle(TableStyle(style))
    story += [P("CHARGES", st["h"]), charges, Spacer(1, 8)]

    # ---- totals (kept together) + penalty explanation
    balance = Decimal(s["balance"])
    total_rows = [[P("Total amount due", st["cellb"]), P(peso(s["total"]), st["numb"])],
                  [P("Payments received", st["cell"]), P(peso(s["amountPaid"], "-"), st["num"])],
                  [P("CREDIT (OVERPAID)" if balance < 0 else "BALANCE DUE", st["big"]), P(peso(abs(balance)), st["bignum"])]]
    if s.get("issuedAmount") is not None and s["issuedAmount"] != s["total"]:
        total_rows.insert(0, [P("Amount due as first issued", st["sub"]), P(peso(s["issuedAmount"]), ParagraphStyle("si", parent=st["sub"], alignment=TA_RIGHT))])
    totals = Table(total_rows, colWidths=[width * 0.30, width * 0.22], hAlign="RIGHT")
    totals.setStyle(TableStyle([("LINEABOVE", (0, -1), (-1, -1), 1.2, NAVY), ("BACKGROUND", (0, -1), (-1, -1), TEAL_LIGHT),
                                ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5), ("TOPPADDING", (0, -1), (-1, -1), 6), ("BOTTOMPADDING", (0, -1), (-1, -1), 6)]))
    block = [totals, Spacer(1, 6), P(f"<b>Penalty.</b> {esc(pen['explanation'])}", st["small"])]
    if s.get("correctedByHand"):
        block.append(P("This statement was corrected by the Admin Office.", st["small"]))
    if s.get("note"):
        block.append(P(f"<b>Note.</b> {esc(s['note'])}", st["small"]))
    story.append(KeepTogether(block))

    # ---- payment history
    story.append(P("PAYMENTS FOR THIS STATEMENT", st["h"]))
    if not s["payments"]:
        story.append(P("No payments recorded for this statement yet.", st["body"]))
    else:
        pay = [[P(h, st["label"]) for h in ("DATE", "OR NO.", "REFERENCE", "METHOD")] + [P("AMOUNT", ParagraphStyle("pr", parent=st["label"], alignment=TA_RIGHT))]]
        for p in s["payments"]:
            amount = peso(p["amount"]) + (" (reversed)" if p["reversed"] else "")
            pay.append([P(date_label(p["date"]), st["cell"]), P(esc(p["receiptNo"] or "—"), st["cell"]), P(esc(p["reference"] or "—"), st["cell"]),
                        P(esc(f"{(p['type'] or '').title()} · {(p['method'] or '').title()}"), st["cell"]), P(esc(amount), st["num"])])
        pt = Table(pay, colWidths=[width * 0.16, width * 0.20, width * 0.24, width * 0.18, width * 0.22], repeatRows=1)
        pt.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 0.8, NAVY), ("LINEBELOW", (0, 1), (-1, -1), 0.4, RULE),
                                ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5), ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
        story.append(pt)

    # ---- how to pay (+ QR only when a payment link is configured)
    how = [P("HOW TO PAY", st["h"]), P(esc(s["payment"]["instructions"]), st["body"]),
           P(f"Quote statement no. <b>{esc(s['statementNo'])}</b> and unit <b>{esc(acc['unitNo'])}</b>. "
             "For questions about this statement, contact the Admin Office.", st["small"])]
    if qr_payload:
        import qrcode
        img = qrcode.make(qr_payload)
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        qr = Image(buf, width=30 * mm, height=30 * mm)
        box = Table([[how, [qr, P("Scan to pay online", ParagraphStyle("qc", parent=st["small"], alignment=1))]]], colWidths=[width - 36 * mm, 36 * mm])
        box.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        story.append(KeepTogether([Spacer(1, 4), box]))
    else:
        story.append(KeepTogether([Spacer(1, 4)] + how))

    out = BytesIO()
    footer = f"{s['property']['name']} · Statement {s['statementNo']} · Unit {acc['unitNo']}"
    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm, bottomMargin=18 * mm,
                            title=f"Statement of Account {s['statementNo']}", author=s["property"]["name"],
                            subject=f"Unit {acc['unitNo']} · {month_label(s['billingMonth'])}")
    doc.build(story, canvasmaker=lambda *a, **k: NumberedCanvas(*a, footer=footer, **k))
    return out.getvalue()


def pdf_response(legacy, bill):
    """The SOA PDF as a download (application/pdf, attachment). A generation failure answers a JSON
    error (never an HTML page saved as .pdf) and is logged."""
    from flask import current_app, make_response

    from ..utils.auth import json_error
    from .soa import soa_detail
    try:
        data = build_soa_pdf(soa_detail(legacy, bill), legacy.soa_qr_payload(bill, configured_only=True))
    except Exception:
        current_app.logger.exception("SOA PDF generation failed for bill %s", bill.id)
        return json_error(500, "The SOA PDF could not be generated. Nothing was changed; please try again or contact the administrator.")
    unit = re.sub(r"[^A-Za-z0-9-]+", "-", bill.unit.unit_no if bill.unit else str(bill.unit_id)).strip("-") or "unit"
    resp = make_response(data)
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = f'attachment; filename="SOA_{unit}_{bill.billing_month}.pdf"'
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp
