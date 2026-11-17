import io
from datetime import date, timedelta

import pytest
from django.core.management import call_command
from pypdf import PdfReader

from audit.models import verify_chain
from buchhaltung.models import Buchung
from core import tenancy
from core.models import Tenant
from praxis import services
from praxis.models import Mietabrechnung, Mietverhaeltnis, PraxisProfil
from praxis.pdf import render_pdf
from rechnungen.models import ImmutableError
from tests.conftest import make_user, verified_client

TODAY = date.today()
PERIOD = TODAY.strftime("%Y-%m")
IBAN = "AT611904300234573201"


@pytest.fixture
def owner(user_alpha):
    user_alpha.role = "owner"
    user_alpha.save()
    return user_alpha


@pytest.fixture
def owner_client(owner):
    return verified_client(owner)


@pytest.fixture
def profile():
    return PraxisProfil.objects.create(name="Mag. Alpha", address="Praxisgasse 1\n1010 Wien", iban=IBAN,
                                       tax_text="Fake-Steuerhinweis Vermietung", tax_confirmed_on=TODAY)


@pytest.fixture
def lease(beta):
    return Mietverhaeltnis.objects.create(tenant=beta, rent_cents=45000, utilities_cents=5000,
                                          valid_from=TODAY.replace(day=1) - timedelta(days=60))


# --- Ausstellen ---

def test_issue_numbers_amounts_and_snapshot(in_alpha, profile, lease, beta):
    inv = services.issue(beta, PERIOD)
    assert inv.number == f"M{TODAY.year}-001" and inv.total_cents == 50000 and inv.status == "offen"
    assert inv.due_on == TODAY + timedelta(days=14)
    profile.iban, profile.name = "AT000000000000000000", "Anderer"
    profile.save()
    assert IBAN in Mietabrechnung.objects.get(pk=inv.pk).issuer_snapshot


def test_issue_blocked_without_confirmed_profile(in_alpha, lease, beta):
    with pytest.raises(services.RentError, match="Steuerberater"):
        services.issue(beta, PERIOD)
    PraxisProfil.objects.create(name="A", address="B", iban=IBAN, tax_text="x")  # ohne Bestätigung
    with pytest.raises(services.RentError, match="Steuerberater"):
        services.issue(beta, PERIOD)
    assert Mietabrechnung.objects.count() == 0


def test_issue_rules(in_alpha, profile, lease, beta, alpha):
    services.issue(beta, PERIOD)
    with pytest.raises(services.RentError, match="bereits"):
        services.issue(beta, PERIOD)
    with pytest.raises(services.RentError, match="Zukunft"):
        services.issue(beta, (TODAY.replace(day=1) + timedelta(days=40)).strftime("%Y-%m"))
    with pytest.raises(services.RentError, match="ungültig"):
        services.issue(beta, "2026-13")
    with pytest.raises(services.RentError, match="kein gültiges Mietverhältnis"):
        services.issue(beta, (TODAY.replace(day=1) - timedelta(days=400)).strftime("%Y-%m"))
    with pytest.raises(services.RentError, match="kein gültiges"):
        services.issue(alpha, PERIOD)  # Eigentümer hat kein Mietverhältnis


def test_lease_end_date_limits_billing(in_alpha, profile, beta):
    Mietverhaeltnis.objects.create(tenant=beta, rent_cents=1000, valid_from=date(2026, 1, 1), valid_until=date(2026, 3, 15))
    with pytest.raises(services.RentError):
        services.issue(beta, "2026-04", today=date(2026, 5, 1))
    assert services.issue(beta, "2026-03", today=date(2026, 5, 1)).rent_cents == 1000  # am Ersten gültig: voller Monat, keine Aliquotierung


def test_numbers_are_gapless_per_year(in_alpha, profile, beta):
    Mietverhaeltnis.objects.create(tenant=beta, rent_cents=1000, valid_from=date(2025, 1, 1))
    a = services.issue(beta, "2026-12", today=date(2026, 12, 5))
    b = services.issue(beta, "2027-01", today=date(2027, 1, 5))
    c = services.issue(beta, "2027-02", today=date(2027, 2, 5))
    assert [a.number, b.number, c.number] == ["M2026-001", "M2027-001", "M2027-002"]


def test_issue_all_reports_skipped(in_alpha, profile, lease, beta):
    other = Tenant.provision("gamma", "Gamma")
    Mietverhaeltnis.objects.create(tenant=other, rent_cents=1000, valid_from=date(2099, 1, 1))
    created, skipped = services.issue_all(PERIOD)
    assert [i.tenant for i in created] == [beta] and skipped[0][0] == "Gamma"


def test_issued_invoice_is_immutable(in_alpha, profile, lease, beta):
    inv = Mietabrechnung.objects.get(pk=services.issue(beta, PERIOD).pk)
    inv.rent_cents = 1
    with pytest.raises(ImmutableError):
        inv.save()
    with pytest.raises(ImmutableError):
        Mietabrechnung.objects.get(pk=inv.pk).delete()


# --- Zahlung und Buchung in beiden Mandanten ---

def test_mark_paid_books_income_for_owner(in_alpha, profile, lease, beta):
    inv = services.issue(beta, PERIOD)
    services.mark_paid(inv, TODAY)
    b = Buchung.objects.get()
    assert b.kind == "einnahme" and b.amount_cents == 50000 and inv.number in b.text
    assert Mietabrechnung.objects.get(pk=inv.pk).status == "bezahlt"
    with pytest.raises(services.RentError):
        services.mark_paid(Mietabrechnung.objects.get(pk=inv.pk), TODAY)
    assert Buchung.objects.count() == 1


def test_mark_paid_date_rules(in_alpha, profile, lease, beta):
    inv = services.issue(beta, PERIOD)
    with pytest.raises(services.RentError, match="Zukunft"):
        services.mark_paid(inv, TODAY + timedelta(days=1))
    with pytest.raises(services.RentError, match="vor dem"):
        services.mark_paid(inv, TODAY - timedelta(days=5))
    assert Buchung.objects.count() == 0


def test_colleague_books_expense_in_own_tenant_only(alpha, beta, profile, lease):
    with tenancy.tenant_context(alpha):
        inv = services.issue(beta, PERIOD)
    with tenancy.tenant_context(beta):
        services.book_as_expense(Mietabrechnung.objects.get(pk=inv.pk))
        b = Buchung.objects.get()
        assert b.kind == "ausgabe" and b.amount_cents == 50000 and b.category.name == "Miete"
        with pytest.raises(services.RentError, match="bereits"):
            services.book_as_expense(Mietabrechnung.objects.get(pk=inv.pk))
    with tenancy.tenant_context(alpha):
        assert Buchung.objects.count() == 0  # Eigentümer-Buchhaltung unberührt
        with pytest.raises(services.RentError, match="nicht zu Ihrem"):
            services.book_as_expense(Mietabrechnung.objects.get(pk=inv.pk))


def test_storno_rules(alpha, beta, profile, lease):
    with tenancy.tenant_context(alpha):
        inv = services.issue(beta, PERIOD)
        services.storno(Mietabrechnung.objects.get(pk=inv.pk))
        stored = Mietabrechnung.objects.get(pk=inv.pk)
        assert stored.status == "storniert"
        with pytest.raises(services.RentError):
            services.storno(stored)
        again = services.issue(beta, PERIOD)  # nach Storno neu ausstellbar, nächste Nummer
        assert again.number == f"M{TODAY.year}-002"
    with tenancy.tenant_context(beta):
        with pytest.raises(services.RentError):
            services.book_as_expense(Mietabrechnung.objects.get(pk=inv.pk))


def test_cannot_storno_after_colleague_booked(alpha, beta, profile, lease):
    with tenancy.tenant_context(alpha):
        inv = services.issue(beta, PERIOD)
    with tenancy.tenant_context(beta):
        services.book_as_expense(Mietabrechnung.objects.get(pk=inv.pk))
    with tenancy.tenant_context(alpha):
        with pytest.raises(services.RentError, match="bereits gebucht"):
            services.storno(Mietabrechnung.objects.get(pk=inv.pk))


# --- PDF ---

def test_pdf_content(in_alpha, profile, lease, beta):
    inv = services.issue(beta, PERIOD)
    text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(render_pdf(Mietabrechnung.objects.get(pk=inv.pk)))).pages)
    for needle in (f"Mietabrechnung {inv.number}", "Therapeut Beta", "Mag. Alpha", "450,00 EUR", "50,00 EUR", "500,00 EUR",
                   "Fake-Steuerhinweis", IBAN, f"Zahlbar bis {inv.due_on:%d.%m.%Y}"):
        assert needle in text, needle


# --- HTTP: Rollen und Sichtbarkeit ---

def test_visibility_owner_all_colleague_only_own(alpha, beta, profile, lease, owner, user_beta):
    third = Tenant.provision("gamma", "Gamma")
    with tenancy.tenant_context(alpha):
        Mietverhaeltnis.objects.create(tenant=third, rent_cents=2000, valid_from=date(2020, 1, 1))
        services.issue_all(PERIOD)
        own = Mietabrechnung.objects.get(tenant=beta)
        foreign = Mietabrechnung.objects.get(tenant=third)
    assert services.visible_invoices(owner).count() == 2
    assert list(services.visible_invoices(user_beta)) == [own]
    client = verified_client(user_beta)
    assert client.get(f"/praxis/{own.pk}/").status_code == 200
    assert client.get(f"/praxis/{own.pk}/pdf/").status_code == 200
    for method, path in (("get", f"/praxis/{foreign.pk}/"), ("get", f"/praxis/{foreign.pk}/pdf/"),
                         ("post", f"/praxis/{foreign.pk}/buchen/")):
        assert getattr(client, method)(path).status_code == 404, path
    page = client.get("/praxis/").content.decode()
    assert own.number in page and foreign.number not in page and "Gamma" not in page


def test_colleague_cannot_use_owner_functions(client_beta, alpha, beta, profile, lease, owner):
    with tenancy.tenant_context(alpha):
        inv = services.issue(beta, PERIOD)
    for method, path in (("get", "/praxis/profil/"), ("get", "/praxis/mietvertraege/"), ("post", "/praxis/ausstellen/"),
                         ("post", f"/praxis/{inv.pk}/bezahlt/"), ("post", f"/praxis/{inv.pk}/storno/")):
        assert getattr(client_beta, method)(path, {"period": PERIOD}).status_code == 403, path
    assert Mietabrechnung.objects.get(pk=inv.pk).status == "offen"


def test_owner_http_flow(owner_client, alpha, beta, user_beta):
    r = owner_client.post("/praxis/profil/", {"name": "Mag. Alpha", "address": "Gasse 1\n1010 Wien", "iban": IBAN,
                                              "tax_text": "Fake-Hinweis", "tax_confirmed_on": TODAY.isoformat(), "payment_days": 14})
    assert r.status_code == 302
    r = owner_client.post("/praxis/mietvertraege/", {"tenant": beta.pk, "rent": "450,00", "utilities": "50,00",
                                                     "valid_from": date(2020, 1, 1).isoformat()})
    assert r.status_code == 302
    assert Mietverhaeltnis.objects.get().total_cents == 50000
    overlap = owner_client.post("/praxis/mietvertraege/", {"tenant": beta.pk, "rent": "10", "valid_from": date(2021, 1, 1).isoformat()})
    assert overlap.status_code == 200 and Mietverhaeltnis.objects.count() == 1
    owner_client.post("/praxis/ausstellen/", {"period": PERIOD})
    inv = Mietabrechnung.objects.get()
    owner_client.post(f"/praxis/{inv.pk}/bezahlt/", {"date": TODAY.isoformat()})
    assert Mietabrechnung.objects.get().status == "bezahlt"
    with tenancy.tenant_context(alpha):
        assert Buchung.objects.get().amount_cents == 50000 and verify_chain()[0]
    assert "500,00 EUR" in owner_client.get(f"/praxis/{inv.pk}/").content.decode()


def test_lease_form_excludes_owner_and_validates(owner_client, alpha):
    bad = owner_client.post("/praxis/mietvertraege/", {"tenant": alpha.pk, "rent": "100", "valid_from": TODAY.isoformat()})
    assert bad.status_code == 200 and Mietverhaeltnis.objects.count() == 0


def test_issue_rent_command(alpha, beta, owner, profile, lease, capsys):
    call_command("issue_rent", PERIOD)
    assert "1 ausgestellt" in capsys.readouterr().out
    assert Mietabrechnung.objects.count() == 1


def test_praxis_data_contains_no_patient_tables():
    from django.apps import apps

    names = {f.name for m in apps.get_app_config("praxis").get_models() for f in m._meta.get_fields()}
    assert not {"patient", "diagnosis", "first_name", "last_name"} & names


def test_requires_login():
    from django.test import Client

    for path in ("/praxis/", "/praxis/1/", "/praxis/1/pdf/", "/praxis/profil/"):
        assert Client().get(path).status_code == 302
