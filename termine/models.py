from datetime import timedelta

from django.db import models


class Termin(models.Model):
    """Termin im Kalender eines Therapeuten (liegt in dessen Mandanten-Datenbank)."""

    class Status(models.TextChoices):
        GEPLANT = "geplant", "Geplant"
        ERLEDIGT = "erledigt", "Erledigt"
        ABGESAGT = "abgesagt", "Abgesagt"
        NICHT_ERSCHIENEN = "nicht_erschienen", "Nicht erschienen"

    patient = models.ForeignKey("patients.Patient", on_delete=models.PROTECT, related_name="appointments")
    start = models.DateTimeField()
    duration_min = models.PositiveSmallIntegerField(default=30)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.GEPLANT)
    room_booking_id = models.BigIntegerField(null=True, blank=True)  # Raumbelegung liegt in der Praxis-DB (kein FK über Datenbanken)
    reminder_sent_at = models.DateTimeField(null=True, blank=True)
    reminder_failed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Termin"
        verbose_name_plural = "Termine"
        ordering = ["start"]
        indexes = [models.Index(fields=["start"])]

    def __str__(self):
        return f"Termin {self.pk}"

    @property
    def end(self):
        return self.start + timedelta(minutes=self.duration_min)

    @classmethod
    def conflicts(cls, start, end, exclude_pk=None):
        """Überschneidende, nicht abgesagte Termine im eigenen Kalender."""
        candidates = cls.objects.filter(
            start__lt=end, start__gte=start - timedelta(hours=12), status__in=[cls.Status.GEPLANT, cls.Status.ERLEDIGT]
        )
        if exclude_pk:
            candidates = candidates.exclude(pk=exclude_pk)
        return [t for t in candidates if t.end > start]
