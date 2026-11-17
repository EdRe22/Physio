from datetime import timedelta
from smtplib import SMTPException

from django.core import mail
from django.core.management import call_command
from django.utils import timezone

from audit.models import AuditEntry, verify_chain
from core import tenancy
from patients.models import Einwilligung, Patient
from termine.models import Termin
from termine.reminders import send_due_reminders

NOW = timezone.now()


def make_patient(email="fake@example.org", consent=True, **kw):
    p = Patient.objects.create(first_name="Maria", last_name="Muster", email=email, **kw)
    if consent:
        Einwilligung.objects.create(patient=p, kind="email_reminder", granted_at=NOW)
    return p


def make_termin(patient, in_hours=20, minutes=30, **kw):
    return Termin.objects.create(patient=patient, start=NOW + timedelta(hours=in_hours), duration_min=minutes, **kw)


def local_fields(dt):
    local = timezone.localtime(dt)
    return {"date": local.date().isoformat(), "time": local.strftime("%H:%M")}


# --- Kalender ---

def test_create_and_week_view(client_alpha, alpha):
    with tenancy.tenant_context(alpha):
        pid = make_patient().pk
    start = NOW + timedelta(days=2)
    data = {"patient": pid, "duration_min": 45, "repeat": 1, **local_fields(start)}
    assert client_alpha.post("/termine/neu/", data).status_code == 302
    body = client_alpha.get(f"/termine/?start={local_fields(start)['date']}").content.decode()
    assert "Muster Maria" in body
    with tenancy.tenant_context(alpha):
        assert Termin.objects.get().duration_min == 45


def test_weekly_series_and_conflicts(client_alpha, alpha):
    with tenancy.tenant_context(alpha):
        pid = make_patient().pk
    start = NOW + timedelta(days=3)
    series = {"patient": pid, "duration_min": 30, "repeat": 4, **local_fields(start)}
    assert client_alpha.post("/termine/neu/", series).status_code == 302
    with tenancy.tenant_context(alpha):
        assert Termin.objects.count() == 4
        gaps = {(b.start - a.start).days for a, b in zip(Termin.objects.all(), Termin.objects.all()[1:])}
        assert gaps == {7}
    # Überschneidung mit dem 3. Termin der Serie: gesamte neue Serie wird abgelehnt
    clash = {"patient": pid, "duration_min": 30, "repeat": 2, **local_fields(start + timedelta(days=14, minutes=10))}
    assert client_alpha.post("/termine/neu/", clash).status_code == 200
    with tenancy.tenant_context(alpha):
        assert Termin.objects.count() == 4


def test_cancelled_appointment_frees_the_slot(client_alpha, alpha):
    with tenancy.tenant_context(alpha):
        p = make_patient()
        t = make_termin(p, in_hours=48)
        pid, tid, start = p.pk, t.pk, t.start
    data = {"patient": pid, "duration_min": 30, "repeat": 1, **local_fields(start)}
    assert client_alpha.post("/termine/neu/", data).status_code == 200  # belegt
    assert client_alpha.post(f"/termine/{tid}/status/", {"status": "abgesagt"}).status_code == 302
    assert client_alpha.post("/termine/neu/", data).status_code == 302  # wieder frei


def test_edit_moves_appointment_and_resets_reminder(client_alpha, alpha):
    with tenancy.tenant_context(alpha):
        t = make_termin(make_patient(), in_hours=30, reminder_sent_at=NOW)
        tid = t.pk
    new_start = NOW + timedelta(days=5)
    assert client_alpha.post(f"/termine/{tid}/aendern/", {"duration_min": 60, **local_fields(new_start)}).status_code == 302
    with tenancy.tenant_context(alpha):
        t = Termin.objects.get(pk=tid)
        assert t.duration_min == 60 and t.reminder_sent_at is None


def test_invalid_input_and_archived_patient(client_alpha, alpha):
    with tenancy.tenant_context(alpha):
        p = make_patient()
        p.archived_at = NOW
        p.save()
        pid = p.pk
    data = {"patient": pid, "duration_min": 30, "repeat": 1, **local_fields(NOW + timedelta(days=1))}
    assert client_alpha.post("/termine/neu/", data).status_code == 200
    assert client_alpha.post("/termine/neu/", {**data, "patient": "abc"}).status_code == 200
    with tenancy.tenant_context(alpha):
        assert Termin.objects.count() == 0


def test_calendars_are_separate_per_therapist(client_alpha, client_beta, alpha, beta):
    with tenancy.tenant_context(alpha):
        p = make_patient()
        make_termin(p, in_hours=48)
        t = make_termin(p, in_hours=50)  # ID 2: bei Beta existiert nur ID 1
        tid, day = t.pk, local_fields(t.start)["date"]
    with tenancy.tenant_context(beta):
        make_termin(Patient.objects.create(first_name="Berta", last_name="Beta"), in_hours=48)
    body_a = client_alpha.get(f"/termine/?start={day}").content.decode()
    body_b = client_beta.get(f"/termine/?start={day}").content.decode()
    assert "Muster" in body_a and "Beta Berta" not in body_a
    assert "Beta Berta" in body_b and "Muster" not in body_b
    assert client_beta.get(f"/termine/{tid}/aendern/").status_code == 404
    assert client_beta.post(f"/termine/{tid}/status/", {"status": "abgesagt"}).status_code == 404
    with tenancy.tenant_context(alpha):
        assert Termin.objects.get(pk=tid).status == "geplant"


# --- Erinnerungen ---

def test_reminder_sent_with_consent_and_contains_no_health_data(alpha, mailoutbox):
    with tenancy.tenant_context(alpha):
        p = make_patient(email="maria@example.org")
        make_termin(p)
        counts = send_due_reminders("Therapeut Alpha", reply_to="alpha@example.org", now=NOW)
        assert counts["sent"] == 1
        assert Termin.objects.get().reminder_sent_at is not None
        assert AuditEntry.objects.filter(action="termin.reminder_sent").count() == 1
        assert verify_chain()[0]
    (msg,) = mailoutbox
    assert msg.to == ["maria@example.org"] and msg.reply_to == ["alpha@example.org"]
    content = msg.subject + msg.body
    for secret in ("Muster", "Maria", "Diagnose", "Therapie", "Behandlung", "Befund"):
        assert secret not in content
    assert "Therapeut Alpha" in msg.body and "Uhr" in msg.body


def test_reminder_only_once(alpha, mailoutbox):
    with tenancy.tenant_context(alpha):
        make_termin(make_patient())
        send_due_reminders("Alpha", now=NOW)
        assert send_due_reminders("Alpha", now=NOW)["sent"] == 0
    assert len(mailoutbox) == 1


def test_no_reminder_without_consent_or_email_or_after_revocation(alpha, mailoutbox):
    with tenancy.tenant_context(alpha):
        make_termin(make_patient(consent=False))
        make_termin(make_patient(email=""), in_hours=21)
        revoked = make_patient()
        make_termin(revoked, in_hours=22)
        revoked.consents.update(revoked_at=NOW)
        counts = send_due_reminders("Alpha", now=NOW)
    assert counts == {"sent": 0, "no_consent": 2, "no_email": 1, "failed": 0}
    assert mailoutbox == []


def test_no_reminder_for_cancelled_past_or_distant_appointments(alpha, mailoutbox):
    with tenancy.tenant_context(alpha):
        p = make_patient()
        make_termin(p, in_hours=20, status=Termin.Status.ABGESAGT)
        make_termin(p, in_hours=-5)
        make_termin(p, in_hours=100)
        assert send_due_reminders("Alpha", now=NOW)["sent"] == 0
    assert mailoutbox == []


def test_delivery_failure_is_marked_and_not_fatal(alpha, monkeypatch, mailoutbox):
    def boom(self, fail_silently=False):
        raise SMTPException("Server down")

    monkeypatch.setattr(mail.EmailMessage, "send", boom)
    with tenancy.tenant_context(alpha):
        make_termin(make_patient())
        make_termin(make_patient(email="zweite@example.org"), in_hours=21)
        counts = send_due_reminders("Alpha", now=NOW)
        assert counts["failed"] == 2
        assert Termin.objects.filter(reminder_sent_at__isnull=True, reminder_failed_at__isnull=False).count() == 2
    monkeypatch.undo()
    with tenancy.tenant_context(alpha):  # beim nächsten Lauf wird erneut versucht
        assert send_due_reminders("Alpha", now=NOW)["sent"] == 2


def test_command_runs_per_tenant_and_never_crosses_mandates(alpha, beta, mailoutbox):
    from accounts.models import Therapist

    Therapist.objects.create_user(username="a", password="x" * 12, tenant=alpha, email="alpha@example.org")
    with tenancy.tenant_context(alpha):
        make_termin(make_patient(email="pat-alpha@example.org"))
    with tenancy.tenant_context(beta):
        p = Patient.objects.create(first_name="Berta", last_name="Beta", email="pat-beta@example.org")
        Einwilligung.objects.create(patient=p, kind="email_reminder", granted_at=NOW)
        make_termin(p)
    call_command("send_reminders")
    by_recipient = {m.to[0]: m for m in mailoutbox}
    assert set(by_recipient) == {"pat-alpha@example.org", "pat-beta@example.org"}
    assert "Therapeut Alpha" in by_recipient["pat-alpha@example.org"].body
    assert "Therapeut Beta" in by_recipient["pat-beta@example.org"].body
    assert by_recipient["pat-alpha@example.org"].reply_to == ["alpha@example.org"]
    with tenancy.tenant_context(alpha):
        assert AuditEntry.objects.filter(action="termin.reminder_sent", actor_label="system:reminders").count() == 1
