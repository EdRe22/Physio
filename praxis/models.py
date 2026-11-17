"""Praxisbereich: Miete vom Eigentümer an die Kollegen.

Liegt in der gemeinsamen Praxis-Datenbank ("default"), enthält aber ausschließlich Beträge und
Stammdaten der Therapeuten, niemals Patientendaten. Der Eigentümer sieht alle Abrechnungen,
jeder andere Therapeut nur die eigenen (Filter zentral in services.visible_invoices).
"""
from django.db import models

from rechnungen.models import ImmutableError, eur


class PraxisProfil(models.Model):
    """Absenderdaten des Eigentümers für Mietabrechnungen (Einzelsatz)."""

    name = models.CharField(max_length=120)
    address = models.TextField()
    iban = models.CharField(max_length=34)
    tax_text = models.CharField("Steuerhinweis", max_length=300)
    tax_confirmed_on = models.DateField("Steuerhinweis bestätigt am", null=True, blank=True)
    payment_days = models.PositiveSmallIntegerField(default=14)

    @property
    def ready(self):
        return bool(self.name and self.address and self.iban and self.tax_text and self.tax_confirmed_on)


class Mietverhaeltnis(models.Model):
    tenant = models.ForeignKey("core.Tenant", on_delete=models.PROTECT, related_name="leases")
    rent_cents = models.PositiveIntegerField("Miete pro Monat (Cent)")
    utilities_cents = models.PositiveIntegerField("Betriebskosten pro Monat (Cent)", default=0)
    valid_from = models.DateField()
    valid_until = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["tenant_id", "-valid_from"]
        verbose_name_plural = "Mietverhältnisse"

    @property
    def total_cents(self):
        return self.rent_cents + self.utilities_cents

    @property
    def rent_eur(self):
        return eur(self.rent_cents)

    @property
    def utilities_eur(self):
        return eur(self.utilities_cents)


class Zaehler(models.Model):
    key = models.CharField(max_length=32, unique=True)
    last = models.PositiveIntegerField(default=0)


class Mietabrechnung(models.Model):
    FROZEN = ("tenant_id", "lease_id", "period", "number", "rent_cents", "utilities_cents", "issued_on", "due_on",
              "issuer_snapshot")

    class Status(models.TextChoices):
        OFFEN = "offen", "Offen"
        BEZAHLT = "bezahlt", "Bezahlt"
        STORNIERT = "storniert", "Storniert"

    tenant = models.ForeignKey("core.Tenant", on_delete=models.PROTECT, related_name="rent_invoices")
    lease = models.ForeignKey(Mietverhaeltnis, on_delete=models.PROTECT, related_name="invoices")
    period = models.CharField(max_length=7)  # JJJJ-MM
    number = models.CharField(max_length=20, unique=True)
    rent_cents = models.PositiveIntegerField()
    utilities_cents = models.PositiveIntegerField()
    issued_on = models.DateField()
    due_on = models.DateField()
    issuer_snapshot = models.TextField()  # JSON, Stand beim Ausstellen
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OFFEN)
    paid_on = models.DateField(null=True, blank=True)
    tenant_booked_on = models.DateField(null=True, blank=True)  # Kollege hat sie in seine Buchhaltung übernommen
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period", "number"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "period"], condition=~models.Q(status="storniert"),
                                    name="one_active_rent_invoice_per_period"),
        ]

    def __str__(self):
        return f"Mietabrechnung {self.number}"

    @property
    def total_cents(self):
        return self.rent_cents + self.utilities_cents

    @property
    def rent_eur(self):
        return eur(self.rent_cents)

    @property
    def utilities_eur(self):
        return eur(self.utilities_cents)

    @property
    def total_eur(self):
        return eur(self.total_cents)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            old = type(self).objects.get(pk=self.pk)
            for name in self.FROZEN:
                if getattr(old, name) != getattr(self, name):
                    raise ImmutableError("Ausgestellte Mietabrechnungen sind unveränderlich.")
            if old.status == self.Status.STORNIERT:
                raise ImmutableError("Eine stornierte Mietabrechnung ist abgeschlossen.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableError("Mietabrechnungen dürfen nicht gelöscht werden.")
