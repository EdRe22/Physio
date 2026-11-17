"""Honorarnoten (Phase 4).

Grundsätze:
- Ausgestellte Honorarnoten sind unveränderlich (Nachvollziehbarkeit, BAO). Korrektur nur per Storno.
- Die Rechnungsnummer wird erst beim Ausstellen vergeben, fortlaufend und lückenlos pro Jahr.
- Empfänger und Aussteller werden beim Ausstellen als Schnappschuss festgehalten.
- Beträge in Cent (ganze Zahlen), keine Gleitkommazahlen.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.db import models

from core.fields import EncryptedTextField

KLEINBETRAG_CENTS = 40000  # Kleinbetragsrechnung bis 400 EUR (§ 11 Abs. 6 UStG): Empfänger nicht zwingend


class ImmutableError(Exception):
    """Änderung an einem ausgestellten (festgeschriebenen) Beleg."""


def eur(cents) -> str:
    """Cent als deutschsprachiger Betrag, z. B. 1234 -> '12,34 EUR'."""
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    return f"{sign}{cents // 100:,}".replace(",", ".") + f",{cents % 100:02d} EUR"


class Rechnungsprofil(models.Model):
    """Angaben des Therapeuten für seine Honorarnoten (genau ein Datensatz pro Mandant)."""

    name = models.CharField("Name", max_length=120)
    title = models.CharField("Berufsbezeichnung", max_length=120, default="Physiotherapeut/in")
    address = models.TextField("Anschrift")
    phone = models.CharField("Telefon", max_length=60, blank=True)
    email = models.CharField("E-Mail", max_length=120, blank=True)
    tax_no = models.CharField("Steuernummer", max_length=40, blank=True)
    uid = models.CharField("UID-Nummer (falls vorhanden)", max_length=20, blank=True)
    iban = models.CharField("IBAN", max_length=40, blank=True)
    bic = models.CharField("BIC", max_length=20, blank=True)
    payment_days = models.PositiveSmallIntegerField("Zahlungsziel (Tage)", default=14)
    exemption_text = models.TextField(
        "Hinweis zur Umsatzsteuerbefreiung",
        default="PLATZHALTER: Umsatzsteuerbefreite Leistung. Wortlaut und Rechtsgrundlage vom Steuerberater bestätigen lassen.",
    )
    reminder_days = models.PositiveSmallIntegerField("Zahlungsfrist in Mahnungen (Tage)", default=7)
    reminder_fee_cents = models.PositiveIntegerField(default=0)  # Mahnspesen ab Stufe 2, nur bei vertraglicher Vereinbarung
    logo_b64 = models.TextField(blank=True, default="", editable=False)  # fertig umgerechnetes PNG, siehe core/logo.py
    exemption_confirmed_on = models.DateField(
        "Vom Steuerberater bestätigt am", null=True, blank=True,
        help_text="Ohne dieses Datum können keine Honorarnoten ausgestellt werden.",
    )

    class Meta:
        verbose_name = "Rechnungsprofil"

    def __str__(self):
        return "Rechnungsprofil"

    @property
    def ready_to_issue(self):
        return bool(self.name and self.address and self.exemption_text and self.exemption_confirmed_on)


class Leistung(models.Model):
    name = models.CharField("Bezeichnung", max_length=120)
    price_cents = models.PositiveIntegerField("Preis (Cent)")
    active = models.BooleanField("Aktiv", default=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Leistungen"

    def __str__(self):
        return f"Leistung {self.pk}"

    @property
    def price_eur(self):
        return eur(self.price_cents)


class Nummernkreis(models.Model):
    year = models.PositiveSmallIntegerField(unique=True)
    last = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"Nummernkreis {self.year}"


class Honorarnote(models.Model):
    class Kind(models.TextChoices):
        RECHNUNG = "rechnung", "Honorarnote"
        STORNO = "storno", "Stornorechnung"

    class Status(models.TextChoices):
        ENTWURF = "entwurf", "Entwurf"
        AUSGESTELLT = "ausgestellt", "Offen"
        BEZAHLT = "bezahlt", "Bezahlt"
        STORNIERT = "storniert", "Storniert"

    FROZEN = (
        "patient_id", "number", "kind", "issue_date", "service_from", "service_to", "due_date", "total_cents",
        "recipient_name", "recipient_address", "issuer_snapshot", "cancels_id", "referral_id",
    )

    patient = models.ForeignKey("patients.Patient", on_delete=models.PROTECT, related_name="invoices")
    referral = models.ForeignKey("patients.Zuweisung", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.RECHNUNG)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ENTWURF)
    number = models.CharField(max_length=20, unique=True, null=True, blank=True)
    cancels = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT, related_name="cancelled_by")
    issue_date = models.DateField(null=True, blank=True)
    service_from = models.DateField(null=True, blank=True)
    service_to = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    total_cents = models.IntegerField(default=0)
    recipient_name = EncryptedTextField(blank=True, default="")
    recipient_address = EncryptedTextField(blank=True, default="")
    issuer_snapshot = models.TextField(blank=True, default="")  # JSON des Rechnungsprofils zum Ausstellungszeitpunkt
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-issue_date", "-id"]
        verbose_name = "Honorarnote"
        verbose_name_plural = "Honorarnoten"

    def __str__(self):
        return f"Honorarnote {self.number or 'Entwurf ' + str(self.pk)}"

    @classmethod
    def from_db(cls, db, field_names, values):
        obj = super().from_db(db, field_names, values)
        obj._orig = {name: value for name, value in zip(field_names, values) if name in cls.FROZEN}
        return obj

    @property
    def is_draft(self):
        return self.status == self.Status.ENTWURF

    @property
    def is_issued(self):
        return self.number is not None

    def save(self, *args, **kwargs):
        orig = getattr(self, "_orig", None)
        if orig is not None and orig.get("number") is not None:
            changed = [n for n in self.FROZEN if n in orig and getattr(self, n) != orig[n]]
            if changed:
                raise ImmutableError("Ausgestellte Honorarnoten sind unveränderlich: " + ", ".join(changed))
        super().save(*args, **kwargs)
        self._orig = {name: getattr(self, name) for name in self.FROZEN}

    def delete(self, *args, **kwargs):
        if self.is_issued:
            raise ImmutableError("Ausgestellte Honorarnoten dürfen nicht gelöscht werden (Aufbewahrungspflicht).")
        super().delete(*args, **kwargs)

    @property
    def total_eur(self):
        return eur(self.total_cents)

    @property
    def paid_cents(self):
        return sum(p.amount_cents for p in self.payments.all())

    @property
    def open_cents(self):
        return max(self.total_cents - self.paid_cents, 0) if self.kind == self.Kind.RECHNUNG else 0

    @property
    def open_eur(self):
        return eur(self.open_cents)

    def recalculate(self):
        self.total_cents = sum(p.line_total_cents for p in self.positions.all())


class Position(models.Model):
    invoice = models.ForeignKey(Honorarnote, on_delete=models.PROTECT, related_name="positions")
    entry = models.ForeignKey("patients.Eintrag", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    date = models.DateField("Datum der Leistung")
    description = EncryptedTextField("Leistung")
    quantity = models.DecimalField("Menge", max_digits=6, decimal_places=2, default=Decimal("1"))
    unit_price_cents = models.IntegerField("Einzelpreis (Cent)")
    line_total_cents = models.IntegerField(default=0)

    class Meta:
        ordering = ["date", "id"]

    def __str__(self):
        return f"Position {self.pk}"

    def save(self, *args, **kwargs):
        if self.invoice.is_issued and not self._state.adding:
            raise ImmutableError("Positionen ausgestellter Honorarnoten sind unveränderlich.")
        if self.invoice.is_issued and self._state.adding:
            raise ImmutableError("Zu ausgestellten Honorarnoten können keine Positionen hinzugefügt werden.")
        self.line_total_cents = int((Decimal(self.quantity) * self.unit_price_cents).quantize(Decimal("1"), ROUND_HALF_UP))
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.invoice.is_issued:
            raise ImmutableError("Positionen ausgestellter Honorarnoten dürfen nicht gelöscht werden.")
        super().delete(*args, **kwargs)

    @property
    def unit_price_eur(self):
        return eur(self.unit_price_cents)

    @property
    def total_eur(self):
        return eur(self.line_total_cents)


class _Immutable(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ImmutableError("Dieser Eintrag ist unveränderlich.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableError("Dieser Eintrag darf nicht gelöscht werden.")


class Zahlung(_Immutable):
    class Method(models.TextChoices):
        UEBERWEISUNG = "ueberweisung", "Überweisung"
        SONSTIGE = "sonstige", "Sonstige (unbar)"

    invoice = models.ForeignKey(Honorarnote, on_delete=models.PROTECT, related_name="payments")
    date = models.DateField("Zahlungsdatum")
    amount_cents = models.PositiveIntegerField("Betrag (Cent)")
    method = models.CharField(max_length=16, choices=Method.choices, default=Method.UEBERWEISUNG)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "id"]
        verbose_name_plural = "Zahlungen"

    def __str__(self):
        return f"Zahlung {self.pk}"

    @property
    def amount_eur(self):
        return eur(self.amount_cents)


class Mahnung(_Immutable):
    invoice = models.ForeignKey(Honorarnote, on_delete=models.PROTECT, related_name="reminders")
    level = models.PositiveSmallIntegerField()
    date = models.DateField()
    # Stand beim Erstellen (Mahnungen sind unveränderlich, spätere Profiländerungen betreffen sie nicht)
    open_cents = models.PositiveIntegerField(default=0)
    fee_cents = models.PositiveIntegerField(default=0)
    pay_until = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["level"]
        constraints = [models.UniqueConstraint(fields=["invoice", "level"], name="uniq_invoice_level")]

    def __str__(self):
        return f"Mahnung {self.pk}"
