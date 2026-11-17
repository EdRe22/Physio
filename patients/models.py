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
