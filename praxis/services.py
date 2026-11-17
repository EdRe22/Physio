import json
from datetime import date, timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from audit.models import log as audit_log
from buchhaltung import services as books
from core import tenancy
from core.models import Tenant
from rechnungen.services import BillingError

from .models import Mietabrechnung, Mietverhaeltnis, PraxisProfil, Zaehler


class RentError(BillingError):
    pass


def profile():
    return PraxisProfil.objects.first()


def visible_invoices(user):
    """Eigentümer: alle. Sonst nur die eigenen. Zentrale Stelle für die Sichtbarkeit."""
    qs = Mietabrechnung.objects.select_related("tenant")
    if user.is_owner:
        return qs
    return qs.filter(tenant_id=user.tenant_id)


def parse_period(text):
    try:
        year, month = (int(x) for x in text.split("-"))
        date(year, month, 1)
    except (ValueError, AttributeError):
        raise RentError("Abrechnungsmonat ungültig (JJJJ-MM erwartet).")
    return year, month


def lease_for(tenant, year, month):
    """Mietverhältnis, das am Ersten des Monats gilt. Es wird immer der volle Monat abgerechnet (keine Aliquotierung)."""
    first = date(year, month, 1)
    return (Mietverhaeltnis.objects.filter(tenant=tenant, valid_from__lte=first)
            .exclude(valid_until__lt=first).order_by("-valid_from").first())


def _next_number(year):
    counter, _ = Zaehler.objects.get_or_create(key=f"m{year}")
    Zaehler.objects.filter(pk=counter.pk).update(last=F("last") + 1)
    counter.refresh_from_db()
    return f"M{year}-{counter.last:03d}"


def issue(tenant, period, today=None):
    today = today or timezone.localdate()
    year, month = parse_period(period)
    if date(year, month, 1) > today.replace(day=1):
        raise RentError("Der Abrechnungsmonat liegt in der Zukunft.")
    prof = profile()
    if not prof or not prof.ready:
        raise RentError("Praxisprofil unvollständig oder Steuerhinweis nicht vom Steuerberater bestätigt.")
    lease = lease_for(tenant, year, month)
    if lease is None:
        raise RentError("Für diesen Monat gibt es kein gültiges Mietverhältnis.")
    if lease.total_cents <= 0:
        raise RentError("Die Miete muss größer als 0 sein.")
    with transaction.atomic():
        if Mietabrechnung.objects.filter(tenant=tenant, period=period).exclude(status="storniert").exists():
            raise RentError("Für diesen Monat gibt es bereits eine Mietabrechnung.")
        snapshot = json.dumps({"name": prof.name, "address": prof.address, "iban": prof.iban, "tax_text": prof.tax_text},
                              ensure_ascii=False)
        invoice = Mietabrechnung.objects.create(
            tenant=tenant, lease=lease, period=period, number=_next_number(year), rent_cents=lease.rent_cents,
            utilities_cents=lease.utilities_cents, issued_on=today, due_on=today + timedelta(days=prof.payment_days),
            issuer_snapshot=snapshot,
        )
        audit_log("miete.issue", invoice)
    return invoice


def issue_all(period, today=None):
    """Alle aktiven Kollegen mit Mietverhältnis. Rückgabe: (angelegt, übersprungen mit Grund)."""
    created, skipped = [], []
    for tenant in Tenant.objects.filter(is_active=True, leases__isnull=False).distinct():
        try:
            created.append(issue(tenant, period, today))
        except RentError as exc:
            skipped.append((tenant.name, str(exc)))
    return created, skipped


def mark_paid(invoice, paid_on, today=None):
    """Eigentümer: Zahlung eingegangen. Bucht die Einnahme in der Buchhaltung des Eigentümers (aktiver Mandant)."""
    today = today or timezone.localdate()
    if invoice.status != Mietabrechnung.Status.OFFEN:
        raise RentError("Nur offene Mietabrechnungen können als bezahlt markiert werden.")
    if paid_on > today:
        raise RentError("Das Zahlungsdatum liegt in der Zukunft.")
    if paid_on < invoice.issued_on:
        raise RentError("Das Zahlungsdatum liegt vor dem Ausstellungsdatum.")
    with transaction.atomic(using="default"), transaction.atomic(using=tenancy.require().alias):
        books.create_buchung(
            paid_on, "einnahme", books.category("Sonstige Einnahmen", "einnahme"), invoice.total_cents,
            text=f"Miete {invoice.period} ({invoice.number})", counterpart=invoice.tenant.name, today=today,
        )
        invoice.status, invoice.paid_on = Mietabrechnung.Status.BEZAHLT, paid_on
        invoice.save()
        audit_log("miete.paid", invoice)
    return invoice


def book_as_expense(invoice, today=None):
    """Kollege: übernimmt die Mietabrechnung als Ausgabe (Kategorie Miete) in die eigene Buchhaltung."""
    today = today or timezone.localdate()
    if invoice.tenant.slug != tenancy.require().slug:
        raise RentError("Diese Mietabrechnung gehört nicht zu Ihrem Konto.")
    if invoice.status == Mietabrechnung.Status.STORNIERT:
        raise RentError("Eine stornierte Mietabrechnung kann nicht gebucht werden.")
    if invoice.tenant_booked_on:
        raise RentError("Diese Mietabrechnung wurde bereits gebucht.")
    with transaction.atomic(using="default"), transaction.atomic(using=tenancy.require().alias):
        books.create_buchung(
            min(invoice.issued_on, today), "ausgabe", books.category("Miete", "ausgabe"), invoice.total_cents,
            text=f"Miete {invoice.period} ({invoice.number})", counterpart=json.loads(invoice.issuer_snapshot)["name"],
            today=today,
        )
        invoice.tenant_booked_on = today
        invoice.save()
        audit_log("miete.booked", invoice)
    return invoice


def storno(invoice):
    """Storniert eine offene Mietabrechnung, solange der Kollege sie nicht gebucht hat."""
    if invoice.status != Mietabrechnung.Status.OFFEN:
        raise RentError("Nur offene Mietabrechnungen können storniert werden.")
    if invoice.tenant_booked_on:
        raise RentError("Der Kollege hat die Mietabrechnung bereits gebucht, bitte mit ihm klären.")
    invoice.status = Mietabrechnung.Status.STORNIERT
    invoice.save()
    audit_log("miete.storno", invoice)
    return invoice
