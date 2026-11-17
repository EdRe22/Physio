import io
import zipfile
from datetime import date, timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections

from audit.models import verify_chain
from buchhaltung import services
from buchhaltung.camt import CamtError, parse_camt053
from buchhaltung.models import BankTransaktion, Beleg, Buchung, Kategorie
from core import tenancy
from importer.profiles import parse_amount_cents
from patients.models import Patient
from rechnungen import services as billing
from rechnungen.models import ImmutableError, Rechnungsprofil

TODAY = date.today()
PDF = b"%PDF-1.4\n% fake beleg\n"


def kat(name="Miete", kind="ausgabe"):
    return services.category(name, kind)


def issued(price=5500, last="Çelik"):
    if not Rechnungsprofil.objects.exists():
        Rechnungsprofil.objects.create(name="Alpha", address="A 1\n1010 Wien", iban="AT611904300234573201",
                                       exemption_text="Fake", exemption_confirmed_on=TODAY)
    p = Patient.objects.create(first_name="Ida", last_name=last, address="X 1\n1100 Wien")
    inv = billing.Honorarnote.objects.create(patient=p)
    billing.add_position(inv, TODAY - timedelta(days=1), "Therapie", 1, price)
    return billing.issue(inv)


def tx(amount, purpose="", name="", d=None, n=[0]):
    n[0] += 1
    return BankTransaktion.objects.create(date=d or TODAY, amount_cents=amount, purpose=purpose, counterpart=name, tx_hash=f"h{n[0]}")


# --- Buchungen ---

def test_buchung_numbers_and_validation(in_alpha):
    a = services.create_buchung(TODAY, "ausgabe", kat(), 10000, "Miete Jänner")
    b = services.create_buchung(TODAY, "ausgabe", kat(), 500)
    assert a.number == f"{TODAY.year}-00001" and b.number == f"{TODAY.year}-00002"
    with pytest.raises(services.BookkeepingError, match="größer als 0"):
        services.create_buchung(TODAY, "ausgabe", kat(), 0)
    with pytest.raises(services.BookkeepingError, match="passt nicht"):
        services.create_buchung(TODAY, "einnahme", kat(), 100)
    with pytest.raises(services.BookkeepingError, match="Zukunft"):
        services.create_buchung(TODAY + timedelta(days=1), "ausgabe", kat(), 100)


def test_buchung_immutable_and_reversal(in_alpha):
    b = services.create_buchung(TODAY, "ausgabe", kat(), 10000)
    b = Buchung.objects.get(pk=b.pk)
    b.amount_cents = 1
    with pytest.raises(ImmutableError):
        b.save()
    with pytest.raises(ImmutableError):
        b.delete()
    b = Buchung.objects.get(pk=b.pk)
    s = services.reverse(b)
    assert s.amount_cents == -10000 and s.reverses_id == b.pk and s.source == "storno"
    with pytest.raises(services.BookkeepingError):
        services.reverse(Buchung.objects.get(pk=b.pk))
    with pytest.raises(services.BookkeepingError):
        services.reverse(s)
    assert services.year_report(TODAY.year)["total_ausgaben_cents"] == 0


def test_payment_creates_income_booking(in_alpha):
    inv = issued()
    pay = billing.add_payment(inv, TODAY, 5500)
    b = Buchung.objects.get(zahlung=pay)
    assert b.kind == "einnahme" and b.amount_cents == 5500 and b.category.name == "Honorare" and inv.number in b.text
    assert services.year_report(TODAY.year)["ueberschuss_cents"] == 5500


# --- Belege ---

def test_beleg_encrypted_roundtrip_and_validation(alpha):
    with tenancy.tenant_context(alpha):
        b = services.store_beleg(SimpleUploadedFile("rechnung.pdf", PDF))
        assert services.read_beleg(b) == PDF and b.number.startswith(f"B{TODAY.year}-")
        raw_file = services._beleg_path(b.storage_name).read_bytes()
        assert PDF not in raw_file and b"fake beleg" not in raw_file
        for name, data, msg in (("x.exe", b"MZ....", "PDF"), ("x.pdf", b"", "leer"),
                                ("big.pdf", PDF + b"0" * (services.MAX_BELEG_BYTES + 1), "10 MB")):
            with pytest.raises(services.BookkeepingError, match=msg):
                services.store_beleg(SimpleUploadedFile(name, data))
        with pytest.raises(ImmutableError):
            b.delete()
        services._beleg_path(b.storage_name).write_bytes(b"x" * 40)
        with pytest.raises(Exception):
            services.read_beleg(b)
    with connections["t_alpha"].cursor() as cur:
        cur.execute("select original_name from buchhaltung_beleg")
        assert cur.fetchone()[0].startswith("v1:")


def test_beleg_attach_once(in_alpha):
    buchung = services.create_buchung(TODAY, "ausgabe", kat(), 100)
    beleg = services.store_beleg(SimpleUploadedFile("a.pdf", PDF))
    services.attach_beleg(beleg, buchung)
    with pytest.raises(services.BookkeepingError):
        services.attach_beleg(Beleg.objects.get(pk=beleg.pk), buchung)


# --- Bankabgleich ---

def test_amount_parsing():
    assert parse_amount_cents("-1.234,56") == -123456 and parse_amount_cents("1234.56") == 123456
    assert parse_amount_cents("45,5-") == -4550 and parse_amount_cents("55,00 EUR") == 5500
    for bad in ("abc", "1,234", "", "1,2,3"):
        assert parse_amount_cents(bad) is None


def test_suggest_confidence_levels(in_alpha):
    inv = issued(5500, last="Meier")
    invoices = services.open_invoices()
    assert services.suggest(tx(5500, f"Honorarnote {inv.number}"), invoices) == (inv, "sicher")
    assert services.suggest(tx(2000, f"Zahlung {inv.number}"), invoices) == (inv, "teilzahlung")
    assert services.suggest(tx(9000, f"Zahlung {inv.number}"), invoices) is None  # Überzahlung nie automatisch
    assert services.suggest(tx(5500, "Physio", "Ida Meier"), invoices) == (inv, "wahrscheinlich")
    assert services.suggest(tx(5500, "Physio", "Hans Huber"), invoices) is None
    assert services.suggest(tx(-5500, inv.number), invoices) is None  # Ausgang nie


def test_ambiguous_same_amount_is_not_suggested(in_alpha):
    issued(5500, last="Meier")
    issued(5500, last="Meier")
    assert services.suggest(tx(5500, "x", "Meier"), services.open_invoices()) is None


def test_accept_match_books_payment_once(in_alpha):
    inv = issued()
    t = tx(5500, inv.number)
    services.accept_match(t, inv)
    t = BankTransaktion.objects.get(pk=t.pk)
    assert t.status == "zugeordnet" and t.invoice_id == inv.pk and t.buchung.zahlung is not None
    assert billing.Honorarnote.objects.get(pk=inv.pk).status == "bezahlt"
    with pytest.raises(services.BookkeepingError):
        services.accept_match(t, inv)
    assert Buchung.objects.filter(kind="einnahme").count() == 1  # keine Doppelbuchung


def test_accept_all_sure_only_takes_sure(in_alpha):
    a, b = issued(5500), issued(4000, last="Huber")
    tx(5500, a.number)
    tx(1000, b.number)  # Teilzahlung: nicht automatisch
    assert services.accept_all_sure() == 1
    assert BankTransaktion.objects.filter(status="offen").count() == 1


def test_book_and_ignore_transactions(in_alpha):
    out, income, junk = tx(-12000, "Miete", "Vermieter"), tx(3000, "Sonstiges"), tx(-999, "privat")
    services.book_transaction(out, kat("Miete"))
    services.book_transaction(income, kat("Sonstige Einnahmen", "einnahme"))
    services.ignore_transaction(junk)
    assert Buchung.objects.get(source="bank", kind="ausgabe").amount_cents == 12000
    with pytest.raises(services.BookkeepingError):
        services.book_transaction(BankTransaktion.objects.get(pk=out.pk), kat("Miete"))
    with pytest.raises(services.BookkeepingError, match="passt nicht"):
        services.book_transaction(tx(-100), kat("Honorare", "einnahme"))
    with pytest.raises(ImmutableError):
        out.delete()


# --- camt.053 ---

CAMT = """<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:camt.053.001.02"><BkToCstmrStmt><Stmt>
<Ntry><Amt Ccy="EUR">55.00</Amt><CdtDbtInd>CRDT</CdtDbtInd><BookgDt><Dt>2026-11-10</Dt></BookgDt>
 <NtryDtls><TxDtls><Refs><EndToEndId>NOTPROVIDED</EndToEndId></Refs><RltdPties><Dbtr><Nm>Ida Meier</Nm></Dbtr><DbtrAcct><Id><IBAN>AT611904300234573201</IBAN></Id></DbtrAcct></RltdPties>
 <RmtInf><Ustrd>Honorarnote 2026-0001</Ustrd></RmtInf></TxDtls></NtryDtls></Ntry>
<Ntry><Amt Ccy="EUR">1200.50</Amt><CdtDbtInd>DBIT</CdtDbtInd><BookgDt><Dt>2026-11-01</Dt></BookgDt>
 <NtryDtls><TxDtls><RltdPties><Cdtr><Nm>Vermieter GmbH</Nm></Cdtr></RltdPties><RmtInf><Ustrd>Miete</Ustrd></RmtInf></TxDtls></NtryDtls></Ntry>
</Stmt></BkToCstmrStmt></Document>""".encode()


def test_camt_parsing():
    headers, rows = parse_camt053(CAMT)
    assert rows[0] == ["2026-11-10", "55.00", "Ida Meier", "AT611904300234573201", "Honorarnote 2026-0001", ""]
    assert rows[1][:5] == ["2026-11-01", "-1200.50", "Vermieter GmbH", "", "Miete"]


def test_camt_rejects_xxe_bomb_and_garbage():
    xxe = b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/passwd">]><Document><BkToCstmrStmt>&x;</BkToCstmrStmt></Document>'
    for bad in (xxe, b"not xml", b"<a/>", b"<Document><BkToCstmrStmt/></Document>"):
        with pytest.raises(CamtError):
            parse_camt053(bad)


def test_camt_upload_is_accepted_by_import_wizard(client_alpha):
    r = client_alpha.post("/import/", {"profile": "bank", "file": SimpleUploadedFile("auszug.xml", CAMT)})
    assert r.status_code == 302 and client_alpha.get(r["Location"]).status_code == 200


def test_bank_import_via_profile_skips_duplicates(alpha):
    from importer.profiles import BankProfile

    with tenancy.tenant_context(alpha):
        _, rows = parse_camt053(CAMT)
        mapping = {"date": 0, "amount": 1, "counterpart": 2, "iban": 3, "purpose": 4, "reference": 5}
        for expected_created in (2, 0):
            profile, created = BankProfile(), 0
            for row in rows:
                clean, errors = profile.validate(row, mapping)
                assert not errors, errors
                if not profile.is_duplicate(clean):
                    profile.create(clean)
                    created += 1
            assert created == expected_created
        assert BankTransaktion.objects.count() == 2


# --- Export ---

def test_csv_export_escapes_formulas_and_hides_patient_names(in_alpha):
    services.create_buchung(TODAY, "ausgabe", kat(), 1234, "=HYPERLINK(1)", "+Lieferant")
    inv = issued(5500, last="Geheim")
    billing.add_payment(inv, TODAY, 5500)
    text = services.journal_csv(TODAY.year).decode("utf-8-sig")
    assert "'=HYPERLINK(1)" in text and "'+Lieferant" in text
    assert "12,34" in text and "55,00" in text and "Geheim" not in text and "Çelik" not in text


def test_zip_export_contains_decrypted_beleg(in_alpha):
    b = services.create_buchung(TODAY, "ausgabe", kat(), 100)
    beleg = services.store_beleg(SimpleUploadedFile("a.pdf", PDF))
    services.attach_beleg(beleg, b)
    archive = zipfile.ZipFile(io.BytesIO(services.export_zip(TODAY.year)))
    assert f"journal_{TODAY.year}.csv" in archive.namelist()
    assert archive.read(f"belege/{beleg.number}.pdf") == PDF


# --- HTTP / Mandantentrennung ---

def test_http_booking_flow_and_audit(client_alpha, alpha):
    client_alpha.get("/buchhaltung/")
    with tenancy.tenant_context(alpha):
        cat = kat().pk
    r = client_alpha.post("/buchhaltung/neu/", {"date": TODAY.isoformat(), "kind": "ausgabe", "category": cat, "amount": "12,34",
                                                "text": "Material", "beleg": SimpleUploadedFile("b.pdf", PDF)})
    assert r.status_code == 302
    page = client_alpha.get(r["Location"]).content.decode()
    assert "12,34 EUR" in page and "B20" in page
    with tenancy.tenant_context(alpha):
        beleg = Beleg.objects.get()
        assert client_alpha.get(f"/buchhaltung/beleg/{beleg.pk}/").content == PDF
        assert verify_chain()[0]
    assert client_alpha.get(f"/buchhaltung/export/?jahr={TODAY.year}").status_code == 200
    assert client_alpha.get(f"/buchhaltung/export/?jahr={TODAY.year}&format=zip").status_code == 200
    assert "Einnahmen" in client_alpha.get("/buchhaltung/auswertung/").content.decode()


def test_other_tenant_cannot_access_bookkeeping(client_beta, alpha, beta):
    with tenancy.tenant_context(alpha):
        b = services.create_buchung(TODAY, "ausgabe", kat(), 100)
        beleg = services.store_beleg(SimpleUploadedFile("a.pdf", PDF))
        services.attach_beleg(beleg, b)
        t = tx(-100, "x")
        bid, beid, tid = b.pk, beleg.pk, t.pk
    for method, path in (("get", f"/buchhaltung/buchung/{bid}/"), ("post", f"/buchhaltung/buchung/{bid}/storno/"),
                         ("get", f"/buchhaltung/beleg/{beid}/"), ("post", f"/buchhaltung/bank/{tid}/ignorieren/")):
        assert getattr(client_beta, method)(path).status_code == 404, path
    with tenancy.tenant_context(beta):
        assert Buchung.objects.count() == 0 and BankTransaktion.objects.count() == 0


def test_requires_login():
    from django.test import Client

    for path in ("/buchhaltung/", "/buchhaltung/bank/", "/buchhaltung/export/", "/buchhaltung/beleg/1/"):
        assert Client().get(path).status_code == 302
