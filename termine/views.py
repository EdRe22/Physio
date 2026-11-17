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
        with transaction.atomic(using=tenancy.require().alias):
            for start in form.starts:
                Termin.objects.create(patient=data["patient"], start=start, duration_min=data["duration_min"])
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
        termin.status = new
        termin.save(update_fields=["status"])
    return redirect(f"/termine/?start={timezone.localtime(termin.start).date().isoformat()}")
