from datetime import date

from django import forms

from core.models import Tenant
from rechnungen.forms import DATE_WIDGET, iban_valid, parse_eur

from core.logo import LogoFormMixin

from .models import PraxisProfil


class ProfilForm(LogoFormMixin):
    tax_confirmed_on = forms.DateField(label="Steuerhinweis bestätigt am (Steuerberater)", required=False, widget=DATE_WIDGET)

    class Meta:
        model = PraxisProfil
        fields = ["name", "address", "iban", "tax_text", "tax_confirmed_on", "payment_days"]
        widgets = {"address": forms.Textarea(attrs={"rows": 3})}

    def clean_iban(self):
        value = self.cleaned_data["iban"].replace(" ", "").upper()
        if not iban_valid(value):
            raise forms.ValidationError("Die IBAN ist ungültig.")
        return value

    def clean_tax_text(self):
        text = self.cleaned_data["tax_text"].strip()
        if text.upper().startswith("PLATZHALTER"):
            raise forms.ValidationError("Bitte den vom Steuerberater bestätigten Wortlaut eintragen.")
        return text

    def clean_tax_confirmed_on(self):
        value = self.cleaned_data["tax_confirmed_on"]
        if value and value > date.today():
            raise forms.ValidationError("Das Datum liegt in der Zukunft.")
        return value

    def clean_payment_days(self):
        if not 1 <= self.cleaned_data["payment_days"] <= 90:
            raise forms.ValidationError("Zahlungsziel zwischen 1 und 90 Tagen.")
        return self.cleaned_data["payment_days"]


class LeaseForm(forms.Form):
    tenant = forms.ModelChoiceField(label="Therapeut", queryset=Tenant.objects.none())
    rent = forms.CharField(label="Miete pro Monat (EUR)")
    utilities = forms.CharField(label="Betriebskosten pro Monat (EUR)", required=False)
    valid_from = forms.DateField(label="Gültig ab", widget=DATE_WIDGET)
    valid_until = forms.DateField(label="Gültig bis (leer = unbefristet)", required=False, widget=DATE_WIDGET)
    note = forms.CharField(label="Notiz", max_length=200, required=False)

    def __init__(self, *args, owner_tenant_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tenant"].queryset = Tenant.objects.filter(is_active=True).exclude(pk=owner_tenant_id)

    def clean_rent(self):
        return parse_eur(self.cleaned_data["rent"])

    def clean_utilities(self):
        value = self.cleaned_data["utilities"].strip()
        return parse_eur(value) if value else 0

    def clean(self):
        data = super().clean()
        start, end = data.get("valid_from"), data.get("valid_until")
        if start and end and end < start:
            self.add_error("valid_until", "Das Ende liegt vor dem Beginn.")
        if data.get("tenant") and start:
            from .models import Mietverhaeltnis

            for other in Mietverhaeltnis.objects.filter(tenant=data["tenant"]):
                other_end = other.valid_until or date.max
                if start <= other_end and (end or date.max) >= other.valid_from:
                    raise forms.ValidationError("Der Zeitraum überschneidet sich mit einem bestehenden Mietverhältnis.")
        return data


class PeriodForm(forms.Form):
    period = forms.RegexField(r"^\d{4}-(0[1-9]|1[0-2])$", label="Abrechnungsmonat (JJJJ-MM)")


class PaidForm(forms.Form):
    date = forms.DateField(widget=DATE_WIDGET)
