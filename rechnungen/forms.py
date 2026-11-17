from datetime import date
from decimal import Decimal, InvalidOperation

from django import forms

from core.logo import LogoFormMixin

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


class MailServerForm(forms.ModelForm):
    """Eigener SMTP-Zugang des Therapeuten. Leeres Passwortfeld = bisheriges Passwort behalten."""
    smtp_password_new = forms.CharField(
        label="Passwort", required=False, widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text="Wird verschlüsselt gespeichert und nie wieder angezeigt. Leer lassen = bisheriges Passwort behalten.")
    remove = forms.BooleanField(label="Eigenen Mailserver entfernen (wieder den Zugang der Praxis verwenden)", required=False)

    class Meta:
        model = Rechnungsprofil
        fields = ["smtp_host", "smtp_port", "smtp_user", "smtp_from"]
        widgets = {"smtp_port": forms.Select(choices=[(587, "587 (STARTTLS, empfohlen)"), (465, "465 (SSL/TLS)")])}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            f.required = False  # leer = eigenen Mailserver entfernen (Pflichtangaben prüft clean)

    def clean(self):
        data = super().clean()
        from .mailconfig import ALLOWED_PORTS, MailConfigError, check_host

        if data.get("remove"):
            return data
        host = (data.get("smtp_host") or "").strip().lower()
        data["smtp_host"] = host
        if not host:
            return data
        if data.get("smtp_port") not in ALLOWED_PORTS:
            self.add_error("smtp_port", "Erlaubt sind nur Port 587 (STARTTLS) und 465 (SSL/TLS).")
        try:
            check_host(host)
        except MailConfigError as exc:
            self.add_error("smtp_host", str(exc))
        if not data.get("smtp_user"):
            self.add_error("smtp_user", "Bitte den Benutzernamen des Mailkontos angeben.")
        sender = (data.get("smtp_from") or "").strip()
        if not sender or "@" not in sender or any(c in sender for c in " <>,;\r\n"):
            self.add_error("smtp_from", "Bitte eine gültige Absenderadresse angeben, zum Beispiel praxis@ihre-domain.at.")
        if not data.get("smtp_password_new") and not (self.instance and self.instance.smtp_password):
            self.add_error("smtp_password_new", "Bitte das Passwort des Mailkontos angeben.")
        return data

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.cleaned_data.get("remove") or not self.cleaned_data.get("smtp_host"):
            obj.smtp_host = obj.smtp_user = obj.smtp_password = obj.smtp_from = ""
            obj.smtp_port = 587
        elif self.cleaned_data.get("smtp_password_new"):
            obj.smtp_password = self.cleaned_data["smtp_password_new"]
        if commit:
            obj.save()
        return obj


class MahnForm(forms.ModelForm):
    """Mahnwesen je Therapeut: Zahlungsfristen, Mahnspesen und die drei Schreiben. Leere Texte = Vorlage der App."""
    reminder_fee = forms.CharField(
        label="Mahnspesen pro Mahnung ab Stufe 2 (EUR)", required=False, initial="0,00",
        help_text="Nur eintragen, wenn Mahnspesen mit dem Patienten vertraglich vereinbart sind. Sonst 0 lassen.")

    class Meta:
        model = Rechnungsprofil
        fields = ["reminder_days", "reminder_days_2", "reminder_days_3", "reminder_text_1", "reminder_text_2", "reminder_text_3",
                  "mail_text_invoice", "mail_text_reminder", "auto_reminders", "auto_max_level"]
        widgets = {**{f"reminder_text_{i}": forms.Textarea(attrs={"rows": 6}) for i in (1, 2, 3)},
                   "mail_text_invoice": forms.Textarea(attrs={"rows": 5}), "mail_text_reminder": forms.Textarea(attrs={"rows": 5})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .services import DEFAULT_REMINDER_TEXTS

        self.fields["reminder_days"].label = "Zahlungsfrist Zahlungserinnerung (Tage)"
        self.fields["reminder_days"].required = False
        for lvl in (2, 3):
            self.fields[f"reminder_days_{lvl}"].help_text = "Leer lassen = gleiche Frist wie bei der Zahlungserinnerung."
        for lvl in (1, 2, 3):
            f = self.fields[f"reminder_text_{lvl}"]
            f.required = False
            f.help_text = "Leer lassen = Vorlage der App. Platzhalter: {frist}, {betrag}, {nummer}."
            f.widget.attrs["placeholder"] = DEFAULT_REMINDER_TEXTS[lvl]
        from .mailing import DEFAULT_MAIL_TEXTS

        self.fields["auto_max_level"].required = False

        for kind in ("invoice", "reminder"):
            f = self.fields[f"mail_text_{kind}"]
            f.required = False
            f.help_text = "Leer lassen = Vorlage der App. Platzhalter: {nummer}, {praxis}" + (", {frist}." if kind == "reminder" else ".")
            f.widget.attrs["placeholder"] = DEFAULT_MAIL_TEXTS[kind]
        if self.instance and self.instance.pk:
            self.fields["reminder_fee"].initial = f"{self.instance.reminder_fee_cents / 100:.2f}".replace(".", ",")

    def clean_reminder_fee(self):
        return parse_eur(self.cleaned_data.get("reminder_fee") or "0")

    def _days(self, name, default):
        value = self.cleaned_data.get(name)
        if value is None:
            return default
        if not 1 <= value <= 60:
            raise forms.ValidationError("Bitte 1 bis 60 Tage angeben.")
        return value

    def clean_reminder_days(self):
        return self._days("reminder_days", 7)

    def clean_reminder_days_2(self):
        return self._days("reminder_days_2", None)

    def clean_reminder_days_3(self):
        return self._days("reminder_days_3", None)

    def _clean_text(self, name):
        text = (self.cleaned_data.get(name) or "").strip()
        if len(text) > 1500:
            raise forms.ValidationError("Bitte höchstens 1500 Zeichen.")
        return text

    def clean_reminder_text_1(self):
        return self._clean_text("reminder_text_1")

    def clean_reminder_text_2(self):
        return self._clean_text("reminder_text_2")

    def clean_auto_max_level(self):
        return self.cleaned_data.get("auto_max_level") or 2

    def clean_mail_text_invoice(self):
        return self._clean_text("mail_text_invoice")

    def clean_mail_text_reminder(self):
        return self._clean_text("mail_text_reminder")

    def clean_reminder_text_3(self):
        return self._clean_text("reminder_text_3")

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.reminder_fee_cents = self.cleaned_data["reminder_fee"]
        if commit:
            obj.save()
        return obj


class ProfilForm(LogoFormMixin):
    class Meta:
        model = Rechnungsprofil
        fields = ["name", "title", "address", "phone", "email", "tax_no", "uid", "iban", "bic", "payment_days",
                  "exemption_text", "exemption_confirmed_on"]  # Logo: siehe LogoFormMixin
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
