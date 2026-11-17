"""PDF-Erzeugung der Honorarnote. Das PDF wird bei Bedarf erzeugt und nicht gespeichert:
Inhalt kommt aus den festgeschriebenen Daten der Honorarnote (Schnappschuss), ist also jederzeit reproduzierbar."""
import json
from pathlib import Path

import font_roboto
from fpdf import FPDF

from core import logo

from .models import Honorarnote, eur
from .services import get_profile

def _font(name):
    """Sucht die Schriftdatei im Paket (Ordnerstruktur kann je nach Version variieren)."""
    for path in Path(font_roboto.__file__).parent.rglob(name):
        return str(path)
    raise FileNotFoundError(f"Schrift {name} nicht gefunden (Paket font-roboto installiert?)")


class _Pdf(FPDF):
    def __init__(self, draft):
        super().__init__(format="A4")
        self.draft = draft
        self.set_auto_page_break(auto=True, margin=22)
        self.set_margins(20, 18, 20)
        self.add_font("Roboto", "", _font("Roboto-Regular.ttf"))
        self.add_font("Roboto", "B", _font("Roboto-Bold.ttf"))

    def header(self):
        if self.draft:
            self.set_font("Roboto", "B", 11)
            self.set_text_color(170, 20, 20)
            self.cell(0, 6, "ENTWURF: keine gültige Honorarnote", align="C", new_x="LMARGIN", new_y="NEXT")
            self.set_text_color(0, 0, 0)

    def footer(self):
        self.set_y(-15)
        self.set_font("Roboto", "", 8)
        self.set_text_color(110, 110, 110)
        self.cell(0, 8, f"Seite {self.page_no()} von {{nb}}", align="C")
        self.set_text_color(0, 0, 0)


def _lines(text):
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def render_pdf(invoice: Honorarnote, compress=True) -> bytes:
    draft = invoice.is_draft
    snap = json.loads(invoice.issuer_snapshot) if invoice.issuer_snapshot else None
    if snap is None:  # Entwurf: aktuelle Angaben des Therapeuten
        profile = get_profile()
        snap = {
            "name": profile.name if profile else "", "title": profile.title if profile else "",
            "address": profile.address if profile else "", "phone": profile.phone if profile else "",
            "email": profile.email if profile else "", "tax_no": profile.tax_no if profile else "",
            "uid": profile.uid if profile else "", "iban": profile.iban if profile else "", "bic": profile.bic if profile else "",
            "exemption_text": profile.exemption_text if profile else "", "payment_days": profile.payment_days if profile else 14,
            "logo": profile.logo_b64 if profile else "",
        }
    recipient_name = invoice.recipient_name or invoice.patient.full_name
    recipient_address = invoice.recipient_address if invoice.is_issued else invoice.patient.address

    pdf = _Pdf(draft)
    pdf.set_compression(compress)
    pdf.set_title(f"Honorarnote {invoice.number or 'Entwurf'}")
    pdf.alias_nb_pages()
    pdf.add_page()

    # Aussteller (Logo oben rechts, falls vorhanden)
    logo.draw_on_pdf(pdf, snap.get("logo", ""))
    pdf.set_font("Roboto", "B", 13)
    pdf.cell(0, 7, snap["name"], new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Roboto", "", 9.5)
    for line in [snap["title"], *_lines(snap["address"])]:
        pdf.cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")
    contact = " · ".join(x for x in (snap["phone"], snap["email"]) if x)
    if contact:
        pdf.cell(0, 5, contact, new_x="LMARGIN", new_y="NEXT")
    ids = " · ".join(x for x in (f"Steuernr. {snap['tax_no']}" if snap["tax_no"] else "", f"UID {snap['uid']}" if snap["uid"] else "") if x)
    if ids:
        pdf.cell(0, 5, ids, new_x="LMARGIN", new_y="NEXT")

    # Empfänger
    pdf.ln(10)
    pdf.set_font("Roboto", "", 11)
    for line in [recipient_name, *_lines(recipient_address)]:
        pdf.cell(0, 5.5, line, new_x="LMARGIN", new_y="NEXT")

    # Titel
    pdf.ln(10)
    pdf.set_font("Roboto", "B", 15)
    if invoice.kind == Honorarnote.Kind.STORNO:
        title = f"Stornorechnung {invoice.number or ''}".strip()
    else:
        title = f"Honorarnote {invoice.number}" if invoice.number else "Honorarnote"
    pdf.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Roboto", "", 10)
    if invoice.kind == Honorarnote.Kind.STORNO and invoice.cancels_id:
        pdf.cell(0, 5.5, f"Storno zur Honorarnote {invoice.cancels.number}", new_x="LMARGIN", new_y="NEXT")
    if invoice.issue_date:
        pdf.cell(0, 5.5, f"Rechnungsdatum: {invoice.issue_date:%d.%m.%Y}", new_x="LMARGIN", new_y="NEXT")
    if invoice.service_from:
        period = f"{invoice.service_from:%d.%m.%Y}" + (
            f" bis {invoice.service_to:%d.%m.%Y}" if invoice.service_to != invoice.service_from else ""
        )
        pdf.cell(0, 5.5, f"Leistungszeitraum: {period}", new_x="LMARGIN", new_y="NEXT")
    if invoice.referral_id:
        pdf.cell(0, 5.5, f"Zuweisender Arzt: {invoice.referral.doctor}", new_x="LMARGIN", new_y="NEXT")

    # Positionen
    pdf.ln(6)
    widths = (24, 76, 16, 27, 27)
    pdf.set_font("Roboto", "B", 9.5)
    for text, w, align in zip(("Datum", "Leistung", "Menge", "Einzelpreis", "Betrag"), widths, ("L", "L", "R", "R", "R")):
        pdf.cell(w, 7, text, border="B", align=align)
    pdf.ln()
    pdf.set_font("Roboto", "", 9.5)
    for p in invoice.positions.all():
        y = pdf.get_y()
        pdf.cell(widths[0], 6.5, f"{p.date:%d.%m.%Y}")
        x_after = pdf.get_x()
        pdf.multi_cell(widths[1], 6.5, p.description, new_x="RIGHT", new_y="TOP", max_line_height=6.5)
        pdf.set_xy(x_after + widths[1], y)
        pdf.cell(widths[2], 6.5, f"{p.quantity.normalize():f}".replace(".", ","), align="R")
        pdf.cell(widths[3], 6.5, p.unit_price_eur, align="R")
        pdf.cell(widths[4], 6.5, p.total_eur, align="R")
        pdf.ln(7)
    pdf.set_font("Roboto", "B", 11)
    pdf.cell(sum(widths[:4]), 9, "Gesamtbetrag", border="T", align="R")
    pdf.cell(widths[4], 9, eur(invoice.total_cents), border="T", align="R")
    pdf.ln(12)

    # Steuerhinweis und Zahlung
    pdf.set_font("Roboto", "", 9.5)
    pdf.multi_cell(0, 5, snap["exemption_text"])
    if invoice.kind == Honorarnote.Kind.RECHNUNG and (snap["iban"] or invoice.due_date):
        pdf.ln(4)
        pdf.set_font("Roboto", "B", 9.5)
        pdf.cell(0, 5.5, "Zahlung", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Roboto", "", 9.5)
        if invoice.due_date:
            pdf.cell(0, 5, f"Zahlbar bis {invoice.due_date:%d.%m.%Y} ohne Abzug.", new_x="LMARGIN", new_y="NEXT")
        if snap["iban"]:
            bank = f"IBAN {snap['iban']}" + (f" · BIC {snap['bic']}" if snap["bic"] else "")
            pdf.cell(0, 5, bank, new_x="LMARGIN", new_y="NEXT")
        if invoice.number:
            pdf.cell(0, 5, f"Verwendungszweck: {invoice.number}", new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())
