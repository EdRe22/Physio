from datetime import date, timedelta

import pytest
from django.db import connections
from django.db.models import ProtectedError

from audit.models import AuditEntry, verify_chain
from core import tenancy
from patients.models import Eintrag, EintragVersion, Einwilligung, Patient, ProtectedDeletion, Zuweisung

TODAY = date.today()


def patient():
    return Patient.objects.create(first_name="Maria", last_name="Muster")


def referral(p, units=3, start=None, end=None):
    return Zuweisung.objects.create(
        patient=p, doctor="Dr. Beispiel", diagnosis="Fake-Diagnose", units_total=units,
        valid_from=start or TODAY - timedelta(days=10), valid_until=end,
    )


def post_entry(client, pid, **extra):
    data = {"kind": "behandlung", "date": TODAY.isoformat(), "referral": "", "text": "Behandlung ok"}
    data.update(extra)
    return client.post(f"/patients/{pid}/eintrag/neu/", data)


# --- Zuweisung ---

def test_referral_units_are_counted(client_alpha, alpha):
    with tenancy.tenant_context(alpha):
        p = patient()
        r = referral(p, units=2)
        pid, rid = p.pk, r.pk
    assert post_entry(client_alpha, pid, referral=rid).status_code == 302
    with tenancy.tenant_context(alpha):
        r = Zuweisung.objects.get(pk=rid)
        assert (r.units_used, r.units_left) == (1, 1)
    assert post_entry(client_alpha, pid, referral=rid).status_code == 302
    assert post_entry(client_alpha, pid, referral=rid).status_code == 200  # dritte Einheit: Kontingent leer
    with tenancy.tenant_context(alpha):
        assert Zuweisung.objects.get(pk=rid).units_left == 0
        assert Eintrag.objects.count() == 2


def test_only_treatments_use_up_units(client_alpha, alpha):
    with tenancy.tenant_context(alpha):
        p = patient()
        r = referral(p, units=1)
        pid, rid = p.pk, r.pk
    assert post_entry(client_alpha, pid, kind="befund", referral=rid).status_code == 302
    with tenancy.tenant_context(alpha):
        assert Zuweisung.objects.get(pk=rid).units_used == 0
        assert Eintrag.objects.get().referral is None  # nur Behandlungen hängen an einer Zuweisung


def test_referral_validity_period_enforced(client_alpha, alpha):
    with tenancy.tenant_context(alpha):
        p = patient()
        expired = referral(p, start=TODAY - timedelta(days=60), end=TODAY - timedelta(days=30))
        pid, rid = p.pk, expired.pk
    assert post_entry(client_alpha, pid, referral=rid).status_code == 200
    with tenancy.tenant_context(alpha):
        assert Eintrag.objects.count() == 0


def test_referral_form_validation(client_alpha, alpha):
    with tenancy.tenant_context(alpha):
        pid = patient().pk
    url = f"/patients/{pid}/zuweisung/neu/"
    bad = {"doctor": "Dr. X", "units_total": 0, "valid_from": TODAY.isoformat()}
    assert client_alpha.post(url, bad).status_code == 200
    reversed_dates = {"doctor": "Dr. X", "units_total": 5, "valid_from": TODAY.isoformat(),
                      "valid_until": (TODAY - timedelta(days=1)).isoformat()}
    assert client_alpha.post(url, reversed_dates).status_code == 200
    ok = {"doctor": "Dr. X", "units_total": 5, "valid_from": TODAY.isoformat()}
    assert client_alpha.post(url, ok).status_code == 302


def test_future_entry_date_rejected(client_alpha, alpha):
    with tenancy.tenant_context(alpha):
        pid = patient().pk
    assert post_entry(client_alpha, pid, date=(TODAY + timedelta(days=1)).isoformat()).status_code == 200


# --- Versionierung und Löschschutz ---

def test_editing_creates_new_version_and_keeps_history(client_alpha, alpha):
    with tenancy.tenant_context(alpha):
        p = patient()
        entry = Eintrag.objects.create(patient=p, kind="verlauf", date=TODAY)
        entry.add_version("erste Fassung")
        eid = entry.pk
    assert client_alpha.post(f"/patients/eintrag/{eid}/", {"text": "korrigierte Fassung"}).status_code == 302
    assert client_alpha.post(f"/patients/eintrag/{eid}/", {"text": "korrigierte Fassung"}).status_code == 302  # unverändert
    with tenancy.tenant_context(alpha):
        versions = list(EintragVersion.objects.filter(entry_id=eid))
        assert [v.text for v in versions] == ["erste Fassung", "korrigierte Fassung"]
        assert Eintrag.objects.get(pk=eid).current.text == "korrigierte Fassung"
    body = client_alpha.get(f"/patients/eintrag/{eid}/").content.decode()
    assert "erste Fassung" in body and "korrigierte Fassung" in body


def test_versions_and_entries_cannot_be_changed_or_deleted(in_alpha):
    p = patient()
    entry = Eintrag.objects.create(patient=p, kind="befund", date=TODAY)
    version = entry.add_version("Text")
    with pytest.raises(ProtectedDeletion):
        version.text = "anders"
        version.save()
    with pytest.raises(ProtectedDeletion):
        version.delete()
    with pytest.raises(ProtectedDeletion):
        entry.delete()
    with pytest.raises(ProtectedError):
        p.delete()  # Patient mit Dokumentation ist nicht löschbar


def test_documentation_text_encrypted_at_rest(alpha):
    with tenancy.tenant_context(alpha):
        p = patient()
        entry = Eintrag.objects.create(patient=p, kind="befund", date=TODAY)
        entry.add_version("Streng geheimer Befundtext")
        referral(p)
    with connections["t_alpha"].cursor() as cur:
        cur.execute("select text from patients_eintragversion")
        assert cur.fetchone()[0].startswith("v1:")
        cur.execute("select doctor, diagnosis from patients_zuweisung")
        assert all(v.startswith("v1:") for v in cur.fetchone())


def test_documentation_changes_are_audited(alpha):
    with tenancy.tenant_context(alpha):
        entry = Eintrag.objects.create(patient=patient(), kind="verlauf", date=TODAY)
        entry.add_version("a")
        entry.add_version("b")
        actions = [(e.action, e.object_type) for e in AuditEntry.objects.all()]
        assert ("create", "patients.eintragversion") in actions
        assert verify_chain()[0]


# --- Einwilligungen ---

def test_consent_grant_and_revoke(client_alpha, alpha):
    with tenancy.tenant_context(alpha):
        pid = patient().pk
    grant = f"/patients/{pid}/einwilligung/email_reminder/erteilen/"
    revoke = f"/patients/{pid}/einwilligung/email_reminder/widerrufen/"
    assert client_alpha.post(grant).status_code == 302
    assert client_alpha.post(grant).status_code == 302  # doppelt erteilen legt nichts doppelt an
    with tenancy.tenant_context(alpha):
        p = Patient.objects.get(pk=pid)
        assert p.has_consent("email_reminder") and Einwilligung.objects.count() == 1
    assert client_alpha.post(revoke).status_code == 302
    with tenancy.tenant_context(alpha):
        p = Patient.objects.get(pk=pid)
        assert not p.has_consent("email_reminder")
        assert Einwilligung.objects.get().revoked_at is not None  # Historie bleibt
    assert client_alpha.post(f"/patients/{pid}/einwilligung/unbekannt/erteilen/").status_code == 403
    assert client_alpha.get(grant).status_code == 405  # nur POST


# --- Mandantentrennung für die neuen Bereiche ---

def test_other_tenant_cannot_reach_documentation(client_beta, alpha):
    with tenancy.tenant_context(alpha):
        p = patient()
        entry = Eintrag.objects.create(patient=p, kind="befund", date=TODAY)
        entry.add_version("nur für Alpha")
        pid, eid = p.pk, entry.pk
    assert client_beta.get(f"/patients/eintrag/{eid}/").status_code == 404
    assert client_beta.post(f"/patients/eintrag/{eid}/", {"text": "x"}).status_code == 404
    assert client_beta.get(f"/patients/{pid}/eintrag/neu/").status_code == 404
    assert client_beta.get(f"/patients/{pid}/zuweisung/neu/").status_code == 404
    assert client_beta.post(f"/patients/{pid}/einwilligung/photo/erteilen/").status_code == 404
    with tenancy.tenant_context(alpha):
        assert EintragVersion.objects.count() == 1


def test_detail_page_shows_documentation(client_alpha, alpha):
    with tenancy.tenant_context(alpha):
        p = patient()
        r = referral(p, units=4)
        entry = Eintrag.objects.create(patient=p, referral=r, kind="behandlung", date=TODAY)
        entry.add_version("Mobilisation LWS")
        pid = p.pk
    body = client_alpha.get(f"/patients/{pid}/").content.decode()
    assert "Mobilisation LWS" in body and "1 von 4 Einheiten" in body and "3 offen" in body
