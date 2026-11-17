from datetime import date, datetime

from django import forms
from django.core.validators import validate_email

from .models import Patient


class PatientForm(forms.ModelForm):
    class Meta:
        model = Patient
        fields = ["first_name", "last_name", "birth_date", "insurance_no", "insurer", "address", "phone", "email"]
        widgets = {"address": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["birth_date"].help_text = "z. B. 24.12.1980"
        # ISO-Wert für die Anzeige in deutsches Format umwandeln
        value = self.initial.get("birth_date") or getattr(self.instance, "birth_date", "")
        if value:
            try:
                self.initial["birth_date"] = date.fromisoformat(value).strftime("%d.%m.%Y")
            except ValueError:
                pass

    def clean_birth_date(self):
        raw = self.cleaned_data["birth_date"].strip()
        if not raw:
            return ""
        for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
            if parsed > date.today():
                raise forms.ValidationError("Das Geburtsdatum liegt in der Zukunft.")
            return parsed.isoformat()
        raise forms.ValidationError("Bitte als TT.MM.JJJJ eingeben.")

    def clean_email(self):
        value = self.cleaned_data["email"].strip()
        if value:
            validate_email(value)
        return value
