import io
from datetime import datetime
from decimal import Decimal
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.graphics.barcode import code128
from reportlab.graphics.shapes import Drawing, Line, Rect
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

APP_NAME = "Billing Pro"
BRAND_PRIMARY = "#0f766e"
BRAND_ACCENT = "#f59e0b"


def brand_logo():
    logo = Drawing(40, 40)
    logo.add(Rect(0, 0, 40, 40, 8, fillColor=colors.HexColor(BRAND_PRIMARY), strokeColor=None))
    logo.add(Rect(11, 9, 19, 25, 2, fillColor=colors.HexColor("#f4fbf9"), strokeColor=None))
    for y in (15, 21, 27):
        logo.add(Line(15, y, 26 if y != 15 else 22, y, strokeColor=colors.HexColor(BRAND_PRIMARY), strokeWidth=2))
    logo.add(Line(29, 31, 33, 35, strokeColor=colors.HexColor(BRAND_ACCENT), strokeWidth=2.5))
    return logo


def build_invoice_pdf(cart_items, total, invoice_no, customer_name=""):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=17 * mm, bottomMargin=17 * mm, leftMargin=18 * mm, rightMargin=18 * mm)
    styles = getSampleStyleSheet()
    title = ParagraphStyle("InvoiceTitle", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=22, textColor=colors.HexColor(BRAND_PRIMARY), alignment=TA_CENTER)
    meta = ParagraphStyle("Meta", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#64748b"))
    right = ParagraphStyle("Right", parent=meta, alignment=TA_RIGHT)
    logo = brand_logo()
    barcode = code128.Code128(f"INV-{invoice_no}", barHeight=11 * mm, barWidth=.38)
    elements = [Table([[logo, Paragraph(APP_NAME.upper(), title), barcode]], colWidths=[20 * mm, 105 * mm, 45 * mm], style=TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (2, 0), (2, 0), "RIGHT")])), Paragraph("Retail billing and point of sale", ParagraphStyle("Tag", parent=meta, alignment=TA_CENTER)), Spacer(1, 7 * mm)]
    info = [[Paragraph(f"<b>Invoice</b><br/>INV-{invoice_no}<br/><br/><b>Customer</b><br/>{customer_name or 'Walk-in Customer'}", meta), Paragraph(f"<b>Issued</b><br/>{datetime.now().strftime('%d %b %Y, %H:%M')}<br/><br/><b>Status</b><br/>Paid", right)]]
    info_table = Table(info, colWidths=[90 * mm, 80 * mm])
    info_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOX", (0, 0), (-1, -1), .5, colors.HexColor("#e2e8f0")), ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")), ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 9)]))
    elements += [info_table, Spacer(1, 8 * mm)]
    rows = [["Item", "Code", "Qty", "Rate (Rs.)", "Tax (Rs.)", "Amount (Rs.)"]]
    for item in cart_items:
        rate = Decimal(str(item.get("unit_price", 0)))
        tax = Decimal(str(item.get("tax", 0)))
        rows.append([item.get("detail", ""), item.get("product_id", ""), str(item.get("quantity", 0)), f"{rate:.2f}", f"{tax:.2f}", f"{rate * item.get('quantity', 0) + tax:.2f}"])
    table = Table(rows, colWidths=[58 * mm, 25 * mm, 17 * mm, 23 * mm, 20 * mm, 27 * mm], repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 8.5), ("GRID", (0, 0), (-1, -1), .35, colors.HexColor("#e2e8f0")), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]), ("ALIGN", (2, 1), (-1, -1), "RIGHT"), ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    elements += [table, Spacer(1, 8 * mm), Paragraph(f"<b>Grand total: Rs. {Decimal(str(total)):.2f}</b>", ParagraphStyle("Total", parent=right, fontSize=14, textColor=colors.HexColor(BRAND_PRIMARY))), Spacer(1, 18 * mm), Paragraph("Thank you for your business.", ParagraphStyle("Footer", parent=meta, alignment=TA_CENTER, textColor=colors.HexColor(BRAND_ACCENT)))]
    doc.build(elements)
    return buffer.getvalue()
