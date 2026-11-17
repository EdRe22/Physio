import io
from datetime import timedelta

import pytest
from pypdf import PdfReader

from rechnungen import services
from rechnungen.models import ImmutableError, Mahnung, Rechnungsprofil
from rechnungen.pdf import render_reminder_pdf

from .test_rechnungen import TODAY, draft, patient, profile


def overdue_invoice(**profile_kwargs):
    profile(payment_days=10, **profile_kwargs)
    return services.issue(draft(patient()), today=TODAY - timedelta(days=30))


def pdf_text(reminder):
    reader = PdfReader(io.BytesIO(render_reminder_pdf(reminder)))
    return "\n".join(page.extract_text() for page in reader.pages)


def test_levels_need_the_deadline_to_pass(in_alpha):
    invoice = overdue_invoice(reminder_days=7)
    first = services.record_reminder(invoice, TODAY)
    assert first.level == 1 and first.pay_until == TODAY + timedelta(days=7) and first.open_cents == invoice.open_cents
    with pytest.raises(services.BillingError, match="Zahlungsfrist"):
        services.record_reminder(invoice, TODAY + timedelta(days=7))  # Fristtag selbst zählt noch
    assert services.record_reminder(invoice, TODAY + timedelta(days=8)).level == 2
    assert services.record_reminder(invoice, TODAY + timedelta(days=16)).level == 3
    with pytest.raises(services.BillingError, match="alle Mahnstufen"):
        services.record_reminder(invoice, TODAY + timedelta(days=30))


def test_fee_only_from_level_two_and_frozen(in_alpha):
    invoice = overdue_invoice(reminder_days=5, reminder_fee_cents=500)
    one = services.record_reminder(invoice, TODAY)
    two = services.record_reminder(invoice, TODAY + timedelta(days=6))
    assert one.fee_cents == 0 and two.fee_cents == 500
    Rechnungsprofil.objects.update(reminder_fee_cents=9900, reminder_days=30)
    again = Mahnung.objects.get(pk=two.pk)
    assert again.fee_cents == 500  # spätere Profiländerung ändert die Mahnung nicht
    with pytest.raises(ImmutableError):
        again.fee_cents = 1
        again.save()


def test_paid_invoice_cannot_be_reminded(in_alpha):
    invoice = overdue_invoice()
    services.add_payment(invoice, TODAY, invoice.total_cents)
    invoice.refresh_from_db()
    with pytest.raises(services.BillingError):
        services.record_reminder(invoice, TODAY)


def test_partial_payment_is_reflected(in_alpha):
    invoice = overdue_invoice()
    services.add_payment(invoice, TODAY - timedelta(days=5), 1000)
    invoice.refresh_from_db()
    assert services.record_reminder(invoice, TODAY).open_cents == invoice.total_cents - 1000


def test_reminder_pdf_content_per_level(in_alpha):
    invoice = overdue_invoice(reminder_days=7, reminder_fee_cents=500)
    texts = []
    for i, offset in enumerate((0, 8, 16)):
        reminder = services.record_reminder(invoice, TODAY + timedelta(days=offset))
        texts.append(pdf_text(reminder))
    assert "Zahlungserinnerung" in texts[0] and "Mahnspesen" not in texts[0] and "gegenstandslos" in texts[0]
    assert "1. Mahnung" in texts[1] and "Mahnspesen" in texts[1] and "5,00" in texts[1]
    assert "Letzte Mahnung" in texts[2] and "Rechtsweg" in texts[2]
    assert invoice.number in texts[0] and "Verwendungszweck" in texts[0]


def test_views_create_and_show_pdf(client_alpha, in_alpha):
    invoice = overdue_invoice()
    page = client_alpha.get("/rechnungen/ueberfaellig/").content.decode()
    assert "Zahlungserinnerung erstellen" in page
    r = client_alpha.post(f"/rechnungen/{invoice.pk}/mahnung/", {"next": "overdue"})
    assert r.status_code == 302 and "/mahnung/" in r["Location"] and r["Location"].endswith("/pdf/")
    pdf = client_alpha.get(r["Location"])
    assert pdf["Content-Type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    page = client_alpha.get("/rechnungen/ueberfaellig/").content.decode()
    assert "Zahlungsfrist" in page and "1. Mahnung erstellen" not in page  # Frist läuft noch
    again = client_alpha.post(f"/rechnungen/{invoice.pk}/mahnung/", {"next": "overdue"})
    assert again.status_code == 302 and "/pdf/" not in again["Location"]
    assert Mahnung.objects.count() == 1
    detail = client_alpha.get(f"/rechnungen/{invoice.pk}/").content.decode()
    assert "Mahnwesen" in detail and "/pdf/" in detail


def test_reminder_pdf_not_visible_to_other_therapist(client_alpha, client_beta, in_alpha):
    invoice = overdue_invoice()
    reminder = services.record_reminder(invoice, TODAY)
    assert client_alpha.get(f"/rechnungen/mahnung/{reminder.pk}/pdf/").status_code == 200
    assert client_beta.get(f"/rechnungen/mahnung/{reminder.pk}/pdf/").status_code in (403, 404)


def test_settings_page_saves_days_fee_and_texts(client_alpha, in_alpha):
    profile()
    data = {"reminder_days": 10, "reminder_days_2": 5, "reminder_days_3": "", "reminder_fee": "7,50",
            "reminder_text_1": "Hallo, bitte {betrag} zu {nummer} bis {frist}.", "reminder_text_2": "", "reminder_text_3": ""}
    assert client_alpha.post("/einstellungen/mahnwesen/", data).status_code == 302
    p = Rechnungsprofil.objects.get()
    assert (p.reminder_days, p.reminder_days_2, p.reminder_days_3, p.reminder_fee_cents) == (10, 5, None, 750)
    assert services.reminder_days_for(p, 1) == 10 and services.reminder_days_for(p, 2) == 5 and services.reminder_days_for(p, 3) == 10
    bad = client_alpha.post("/einstellungen/mahnwesen/", {**data, "reminder_fee": "-5", "reminder_days": 0})
    assert bad.status_code == 200 and Rechnungsprofil.objects.get().reminder_fee_cents == 750
    assert client_alpha.post("/einstellungen/mahnwesen/", {**data, "reminder_days_3": 99}).status_code == 200
    assert client_alpha.get("/einstellungen/mahnwesen/").status_code == 200


def test_own_text_and_days_used_and_frozen(in_alpha):
    invoice = overdue_invoice(reminder_days=7, reminder_days_2=3, reminder_text_1="Eigener Text {nummer} über {betrag} bis {frist} {unbekannt}")
    one = services.record_reminder(invoice, TODAY)
    assert one.text.startswith("Eigener Text") and one.pay_until == TODAY + timedelta(days=7)
    two = services.record_reminder(invoice, TODAY + timedelta(days=8))
    assert two.pay_until == TODAY + timedelta(days=11) and two.text == services.DEFAULT_REMINDER_TEXTS[2]
    text = pdf_text(one)
    assert f"Eigener Text {invoice.number} über" in text and "{unbekannt}" in text and "{frist}" not in text
    Rechnungsprofil.objects.update(reminder_text_1="Ganz anders")
    assert "Eigener Text" in pdf_text(Mahnung.objects.get(pk=one.pk))


def test_settings_page_is_per_therapist(client_alpha, client_beta, in_alpha):
    profile()
    client_alpha.post("/einstellungen/mahnwesen/", {"reminder_days": 9, "reminder_fee": "0", "reminder_text_1": "Nur Alpha"})
    assert client_beta.get("/einstellungen/mahnwesen/").status_code in (200, 302)
    assert "Nur Alpha" not in client_beta.get("/einstellungen/mahnwesen/", follow=True).content.decode()


def test_reminder_is_one_page(in_alpha):
    invoice = overdue_invoice(reminder_fee_cents=500)
    reminder = services.record_reminder(invoice, TODAY)
    assert len(PdfReader(io.BytesIO(render_reminder_pdf(reminder))).pages) == 1
