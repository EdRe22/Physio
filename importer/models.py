from django.db import models

from core.fields import EncryptedTextField


class ImportJob(models.Model):
    """Ein Excel/CSV-Import eines Therapeuten (Mandanten-Datenbank).

    Die hochgeladene Datei selbst wird nie gespeichert. Der geparste Inhalt liegt nur
    verschlüsselt (payload) bis zum Abschluss oder Abbruch und wird danach geleert.
    Das Protokoll (report) enthält nur Zähler und Zeilennummern, keine Zellwerte.
    """

    class Status(models.TextChoices):
        UPLOADED = "uploaded", "Hochgeladen"
        CHECKED = "checked", "Geprüft (Trockenlauf)"
        DONE = "done", "Abgeschlossen"
        CANCELLED = "cancelled", "Abgebrochen"

    profile = models.CharField(max_length=16)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.UPLOADED)
    payload = EncryptedTextField(blank=True, default="")  # JSON mit Kopfzeile und Zeilen, nach Abschluss leer
    mapping = models.TextField(blank=True, default="")  # JSON Feld -> Spaltenindex
    report = models.TextField(blank=True, default="")  # JSON nur mit Zählern und Zeilennummern
    row_count = models.PositiveIntegerField(default=0)
    created_by = models.BigIntegerField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Import {self.pk}"

    def delete(self, *args, **kwargs):
        raise RuntimeError("Importprotokolle werden nicht gelöscht.")

    def wipe(self):
        """Entfernt den zwischengespeicherten Dateiinhalt."""
        self.payload = ""
        self.mapping = ""
