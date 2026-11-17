"""E-Mail-Versand von Honorarnoten und Mahnungen.

Datenschutz:
- Nur mit aktiver Einwilligung des Patienten ("Honorarnoten und Mahnungen per E-Mail"), Widerruf wirkt sofort.
- Der Text der Nachricht enthält keine Gesundheitsangaben und keinen Patientennamen; der Anhang (PDF) enthält die
  Honorarnote selbst. Betreff und Text sind daher neutral gehalten.
- Versand nur über den konfigurierten EU-SMTP-Dienst (TLS). Jeder Versuch wird im Versandprotokoll festgehalten.
"""
import logging

from django.conf import settings
from django.core.mail import EmailMessage

from audit.models import log as audit_log
from patients.models import Einwilligung

from . import services
from .models import Honorarnote, Versand
from .pdf import render_pdf, render_reminder_pdf

logger = logging.getLogger(__name__)


class MailError(Exception):
    """Versand nicht möglich (Meldung ist für Anwender gedacht)."""


def mail_block_reason(patient) -> str:
    """Leer, wenn ein Versand an diesen Patienten zulässig ist, sonst der Grund."""
    if not patient.email:
        return "Beim Patienten ist keine E-Mail-Adresse hinterlegt."
    if not patient.has_consent(Einwilligung.Kind.EMAIL_DOCUMENTS):
        return "Es liegt keine Einwilligung zum Versand von Honorarnoten per E-Mail vor (beim Patienten erfassen)."
    return ""


DEFAULT_MAIL_TEXTS = {
    "invoice": "Guten Tag,\n\nim Anhang finden Sie die Honorarnote {nummer} von {praxis} als PDF.",
    "reminder": ("Guten Tag,\n\nim Anhang finden Sie das Schreiben zur Honorarnote {nummer} von {praxis} als PDF. "
                 "Bitte beachten Sie die Zahlungsfrist bis {frist}."),
}
FOOTER = (
    "\n\nSie erhalten diese Nachricht, weil Sie dem Versand per E-Mail zugestimmt haben. "
    "Die Zustimmung können Sie jederzeit bei Ihrer Therapeutin bzw. Ihrem Therapeuten widerrufen.\n"
)


def mail_text_for(profile, kind):
    own = (getattr(profile, f"mail_text_{kind}", "") or "").strip() if profile else ""
    return own or DEFAULT_MAIL_TEXTS[kind]


def _body(profile, kind, number, praxis, frist=""):
    """Eigener Text oder Vorlage; die Hinweiszeile zur Einwilligung wird immer angehängt."""
    text = mail_text_for(profile, kind)
    for token, value in (("{nummer}", number), ("{praxis}", praxis), ("{frist}", frist)):
        text = text.replace(token, value)
    return text + FOOTER


def _send(invoice, reminder):
    patient = invoice.patient
    reason = mail_block_reason(patient)
    if reason:
        raise MailError(reason)
    if invoice.is_draft:
        raise MailError("Entwürfe können nicht versendet werden. Bitte zuerst ausstellen.")
    snap = services_snapshot(invoice)
    profile = services.get_profile()
    name = snap.get("name") or "Ihrer Praxis"
    if reminder is None:
        subject = f"Honorarnote {invoice.number}"
        body = _body(profile, "invoice", invoice.number, name)
        pdf, filename = render_pdf(invoice), f"Honorarnote_{invoice.number}.pdf"
    else:
        title = services.REMINDER_TITLES[reminder.level]
        subject = f"{title} zur Honorarnote {invoice.number}"
        body = _body(profile, "reminder", invoice.number, name, f"{reminder.pay_until:%d.%m.%Y}" if reminder.pay_until else "")
        pdf, filename = render_reminder_pdf(reminder), f"{title.replace('. ', '_').replace(' ', '_')}_{invoice.number}.pdf"
    message = EmailMessage(
        subject=subject, body=body, from_email=f"{name} <{settings.DEFAULT_FROM_EMAIL}>", to=[patient.email],
        reply_to=[snap["email"]] if snap.get("email") else None,
    )
    message.attach(filename, pdf, "application/pdf")
    ok = True
    try:
        message.send(fail_silently=False)
    except Exception:  # keine Adresse ins Log
        logger.exception("Versand der Honorarnote %s fehlgeschlagen", invoice.pk)
        ok = False
    entry = Versand.objects.create(invoice=invoice, reminder=reminder, recipient=patient.email, subject=subject, ok=ok)
    audit_log("rechnung.mail_sent" if ok else "rechnung.mail_failed", invoice)
    if not ok:
        raise MailError("Der Versand ist fehlgeschlagen. Bitte später erneut versuchen oder das PDF selbst versenden.")
    return entry


def services_snapshot(invoice):
    import json

    return json.loads(invoice.issuer_snapshot) if invoice.issuer_snapshot else {}


def send_invoice(invoice):
    return _send(invoice, None)


def send_reminder(reminder):
    return _send(reminder.invoice, reminder)


AUTO_MAX_LEVEL = 2  # die letzte Mahnung (Stufe 3) wird nie automatisch versendet


def send_auto_reminders(today=None):
    """Erstellt und versendet fällige Zahlungserinnerungen/1. Mahnungen, nur wenn die Therapeutin/der Therapeut das aktiviert hat
    und der Patient eingewilligt hat. Die Fristenregel gilt wie bei manueller Erstellung. Zähler ohne Personenbezug."""
    from django.utils import timezone

    today = today or timezone.localdate()
    counts = {"sent": 0, "skipped": 0, "failed": 0}
    profile = services.get_profile()
    if profile is None or not profile.auto_reminders:
        return counts
    for invoice in services.overdue(today):
        level, _title, blocked = services.reminder_state(invoice, today)
        if level is None or level > AUTO_MAX_LEVEL or blocked or mail_block_reason(invoice.patient):
            counts["skipped"] += 1
            continue
        try:
            reminder = services.record_reminder(invoice, today)
        except services.BillingError:
            counts["skipped"] += 1
            continue
        audit_log("mahnung.auto", invoice)
        try:
            send_reminder(reminder)
            counts["sent"] += 1
        except MailError:
            counts["failed"] += 1  # Mahnung ist erfasst, Versand im Protokoll; manuell nachholbar
    return counts
