"""Geschäftslogik der Buchhaltung: Buchungen, Belege, Bankabgleich, Auswertung, Export."""
import csv
import hashlib
import io
import re
import uuid
import zipfile
from collections import defaultdict
from datetime import date as date_type
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from audit.models import log as audit_log
from core import crypto, tenancy
from rechnungen import services as billing
from rechnungen.models import Honorarnote, eur

from .models import DEFAULT_CATEGORIES, BankTransaktion, Beleg, Buchung, Kategorie, Zaehler

MAX_BELEG_BYTES = 10 * 1024 * 1024
MAGIC = ((b"%PDF-", "application/pdf", "pdf"), (b"\xff\xd8\xff", "image/jpeg", "jpg"), (b"\x89PNG\r\n\x1a\n", "image/png", "png"))
INVOICE_NO = re.compile(r"(?<!\d)(\d{4}-\d{4})(?!\d)")


class BookkeepingError(billing.BillingError):
    """Fachlicher Fehler der Buchhaltung (Meldung ohne Personenbezug)."""


def _atomic():
    return transaction.atomic(using=tenancy.require().alias)


def next_counter(key):
    counter, _ = Zaehler.objects.get_or_create(key=key)
    Zaehler.objects.filter(pk=counter.pk).update(last=F("last") + 1)
    counter.refresh_from_db()
    return counter.last


def ensure_categories():
    for name, kind in DEFAULT_CATEGORIES:
        Kategorie.objects.get_or_create(name=name, kind=kind)


def category(name, kind):
    ensure_categories()
    return Kategorie.objects.get(name=name, kind=kind)


# --- Buchungen ---

def create_buchung(date, kind, cat, amount_cents, text="", counterpart="", source=Buchung.Source.MANUELL,
                   zahlung=None, reverses=None, today=None):
    today = today or timezone.localdate()
    if reverses is None and amount_cents <= 0:
        raise BookkeepingError("Der Betrag muss größer als 0 sein.")
    if cat.kind != kind:
        raise BookkeepingError("Die Kategorie passt nicht zur Art der Buchung (Einnahme/Ausgabe).")
    if not cat.active:
        raise BookkeepingError("Diese Kategorie ist nicht mehr aktiv.")
    if date > today:
        raise BookkeepingError("Das Buchungsdatum liegt in der Zukunft.")
    if date.year < 2000:
        raise BookkeepingError("Das Buchungsdatum ist ungültig.")
    with _atomic():
        number = f"{today.year}-{next_counter(f'bu{today.year}'):05d}"
        return Buchung.objects.create(
            number=number, date=date, kind=kind, category=cat, amount_cents=amount_cents, text=text,
            counterpart=counterpart, source=source, zahlung=zahlung, reverses=reverses,
        )


def reverse(buchung, today=None):
    """Gegenbuchung (negativ) zur Korrektur. Die Originalbuchung bleibt unverändert."""
    if buchung.reverses_id:
        raise BookkeepingError("Eine Stornobuchung kann nicht erneut storniert werden.")
    if hasattr(buchung, "reversed_by"):
        raise BookkeepingError("Diese Buchung wurde bereits storniert.")
    with _atomic():
        result = create_buchung(
            buchung.date, buchung.kind, buchung.category, -buchung.amount_cents,
            text=f"Storno zu {buchung.number}", counterpart=buchung.counterpart, source=Buchung.Source.STORNO,
            reverses=buchung, today=today,
        )
        audit_log("buchung.reverse", buchung)
    return result


def on_payment_recorded(zahlung):
    """Jede Zahlung auf eine Honorarnote ist eine Einnahme (Zuflussprinzip). Aufruf per Signal."""
    invoice = zahlung.invoice
    create_buchung(
        zahlung.date, Kategorie.Kind.EINNAHME, category("Honorare", "einnahme"), zahlung.amount_cents,
        text=f"Honorarnote {invoice.number}", counterpart=invoice.recipient_name,
        source=Buchung.Source.ZAHLUNG, zahlung=zahlung,
    )


# --- Belege ---

def _beleg_path(storage_name):
    return Path(settings.BELEG_DIR) / tenancy.require().slug / f"{storage_name}.bin"


def store_beleg(uploaded):
    raw = uploaded.read()
    if not raw:
        raise BookkeepingError("Die Datei ist leer.")
    if len(raw) > MAX_BELEG_BYTES:
        raise BookkeepingError("Der Beleg ist größer als 10 MB.")
    for prefix, content_type, _ext in MAGIC:
        if raw.startswith(prefix):
            break
    else:
        raise BookkeepingError("Nur PDF-, JPEG- oder PNG-Dateien sind als Beleg erlaubt.")
    storage_name = uuid.uuid4().hex
    today = timezone.localdate()
    with _atomic():
        beleg = Beleg.objects.create(
            number=f"B{today.year}-{next_counter(f'be{today.year}'):04d}",
            original_name=(uploaded.name or "beleg")[:200], content_type=content_type, size=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(), storage_name=storage_name,
        )
        path = _beleg_path(storage_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(crypto.encrypt_bytes(tenancy.require().data_key, raw))
    return beleg


def read_beleg(beleg):
    blob = _beleg_path(beleg.storage_name).read_bytes()
    raw = crypto.decrypt_bytes(tenancy.require().data_key, blob)
    if hashlib.sha256(raw).hexdigest() != beleg.sha256:
        raise BookkeepingError("Der Beleg wurde verändert (Prüfsumme stimmt nicht).")
    return raw


def attach_beleg(beleg, buchung):
    updated = Beleg.objects.filter(pk=beleg.pk, buchung__isnull=True).update(buchung=buchung)
    if not updated:
        raise BookkeepingError("Dieser Beleg ist bereits einer Buchung zugeordnet.")
    audit_log("beleg.attach", beleg)


# --- Bankabgleich ---

def open_invoices():
    return [i for i in Honorarnote.objects.filter(kind=Honorarnote.Kind.RECHNUNG, status=Honorarnote.Status.AUSGESTELLT)
            .select_related("patient") if i.open_cents > 0]


def suggest(tx, invoices):
    """Schlägt für einen Zahlungseingang eine Honorarnote vor. Rückgabe: (Honorarnote, Sicherheit) oder None.

    sicher: Rechnungsnummer im Verwendungszweck und Betrag = offener Betrag
    teilzahlung: Rechnungsnummer passt, Betrag kleiner als offen
    wahrscheinlich: genau eine offene Honorarnote mit gleichem Betrag, Nachname in der Gegenseite
    """
    if tx.amount_cents <= 0 or tx.status != BankTransaktion.Status.OFFEN:
        return None
    by_number = {i.number: i for i in invoices}
    for number in INVOICE_NO.findall(f"{tx.purpose} {tx.reference}"):
        invoice = by_number.get(number)
        if invoice:
            if tx.amount_cents == invoice.open_cents:
                return invoice, "sicher"
            if 0 < tx.amount_cents < invoice.open_cents:
                return invoice, "teilzahlung"
            return None
    same_amount = [i for i in invoices if i.open_cents == tx.amount_cents]
    named = [i for i in same_amount if i.patient.last_name.casefold() in tx.counterpart.casefold() and i.patient.last_name]
    if len(named) == 1:
        return named[0], "wahrscheinlich"
    return None


def accept_match(tx, invoice, today=None):
    if tx.status != BankTransaktion.Status.OFFEN or tx.amount_cents <= 0:
        raise BookkeepingError("Dieser Bankumsatz kann nicht zugeordnet werden.")
    with _atomic():
        payment = billing.add_payment(invoice, tx.date, tx.amount_cents, today=today)
        tx.invoice = invoice
        tx.buchung = Buchung.objects.get(zahlung=payment)
        tx.status = BankTransaktion.Status.ZUGEORDNET
        tx.save()
        audit_log("bank.match", tx)
    return payment


def accept_all_sure(today=None):
    """Übernimmt alle sicheren Vorschläge. Rückgabe: Anzahl."""
    invoices, done = open_invoices(), 0
    for tx in BankTransaktion.objects.filter(status=BankTransaktion.Status.OFFEN, amount_cents__gt=0):
        match = suggest(tx, invoices)
        if match and match[1] == "sicher":
            try:
                accept_match(tx, match[0], today=today)
            except billing.BillingError:
                continue
            invoices = open_invoices()
            done += 1
    return done


def book_transaction(tx, cat, text=""):
    if tx.status != BankTransaktion.Status.OFFEN:
        raise BookkeepingError("Dieser Bankumsatz ist bereits bearbeitet.")
    kind = Kategorie.Kind.EINNAHME if tx.amount_cents > 0 else Kategorie.Kind.AUSGABE
    with _atomic():
        buchung = create_buchung(
            tx.date, kind, cat, abs(tx.amount_cents), text=text or tx.purpose[:200], counterpart=tx.counterpart,
            source=Buchung.Source.BANK,
        )
        tx.buchung, tx.status = buchung, BankTransaktion.Status.ZUGEORDNET
        tx.save()
        audit_log("bank.book", tx)
    return buchung


def ignore_transaction(tx):
    if tx.status != BankTransaktion.Status.OFFEN:
        raise BookkeepingError("Dieser Bankumsatz ist bereits bearbeitet.")
    tx.status = BankTransaktion.Status.IGNORIERT
    tx.save()
    audit_log("bank.ignore", tx)


# --- Auswertung ---

def year_report(year):
    rows = Buchung.objects.filter(date__year=year).select_related("category")
    by_category = defaultdict(int)
    months = [{"einnahmen": 0, "ausgaben": 0} for _ in range(12)]
    totals = {"einnahme": 0, "ausgabe": 0}
    for b in rows:
        by_category[(b.kind, b.category.name)] += b.amount_cents
        totals[b.kind] += b.amount_cents
        months[b.date.month - 1]["einnahmen" if b.kind == "einnahme" else "ausgaben"] += b.amount_cents
    return {
        "year": year,
        "einnahmen": sorted(((n, eur(c)) for (k, n), c in by_category.items() if k == "einnahme")),
        "ausgaben": sorted(((n, eur(c)) for (k, n), c in by_category.items() if k == "ausgabe")),
        "total_einnahmen": eur(totals["einnahme"]), "total_ausgaben": eur(totals["ausgabe"]),
        "ueberschuss": eur(totals["einnahme"] - totals["ausgabe"]),
        "ueberschuss_cents": totals["einnahme"] - totals["ausgabe"],
        "total_einnahmen_cents": totals["einnahme"], "total_ausgaben_cents": totals["ausgabe"],
        "months": [(i + 1, m["einnahmen"], m["ausgaben"], m["einnahmen"] - m["ausgaben"]) for i, m in enumerate(months)],
    }


def _safe(text):
    """Schutz vor Formel-Einschleusung in Excel (Zellen, die mit = + - @ beginnen)."""
    text = (text or "").replace("\r", " ").replace("\n", " ")
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t") else text


def _decimal(cents):
    sign = "-" if cents < 0 else ""
    return f"{sign}{abs(cents) // 100},{abs(cents) % 100:02d}"


def journal_csv(year):
    """Journal für den Steuerberater. Gegenseite nur bei Ausgaben (keine Patientennamen bei Honoraren)."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    writer.writerow(["Buchungsnr", "Datum", "Kategorie", "Text", "Gegenseite", "Einnahme", "Ausgabe", "Belegnr", "Herkunft"])
    for b in Buchung.objects.filter(date__year=year).select_related("category").prefetch_related("belege"):
        writer.writerow([
            b.number, f"{b.date:%d.%m.%Y}", b.category.name, _safe(b.text),
            _safe(b.counterpart) if b.kind == "ausgabe" else "",
            _decimal(b.amount_cents) if b.kind == "einnahme" else "",
            _decimal(b.amount_cents) if b.kind == "ausgabe" else "",
            ", ".join(x.number for x in b.belege.all()), b.get_source_display(),
        ])
    return ("﻿" + out.getvalue()).encode("utf-8")


def export_zip(year):
    """ZIP mit Journal (CSV) und den Belegen des Jahres (entschlüsselt, Dateiname = Belegnummer)."""
    exts = {ct: ext for _p, ct, ext in MAGIC}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"journal_{year}.csv", journal_csv(year))
        for beleg in Beleg.objects.filter(buchung__date__year=year):
            archive.writestr(f"belege/{beleg.number}.{exts[beleg.content_type]}", read_beleg(beleg))
    audit_log("export.buchhaltung", object_type="export", object_id=str(year))
    return buffer.getvalue()
