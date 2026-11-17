from django.contrib.auth.models import AbstractUser
from django.db import models


class Therapist(AbstractUser):
    """Benutzer. Jeder Therapeut gehört zu genau einem Mandanten."""

    class Role(models.TextChoices):
        THERAPIST = "therapist", "Therapeut"
        OWNER = "owner", "Eigentümer"

    tenant = models.OneToOneField("core.Tenant", null=True, blank=True, on_delete=models.PROTECT, related_name="user")
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.THERAPIST)
    must_change_password = models.BooleanField(default=False)  # nach Startpasswort/Zurücksetzen: erst ändern, dann arbeiten
    theme_palette = models.CharField(max_length=20, blank=True, default="")  # eigene Farbe; leer = Standardfarbe der Praxis
    theme_custom = models.CharField(max_length=7, blank=True, default="")  # bei Palette "logo": aus dem eigenen Logo berechnet
    failed_logins = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    @property
    def is_owner(self):
        return self.role == self.Role.OWNER
