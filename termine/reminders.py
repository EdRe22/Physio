"""E-Mail-Terminerinnerungen.

Datenschutz: Die Nachricht enthält nur Datum, Uhrzeit und Namen der Praxis, keinerlei
Gesundheitsinformation, keinen Patientennamen, keine Therapieart. Versand nur mit aktiver
Einwilligung (Widerruf wirkt sofort) und nur über den konfigurierten EU-SMTP-Dienst (TLS).
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone

from audit.models import log as audit_log
from patients.models import Einwilligung

from .models import Termin

logger = logging.getLogger(__name__)

SUBJECT = "Terminerinnerung"


def build_message(termin, practice_name, reply_to="", sender=None, connection=None):
    local = timezone.localtime(termin.start)
    body = (
        "Guten Tag,\n\n"
        f"wir erinnern Sie an Ihren Termin am {local:%d.%m.%Y} um {local:%H:%M} Uhr "
        f"bei {practice_name}.\n"
    )
    if reply_to:
        body += "Falls Sie den Termin nicht wahrnehmen können, antworten Sie bitte auf diese E-Mail.\n"
    body += (
        "\nSie erhalten diese Erinnerung, weil Sie zugestimmt haben. "
        "Die Zustimmung können Sie jederzeit bei Ihrer Therapeutin bzw. Ihrem Therapeuten widerrufen.\n"
    )
    return EmailMessage(
        subject=SUBJECT,
        body=body,
        from_email=f"{practice_name} <{sender or settings.DEFAULT_FROM_EMAIL}>",
        connection=connection,
        to=[termin.patient.email],
        reply_to=[reply_to] if reply_to else None,
    )


def send_due_reminders(practice_name, reply_to="", hours=36, now=None):
    """Sendet fällige Erinnerungen im aktiven Mandanten. Gibt Zähler zurück (ohne Personenbezug)."""
    now = now or timezone.now()
    due = Termin.objects.filter(
        status=Termin.Status.GEPLANT, reminder_sent_at__isnull=True, start__gt=now, start__lte=now + timedelta(hours=hours)
    ).select_related("patient")
    counts = {"sent": 0, "no_consent": 0, "no_email": 0, "failed": 0}
    from rechnungen import mailconfig, services as billing

    try:
        connection, sender = mailconfig.connection_for(billing.get_profile())
    except mailconfig.MailConfigError:
        logger.exception("Eigener Mailserver unzulässig, Erinnerungen werden nicht gesendet")
        connection, sender, due = None, None, due.none()
    for termin in due:
        patient = termin.patient
        if not patient.has_consent(Einwilligung.Kind.EMAIL_REMINDER):
            counts["no_consent"] += 1
            continue
        if not patient.email:
            counts["no_email"] += 1
            continue
        try:
            build_message(termin, practice_name, reply_to, sender, connection).send(fail_silently=False)
        except Exception:  # Zustellfehler: markieren, nicht abbrechen; keine Adresse ins Log
            logger.exception("Erinnerung für Termin %s konnte nicht gesendet werden", termin.pk)
            termin.reminder_failed_at = now
            termin.save(update_fields=["reminder_failed_at"])
            counts["failed"] += 1
            continue
        termin.reminder_sent_at = now
        termin.reminder_failed_at = None
        termin.save(update_fields=["reminder_sent_at", "reminder_failed_at"])
        audit_log("termin.reminder_sent", termin)
        counts["sent"] += 1
    return counts
