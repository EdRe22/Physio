from datetime import date, datetime, time, timedelta
from itertools import groupby

from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from audit.models import log as audit_log
from core import tenancy
from patients.views import tenant_required
from raeume import services as room_services
from raeume.models import Raumbelegung

from .forms import TerminForm
from .models import Termin


def _monday(day):
    return day - timedelta(days=day.weekday())


@tenant_required
def week(request):
    try:
        anchor = date.fromisoformat(request.GET.get("start", ""))
    except ValueError:
        anchor = timezone.localdate()
    monday = _monday(anchor)
    lo = timezone.make_aware(datetime.combine(monday, time.min))
    hi = lo + timedelta(days=7)
    termine = Termin.objects.filter(start__gte=lo, start__lt=hi).select_related("patient")
    days = []
    for day, items in groupby(termine, key=lambda t: timezone.localtime(t.start).date()):
        days.append((day, list(items)))
    audit_log("termine.list")
    return render(request, "termine/week.html", {
        "days": days, "monday": monday, "sunday": monday + timedelta(days=6),
        "prev": monday - timedelta(days=7), "next": monday + timedelta(days=7),
        "statuses": Termin.Status.choices,
    })


@tenant_required
def termin_create(request):
    initial = {"patient": request.GET.get("patient", ""), "date": timezone.localdate()}
    form = TerminForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        room, booked = data.get("room"), []
        try:
            with transaction.atomic(using=tenancy.require().alias):
                for start in form.starts:
                    termin = Termin.objects.create(patient=data["patient"], start=start, duration_min=data["duration_min"])
                    if room:
                        booking = room_services.book(request.user.tenant, room, start, termin.end, kind=Raumbelegung.Kind.TERMIN)
                        booked.append(booking)
                        termin.room_booking_id = booking.pk
                        termin.save(update_fields=["room_booking_id"])
        except room_services.RoomError as exc:
            for booking in booked:  # Ausgleich: Termine wurden zurückgerollt, also auch die schon gebuchten Räume freigeben
                room_services.release(booking.pk)
            form.add_error(None, str(exc))
            return render(request, "patients/form.html", {"form": form, "title": "Neuer Termin"})
        messages.success(request, f"{len(form.starts)} Termin(e) angelegt.")
        return redirect(f"/termine/?start={form.starts[0].date().isoformat()}")
    return render(request, "patients/form.html", {"form": form, "title": "Neuer Termin"})


@tenant_required
def termin_edit(request, pk):
    termin = get_object_or_404(Termin, pk=pk, status=Termin.Status.GEPLANT)
    local = timezone.localtime(termin.start)
    form = TerminForm(
        request.POST or None, editing=True,
        initial={"date": local.date(), "time": local.time().replace(second=0, microsecond=0), "duration_min": termin.duration_min},
    )
    form.exclude_pk = termin.pk
    if request.method == "POST" and form.is_valid():
        termin.start = form.starts[0]
        termin.duration_min = form.cleaned_data["duration_min"]
        if termin.room_booking_id:  # Raum mitverschieben; bei Konflikt bleibt alles unverändert
            try:
                room_services.move(termin.room_booking_id, request.user.tenant, termin.start, termin.end)
            except room_services.RoomError as exc:
                form.add_error(None, str(exc))
                return render(request, "patients/form.html", {"form": form, "title": "Termin ändern"})
        termin.reminder_sent_at = termin.reminder_failed_at = None  # neue Zeit, neue Erinnerung
        termin.save()
        messages.success(request, "Termin geändert.")
        return redirect(f"/termine/?start={termin.start.date().isoformat()}")
    return render(request, "patients/form.html", {"form": form, "title": "Termin ändern"})


@tenant_required
@require_POST
def termin_status(request, pk):
    termin = get_object_or_404(Termin, pk=pk)
    new = request.POST.get("status", "")
    if new not in Termin.Status.values:
        messages.error(request, "Unbekannter Status.")
    else:
        gone = (Termin.Status.ABGESAGT, Termin.Status.NICHT_ERSCHIENEN)
        if termin.room_booking_id and new in gone and termin.status not in gone:
            room_services.release(termin.room_booking_id)
        elif termin.room_booking_id and new == Termin.Status.GEPLANT and termin.status in gone:
            try:  # Wiederaufnahme: Raum wieder belegen, falls noch frei
                room_services.move(termin.room_booking_id, request.user.tenant, termin.start, termin.end)
            except room_services.RoomError as exc:
                messages.error(request, f"Termin nicht wieder aufgenommen: {exc}")
                return redirect(f"/termine/?start={timezone.localtime(termin.start).date().isoformat()}")
        termin.status = new
        termin.save(update_fields=["status"])
    return redirect(f"/termine/?start={timezone.localtime(termin.start).date().isoformat()}")
