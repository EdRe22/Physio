from audit.models import verify_chain
from core import tenancy
from patients.models import Patient


def test_create_edit_search_archive(client_alpha, alpha):
    response = client_alpha.post("/patients/neu/", {
        "first_name": "Maria", "last_name": "Muster", "birth_date": "24.12.1980",
        "insurance_no": "", "insurer": "ÖGK", "address": "", "phone": "", "email": "maria@example.org",
    })
    assert response.status_code == 302
    with tenancy.tenant_context(alpha):
        patient = Patient.objects.get()
        assert patient.birth_date == "1980-12-24"
        pid = patient.pk

    assert "Muster" in client_alpha.get("/patients/?q=mus").content.decode()
    assert "Muster" not in client_alpha.get("/patients/?q=xyz").content.decode()

    assert client_alpha.get(f"/patients/{pid}/bearbeiten/").status_code == 200
    assert "24.12.1980" in client_alpha.get(f"/patients/{pid}/bearbeiten/").content.decode()

    assert client_alpha.post(f"/patients/{pid}/archivieren/").status_code == 302
    assert "Muster" not in client_alpha.get("/patients/").content.decode()
    assert "Muster" in client_alpha.get("/patients/?archiviert=1").content.decode()
    with tenancy.tenant_context(alpha):
        assert Patient.objects.get().archived_at is not None  # gesperrt, nicht gelöscht
        assert verify_chain()[0]


def test_validation(client_alpha, alpha):
    response = client_alpha.post("/patients/neu/", {
        "first_name": "A", "last_name": "B", "birth_date": "31.02.1980", "email": "kein-mail",
    })
    assert response.status_code == 200
    with tenancy.tenant_context(alpha):
        assert Patient.objects.count() == 0
    future = client_alpha.post("/patients/neu/", {"first_name": "A", "last_name": "B", "birth_date": "01.01.2999"})
    assert future.status_code == 200


def test_csrf_enforced(user_alpha):
    from django.test import Client
    from tests.conftest import verified_client

    client = verified_client(user_alpha)
    strict = Client(enforce_csrf_checks=True)
    strict.cookies = client.cookies
    assert strict.post("/patients/neu/", {"first_name": "A", "last_name": "B"}).status_code == 403
