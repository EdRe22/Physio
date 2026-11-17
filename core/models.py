from django.core.validators import RegexValidator
from django.db import models

from . import crypto, tenancy


class Tenant(models.Model):
    """Ein Therapeut = ein Mandant. Liegt in der Praxis-Datenbank, enthält keine Patientendaten."""

    slug = models.CharField(
        max_length=32,
        unique=True,
        validators=[RegexValidator(tenancy.SLUG_RE, "Kürzel: Kleinbuchstaben, Ziffern, _, 3-32 Zeichen")],
    )
    name = models.CharField(max_length=120)
    wrapped_key = models.TextField(editable=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # Praxisende / Ausscheiden: gesperrt, Daten bis zum Fristende aufbewahrt, danach per Schlüsselvernichtung gelöscht.
    offboarded_on = models.DateField(null=True, blank=True)
    retain_until = models.DateField(null=True, blank=True)
    purged_on = models.DateField(null=True, blank=True)

    def __str__(self):
        return self.name

    @classmethod
    def provision(cls, slug: str, name: str) -> "Tenant":
        """Legt den Mandanten mit neuem, durch den Master-Schlüssel geschützten Datenschlüssel an."""
        tenancy.alias_for(slug)  # validiert das Kürzel
        return cls.objects.create(slug=slug, name=name, wrapped_key=crypto.wrap_key(crypto.new_data_key()))

    def context(self) -> tenancy.TenantContext:
        return tenancy.TenantContext(
            slug=self.slug,
            alias=tenancy.alias_for(self.slug),
            data_key=crypto.unwrap_key(self.wrapped_key),
        )
