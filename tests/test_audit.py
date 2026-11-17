import pytest
from django.db import connections

from audit.models import AppendOnlyError, AuditEntry, log, verify_chain
from core import tenancy
from patients.models import Patient


def _remove_triggers():
    """Simuliert einen Angreifer mit Schema-Rechten: nur so lassen sich die DB-Trigger umgehen."""
    with connections["t_alpha"].cursor() as cur:
        cur.execute("drop trigger audit_no_update")
        cur.execute("drop trigger audit_no_delete")


def test_writes_are_logged_without_patient_data(in_alpha):
    patient = Patient.objects.create(first_name="Maria", last_name="Muster")
    patient.phone = "0660 1234567"
    patient.save()
    entries = list(AuditEntry.objects.all())
    assert [e.action for e in entries] == ["create", "update"]
    assert entries[0].object_id == str(patient.pk)
    dump = " ".join(f"{e.action}{e.object_type}{e.object_id}{e.actor_label}" for e in entries)
    assert "Muster" not in dump and "1234567" not in dump


def test_chain_valid_and_detects_tampering(in_alpha):
    for i in range(4):
        log("test", object_type="x", object_id=str(i))
    assert verify_chain() == (True, None)
    _remove_triggers()
    with connections["t_alpha"].cursor() as cur:  # Manipulation an der Datenbank vorbei am ORM
        cur.execute("update audit_auditentry set action='harmlos' where id=2")
    ok, bad = verify_chain()
    assert not ok and bad == 2


def test_chain_detects_deleted_entry(in_alpha):
    for i in range(4):
        log("test", object_id=str(i))
    _remove_triggers()
    with connections["t_alpha"].cursor() as cur:
        cur.execute("delete from audit_auditentry where id=2")
    ok, bad = verify_chain()
    assert not ok and bad == 3


def test_entries_are_append_only(in_alpha):
    entry = log("test")
    with pytest.raises(AppendOnlyError):
        entry.action = "x"
        entry.save()
    with pytest.raises(AppendOnlyError):
        entry.delete()
    with pytest.raises(AppendOnlyError):
        AuditEntry.objects.all().delete()
    with pytest.raises(AppendOnlyError):
        AuditEntry.objects.all().update(action="x")


def test_each_tenant_has_own_chain(alpha, beta):
    with tenancy.tenant_context(alpha):
        log("a")
        log("a")
    with tenancy.tenant_context(beta):
        log("b")
        assert AuditEntry.objects.count() == 1
        assert verify_chain() == (True, None)


def test_reads_are_logged(client_alpha, alpha):
    with tenancy.tenant_context(alpha):
        pid = Patient.objects.create(first_name="A", last_name="B").pk
    client_alpha.get(f"/patients/{pid}/")
    client_alpha.get("/patients/")
    with tenancy.tenant_context(alpha):
        actions = [e.action for e in AuditEntry.objects.all()]
        entry = AuditEntry.objects.filter(action="patient.read").get()
    assert "patient.list" in actions and entry.actor_label == "alpha_user"
