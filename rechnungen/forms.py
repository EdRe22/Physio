from datetime import date
from decimal import Decimal, InvalidOperation

from django import forms

from .models import Leistung, Rechnungsprofil, Zahlung

DATE_WIDGET = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


def parse_eur(raw: str) -> int:
    """'45,50' oder '45.5' -> Cent. Höchstens zwei Nachkommastellen, nie negativ."""
    text = (raw or "").strip().replace("€", "").replace("EUR", "").replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise forms.ValidationError("Bitte einen Betrag wie 45,50 eingeben.")
    if value < 0 or value != value.quantize(Decimal("0.01")) or value > Decimal("99999.99"):
        raise forms.ValidationError("Bitte einen gültigen Betrag mit höchstens zwei Nachkommastellen eingeben.")
    return int(value * 100)


def iban_valid(iban: str) -> bool:
    iban = iban.replace(" ", "").upper()
    if not (15 <= len(iban) <= 34) or not iban.isalnum():
        return False
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(c, 36)) for c in rearranged)
    return int(digits) % 97 == 1


class ProfilForm(forms.ModelForm):
    class Meta:
        model = Rechnungsprofil
        fields = ["name", "title", "address", "phone", "email", "tax_no", "uid", "iban", "bic", "payment_days",
                  "exemption_text", "exemption_confirmed_on"]
        widgets = {"address": forms.Textarea(attrs={"rows": 3}), "exemption_text": forms.Textarea(attrs={"rows": 4}),
                   "exemption_confirmed_on": DATE_WIDGET}

    def clean_iban(self):
        iban = self.cleaned_data["iban"].replace(" ", "").upper()
        if iban and not iban_valid(iban):
            raise forms.ValidationError("Die IBAN ist ungültig.")
        return iban

    def clean_payment_days(self):
        days = self.cleaned_data["payment_days"]
        if not 0 <= days <= 90:
            raise forms.ValidationError("Bitte 0 bis 90 Tage angeben.")
        return days

    def clean_exemption_confirmed_on(self):
        day = self.cleaned_data["exemption_confirmed_on"]
        if day and day > date.today():
            raise forms.ValidationError("Das Datum liegt in der Zukunft.")
        return day

    def clean(self):
        data = super().clean()
        if data.get("exemption_confirmed_on") and "PLATZHALTER" in (data.get("exemption_text") or "").upper():
            self.add_error("exemption_text", "Der Platzhaltertext muss vor der Bestätigung durch den Wortlaut des Steuerberaters ersetzt werden.")
        return data


class LeistungForm(forms.ModelForm):
    price = forms.CharField(label="Preis (EUR)")

    class Meta:
        model = Leistung
        fields = ["name", "active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.initial["price"] = f"{self.instance.price_cents / 100:.2f}".replace(".", ",")

    def clean_price(self):
        return parse_eur(self.cleaned_data["price"])

    def save(self, commit=True):
        self.instance.price_cents = self.cleaned_data["price"]
        return super().save(commit)


class PositionForm(forms.Form):
    leistung = forms.ModelChoiceField(label="Leistung aus Katalog", queryset=Leistung.objects.none(), required=False)
    description = forms.CharField(label="oder freie Bezeichnung", max_length=200, required=False)
    date = forms.DateField(label="Datum der Leistung", widget=DATE_WIDGET)
    quantity = forms.DecimalField(label="Menge", min_value=Decimal("0.01"), max_value=Decimal("999"), decimal_places=2, initial=1)
    price = forms.CharField(label="Einzelpreis (EUR, leer = Katalogpreis)", required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["leistung"].queryset = Leistung.objects.filter(active=True)

    def clean_date(self):
        if self.cleaned_data["date"] > date.today():
            raise forms.ValidationError("Das Datum liegt in der Zukunft.")
        return self.cleaned_data["date"]

    def clean(self):
        data = super().clean()
        leistung, text, price = data.get("leistung"), data.get("description", "").strip(), data.get("price", "").strip()
        if not leistung and not text:
            raise forms.ValidationError("Bitte eine Leistung wählen oder eine Bezeichnung eingeben.")
        data["final_description"] = text or leistung.name
        if price:
            data["final_price_cents"] = parse_eur(price)
        elif leistung:
            data["final_price_cents"] = leistung.price_cents
        else:
            raise forms.ValidationError("Bitte einen Preis eingeben.")
        return data


class ZahlungForm(forms.Form):
    date = forms.DateField(label="Zahlungsdatum", widget=DATE_WIDGET)
    amount = forms.CharField(label="Betrag (EUR)")
    method = forms.ChoiceField(label="Art", choices=Zahlung.Method.choices)

    def clean_amount(self):
        return parse_eur(self.cleaned_data["amount"])
