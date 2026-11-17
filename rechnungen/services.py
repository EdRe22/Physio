"""Geschäftslogik der Honorarnoten. Alle Regeln liegen hier, die Views rufen nur diese Funktionen."""
import json
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from audit.models import log as audit_log
from core import tenancy
from patients.models import Eintrag

from .models import KLEINBETRAG_CENTS, Honorarnote, Mahnung, Nummernkreis, Position, Rechnungsprofil, Zahlung

MAX_REMINDER_LEVEL = 3


class BillingError(Exception):
    """Fachlicher Fehler mit anwenderfreundlicher Meldung (ohne Personenbezug)."""


def _atomic():
    return transaction.atomic(using=tenancy.require().alias)


def get_profile():
    return Rechnungsprofil.objects.first()


def next_number(issue_date):
    """Fortlaufende, lückenlose Nummer pro Jahr. Läuft in der Transaktion des Ausstellens:
    schlägt das Ausstellen fehl, wird auch die Nummer zurückgerollt (keine Lücke)."""
    circle, _ = Nummernkreis.objects.get_or_create(year=issue_date.year)
    Nummernkreis.objects.filter(pk=circle.pk).update(last=F("last") + 1)
    circle.refresh_from_db()
    return f"{issue_date.year}-{circle.last:04d}"


def add_position(invoice, date, description, quantity, unit_price_cents, entry=None):
    if not invoice.is_draft:
        raise BillingError("Nur Entwürfe können bearbeitet werden.")
    quantity = Decimal(quantity)
    if date > timezone.localdate():
        raise BillingError("Das Datum der Leistung liegt in der Zukunft.")
    if quantity <= 0:
        raise BillingError("Die Menge muss größer als 0 sein.")
    if unit_price_cents < 0:
        raise BillingError("Der Preis darf nicht negativ sein.")
    with _atomic():
        position = Position.objects.create(
            invoice=invoice, date=date, description=description, quantity=quantity,
            unit_price_cents=unit_price_cents, entry=entry,
        )
        invoice.recalculate()
        invoice.save(update_fields=["total_cents"])
    return position


def remove_position(position):
    invoice = position.invoice
    if not invoice.is_draft:
        raise BillingError("Nur Entwürfe können bearbeitet werden.")
    with _atomic():
        position.delete()
        invoice.recalculate()
        invoice.save(update_fields=["total_cents"])


def unbilled_entries(patient):
    """Behandlungen, die noch auf keiner aktiven Honorarnote stehen (stornierte zählen als nicht abgerechnet)."""
    billed = Position.objects.filter(entry__isnull=False).exclude(invoice__status=Honorarnote.Status.STORNIERT)
    return Eintrag.objects.filter(patient=patient, kind=Eintrag.Kind.BEHANDLUNG).exclude(
        pk__in=list(billed.values_list("entry_id", flat=True))
    )


def create_draft(patient, entries=(), leistung=None):
    """Legt einen Entwurf an, optional mit einer Position je ausgewählter Behandlung."""
    entries = list(entries)
    if entries and leistung is None:
        raise BillingError("Bitte eine Leistung für die gewählten Behandlungen angeben.")
    with _atomic():
        referrals = {e.referral_id for e in entries}
        invoice = Honorarnote.objects.create(patient=patient, referral_id=referrals.pop() if len(referrals) == 1 else None)
        for entry in sorted(entries, key=lambda e: (e.date, e.pk)):
            add_position(invoice, entry.date, leistung.name, 1, leistung.price_cents, entry=entry)
    return invoice


def delete_draft(invoice):
    if not invoice.is_draft or invoice.is_issued:
        raise BillingError("Nur Entwürfe können gelöscht werden.")
    with _atomic():
        for position in list(invoice.positions.all()):
            position.delete()
        invoice.delete()


def _issuer_snapshot(profile):
    return json.dumps({
        "name": profile.name, "title": profile.title, "address": profile.address, "phone": profile.phone,
        "email": profile.email, "tax_no": profile.tax_no, "uid": profile.uid, "iban": profile.iban, "bic": profile.bic,
        "exemption_text": profile.exemption_text, "payment_days": profile.payment_days,
    }, ensure_ascii=False)


def issue(invoice, today=None, snapshot_from=None):
    """Stellt einen Entwurf aus: vergibt die Nummer, hält Empfänger und Aussteller fest, sperrt den Beleg."""
    today = today or timezone.localdate()
    with _atomic():
        invoice = Honorarnote.objects.select_for_update().get(pk=invoice.pk)
        if not invoice.is_draft:
            raise BillingError("Diese Honorarnote ist bereits ausgestellt.")
        profile = get_profile()
        if snapshot_from is None and (profile is None or not profile.ready_to_issue):
            raise BillingError(
                "Das Rechnungsprofil ist unvollständig, oder der Hinweis zur Umsatzsteuerbefreitung ist noch nicht "
                "vom Steuerberater bestätigt."
            )
        positions = list(invoice.positions.all())
        if not positions:
            raise BillingError("Die Honorarnote enthält keine Positionen.")
        invoice.recalculate()
        if invoice.kind == Honorarnote.Kind.RECHNUNG and invoice.total_cents <= 0:
            raise BillingError("Der Gesamtbetrag muss größer als 0 sein.")

        patient = invoice.patient
        if snapshot_from is not None:
            invoice.recipient_name, invoice.recipient_address = snapshot_from.recipient_name, snapshot_from.recipient_address
            invoice.issuer_snapshot = snapshot_from.issuer_snapshot
        else:
            invoice.recipient_name = patient.full_name
            invoice.recipient_address = patient.address
            invoice.issuer_snapshot = _issuer_snapshot(profile)
            if invoice.total_cents > KLEINBETRAG_CENTS and not invoice.recipient_address.strip():
                raise BillingError(
                    "Ab 400 EUR muss die Anschrift des Empfängers auf der Honorarnote stehen. "
                    "Bitte die Adresse beim Patienten ergänzen."
                )
        invoice.service_from = min(p.date for p in positions)
        invoice.service_to = max(p.date for p in positions)
        invoice.issue_date = today
        if invoice.kind == Honorarnote.Kind.RECHNUNG:
            days = json.loads(invoice.issuer_snapshot)["payment_days"]
            invoice.due_date = today + timedelta(days=days)
        invoice.number = next_number(today)
        invoice.status = Honorarnote.Status.AUSGESTELLT
        invoice.save()
        audit_log("rechnung.issue", invoice)
    return invoice


def storno(invoice, today=None):
    """Korrektur durch Stornorechnung mit eigener Nummer. Die Originalrechnung bleibt unverändert erhalten."""
    with _atomic():
        invoice = Honorarnote.objects.select_for_update().get(pk=invoice.pk)
        if invoice.kind != Honorarnote.Kind.RECHNUNG or invoice.status not in (
            Honorarnote.Status.AUSGESTELLT, Honorarnote.Status.BEZAHLT
        ):
            raise BillingError("Nur ausgestellte Honorarnoten können storniert werden.")
        draft = Honorarnote.objects.create(
            patient=invoice.patient, referral=invoice.referral, kind=Honorarnote.Kind.STORNO, cancels=invoice
        )
        for p in invoice.positions.all():
            Position.objects.create(
                invoice=draft, date=p.date, description=p.description, quantity=-p.quantity, unit_price_cents=p.unit_price_cents
            )
        draft.recalculate()
        draft.save(update_fields=["total_cents"])
        storno_invoice = issue(draft, today=today, snapshot_from=invoice)
        invoice.status = Honorarnote.Status.STORNIERT
        invoice.save()
        audit_log("rechnung.storno", invoice)
    return storno_invoice


def add_payment(invoice, date, amount_cents, method=Zahlung.Method.UEBERWEISUNG, today=None):
    today = today or timezone.localdate()
    if invoice.kind != Honorarnote.Kind.RECHNUNG or invoice.status != Honorarnote.Status.AUSGESTELLT:
        raise BillingError("Zahlungen sind nur für offene Honorarnoten möglich.")
    if amount_cents <= 0:
        raise BillingError("Der Betrag muss größer als 0 sein.")
    if date > today:
        raise BillingError("Das Zahlungsdatum liegt in der Zukunft.")
    if date < invoice.issue_date:
        raise BillingError("Das Zahlungsdatum liegt vor dem Rechnungsdatum.")
    with _atomic():
        if amount_cents > invoice.open_cents:
            raise BillingError("Der Betrag ist höher als der offene Betrag.")
        payment = Zahlung.objects.create(invoice=invoice, date=date, amount_cents=amount_cents, method=method)
        if invoice.open_cents == 0:
            invoice.status = Honorarnote.Status.BEZAHLT
            invoice.save()
    return payment


def overdue(today=None):
    today = today or timezone.localdate()
    return [
        i for i in Honorarnote.objects.filter(
            kind=Honorarnote.Kind.RECHNUNG, status=Honorarnote.Status.AUSGESTELLT, due_date__lt=today
        ).select_related("patient") if i.open_cents > 0
    ]


def record_reminder(invoice, today=None):
    """Hält fest, dass eine Zahlungserinnerung/Mahnung erfolgt ist (Stufe 1 bis 3)."""
    today = today or timezone.localdate()
    if invoice.status != Honorarnote.Status.AUSGESTELLT or invoice.kind != Honorarnote.Kind.RECHNUNG:
        raise BillingError("Mahnungen sind nur für offene Honorarnoten möglich.")
    if invoice.due_date is None or invoice.due_date >= today:
        raise BillingError("Die Honorarnote ist noch nicht überfällig.")
    last = invoice.reminders.order_by("-level").first()
    level = (last.level if last else 0) + 1
    if level > MAX_REMINDER_LEVEL:
        raise BillingError("Es wurden bereits alle Mahnstufen erfasst.")
    with _atomic():
        return Mahnung.objects.create(invoice=invoice, level=level, date=today)
