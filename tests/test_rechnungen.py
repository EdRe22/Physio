import io
import json
from datetime import date, timedelta

import pytest
from django.db import connections
from pypdf import PdfReader

from audit.models import AuditEntry, verify_chain
from core import tenancy
from patients.models import Eintrag, Patient, Zuweisung
from rechnungen import services
from rechnungen.forms import iban_valid, parse_eur
from rechnungen.models import Honorarnote, ImmutableError, Leistung, Mahnung, Nummernkreis, Position, Rechnungsprofil, Zahlung, eur
from rechnungen.pdf import render_pdf

TODAY = date.today()
IBAN = "AT611904300234573201"  # Beispiel-IBAN (gültige Prüfsumme, erfunden)


def profile(confirmed=True, **kw):
    data = dict(
        name="Therapeut Alpha", title="Physiotherapeut", address="Praxisgasse 1\n1010 Wien", iban=IBAN,
        exemption_text="Umsatzsteuerfreie Heilbehandlung (Fake-Text für Tests).",
        exemption_confirmed_on=TODAY if confirmed else None,
    )
    data.update(kw)
    return Rechnungsprofil.objects.create(**data)


def patient(address="Teststraße 5\n1100 Wien", **kw):
    return Patient.objects.create(first_name="Łukasz", last_name="Çelik", address=address, **kw)


def leistung(price=5500, name="Physiotherapie Einzel"):
    return Leistung.objects.create(name=name, price_cents=price)


def draft(p, price=5500, qty=1, when=None):
    invoice = Honorarnote.objects.create(patient=p)
    services.add_position(invoice, when or TODAY - timedelta(days=1), "Physiotherapie Einzel", qty, price)
    return invoice


def read_pdf(invoice):
    reader = PdfReader(io.BytesIO(render_pdf(invoice)))
    return "\n".join(page.extract_text() for page in reader.pages)


# --- Hilfsfunktionen ---

def test_money_formatting_and_parsing():
    assert eur(123456) == "1.234,56 EUR" and eur(5) == "0,05 EUR" and eur(-250) == "-2,50 EUR"
    assert parse_eur("45,50") == 4550 and parse_eur("45.5") == 4550 and parse_eur("1.234,56") == 123456
    for bad in ("", "abc", "-5", "1,234", "100000"):
        with pytest.raises(Exception):
            parse_eur(bad)


def test_iban_validation():
    assert iban_valid(IBAN) and iban_valid("AT61 1904 3002 3457 3201")
    assert not iban_valid("AT611904300234573202") and not iban_valid("123")


# --- Ausstellen und Nummernkreis ---

def test_issue_assigns_gapless_numbers_and_freezes(in_alpha):
    profile()
    p = patient()
    first, second = services.issue(draft(p)), services.issue(draft(p))
    year = TODAY.year
    assert (first.number, second.number) == (f"{year}-0001", f"{year}-0002")
    assert first.status == "ausgestellt" and first.due_date == TODAY + timedelta(days=14)
    assert first.recipient_name == "Çelik Łukasz" and "Teststraße" in first.recipient_address
    assert first.service_from == TODAY - timedelta(days=1)
    assert json.loads(first.issuer_snapshot)["iban"] == IBAN


def test_number_resets_each_year_and_never_skips(in_alpha):
    profile()
    p = patient()
    a = services.issue(draft(p), today=date(2026, 12, 30))
    b = services.issue(draft(p), today=date(2027, 1, 2))
    c = services.issue(draft(p), today=date(2027, 1, 3))
    assert [a.number, b.number, c.number] == ["2026-0001", "2027-0001", "2027-0002"]


def test_failed_issue_leaves_no_gap(in_alpha):
    profile()
    p = patient(address="")
    big = draft(p, price=50000)  # über 400 EUR ohne Adresse: abgelehnt
    with pytest.raises(services.BillingError, match="Anschrift"):
        services.issue(big)
    assert Honorarnote.objects.get(pk=big.pk).number is None
    assert not Nummernkreis.objects.filter(year=TODAY.year, last__gt=0).exists()
    ok = services.issue(draft(patient()))
    assert ok.number == f"{TODAY.year}-0001"


def test_small_amount_needs_no_address(in_alpha):
    profile()
    assert services.issue(draft(patient(address=""), price=4000)).number  # 40,00 EUR


def test_cannot_issue_without_confirmed_exemption_or_profile(in_alpha):
    p = patient()
    with pytest.raises(services.BillingError, match="Rechnungsprofil"):
        services.issue(draft(p))
    profile(confirmed=False)
    with pytest.raises(services.BillingError, match="Steuerberater"):
        services.issue(draft(p))
    assert Honorarnote.objects.filter(number__isnull=False).count() == 0


def test_cannot_issue_empty_or_zero_invoices(in_alpha):
    profile()
    with pytest.raises(services.BillingError, match="keine Positionen"):
        services.issue(Honorarnote.objects.create(patient=patient()))
    with pytest.raises(services.BillingError, match="größer als 0"):
        services.issue(draft(patient(), price=0))


def test_cannot_issue_twice(in_alpha):
    profile()
    invoice = services.issue(draft(patient()))
    with pytest.raises(services.BillingError, match="bereits ausgestellt"):
        services.issue(invoice)


# --- Unveränderlichkeit ---

def test_issued_invoice_is_immutable(in_alpha):
    profile()
    invoice = services.issue(draft(patient()))
    invoice = Honorarnote.objects.get(pk=invoice.pk)
    invoice.total_cents = 1
    with pytest.raises(ImmutableError):
        invoice.save()
    invoice = Honorarnote.objects.get(pk=invoice.pk)
    invoice.recipient_name = "Anderer Name"
    with pytest.raises(ImmutableError):
        invoice.save()
    with pytest.raises(ImmutableError):
        invoice.delete()
    position = invoice.positions.get()
    position.unit_price_cents = 1
    with pytest.raises(ImmutableError):
        position.save()
    with pytest.raises(ImmutableError):
        position.delete()
    with pytest.raises(services.BillingError):
        services.add_position(invoice, TODAY, "Nachträglich", 1, 100)
    with pytest.raises(ImmutableError):
        Position.objects.create(invoice=invoice, date=TODAY, description="x", unit_price_cents=1)


def test_patient_changes_do_not_alter_issued_invoice(in_alpha):
    profile()
    p = patient()
    invoice = services.issue(draft(p))
    p.last_name, p.address = "Neuer Name", "Neue Adresse 9"
    p.save()
    fresh = Honorarnote.objects.get(pk=invoice.pk)
    assert fresh.recipient_name == "Çelik Łukasz" and "Teststraße" in fresh.recipient_address
    assert "Çelik" in read_pdf(fresh) and "Neuer Name" not in read_pdf(fresh)


def test_profile_changes_do_not_alter_issued_invoice(in_alpha):
    prof = profile()
    invoice = services.issue(draft(patient()))
    prof.iban, prof.name = "AT000000000000000000", "Jemand anderes"
    prof.save()
    text = read_pdf(Honorarnote.objects.get(pk=invoice.pk))
    assert IBAN in text.replace(" ", "") and "Therapeut Alpha" in text and "Jemand anderes" not in text


def test_zahlung_and_mahnung_are_immutable(in_alpha):
    profile()
    invoice = services.issue(draft(patient()))
    pay = services.add_payment(invoice, TODAY, 1000)
    pay.amount_cents = 1
    with pytest.raises(ImmutableError):
        pay.save()
    with pytest.raises(ImmutableError):
        pay.delete()


# --- Beträge ---

def test_totals_and_rounding(in_alpha):
    profile()
    p = patient()
    invoice = Honorarnote.objects.create(patient=p)
    services.add_position(invoice, TODAY, "A", 1, 5500)
    services.add_position(invoice, TODAY, "B", "2.5", 3333)  # 83,325 -> 83,33
    invoice = Honorarnote.objects.get(pk=invoice.pk)
    assert invoice.total_cents == 5500 + 8333
    assert [x.line_total_cents for x in invoice.positions.all()] == [5500, 8333]
    with pytest.raises(services.BillingError):
        services.add_position(invoice, TODAY, "C", 0, 100)
    with pytest.raises(services.BillingError):
        services.add_position(invoice, TODAY, "C", 1, -100)
    with pytest.raises(services.BillingError, match="Zukunft"):
        services.add_position(invoice, TODAY + timedelta(days=1), "C", 1, 100)


def test_remove_position_updates_total_and_draft_delete(in_alpha):
    p = patient()
    invoice = draft(p)
    services.remove_position(invoice.positions.get())
    assert Honorarnote.objects.get(pk=invoice.pk).total_cents == 0
    services.delete_draft(invoice)
    assert not Honorarnote.objects.filter(pk=invoice.pk).exists()


# --- Behandlungen übernehmen ---

def test_unbilled_entries_flow_including_storno(in_alpha):
    profile()
    p = patient()
    cat = leistung()
    entries = []
    for i in range(3):
        e = Eintrag.objects.create(patient=p, kind="behandlung", date=TODAY - timedelta(days=i + 1))
        e.add_version("Text")
        entries.append(e)
    Eintrag.objects.create(patient=p, kind="befund", date=TODAY)  # Befunde werden nicht abgerechnet
    assert services.unbilled_entries(p).count() == 3
    invoice = services.create_draft(p, entries[:2], cat)
    assert services.unbilled_entries(p).count() == 1  # Entwurf reserviert die Behandlungen
    assert invoice.total_cents == 11000 and invoice.positions.count() == 2
    with pytest.raises(services.BillingError, match="Leistung"):
        services.create_draft(p, entries, None)
    issued = services.issue(invoice)
    assert services.unbilled_entries(p).count() == 1
    services.storno(issued)
    assert services.unbilled_entries(p).count() == 3  # nach Storno wieder abrechenbar


def test_single_referral_is_carried_over(in_alpha):
    p = patient()
    ref = Zuweisung.objects.create(patient=p, doctor="Dr. Fake", units_total=5, valid_from=TODAY - timedelta(days=30))
    e = Eintrag.objects.create(patient=p, referral=ref, kind="behandlung", date=TODAY)
    assert services.create_draft(p, [e], leistung()).referral_id == ref.pk


# --- Storno ---

def test_storno_creates_negative_invoice_with_own_number(in_alpha):
    profile()
    original = services.issue(draft(patient(), price=7000))
    storno = services.storno(original)
    original = Honorarnote.objects.get(pk=original.pk)
    assert storno.kind == "storno" and storno.total_cents == -7000
    assert storno.number == f"{TODAY.year}-0002" and storno.cancels_id == original.pk
    assert original.status == "storniert" and original.number == f"{TODAY.year}-0001" and original.total_cents == 7000
    assert storno.recipient_name == original.recipient_name and storno.issuer_snapshot == original.issuer_snapshot
    with pytest.raises(services.BillingError):
        services.storno(original)  # nicht doppelt
    with pytest.raises(services.BillingError):
        services.storno(storno)  # Storno lässt sich nicht stornieren
    text = read_pdf(storno)
    assert "Stornorechnung" in text and original.number in text and "-70,00" in text


def test_storno_of_paid_invoice_and_open_amount(in_alpha):
    profile()
    invoice = services.issue(draft(patient(), price=5000))
    services.add_payment(invoice, TODAY, 5000)
    assert Honorarnote.objects.get(pk=invoice.pk).status == "bezahlt"
    services.storno(Honorarnote.objects.get(pk=invoice.pk))
    assert Honorarnote.objects.get(pk=invoice.pk).status == "storniert"


# --- Zahlungen ---

def test_payments_partial_full_and_validation(in_alpha):
    profile()
    invoice = services.issue(draft(patient(), price=10000))
    services.add_payment(invoice, TODAY, 4000)
    invoice = Honorarnote.objects.get(pk=invoice.pk)
    assert invoice.status == "ausgestellt" and invoice.open_cents == 6000
    for amount, when, msg in ((6001, TODAY, "höher"), (0, TODAY, "größer als 0"),
                              (100, TODAY + timedelta(days=1), "Zukunft"), (100, TODAY - timedelta(days=30), "vor dem")):
        with pytest.raises(services.BillingError, match=msg):
            services.add_payment(invoice, when, amount)
    services.add_payment(invoice, TODAY, 6000)
    invoice = Honorarnote.objects.get(pk=invoice.pk)
    assert invoice.status == "bezahlt" and invoice.open_cents == 0
    with pytest.raises(services.BillingError):
        services.add_payment(invoice, TODAY, 100)


# --- Mahnwesen ---

def test_overdue_and_reminder_levels(in_alpha):
    profile(payment_days=10)
    invoice = services.issue(draft(patient()), today=TODAY - timedelta(days=30))
    assert services.overdue() == [invoice]
    first = services.record_reminder(invoice, TODAY)
    assert first.level == 1
    services.record_reminder(invoice, TODAY + timedelta(days=8))
    services.record_reminder(invoice, TODAY + timedelta(days=16))
    with pytest.raises(services.BillingError, match="alle Mahnstufen"):
        services.record_reminder(invoice, TODAY + timedelta(days=30))
    services.add_payment(invoice, TODAY, invoice.total_cents)
    assert services.overdue() == []


def test_no_reminder_before_due_date(in_alpha):
    profile()
    invoice = services.issue(draft(patient()))
    assert services.overdue() == []
    with pytest.raises(services.BillingError, match="noch nicht überfällig"):
        services.record_reminder(invoice)
    assert Mahnung.objects.count() == 0


# --- PDF ---

def test_pdf_contains_required_invoice_data_with_special_characters(in_alpha):
    profile()
    p = patient()
    ref = Zuweisung.objects.create(patient=p, doctor="Dr. Müller", units_total=5, valid_from=TODAY - timedelta(days=30))
    invoice = draft(p, price=5500, qty=2)
    invoice.referral = ref
    invoice.save()
    invoice = services.issue(invoice)
    raw = render_pdf(Honorarnote.objects.get(pk=invoice.pk))
    assert raw.startswith(b"%PDF")
    text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(raw)).pages)
    for needle in (f"Honorarnote {invoice.number}", "Therapeut Alpha", "Çelik Łukasz", "Teststraße 5", "1100 Wien",
                   f"Rechnungsdatum: {TODAY:%d.%m.%Y}", "Leistungszeitraum", "Physiotherapie Einzel", "110,00 EUR",
                   "Umsatzsteuerfreie Heilbehandlung", "Dr. Müller", "Verwendungszweck", f"Zahlbar bis {invoice.due_date:%d.%m.%Y}"):
        assert needle in text, needle
    assert "ENTWURF" not in text


def test_draft_pdf_is_clearly_marked(in_alpha):
    profile()
    invoice = draft(patient())
    text = read_pdf(invoice)
    assert "ENTWURF" in text and "Honorarnote 20" not in text


# --- HTTP, Rechte, Mandantentrennung ---

def post_profile(client, **kw):
    data = dict(name="Therapeut Alpha", title="Physiotherapeut", address="Praxisgasse 1\n1010 Wien", payment_days=14,
                exemption_text="Fake-Befreiungstext", iban=IBAN, phone="", email="", tax_no="", uid="", bic="")
    data.update(kw)
    return client.post("/einstellungen/profil/", data)


def test_profile_form_rules(client_alpha, alpha):
    bad_iban = post_profile(client_alpha, iban="AT00")
    assert bad_iban.status_code == 200
    placeholder = post_profile(client_alpha, exemption_text="PLATZHALTER: x", exemption_confirmed_on=TODAY.isoformat())
    assert placeholder.status_code == 200
    future = post_profile(client_alpha, exemption_confirmed_on=(TODAY + timedelta(days=1)).isoformat())
    assert future.status_code == 200
    assert post_profile(client_alpha, exemption_confirmed_on=TODAY.isoformat()).status_code == 302
    with tenancy.tenant_context(alpha):
        assert Rechnungsprofil.objects.get().ready_to_issue


def test_full_http_flow(client_alpha, alpha):
    post_profile(client_alpha, exemption_confirmed_on=TODAY.isoformat())
    with tenancy.tenant_context(alpha):
        pid = patient().pk
    client_alpha.post("/rechnungen/leistungen/neu/", {"name": "Einzeltherapie", "price": "55,00", "active": "on"})
    with tenancy.tenant_context(alpha):
        assert Leistung.objects.get().price_cents == 5500
        lid = Leistung.objects.get().pk
    r = client_alpha.post(f"/rechnungen/neu/{pid}/", {"leistung": lid})
    assert r.status_code == 302
    iid = int(r["Location"].strip("/").split("/")[-1])
    client_alpha.post(f"/rechnungen/{iid}/position/", {"leistung": lid, "date": TODAY.isoformat(), "quantity": "2", "price": ""})
    assert "110,00 EUR" in client_alpha.get(f"/rechnungen/{iid}/").content.decode()
    assert client_alpha.get(f"/rechnungen/{iid}/ausstellen/").status_code == 405  # nur POST
    client_alpha.post(f"/rechnungen/{iid}/ausstellen/")
    page = client_alpha.get(f"/rechnungen/{iid}/").content.decode()
    assert f"{TODAY.year}-0001" in page and "Offen" in page
    pdf = client_alpha.get(f"/rechnungen/{iid}/pdf/")
    assert pdf.status_code == 200 and pdf["Content-Type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    client_alpha.post(f"/rechnungen/{iid}/zahlung/", {"date": TODAY.isoformat(), "amount": "110,00", "method": "ueberweisung"})
    with tenancy.tenant_context(alpha):
        assert Honorarnote.objects.get(pk=iid).status == "bezahlt"
        actions = {e.action for e in AuditEntry.objects.all()}
        assert {"rechnung.issue", "rechnung.pdf", "rechnung.read"} <= actions and verify_chain()[0]
    assert "Bezahlt" in client_alpha.get("/rechnungen/").content.decode()
    assert f"{TODAY.year}-0001" in client_alpha.get(f"/patients/{pid}/").content.decode()  # Liste beim Patienten


def test_issue_blocked_until_profile_confirmed_over_http(client_alpha, alpha):
    with tenancy.tenant_context(alpha):
        p = patient()
        iid = draft(p).pk
    client_alpha.post(f"/rechnungen/{iid}/ausstellen/")
    with tenancy.tenant_context(alpha):
        assert Honorarnote.objects.get(pk=iid).number is None


def test_other_tenant_cannot_access_invoices(client_beta, alpha, beta):
    with tenancy.tenant_context(alpha):
        profile()
        p = patient()
        draft(p)  # ID 1 existiert nur bei Alpha
        second = draft(p)
        services.issue(second)
        iid = second.pk
    with tenancy.tenant_context(beta):
        assert Honorarnote.objects.count() == 0
    for method, path in (("get", f"/rechnungen/{iid}/"), ("get", f"/rechnungen/{iid}/pdf/"),
                         ("post", f"/rechnungen/{iid}/storno/"), ("post", f"/rechnungen/{iid}/zahlung/"),
                         ("post", f"/rechnungen/{iid}/mahnung/"), ("post", f"/rechnungen/{iid}/loeschen/")):
        assert getattr(client_beta, method)(path).status_code == 404, path
    assert client_beta.get(f"/rechnungen/neu/{iid}/").status_code == 404
    with tenancy.tenant_context(alpha):
        assert Honorarnote.objects.get(pk=iid).status == "ausgestellt"


def test_invoice_data_encrypted_at_rest(alpha):
    with tenancy.tenant_context(alpha):
        profile()
        invoice = services.issue(draft(patient()))
    with connections["t_alpha"].cursor() as cur:
        cur.execute("select recipient_name, recipient_address from rechnungen_honorarnote where number is not null")
        name, address = cur.fetchone()
        cur.execute("select description from rechnungen_position")
        description = cur.fetchone()[0]
    assert all(v.startswith("v1:") for v in (name, address, description))
    assert "Çelik" not in name and "Physiotherapie" not in description


def test_requires_login():
    from django.test import Client

    for path in ("/rechnungen/", "/einstellungen/profil/", "/rechnungen/1/pdf/"):
        assert Client().get(path).status_code == 302
