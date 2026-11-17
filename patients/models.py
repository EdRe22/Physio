from django.db import models

from core.fields import EncryptedTextField


class Patient(models.Model):
    """Patientenstammdaten. Alle personenbezogenen Felder sind pro Mandant verschlüsselt."""

    first_name = EncryptedTextField("Vorname")
    last_name = EncryptedTextField("Nachname")
    birth_date = EncryptedTextField("Geburtsdatum", blank=True)  # ISO-Format JJJJ-MM-TT
    insurance_no = EncryptedTextField("Versicherungsnummer", blank=True)
    insurer = EncryptedTextField("Versicherung", blank=True)
    address = EncryptedTextField("Adresse", blank=True)
    phone = EncryptedTextField("Telefon", blank=True)
    email = EncryptedTextField("E-Mail", blank=True)

    # Sperre statt Löschung: Aufbewahrungspflicht (MTD-Gesetz), automatische Löschung nach Fristablauf folgt.
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Patient"
        verbose_name_plural = "Patienten"

    def __str__(self):
        return f"Patient {self.pk}"  # nie Namen in Logs oder Fehlermeldungen

    @property
    def full_name(self):
        return f"{self.last_name} {self.first_name}".strip()

    def has_consent(self, kind) -> bool:
        return self.consents.filter(kind=kind, revoked_at__isnull=True).exists()


class Einwilligung(models.Model):
    class Kind(models.TextChoices):
        EMAIL_REMINDER = "email_reminder", "Terminerinnerung per E-Mail"
        PHOTO = "photo", "Fotos zur Dokumentation"
        TRANSFER = "transfer", "Weitergabe an Dritte"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="consents")
    kind = models.CharField(max_length=32, choices=Kind.choices)
    granted_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Einwilligung"
        verbose_name_plural = "Einwilligungen"

    @property
    def is_active(self):
        return self.revoked_at is None


class Zuweisung(models.Model):
    """Ärztliche Zuweisung/Verordnung mit Einheitenkontingent und Gültigkeit."""

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="referrals")
    doctor = EncryptedTextField("Zuweisender Arzt")
    diagnosis = EncryptedTextField("Diagnose", blank=True)
    units_total = models.PositiveSmallIntegerField("Anzahl Einheiten")
    valid_from = models.DateField("Gültig ab")
    valid_until = models.DateField("Gültig bis", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Zuweisung"
        verbose_name_plural = "Zuweisungen"
        ordering = ["-valid_from", "-id"]

    def __str__(self):
        return f"Zuweisung {self.pk}"

    @property
    def units_used(self):
        return self.entries.filter(kind=Eintrag.Kind.BEHANDLUNG).count()

    @property
    def units_left(self):
        return max(self.units_total - self.units_used, 0)

    def is_valid_on(self, day) -> bool:
        return self.valid_from <= day and (self.valid_until is None or day <= self.valid_until)


class Eintrag(models.Model):
    """Dokumentationseintrag (Anamnese, Befund, Behandlung, Verlauf).

    Einträge werden nie gelöscht und der Text nie überschrieben: Jede Änderung ergibt eine neue
    Version (Dokumentationspflicht, Nachvollziehbarkeit).
    """

    class Kind(models.TextChoices):
        ANAMNESE = "anamnese", "Anamnese"
        BEFUND = "befund", "Befund"
        BEHANDLUNG = "behandlung", "Behandlung"
        VERLAUF = "verlauf", "Verlauf"

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="entries")
    referral = models.ForeignKey(Zuweisung, null=True, blank=True, on_delete=models.PROTECT, related_name="entries")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    date = models.DateField("Datum")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Eintrag"
        verbose_name_plural = "Einträge"
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"Eintrag {self.pk}"

    def delete(self, *args, **kwargs):
        raise ProtectedDeletion("Dokumentationseinträge dürfen nicht gelöscht werden.")

    @property
    def current(self):
        return self.versions.order_by("-version").first()

    def add_version(self, text, author_id=None):
        last = self.current
        return EintragVersion.objects.create(
            entry=self, version=(last.version + 1) if last else 1, text=text, author_id=author_id
        )


class EintragVersion(models.Model):
    entry = models.ForeignKey(Eintrag, on_delete=models.PROTECT, related_name="versions")
    version = models.PositiveIntegerField()
    text = EncryptedTextField("Text")
    author_id = models.BigIntegerField(null=True)  # Benutzer liegt in der Praxis-DB, daher kein FK
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["entry", "version"], name="uniq_entry_version")]
        ordering = ["version"]

    def __str__(self):
        return f"Eintrag {self.entry_id} v{self.version}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ProtectedDeletion("Versionen sind unveränderlich, neue Version anlegen.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedDeletion("Dokumentationsversionen dürfen nicht gelöscht werden.")


class ProtectedDeletion(Exception):
    pass
