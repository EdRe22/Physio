"""Kernstück: Therapeuten dürfen die Daten der Kollegen weder sehen noch lesen können."""
import pytest
from cryptography.exceptions import InvalidTag
from django.core.exceptions import FieldError
from django.db import connections

from core import crypto, tenancy
from patients.models import Patient


def make_patient(**kw):
    data = dict(first_name="Maria", last_name="Muster", birth_date="1980-12-24", insurance_no="1234 241280")
    data.update(kw)
    return Patient.objects.create(**data)


def test_no_tenant_means_no_access():
    with pytest.raises(tenancy.TenantNotSet):
        list(Patient.objects.all())
    with pytest.raises(tenancy.TenantNotSet):
        Patient(first_name="a", last_name="b").save()


def test_patient_lives_only_in_own_database(alpha, beta):
    with tenancy.tenant_context(alpha):
        make_patient()
    with tenancy.tenant_context(beta):
        assert Patient.objects.count() == 0
    with connections["t_alpha"].cursor() as cur:
        cur.execute("select count(*) from patients_patient")
        assert cur.fetchone()[0] == 1
    with connections["t_beta"].cursor() as cur:
        cur.execute("select count(*) from patients_patient")
        assert cur.fetchone()[0] == 0


def test_patient_tables_do_not_exist_in_praxis_db():
    with connections["default"].cursor() as cur:
        cur.execute("select name from sqlite_master where name like 'patients_%'")
        assert cur.fetchall() == []


def test_ciphertext_at_rest(alpha):
    with tenancy.tenant_context(alpha):
        make_patient()
    with connections["t_alpha"].cursor() as cur:
        cur.execute("select first_name, last_name, birth_date, insurance_no from patients_patient")
        row = cur.fetchone()
    assert all(value.startswith("v1:") for value in row)
    assert "Muster" not in "".join(row)


def test_other_tenants_key_cannot_decrypt(alpha, beta):
    with tenancy.tenant_context(alpha):
        make_patient()
    with connections["t_alpha"].cursor() as cur:
        cur.execute("select last_name from patients_patient")
        token = cur.fetchone()[0]
    with pytest.raises(InvalidTag):
        crypto.decrypt(beta.context().data_key, token)
    assert crypto.decrypt(alpha.context().data_key, token) == "Muster"


def test_database_search_on_encrypted_fields_is_blocked(in_alpha):
    make_patient()
    with pytest.raises(FieldError):
        Patient.objects.filter(last_name="Muster").count()
    with pytest.raises(FieldError):
        Patient.objects.filter(last_name__icontains="Mus").count()


def test_str_never_contains_names(in_alpha):
    patient = make_patient()
    assert "Muster" not in str(patient) and "Maria" not in str(patient)


def test_tenant_context_repr_hides_key(alpha):
    assert alpha.context().data_key.hex() not in repr(alpha.context())


# --- über HTTP ---

def test_user_sees_only_own_patients(client_alpha, client_beta, alpha, beta):
    with tenancy.tenant_context(alpha):
        make_patient(last_name="Alphapatient")
    with tenancy.tenant_context(beta):
        make_patient(last_name="Betapatient")
    body_a = client_alpha.get("/patients/").content.decode()
    body_b = client_beta.get("/patients/").content.decode()
    assert "Alphapatient" in body_a and "Betapatient" not in body_a
    assert "Betapatient" in body_b and "Alphapatient" not in body_b


def test_direct_url_to_foreign_patient_is_404(client_alpha, client_beta, alpha):
    with tenancy.tenant_context(alpha):
        pid = make_patient(last_name="Alphapatient").pk
    assert client_alpha.get(f"/patients/{pid}/").status_code == 200
    # Beta hat keinen Patienten mit dieser ID
    assert client_beta.get(f"/patients/{pid}/").status_code == 404
    assert client_beta.get(f"/patients/{pid}/bearbeiten/").status_code == 404
    assert client_beta.post(f"/patients/{pid}/archivieren/").status_code == 404


def test_same_id_in_other_tenant_returns_own_record(client_beta, alpha, beta):
    with tenancy.tenant_context(alpha):
        pid_a = make_patient(last_name="Alphapatient").pk
    with tenancy.tenant_context(beta):
        pid_b = make_patient(last_name="Betapatient").pk
    assert pid_a == pid_b  # gleiche ID in getrennten Datenbanken
    body = client_beta.get(f"/patients/{pid_b}/").content.decode()
    assert "Betapatient" in body and "Alphapatient" not in body


def test_user_without_tenant_is_forbidden(db):
    from accounts.models import Therapist
    from tests.conftest import verified_client

    admin = Therapist.objects.create_user(username="ohne_mandant", password="ein-langes-passwort-123")
    assert verified_client(admin).get("/patients/").status_code == 403
