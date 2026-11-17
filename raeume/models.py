"""Raumbelegung für die ganze Praxis.

Liegt in der gemeinsamen Praxis-Datenbank, weil alle Therapeuten dieselben Räume sehen müssen. Datensparsamkeit:
gespeichert werden NUR Raum, Therapeut (Mandant), Zeitraum und Art. Kein Patient, kein Terminbezug in diese Richtung
(der Termin im Mandanten merkt sich die Buchungs-ID, nicht umgekehrt). Andere Therapeuten sehen also nur
„Raum belegt von <Kollege>“, nicht wofür oder für wen.
"""
from django.db import models
from django.utils import timezone

from rechnungen.models import ImmutableError


class Raum(models.Model):
    name = models.CharField(max_length=60, unique=True)
    note = models.CharField(max_length=200, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Raum"
        verbose_name_plural = "Räume"

    def __str__(self):
        return self.name


class Raumbelegung(models.Model):
    class Kind(models.TextChoices):
        TERMIN = "termin", "Behandlungstermin"
        RESERVIERUNG = "reservierung", "Reservierung"

    room = models.ForeignKey(Raum, on_delete=models.PROTECT, related_name="bookings")
    tenant = models.ForeignKey("core.Tenant", on_delete=models.PROTECT, related_name="room_bookings")
    start = models.DateTimeField()
    end = models.DateTimeField()
    kind = models.CharField(max_length=14, choices=Kind.choices, default=Kind.RESERVIERUNG)
    series = models.CharField(max_length=32, blank=True)  # gemeinsame Kennung einer Wochenserie
    created_at = models.DateTimeField(auto_now_add=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["start"]
        indexes = [models.Index(fields=["room", "start"])]
        verbose_name = "Raumbelegung"
        verbose_name_plural = "Raumbelegungen"

    FROZEN = ("room_id", "tenant_id", "kind", "series")

    def __str__(self):
        return f"Raumbelegung {self.pk}"

    @property
    def is_active(self):
        return self.cancelled_at is None

    def save(self, *args, **kwargs):
        if not self._state.adding:
            old = type(self).objects.get(pk=self.pk)
            if any(getattr(old, f) != getattr(self, f) for f in self.FROZEN):
                raise ImmutableError("Raum, Therapeut und Art einer Belegung sind unveränderlich.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableError("Raumbelegungen werden storniert, nicht gelöscht.")

    def cancel(self):
        if self.cancelled_at is None:
            self.cancelled_at = timezone.now()
            self.save(update_fields=["cancelled_at"])
