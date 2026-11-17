from datetime import datetime, time, timedelta

import pytest
from django.db import connections
from django.utils import timezone

from audit.models import AuditEntry, verify_chain
from core import tenancy
from patients.models import Patient
from raeume import services
from raeume.models import Raum, Raumbelegung
from rechnungen.models import ImmutableError
from termine.models import Termin
from tests.conftest import verified_client


def at(days=1, hour=10, minute=0):
    return timezone.make_aware(datetime.combine(timezone.localdate() + timedelta(days=days), time(hour, minute)))


@pytest.fixture
def room(db):
    return Raum.objects.create(name="Raum 1")


@pytest.fixture
def room2(db):
    return Raum.objects.create(name="Raum 2")


# --- Überschneidungen ---

def test_adjacent_bookings_are_allowed_overlaps_are_not(alpha, beta, room):
    services.book(alpha, room, at(1, 10), at(1, 11))
    services.book(alpha, room, at(1, 11), at(1, 12))  # Ende = Beginn ist kein Konflikt
    services.book(beta, room, at(1, 9), at(1, 10))
    for start, end in ((at(1, 10, 30), at(1, 11, 30)), (at(1, 9, 30), at(1, 10, 30)), (at(1, 8), at(1, 13)), (at(1, 10, 15), at(1, 10, 45))):
        with pytest.raises(services.RoomError, match="belegt"):
            services.book(beta, room, start, end)
    assert Raumbelegung.objects.count() == 3


def test_conflict_across_tenants_and_other_room_is_free(alpha, beta, room, room2):
    services.book(alpha, room, at(1, 10), at(1, 11))
    with pytest.raises(services.RoomError):
        services.book(beta, room, at(1, 10), at(1, 11))
    services.book(beta, room2, at(1, 10), at(1, 11))
    assert services.free_rooms(at(1, 10), at(1, 11)) == []
    assert services.free_rooms(at(1, 11), at(1, 12)) == [room, room2]


def test_cancelled_booking_frees_the_slot(alpha, beta, room, user_alpha):
    b = services.book(alpha, room, at(1, 10), at(1, 11))
    services.cancel(user_alpha, b)
    services.book(beta, room, at(1, 10), at(1, 11))


# --- Prüfungen ---

def test_validation_rules(alpha, room):
    with pytest.raises(services.RoomError, match="Ende"):
        services.book(alpha, room, at(1, 11), at(1, 10))
    with pytest.raises(services.RoomError, match="Stunden"):
        services.book(alpha, room, at(1, 6), at(1, 19))
    with pytest.raises(services.RoomError, match="vergangene"):
        services.book(alpha, room, at(-1, 10), at(-1, 11))
    services.book(alpha, room, at(0, 23, 0), at(0, 23, 30), now=at(0, 22))  # heute spät ist erlaubt
    room.active = False
    room.save()
    with pytest.raises(services.RoomError, match="nicht mehr buchbar"):
        services.book(alpha, room, at(2, 10), at(2, 11))


# --- Serien ---

def test_series_is_all_or_nothing(alpha, beta, room):
    services.book(beta, room, at(15, 10), at(15, 11))  # blockiert die 3. Woche (Tag 1 + 14)
    with pytest.raises(services.RoomError, match="belegt"):
        services.book_series(alpha, room, at(1, 10), at(1, 11), 4)
    assert Raumbelegung.objects.filter(tenant=alpha).count() == 0  # nichts halb angelegt
    made = services.book_series(alpha, room, at(1, 10), at(1, 11), 2)
    assert len(made) == 2 and len({b.series for b in made}) == 1 and made[0].series
    assert (made[1].start - made[0].start) == timedelta(weeks=1)


def test_series_limits(alpha, room):
    for bad in (0, 27):
        with pytest.raises(services.RoomError, match="Serie"):
            services.book_series(alpha, room, at(1, 10), at(1, 11), bad)


# --- Stornieren und Rechte ---

def test_cancel_permissions_and_audit(alpha, beta, room, user_alpha, user_beta):
    mine = services.book(alpha, room, at(1, 10), at(1, 11))
    with pytest.raises(services.RoomError, match="eigenen"):
        services.cancel(user_beta, mine)
    user_beta.role = "owner"  # Eigentümer der Praxis darf jede Belegung stornieren
    services.cancel(user_beta, Raumbelegung.objects.get(pk=mine.pk))
    with pytest.raises(services.RoomError, match="Bereits"):
        services.cancel(user_alpha, Raumbelegung.objects.get(pk=mine.pk))
    with tenancy.tenant_context(alpha):
        actions = [e.action for e in AuditEntry.objects.all()]
        assert actions == ["raum.book", "raum.cancel"] and verify_chain()[0]  # auch die Stornierung durch den Eigentümer
    with tenancy.tenant_context(beta):
        assert AuditEntry.objects.count() == 0  # nichts in fremden Ketten


def test_bookings_are_immutable_and_never_deleted(alpha, beta, room):
    b = Raumbelegung.objects.get(pk=services.book(alpha, room, at(1, 10), at(1, 11)).pk)
    b.tenant = beta
    with pytest.raises(ImmutableError):
        b.save()
    with pytest.raises(ImmutableError):
        Raumbelegung.objects.get(pk=b.pk).delete()


def test_room_data_contains_no_patient_fields():
    from django.apps import apps

    names = {f.name for m in apps.get_app_config("raeume").get_models() for f in m._meta.get_fields()}
    assert not {"patient", "first_name", "last_name", "diagnosis", "text", "note_patient"} & names


# --- Terminkalender ---

def make_patient(tenant):
    with tenancy.tenant_context(tenant):
        return Patient.objects.create(first_name="Łukasz", last_name="Çelik").pk


def post_termin(client, pid, room=None, day=1, hour=10, minutes=45, repeat=1):
    data = {"patient": pid, "date": at(day).date().isoformat(), "time": f"{hour:02d}:00", "duration_min": minutes, "repeat": repeat}
    if room:
        data["room"] = room.pk
    return client.post("/termine/neu/", data)


def test_appointment_with_room_books_it_without_patient_data(client_alpha, alpha, room):
    pid = make_patient(alpha)
    assert post_termin(client_alpha, pid, room).status_code == 302
    with tenancy.tenant_context(alpha):
        t = Termin.objects.get()
    b = Raumbelegung.objects.get(pk=t.room_booking_id)
    assert (b.room, b.tenant, b.start, b.end, b.kind) == (room, alpha, t.start, t.end, "termin")
    with connections["default"].cursor() as cur:  # Gegenprobe: nirgends in der Praxis-DB steht der Patient
        cur.execute("select * from raeume_raumbelegung")
        assert "Çelik" not in str(cur.fetchall())


def test_other_therapist_cannot_double_book_room_via_appointment(client_alpha, client_beta, alpha, beta, room):
    post_termin(client_alpha, make_patient(alpha), room)
    r = post_termin(client_beta, make_patient(beta), room)
    assert r.status_code == 200 and "belegt" in r.content.decode()
    with tenancy.tenant_context(beta):
        assert Termin.objects.count() == 0


def test_appointment_series_books_every_week(client_alpha, alpha, room):
    assert post_termin(client_alpha, make_patient(alpha), room, repeat=3).status_code == 302
    assert Raumbelegung.objects.filter(tenant=alpha, kind="termin").count() == 3
    with tenancy.tenant_context(alpha):
        assert sorted(Termin.objects.values_list("room_booking_id", flat=True)) == sorted(
            Raumbelegung.objects.values_list("pk", flat=True))


def test_series_conflict_in_later_week_creates_nothing(client_alpha, alpha, beta, room):
    services.book(beta, room, at(15, 10), at(15, 11))
    r = post_termin(client_alpha, make_patient(alpha), room, repeat=3)
    assert r.status_code == 200
    with tenancy.tenant_context(alpha):
        assert Termin.objects.count() == 0
    assert Raumbelegung.objects.filter(tenant=alpha).count() == 0


def test_race_during_booking_rolls_back_appointments_and_releases_rooms(client_alpha, alpha, room, monkeypatch):
    from termine import views

    real = services.book
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise services.RoomError("Raum belegt am: Rennen verloren")
        return real(*args, **kwargs)

    monkeypatch.setattr(views.room_services, "book", flaky)
    r = post_termin(client_alpha, make_patient(alpha), room, repeat=3)
    assert r.status_code == 200 and "Rennen verloren" in r.content.decode()
    with tenancy.tenant_context(alpha):
        assert Termin.objects.count() == 0  # Termine zurückgerollt
    assert Raumbelegung.objects.filter(cancelled_at__isnull=True).count() == 0  # bereits gebuchter Raum wieder frei


def test_appointment_without_room_does_not_touch_rooms(client_alpha, alpha, room):
    assert post_termin(client_alpha, make_patient(alpha)).status_code == 302
    assert Raumbelegung.objects.count() == 0


def edit(client, tid, day=2, hour=10, minutes=45):
    return client.post(f"/termine/{tid}/aendern/", {"date": at(day).date().isoformat(), "time": f"{hour:02d}:00", "duration_min": minutes})


def test_moving_appointment_moves_the_room_and_conflict_blocks_the_move(client_alpha, alpha, beta, room):
    post_termin(client_alpha, make_patient(alpha), room)
    with tenancy.tenant_context(alpha):
        t = Termin.objects.get()
    services.book(beta, room, at(2, 10), at(2, 11))
    r = edit(client_alpha, t.pk, day=2)  # belegt
    assert r.status_code == 200 and "belegt" in r.content.decode()
    with tenancy.tenant_context(alpha):
        assert Termin.objects.get().start == at(1, 10)  # unverändert
    assert Raumbelegung.objects.get(pk=t.room_booking_id).start == at(1, 10)
    assert edit(client_alpha, t.pk, day=3).status_code == 302
    b = Raumbelegung.objects.get(pk=t.room_booking_id)
    assert b.start == at(3, 10) and b.end == at(3, 10) + timedelta(minutes=45) and b.is_active


def test_cancelling_frees_room_and_reactivation_needs_free_room(client_alpha, client_beta, alpha, beta, room):
    post_termin(client_alpha, make_patient(alpha), room)
    with tenancy.tenant_context(alpha):
        t = Termin.objects.get()
    client_alpha.post(f"/termine/{t.pk}/status/", {"status": "abgesagt"})
    assert not Raumbelegung.objects.get(pk=t.room_booking_id).is_active
    post_termin(client_beta, make_patient(beta), room)  # Kollege nimmt den frei gewordenen Raum
    client_alpha.post(f"/termine/{t.pk}/status/", {"status": "geplant"})
    with tenancy.tenant_context(alpha):
        assert Termin.objects.get().status == "abgesagt"  # Wiederaufnahme abgelehnt
    assert not Raumbelegung.objects.get(pk=t.room_booking_id).is_active
    # Kollege sagt ab, dann klappt die Wiederaufnahme
    with tenancy.tenant_context(beta):
        tb = Termin.objects.get()
    client_beta.post(f"/termine/{tb.pk}/status/", {"status": "abgesagt"})
    client_alpha.post(f"/termine/{t.pk}/status/", {"status": "geplant"})
    with tenancy.tenant_context(alpha):
        assert Termin.objects.get().status == "geplant"
    assert Raumbelegung.objects.get(pk=t.room_booking_id).is_active


# --- Oberfläche ---

def test_room_plan_shows_colleague_names_but_nothing_else(client_alpha, alpha, beta, room):
    services.book(beta, room, at(1, 10), at(1, 11))
    services.book(alpha, room, at(1, 12), at(1, 13))
    page = client_alpha.get(f"/raeume/?raum={room.pk}&start={at(1).date().isoformat()}").content.decode()
    assert "belegt von Therapeut Beta" in page and "meine Belegung" in page


def test_booking_and_cancelling_over_http(client_alpha, client_beta, alpha, beta, room):
    data = {"room": room.pk, "date": at(1).date().isoformat(), "time": "10:00", "duration_min": 60, "weeks": 1}
    assert client_alpha.post("/raeume/buchen/", data).status_code == 302
    assert client_beta.post("/raeume/buchen/", data).status_code == 302  # Konflikt: Meldung, keine zweite Belegung
    assert Raumbelegung.objects.count() == 1
    b = Raumbelegung.objects.get()
    assert client_beta.post(f"/raeume/{b.pk}/stornieren/").status_code == 302
    assert Raumbelegung.objects.get().is_active  # fremde Belegung bleibt
    assert client_alpha.post(f"/raeume/{b.pk}/stornieren/").status_code == 302
    assert not Raumbelegung.objects.get().is_active
    assert client_alpha.get(f"/raeume/{b.pk}/stornieren/").status_code == 405


def test_free_room_search(client_alpha, alpha, room, room2):
    services.book(alpha, room, at(1, 10), at(1, 11))
    page = client_alpha.get("/raeume/frei/", {"date": at(1).date().isoformat(), "time": "10:30", "duration": 30}).content.decode()
    assert "Raum 2" in page and "Raum 1" not in page
    assert client_alpha.get("/raeume/frei/", {"date": "kaputt"}).status_code == 302


def test_only_owner_manages_rooms(client_alpha, client_beta, user_alpha, room):
    assert client_beta.get("/raeume/verwalten/").status_code == 403
    assert client_beta.post(f"/raeume/{room.pk}/umschalten/").status_code == 403
    user_alpha.role = "owner"
    user_alpha.save()
    owner = verified_client(user_alpha)
    assert owner.post("/raeume/verwalten/", {"name": "Raum 3", "note": "Gruppenraum"}).status_code == 302
    assert Raum.objects.filter(name="Raum 3").exists()
    owner.post(f"/raeume/{room.pk}/umschalten/")
    assert not Raum.objects.get(pk=room.pk).active
    assert owner.post("/raeume/verwalten/", {"name": "Raum 3"}).status_code == 200  # Name nur einmal
