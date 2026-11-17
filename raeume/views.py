from datetime import date, datetime, timedelta

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from audit.models import log as audit_log
from patients.views import tenant_required
from praxis.views import owner_required
from rechnungen.services import BillingError

from . import services
from .forms import BookingForm, RoomForm
from .models import Raum, Raumbelegung


def _monday(day):
    return day - timedelta(days=day.weekday())


@tenant_required
def index(request):
    rooms = list(Raum.objects.filter(active=True))
    room = next((r for r in rooms if str(r.pk) == request.GET.get("raum")), rooms[0] if rooms else None)
    try:
        anchor = date.fromisoformat(request.GET.get("start", ""))
    except ValueError:
        anchor = timezone.localdate()
    monday = _monday(anchor)
    days = [(monday + timedelta(days=i), []) for i in range(7)]
    if room:
        for b in services.week_bookings(room, monday):
            day = timezone.localtime(b.start).date()
            for d, items in days:
                if d == day:
                    items.append({"b": b, "mine": b.tenant_id == request.user.tenant_id,
                                  "can_cancel": services.can_cancel(request.user, b)})
    return render(request, "raeume/index.html", {
        "rooms": rooms, "room": room, "days": days, "monday": monday, "sunday": monday + timedelta(days=6),
        "prev": monday - timedelta(days=7), "next": monday + timedelta(days=7),
        "form": BookingForm(initial={"room": room, "date": timezone.localdate(), "weeks": 1, "duration_min": 60}),
    })


@tenant_required
@require_POST
def book(request):
    form = BookingForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Bitte alle Angaben prüfen: " + " ".join(f"{k}: {v[0]}" for k, v in form.errors.items()))
        return redirect("raeume_index")
    d = form.cleaned_data
    start = timezone.make_aware(datetime.combine(d["date"], d["time"]))
    try:
        made = services.book_series(request.user.tenant, d["room"], start, start + timedelta(minutes=d["duration_min"]), d["weeks"])
    except BillingError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{len(made)} Belegung(en) angelegt.")
    return redirect(f"/raeume/?raum={d['room'].pk}&start={d['date'].isoformat()}")


@tenant_required
@require_POST
def cancel(request, pk):
    booking = get_object_or_404(Raumbelegung, pk=pk)
    try:
        services.cancel(request.user, booking)
        messages.success(request, "Belegung storniert.")
    except BillingError as exc:
        messages.error(request, str(exc))
    return redirect(f"/raeume/?raum={booking.room_id}&start={timezone.localtime(booking.start).date().isoformat()}")


@tenant_required
def free(request):
    """Welche Räume sind zu einer Zeit frei?"""
    try:
        start = timezone.make_aware(datetime.fromisoformat(f"{request.GET['date']}T{request.GET['time']}"))
        minutes = min(max(int(request.GET.get("duration", "60")), 15), 720)
    except (KeyError, ValueError):
        return redirect("raeume_index")
    audit_log("raum.free", object_type="raum", object_id="")
    return render(request, "raeume/free.html", {
        "rooms": services.free_rooms(start, start + timedelta(minutes=minutes)), "start": start, "minutes": minutes,
    })


@owner_required
def manage(request):
    form = RoomForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Raum angelegt.")
        return redirect("raeume_manage")
    return render(request, "raeume/manage.html", {"form": form, "rooms": Raum.objects.all()})


@owner_required
@require_POST
def toggle(request, pk):
    room = get_object_or_404(Raum, pk=pk)
    room.active = not room.active
    room.save(update_fields=["active"])
    messages.success(request, f"{room.name} ist jetzt {'aktiv' if room.active else 'deaktiviert (keine neuen Buchungen)'}.")
    return redirect("raeume_manage")
