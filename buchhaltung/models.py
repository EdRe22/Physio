"""Buchhaltung als Einnahmen-Ausgaben-Rechnung (Zufluss-/Abflussprinzip).

- Buchungen sind unveränderlich. Korrektur nur per Gegenbuchung (Storno), damit der ursprüngliche
  Inhalt immer feststellbar bleibt (BAO). Der Audit-Log speichert nur IDs, nicht die Werte.
- Buchungs- und Belegnummern laufen fortlaufend pro Jahr.
- Belege (PDF/Foto) liegen verschlüsselt (Mandantenschlüssel) im Dateisystem und werden nie gelöscht.
"""
from django.db import models

from core.fields import EncryptedTextField
from rechnungen.models import ImmutableError, eur


class Zaehler(models.Model):
    key = models.CharField(max_length=32, unique=True)
    last = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"Zähler {self.key}"


class Kategorie(models.Model):
    class Kind(models.TextChoices):
        EINNAHME = "einnahme", "Einnahme"
        AUSGABE = "ausgabe", "Ausgabe"

    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["kind", "name"]
        constraints = [models.UniqueConstraint(fields=["name", "kind"], name="uniq_category")]
        verbose_name_plural = "Kategorien"

    def __str__(self):
        return f"Kategorie {self.pk}"


DEFAULT_CATEGORIES = [
    ("Honorare", "einnahme"), ("Sonstige Einnahmen", "einnahme"),
    ("Miete", "ausgabe"), ("Betriebskosten", "ausgabe"), ("Material und Verbrauch", "ausgabe"),
    ("Fortbildung", "ausgabe"), ("Versicherungen", "ausgabe"), ("Fahrtkosten", "ausgabe"),
    ("Büro und Software", "ausgabe"), ("Sonstige Ausgaben", "ausgabe"),
]


class Beleg(models.Model):
    number = models.CharField(max_length=20, unique=True)
    original_name = EncryptedTextField()
    content_type = models.CharField(max_length=40)
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)  # Hash des Originals: Nachweis der Unverändertheit
    storage_name = models.CharField(max_length=64, unique=True)
    buchung = models.ForeignKey("Buchung", null=True, blank=True, on_delete=models.PROTECT, related_name="belege")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["number"]
        verbose_name_plural = "Belege"

    def __str__(self):
        return f"Beleg {self.number}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ImmutableError("Belege sind unveränderlich.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableError("Belege dürfen nicht gelöscht werden (Aufbewahrungspflicht).")


class Buchung(models.Model):
    class Source(models.TextChoices):
        MANUELL = "manuell", "Manuell"
        BANK = "bank", "Bankumsatz"
        ZAHLUNG = "zahlung", "Zahlung zu Honorarnote"
        STORNO = "storno", "Stornobuchung"

    number = models.CharField(max_length=20, unique=True)
    date = models.DateField()
    kind = models.CharField(max_length=10, choices=Kategorie.Kind.choices)
    category = models.ForeignKey(Kategorie, on_delete=models.PROTECT, related_name="bookings")
    amount_cents = models.IntegerField()  # positiv, nur Stornobuchungen sind negativ
    text = EncryptedTextField(blank=True, default="")
    counterpart = EncryptedTextField(blank=True, default="")
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.MANUELL)
    zahlung = models.OneToOneField("rechnungen.Zahlung", null=True, blank=True, on_delete=models.PROTECT, related_name="booking")
    reverses = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversed_by")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "number"]
        verbose_name_plural = "Buchungen"

    def __str__(self):
        return f"Buchung {self.number}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ImmutableError("Buchungen sind unveränderlich, bitte stornieren und neu buchen.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableError("Buchungen dürfen nicht gelöscht werden.")

    @property
    def amount_eur(self):
        return eur(self.amount_cents)


class BankTransaktion(models.Model):
    class Status(models.TextChoices):
        OFFEN = "offen", "Offen"
        ZUGEORDNET = "zugeordnet", "Zugeordnet"
        IGNORIERT = "ignoriert", "Ignoriert (privat/durchlaufend)"

    date = models.DateField()
    amount_cents = models.IntegerField()  # positiv = Eingang, negativ = Ausgang
    counterpart = EncryptedTextField(blank=True, default="")
    iban = EncryptedTextField(blank=True, default="")
    purpose = EncryptedTextField(blank=True, default="")
    reference = EncryptedTextField(blank=True, default="")
    tx_hash = models.CharField(max_length=64, unique=True)  # HMAC über die Umsatzdaten, nur zur Dublettenerkennung
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OFFEN)
    invoice = models.ForeignKey("rechnungen.Honorarnote", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    buchung = models.OneToOneField(Buchung, null=True, blank=True, on_delete=models.PROTECT, related_name="bank_tx")
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "Bankumsatz"
        verbose_name_plural = "Bankumsätze"

    def __str__(self):
        return f"Bankumsatz {self.pk}"

    def delete(self, *args, **kwargs):
        raise ImmutableError("Bankumsätze dürfen nicht gelöscht werden.")

    @property
    def amount_eur(self):
        return eur(self.amount_cents)
