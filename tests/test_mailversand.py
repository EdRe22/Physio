import io
from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone
from pypdf import PdfReader

from patients.models import Einwilligung, Patient
from rechnungen import mailing, services
from rechnungen.models import ImmutableError, Versand

from .test_rechnungen import TODAY, draft, profile


def consenting_patient(email="patient@example.invalid", consent=True):
    p = Patient.objects.create(first_name="Łukasz", last_name="Çelik", address="Teststraße 5\n1100 Wien", email=email)
    if consent:
        Einwilligung.objects.create(patient=p, kind=Einwilligung.Kind.EMAIL_DOCUMENTS, granted_at=timezone.now())
    return p


def issued(p=None, **profile_kw):
    profile(payment_days=10, email="praxis@example.invalid", **profile_kw)
    return services.issue(draft(p or consenting_patient()), today=TODAY - timedelta(days=30))


def test_invoice_mail_has_pdf_and_neutral_text(in_alpha):
    invoice = issued()
    entry = mailing.send_invoice(invoice)
    assert entry.ok and len(mail.outbox) == 1
    msg = mail.outbox[0]
    assert msg.to == ["patient@example.invalid"] and msg.reply_to == ["praxis@example.invalid"]
    assert invoice.number in msg.subject
    assert "Çelik" not in msg.body and "Łukasz" not in msg.body and "Physiotherapie" not in msg.body  # keine Gesundheits-/Personendaten im Text
    name, data, mime = msg.attachments[0]
    assert mime == "application/pdf" and name.endswith(".pdf") and len(PdfReader(io.BytesIO(data)).pages) >= 1


def test_no_consent_or_no_address_blocks_send(in_alpha):
    invoice = issued(consenting_patient(consent=False))
    with pytest.raises(mailing.MailError, match="Einwilligung"):
        mailing.send_invoice(invoice)
    assert mail.outbox == [] and Versand.objects.count() == 0
    invoice2 = issued(consenting_patient(email=""))
    with pytest.raises(mailing.MailError, match="E-Mail-Adresse"):
        mailing.send_invoice(invoice2)


def test_revoked_consent_blocks(in_alpha):
    p = consenting_patient()
    invoice = issued(p)
    Einwilligung.objects.update(revoked_at=timezone.now())
    with pytest.raises(mailing.MailError):
        mailing.send_invoice(invoice)


def test_draft_cannot_be_mailed(in_alpha):
    profile()
    with pytest.raises(mailing.MailError, match="Entwürfe"):
        mailing.send_invoice(draft(consenting_patient()))


def test_reminder_mail_and_protocol(in_alpha):
    invoice = issued()
    reminder = services.record_reminder(invoice, TODAY)
    entry = mailing.send_reminder(reminder)
    msg = mail.outbox[0]
    assert "Zahlungserinnerung" in msg.subject and entry.reminder_id == reminder.pk
    assert "Zahlungsfrist" in msg.body
    with pytest.raises(ImmutableError):
        entry.ok = False
        entry.save()


def test_failed_send_is_logged(in_alpha, monkeypatch):
    invoice = issued()

    def boom(self, fail_silently=False):
        raise OSError("smtp down")

    monkeypatch.setattr("django.core.mail.message.EmailMessage.send", boom)
    with pytest.raises(mailing.MailError, match="fehlgeschlagen"):
        mailing.send_invoice(invoice)
    assert Versand.objects.get().ok is False


def test_views_send_and_show_protocol(client_alpha, in_alpha):
    invoice = issued()
    page = client_alpha.get(f"/rechnungen/{invoice.pk}/").content.decode()
    assert "Per E-Mail senden" in page
    r = client_alpha.post(f"/rechnungen/{invoice.pk}/mail/")
    assert r.status_code == 302 and len(mail.outbox) == 1
    assert "gesendet" in client_alpha.get(f"/rechnungen/{invoice.pk}/").content.decode()
    assert client_alpha.get(f"/rechnungen/{invoice.pk}/mail/").status_code == 405  # nur POST


def test_view_without_consent_shows_reason_and_no_button(client_alpha, in_alpha):
    invoice = issued(consenting_patient(consent=False))
    page = client_alpha.get(f"/rechnungen/{invoice.pk}/").content.decode()
    assert "Per E-Mail senden" not in page and "keine Einwilligung" in page
    client_alpha.post(f"/rechnungen/{invoice.pk}/mail/")
    assert mail.outbox == []


def test_overdue_page_offers_mail_for_reminder(client_alpha, in_alpha):
    invoice = issued()
    reminder = services.record_reminder(invoice, timezone.localdate())
    assert "Per E-Mail" in client_alpha.get("/rechnungen/ueberfaellig/").content.decode()
    r = client_alpha.post(f"/rechnungen/mahnung/{reminder.pk}/mail/", {"next": "overdue"})
    assert r.status_code == 302 and len(mail.outbox) == 1


def test_other_therapist_cannot_mail(client_beta, client_alpha, in_alpha):
    invoice = issued()
    assert client_beta.post(f"/rechnungen/{invoice.pk}/mail/").status_code in (403, 404)
    assert mail.outbox == []
