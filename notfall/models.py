"""Notfallzugriff (Break-glass): Vertretung, wenn ein Therapeut ausfällt (Krankheit, Unfall, Tod).

Regeln (bewusst streng, weil hier Gesundheitsdaten eines Kollegen berührt werden):
- Begründung Pflicht, Antrag durch einen Therapeuten, Freigabe durch einen ANDEREN Therapeuten, der weder
  Antragsteller noch Betroffener ist (Vier-Augen-Prinzip).
- Zeitlich begrenzt (24 Stunden ab Freigabe), nur lesend, jederzeit widerrufbar.
- Jeder Schritt und jeder Lesezugriff steht im Audit-Log des Betroffenen. Der Betroffene sieht die Zugriffe
  auf seiner Seite und wird per E-Mail benachrichtigt, sofern eine Adresse hinterlegt ist.
Liegt in der Praxis-Datenbank und enthält keine Patientendaten.
"""
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

DURATION = timedelta(hours=24)
MIN_REASON = 20


class NotfallZugriff(models.Model):
    target = models.ForeignKey("core.Tenant", on_delete=models.PROTECT, related_name="emergency_accesses")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    reason = models.TextField()
    requested_at = models.DateTimeField(auto_now_add=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)  # widerrufen oder beendet

    class Meta:
        ordering = ["-requested_at"]
        verbose_name = "Notfallzugriff"
        verbose_name_plural = "Notfallzugriffe"

    def __str__(self):
        return f"Notfallzugriff {self.pk}"

    def state(self, now=None):
        now = now or timezone.now()
        if self.ended_at:
            return "beendet"
        if not self.approved_at:
            return "beantragt"
        return "aktiv" if now < self.expires_at else "abgelaufen"

    def is_active(self, now=None):
        return self.state(now) == "aktiv"
