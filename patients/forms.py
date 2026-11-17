from datetime import date, datetime

from django import forms
from django.core.validators import validate_email

from .models import Eintrag, Patient, Zuweisung

DATE_WIDGET = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class ZuweisungForm(forms.ModelForm):
    class Meta:
        model = Zuweisung
        fields = ["doctor", "diagnosis", "units_total", "valid_from", "valid_until"]
        widgets = {"valid_from": DATE_WIDGET, "valid_until": DATE_WIDGET}

    def clean_units_total(self):
        value = self.cleaned_data["units_total"]
        if not 1 <= value <= 99:
            raise forms.ValidationError("Bitte 1 bis 99 Einheiten angeben.")
        return value

    def clean(self):
        data = super().clean()
        start, end = data.get("valid_from"), data.get("valid_until")
        if start and end and end < start:
            self.add_error("valid_until", "Das Ende liegt vor dem Beginn.")
        return data


class ReferralChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        until = f" bis {obj.valid_until:%d.%m.%Y}" if obj.valid_until else ""
        return f"ab {obj.valid_from:%d.%m.%Y}{until}, noch {obj.units_left} von {obj.units_total} Einheiten"


class EintragForm(forms.Form):
    kind = forms.ChoiceField(label="Art", choices=Eintrag.Kind.choices)
    date = forms.DateField(label="Datum", widget=DATE_WIDGET)
    referral = ReferralChoiceField(label="Zuweisung", queryset=Zuweisung.objects.none(), required=False)
    text = forms.CharField(label="Text", widget=forms.Textarea(attrs={"rows": 8}))

    def __init__(self, *args, patient, **kwargs):
        super().__init__(*args, **kwargs)
        self.patient = patient
        self.fields["referral"].queryset = patient.referrals.all()

    def clean(self):
        data = super().clean()
        kind, day, referral = data.get("kind"), data.get("date"), data.get("referral")
        if kind != Eintrag.Kind.BEHANDLUNG:
            data["referral"] = None
        elif referral and day:
            if not referral.is_valid_on(day):
                self.add_error("referral", "Die Zuweisung ist an diesem Datum nicht gültig.")
            elif referral.units_left <= 0:
                self.add_error("referral", "Alle Einheiten dieser Zuweisung sind verbraucht.")
        if day and day > date.today():
            self.add_error("date", "Das Datum liegt in der Zukunft.")
        return data


class EintragTextForm(forms.Form):
    text = forms.CharField(label="Text", widget=forms.Textarea(attrs={"rows": 8}))


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
