from datetime import datetime, timedelta

from django import forms
from django.utils import timezone

from patients.models import Patient

from .models import Termin


def patient_choices():
    patients = sorted(Patient.objects.filter(archived_at__isnull=True), key=lambda p: p.full_name.casefold())
    return [("", "– Patient wählen –")] + [(str(p.pk), p.full_name) for p in patients]


class TerminForm(forms.Form):
    patient = forms.ChoiceField(label="Patient")
    date = forms.DateField(label="Datum", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    time = forms.TimeField(label="Uhrzeit", widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"))
    duration_min = forms.IntegerField(label="Dauer (Minuten)", min_value=5, max_value=240, initial=30)
    repeat = forms.IntegerField(
        label="Wöchentlich wiederholen (Anzahl Termine insgesamt)", min_value=1, max_value=26, initial=1, required=False,
        help_text="1 = nur dieser Termin",
    )

    def __init__(self, *args, editing=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.editing = editing
        self.fields["patient"].choices = patient_choices()
        if editing:  # beim Ändern gibt es weder Patientenwechsel noch Serie
            del self.fields["patient"]
            del self.fields["repeat"]
        self.exclude_pk = None
        self.starts = []

    def clean(self):
        data = super().clean()
        day, at, minutes = data.get("date"), data.get("time"), data.get("duration_min")
        if not (day and at and minutes):
            return data
        count = 1 if self.editing else (data.get("repeat") or 1)
        busy = []
        for i in range(count):
            start = timezone.make_aware(datetime.combine(day + timedelta(weeks=i), at))
            if Termin.conflicts(start, start + timedelta(minutes=minutes), self.exclude_pk):
                busy.append(f"{start:%d.%m.%Y %H:%M}")
            self.starts.append(start)
        if busy:
            raise forms.ValidationError("Überschneidung mit einem bestehenden Termin am: " + ", ".join(busy))
        return data

    def clean_patient(self):
        try:
            return Patient.objects.get(pk=int(self.cleaned_data["patient"]), archived_at__isnull=True)
        except (ValueError, Patient.DoesNotExist):
            raise forms.ValidationError("Bitte einen Patienten wählen.")
