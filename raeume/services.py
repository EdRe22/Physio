import threading
import uuid
from datetime import datetime, time, timedelta

from django.db import transaction
from django.utils import timezone

from audit.models import log as audit_log
from core import tenancy
from rechnungen.services import BillingError

from .models import Raum, Raumbelegung

MAX_HOURS = 12
MAX_SERIES = 26
# Die App läuft mit genau einem Serverprozess (siehe DEPLOYMENT.md). Die Sperre macht "prüfen und buchen" atomar.
_LOCK = threading.Lock()


class RoomError(BillingError):
    pass


def _audit(action, booking):
    """Protokolliert in der Kette des Mandanten, dem die Belegung gehört (auch wenn der Eigentümer sie storniert).
    Der handelnde Benutzer bleibt als Akteur erhalten, soweit ein Request-Kontext existiert."""
    with tenancy.tenant_context(booking.tenant, tenancy.current_actor()):
        audit_log(action, booking)


def overlapping(room, start, end, exclude_pk=None):
    qs = Raumbelegung.objects.filter(room=room, cancelled_at__isnull=True, start__lt=end, end__gt=start)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.select_related("tenant")


def _validate(room, start, end, now):
    if not room.active:
        raise RoomError("Dieser Raum ist nicht mehr buchbar.")
    if end <= start:
        raise RoomError("Das Ende muss nach dem Beginn liegen.")
    if end - start > timedelta(hours=MAX_HOURS):
        raise RoomError(f"Eine Belegung darf höchstens {MAX_HOURS} Stunden dauern.")
    if start < timezone.make_aware(datetime.combine(timezone.localtime(now).date(), time.min)):
        raise RoomError("Räume können nicht für vergangene Tage gebucht werden.")


def _busy_message(clashes):
    return "Raum belegt am: " + ", ".join(f"{timezone.localtime(c.start):%d.%m.%Y %H:%M}-{timezone.localtime(c.end):%H:%M}" for c in clashes)


def book(tenant, room, start, end, kind=Raumbelegung.Kind.RESERVIERUNG, series="", now=None):
    now = now or timezone.now()
    _validate(room, start, end, now)
    with _LOCK, transaction.atomic():
        clashes = list(overlapping(room, start, end))
        if clashes:
            raise RoomError(_busy_message(clashes))
        booking = Raumbelegung.objects.create(room=room, tenant=tenant, start=start, end=end, kind=kind, series=series)
        _audit("raum.book", booking)
    return booking


def book_series(tenant, room, start, end, count, now=None):
    """Wöchentliche Reservierung, alles oder nichts: bei einem Konflikt wird keine einzige Belegung angelegt."""
    now = now or timezone.now()
    if not 1 <= count <= MAX_SERIES:
        raise RoomError(f"Die Serie darf 1 bis {MAX_SERIES} Termine umfassen.")
    slots = [(start + timedelta(weeks=i), end + timedelta(weeks=i)) for i in range(count)]
    for s, e in slots:
        _validate(room, s, e, now)
    series = uuid.uuid4().hex if count > 1 else ""
    with _LOCK, transaction.atomic():
        clashes = [c for s, e in slots for c in overlapping(room, s, e)]
        if clashes:
            raise RoomError(_busy_message(clashes))
        made = [Raumbelegung.objects.create(room=room, tenant=tenant, start=s, end=e, series=series) for s, e in slots]
        for b in made:
            _audit("raum.book", b)
    return made


def can_cancel(user, booking):
    return user.is_owner or booking.tenant_id == user.tenant_id


def cancel(user, booking):
    if not can_cancel(user, booking):
        raise RoomError("Sie dürfen nur Ihre eigenen Belegungen stornieren.")
    if not booking.is_active:
        raise RoomError("Bereits storniert.")
    booking.cancel()
    _audit("raum.cancel", booking)
    return booking


def release(booking_id):
    """Gibt die Belegung zu einem Termin frei (Absage). Fehlt sie, ist das kein Fehler."""
    booking = Raumbelegung.objects.filter(pk=booking_id).first()
    if booking:
        booking.cancel()
        _audit("raum.release", booking)


def move(booking_id, tenant, start, end, now=None):
    """Verschiebt die Belegung eines Termins. Konflikt: Fehler, nichts wird geändert."""
    now = now or timezone.now()
    booking = Raumbelegung.objects.select_related("room").filter(pk=booking_id, tenant=tenant).first()
    if booking is None:
        raise RoomError("Die Raumbelegung wurde nicht gefunden.")
    _validate(booking.room, start, end, now)
    with _LOCK, transaction.atomic():
        clashes = list(overlapping(booking.room, start, end, exclude_pk=booking.pk))
        if clashes:
            raise RoomError(_busy_message(clashes))
        booking.start, booking.end = start, end
        booking.cancelled_at = None
        booking.save()
        _audit("raum.move", booking)
    return booking


def free_rooms(start, end):
    busy = set(Raumbelegung.objects.filter(cancelled_at__isnull=True, start__lt=end, end__gt=start).values_list("room_id", flat=True))
    return [r for r in Raum.objects.filter(active=True) if r.pk not in busy]


def week_bookings(room, monday):
    lo = timezone.make_aware(datetime.combine(monday, time.min))
    return (Raumbelegung.objects.filter(room=room, cancelled_at__isnull=True, start__lt=lo + timedelta(days=7), end__gt=lo)
            .select_related("tenant").order_by("start"))
