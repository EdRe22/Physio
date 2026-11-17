from datetime import date

from django import forms

from rechnungen.forms import DATE_WIDGET, parse_eur

from .models import Kategorie


class BuchungForm(forms.Form):
    date = forms.DateField(label="Datum", widget=DATE_WIDGET)
    kind = forms.ChoiceField(label="Art", choices=Kategorie.Kind.choices)
    category = forms.ModelChoiceField(label="Kategorie", queryset=Kategorie.objects.none())
    amount = forms.CharField(label="Betrag (EUR)")
    text = forms.CharField(label="Buchungstext", max_length=200, required=False)
    counterpart = forms.CharField(label="Gegenseite (z. B. Lieferant)", max_length=200, required=False)
    beleg = forms.FileField(label="Beleg (PDF, JPEG oder PNG, höchstens 10 MB)", required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Kategorie.objects.filter(active=True)
        self.fields["category"].label_from_instance = lambda c: f"{c.name} ({c.get_kind_display()})"

    def clean_date(self):
        if self.cleaned_data["date"] > date.today():
            raise forms.ValidationError("Das Datum liegt in der Zukunft.")
        return self.cleaned_data["date"]

    def clean_amount(self):
        cents = parse_eur(self.cleaned_data["amount"])
        if cents == 0:
            raise forms.ValidationError("Der Betrag muss größer als 0 sein.")
        return cents

    def clean(self):
        data = super().clean()
        if data.get("category") and data.get("kind") and data["category"].kind != data["kind"]:
            self.add_error("category", "Die Kategorie passt nicht zur Art (Einnahme/Ausgabe).")
        return data


class ZuordnungForm(forms.Form):
    category = forms.ModelChoiceField(queryset=Kategorie.objects.none())
    text = forms.CharField(max_length=200, required=False)

    def __init__(self, *args, kind, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Kategorie.objects.filter(active=True, kind=kind)
