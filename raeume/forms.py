from django import forms

from rechnungen.forms import DATE_WIDGET

from .models import Raum


class BookingForm(forms.Form):
    room = forms.ModelChoiceField(label="Raum", queryset=Raum.objects.none())
    date = forms.DateField(label="Datum", widget=DATE_WIDGET)
    time = forms.TimeField(label="Beginn", widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"))
    duration_min = forms.IntegerField(label="Dauer (Minuten)", min_value=15, max_value=720, initial=60)
    weeks = forms.IntegerField(label="Wöchentlich wiederholen (Anzahl insgesamt)", min_value=1, max_value=26, initial=1,
                               help_text="1 = nur dieser Termin")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["room"].queryset = Raum.objects.filter(active=True)


class RoomForm(forms.ModelForm):
    class Meta:
        model = Raum
        fields = ["name", "note"]
