import json

from fpdf import FPDF

from core import logo
from rechnungen.models import eur
from rechnungen.pdf import _font

MONTHS = ["Jänner", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]


def render_pdf(invoice) -> bytes:
    issuer = json.loads(invoice.issuer_snapshot)
    year, month = (int(x) for x in invoice.period.split("-"))
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(True, 20)
    pdf.set_margins(20, 20, 20)
    pdf.add_font("Roboto", "", _font("Roboto-Regular.ttf"))
    pdf.add_font("Roboto", "B", _font("Roboto-Bold.ttf"))
    pdf.add_page()
    logo.draw_on_pdf(pdf, issuer.get("logo", ""))

    def line(text, size=10, style="", h=5):
        pdf.set_font("Roboto", style, size)
        pdf.multi_cell(0, h, text, new_x="LMARGIN", new_y="NEXT")

    line(issuer["name"], 12, "B", 6)
    line(issuer["address"])
    pdf.ln(12)
    line(invoice.tenant.name, 11, "", 6)
    pdf.ln(10)
    storno = invoice.status == "storniert"
    line(f"Mietabrechnung {invoice.number}" + (" (STORNIERT)" if storno else ""), 15, "B", 8)
    line(f"Rechnungsdatum: {invoice.issued_on:%d.%m.%Y}")
    line(f"Abrechnungszeitraum: {MONTHS[month - 1]} {year}")
    pdf.ln(6)
    for label, cents in (("Miete", invoice.rent_cents), ("Betriebskosten", invoice.utilities_cents)):
        if cents:
            pdf.set_font("Roboto", "", 10)
            pdf.cell(120, 7, label)
            pdf.cell(0, 7, eur(cents), align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Roboto", "B", 11)
    pdf.cell(120, 9, "Gesamtbetrag", border="T")
    pdf.cell(0, 9, eur(invoice.total_cents), border="T", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)
    line(issuer["tax_text"])
    pdf.ln(4)
    line("Zahlung", 10, "B")
    line(f"Zahlbar bis {invoice.due_on:%d.%m.%Y} ohne Abzug.")
    line(f"IBAN {issuer['iban']}")
    line(f"Verwendungszweck: {invoice.number}")
    return bytes(pdf.output())
