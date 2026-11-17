from django.core.exceptions import FieldError
from django.db import models

from . import crypto, tenancy


class EncryptedTextField(models.TextField):
    """Text, der mit dem Datenschlüssel des aktiven Mandanten verschlüsselt gespeichert wird.

    Suchen/Filtern in der Datenbank sind bewusst nicht möglich (nur isnull); die
    Suche läuft nach dem Entschlüsseln in der Anwendung.
    """

    description = "Verschlüsselter Text (pro Mandant)"

    def formfield(self, **kwargs):
        # TextField würde jedes Feld als großes Textfeld darstellen; mehrzeilig nur, wo das Formular es ausdrücklich verlangt.
        from django import forms

        kwargs.setdefault("widget", forms.TextInput)
        return super().formfield(**kwargs)

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value is None:
            return None
        return crypto.encrypt(tenancy.require().data_key, value)

    def from_db_value(self, value, expression, connection):
        if value is None:
            return None
        return crypto.decrypt(tenancy.require().data_key, value)

    def get_lookup(self, lookup_name):
        if lookup_name != "isnull":
            raise FieldError("Verschlüsselte Felder erlauben keine Datenbank-Suche.")
        return super().get_lookup(lookup_name)

    def get_transform(self, lookup_name):
        raise FieldError("Verschlüsselte Felder erlauben keine Transformationen.")
