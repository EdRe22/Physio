import io
import json
import zipfile
from datetime import date, timedelta

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from accounts.models import Therapist
from audit.models import verify_chain, AuditEntry
from buchhaltung import services as books
from core import tenancy
from core.models import Tenant
from patients.models import Eintrag, Patient, Zuweisung
from praxis import offboarding
from praxis.models import Mietverhaeltnis
from rechnungen import services as billing
from rechnungen.models import Honorarnote, Rechnungsprofil

TODAY = date.today()


def make_patient(**kw):
    p = Patient.objects.create(first_name="Łukasz", last_name="Çelik", birth_date="1980-02-03", phone="0660 123", **kw)
    Zuweisung.objects.create(patient=p, doctor="Dr. Fake", diagnosis="Fake-Diagnose", units_total=5, valid_from=TODAY)
    e = Eintrag.objects.create(patient=p, kind="befund", date=date(2020, 5, 1))
    e.add_version("Erste Fassung")
    e.add_version("Zweite Fassung")
    return p


def zip_of(response):
    return zipfile.ZipFile(io.BytesIO(response.content))


# --- Export ---

def test_patient_export_contains_all_versions_and_logs(client_alpha, alpha):
    with tenancy.tenant_context(alpha):
        pid = make_patient().pk
    r = client_alpha.get(f"/patients/{pid}/export/")
    assert r.status_code == 200 and r["Content-Type"] == "application/zip" and r["Cache-Control"] == "no-store"
    archive = zip_of(r)
    assert sorted(archive.namelist()) == ["patient/patient.json", "patient/patient.txt"]  # keine Namen in Dateinamen
    data = json.loads(archive.read("patient/patient.json"))
    assert data["patient"]["nachname"] == "Çelik" and data["zuweisungen"][0]["diagnose"] == "Fake-Diagnose"
    assert [v["text"] for v in data["dokumentation"][0]["versionen"]] == ["Erste Fassung", "Zweite Fassung"]
    assert "Zweite Fassung" in archive.read("patient/patient.txt").decode()
    with tenancy.tenant_context(alpha):
        assert AuditEntry.objects.filter(action="export.patient", object_id=str(pid)).count() == 1


def test_patient_export_not_available_to_other_tenant(client_beta, alpha, beta):
    with tenancy.tenant_context(alpha):
        pid = make_patient().pk
    assert client_beta.get(f"/patients/{pid}/export/").status_code == 404


def test_full_export_includes_archived_patients_and_invoices(client_alpha, alpha):
    with tenancy.tenant_context(alpha):
        Rechnungsprofil.objects.create(name="Alpha", address="A 1\n1010 Wien", iban="AT611904300234573201",
                                       exemption_text="Fake", exemption_confirmed_on=TODAY)
        p1, p2 = make_patient(), make_patient()
        from django.utils import timezone

        p2.archived_at = timezone.now()
        p2.save()
        inv = Honorarnote.objects.create(patient=p1)
        billing.add_position(inv, TODAY, "Therapie", 1, 5500)
        number = billing.issue(inv).number
    assert client_alpha.get("/patients/gesamtexport/").status_code == 405  # nur POST
    r = client_alpha.post("/patients/gesamtexport/")
    names = zip_of(r).namelist()
    assert f"patienten/{p1.pk}/patient.json" in names and f"patienten/{p2.pk}/patient.json" in names
    assert f"honorarnoten/{number}.pdf" in names and "LIESMICH.txt" in names
    assert zip_of(r).read(f"honorarnoten/{number}.pdf").startswith(b"%PDF")
    with tenancy.tenant_context(alpha):
        assert AuditEntry.objects.filter(action="export.all").exists() and verify_chain()[0]


def test_exports_require_login():
    from django.test import Client

    assert Client().get("/patients/1/export/").status_code == 302
    assert Client().post("/patients/gesamtexport/").status_code == 302


# --- Aufbewahrungsfrist ---

def test_retention_uses_latest_of_documentation_and_tax_records(in_alpha):
    assert offboarding.retention_end(date(2026, 6, 1)) == date(2026, 6, 1)  # keine Daten
    make_patient()  # letzter Eintrag 2020-05-01 -> 10 Jahre
    assert offboarding.retention_end(date(2026, 6, 1)) == date(2030, 5, 1)
    cat = books.category("Miete", "ausgabe")
    books.create_buchung(date(2025, 3, 3), "ausgabe", cat, 100, today=date(2025, 3, 3))
    assert offboarding.retention_end(date(2026, 6, 1)) == date(2032, 3, 3)  # Buchung 2025 + 7 Jahre ist später


def test_add_years_handles_leap_day():
    assert offboarding.add_years(date(2024, 2, 29), 7) == date(2031, 2, 28)


# --- Ausscheiden ---

def test_offboard_locks_everything_and_ends_lease(alpha, beta, user_alpha):
    with tenancy.tenant_context(alpha):
        make_patient()
    Mietverhaeltnis.objects.create(tenant=alpha, rent_cents=1000, valid_from=date(2020, 1, 1))
    with pytest.raises(offboarding.OffboardingError, match="Gesamtexport"):
        offboarding.offboard(alpha, TODAY, export_confirmed=False)
    end = offboarding.offboard(alpha, TODAY, export_confirmed=True)
    alpha.refresh_from_db()
    assert end == date(2030, 5, 1)
    assert not alpha.is_active and alpha.offboarded_on == TODAY and alpha.retain_until == end
    user_alpha.refresh_from_db()
    assert not user_alpha.is_active
    assert Mietverhaeltnis.objects.get(tenant=alpha).valid_until == TODAY
    with pytest.raises(offboarding.OffboardingError, match="bereits"):
        offboarding.offboard(alpha, TODAY, export_confirmed=True)


def test_offboarded_user_cannot_log_in(alpha, user_alpha):
    offboarding.offboard(alpha, TODAY, export_confirmed=True)
    from django.test import Client

    assert not Client().login(username="alpha_user", password="ein-langes-passwort-123")


def test_offboarded_data_stays_intact_and_readable_internally(alpha):
    with tenancy.tenant_context(alpha):
        make_patient()
    offboarding.offboard(alpha, TODAY, export_confirmed=True)
    with tenancy.tenant_context(alpha):
        assert Patient.objects.get().last_name == "Çelik" and verify_chain()[0]


# --- Löschung ---

def test_purge_only_after_retention_and_only_once(db):
    t = Tenant.provision("gamma", "Gamma")
    with pytest.raises(offboarding.OffboardingError, match="nicht ausgeschieden"):
        offboarding.purge(t, TODAY)
    t.offboarded_on, t.retain_until = TODAY, TODAY + timedelta(days=30)
    t.save()
    with pytest.raises(offboarding.OffboardingError, match="Aufbewahrungsfrist"):
        offboarding.purge(t, TODAY)
    offboarding.purge(t, TODAY + timedelta(days=30))
    t.refresh_from_db()
    assert t.purged_on and t.wrapped_key == ""
    with pytest.raises(offboarding.OffboardingError, match="Bereits"):
        offboarding.purge(t, TODAY + timedelta(days=31))
    with pytest.raises(Exception):
        t.context()  # ohne Datenschlüssel nie wieder lesbar


def test_purge_removes_database_file_and_receipts(db, settings, tmp_path):
    settings.DATA_DIR = tmp_path
    settings.BELEG_DIR = tmp_path / "belege"
    t = Tenant.provision("delta", "Delta")
    db_file = tenancy.db_name_for("delta")
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db_file.write_bytes(b"x")
    (settings.BELEG_DIR / "delta").mkdir(parents=True)
    (settings.BELEG_DIR / "delta" / "a.bin").write_bytes(b"x")
    t.offboarded_on = t.retain_until = TODAY
    t.save()
    offboarding.purge(t, TODAY)
    assert not db_file.exists() and not (settings.BELEG_DIR / "delta").exists()


def test_purged_tenants_are_skipped_by_audit_check_and_backup(alpha, db):
    gone = Tenant.provision("epsilon", "Epsilon")
    gone.offboarded_on = gone.retain_until = TODAY
    gone.save()
    offboarding.purge(gone, TODAY)
    from core import backup

    assert "tenant_epsilon" not in [name for name, _ in backup._db_jobs()]
    call_command("verify_audit")  # darf an gelöschten Mandanten nicht scheitern


def test_commands_enforce_confirmations(alpha, db):
    with pytest.raises(CommandError, match="Bestätigung"):
        call_command("purge_tenant", "alpha", "--confirm", "falsch")
    with pytest.raises(CommandError, match="Gesamtexport"):
        call_command("offboard_tenant", "alpha")
    with pytest.raises(CommandError, match="nicht gefunden"):
        call_command("purge_tenant", "nichtda", "--confirm", "nichtda")
    with tenancy.tenant_context(alpha):
        make_patient()  # ohne Daten wäre die Frist sofort abgelaufen und die Löschung würde greifen
    call_command("offboard_tenant", "alpha", "--export-confirmed")
    with pytest.raises(CommandError, match="Aufbewahrungsfrist"):
        call_command("purge_tenant", "alpha", "--confirm", "alpha")
