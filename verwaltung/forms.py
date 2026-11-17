import re

from django import forms

from accounts.models import Therapist
from core import tenancy
from core.models import Tenant

from .services import slug_from_username


class NewTherapistForm(forms.Form):
    name = forms.CharField(label="Name (wie er in der Praxis erscheint)", max_length=120)
    username = forms.CharField(label="Benutzername", min_length=3, max_length=30,
                               help_text="Kleinbuchstaben, Ziffern, Punkt, Unterstrich oder Bindestrich, z. B. m.huber")
    email = forms.EmailField(label="E-Mail (optional, für Erinnerungen bei Notfallzugriffen)", required=False)

    def clean_username(self):
        value = self.cleaned_data["username"].strip().lower()
        if not re.fullmatch(r"[a-z0-9._-]{3,30}", value):
            raise forms.ValidationError("Erlaubt sind Kleinbuchstaben, Ziffern, Punkt, Unterstrich und Bindestrich.")
        if Therapist.objects.filter(username__iexact=value).exists():
            raise forms.ValidationError("Dieser Benutzername ist schon vergeben.")
        slug = slug_from_username(value)
        if not tenancy.SLUG_RE.match(slug):
            raise forms.ValidationError("Daraus lässt sich kein gültiges Datenbankkürzel bilden. Bitte einen längeren Namen wählen.")
        if Tenant.objects.filter(slug=slug).exists():
            raise forms.ValidationError("Zu diesem Benutzernamen gibt es schon einen Datenbereich. Bitte einen anderen wählen.")
        return value

    def clean_name(self):
        return self.cleaned_data["name"].strip()
