import io
import json
import zipfile
from datetime import date, timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connections
from django.utils import timezone
from openpyxl import Workbook

from audit.models import AuditEntry, verify_chain
from core import tenancy
from importer.models import ImportJob
from patients.models import Eintrag, Patient

HEADER = ["Nachname", "Vorname", "Geb.datum", "SV-Nr", "Kasse", "Adresse", "Tel", "Mail"]


def xlsx(rows, header=HEADER, name="daten.xlsx"):
    wb = Workbook()
    ws = wb.active
    ws.append(header)
    for row in rows:
        ws.append(row)
    out = io.BytesIO()
    wb.save(out)
    return SimpleUploadedFile(name, out.getvalue())


def upload(client, file, profile="patients"):
    response = client.post("/import/", {"profile": profile, "file": file})
    return response


def job_id(response):
    assert response.status_code == 302, response.content.decode()[:500]
    return int(response["Location"].strip("/").split("/")[-2])


def map_columns(client, jid, mapping):
    return client.post(f"/import/{jid}/zuordnung/", {k: str(v) for k, v in mapping.items()})


PATIENT_MAP = {"last_name": 0, "first_name": 1, "birth_date": 2, "insurance_no": 3, "insurer": 4,
               "address": 5, "phone": 6, "email": 7}

GOOD_ROWS = [
    ["Muster", "Maria", date(1980, 12, 24), "1234 241280", "ÖGK", "Teststraße 1", 6601234567, "maria@example.org"],
    ["Beispiel", "Hans", "05.03.1975", "", "SVS", "", "", ""],
]


# --- Ablauf ---

def test_full_flow_patients(client_alpha, alpha):
    jid = job_id(upload(client_alpha, xlsx(GOOD_ROWS)))
    page = client_alpha.get(f"/import/{jid}/zuordnung/").content.decode()
    assert "Nachname" in page and "selected" in page  # Vorschlag aus den Überschriften
    assert map_columns(client_alpha, jid, PATIENT_MAP).status_code == 302
    with tenancy.tenant_context(alpha):
        assert Patient.objects.count() == 0  # Trockenlauf schreibt nichts
        job = ImportJob.objects.get(pk=jid)
        assert job.status == "checked" and json.loads(job.report)["valid"] == 2
    assert client_alpha.post(f"/import/{jid}/ausfuehren/").status_code == 302
    with tenancy.tenant_context(alpha):
        patients = {p.last_name: p for p in Patient.objects.all()}
        assert set(patients) == {"Muster", "Beispiel"}
        assert patients["Muster"].birth_date == "1980-12-24"
        assert patients["Muster"].phone == "6601234567"  # Zahl ohne ".0"
        assert patients["Beispiel"].birth_date == "1975-03-05"
        job = ImportJob.objects.get(pk=jid)
        assert job.status == "done" and job.payload == "" and job.mapping == ""
        assert json.loads(job.report)["imported"] == 2
        assert AuditEntry.objects.filter(action__in=["import.upload", "import.run"]).count() == 2
        assert verify_chain()[0]


def test_run_twice_does_not_import_twice(client_alpha, alpha):
    jid = job_id(upload(client_alpha, xlsx(GOOD_ROWS)))
    map_columns(client_alpha, jid, PATIENT_MAP)
    client_alpha.post(f"/import/{jid}/ausfuehren/")
    client_alpha.post(f"/import/{jid}/ausfuehren/")
    with tenancy.tenant_context(alpha):
        assert Patient.objects.count() == 2


def test_duplicates_in_file_and_existing_are_skipped(client_alpha, alpha):
    with tenancy.tenant_context(alpha):
        Patient.objects.create(first_name="maria", last_name="MUSTER", birth_date="1980-12-24")
    rows = GOOD_ROWS + [GOOD_ROWS[1]]  # Hans doppelt in der Datei
    jid = job_id(upload(client_alpha, xlsx(rows)))
    map_columns(client_alpha, jid, PATIENT_MAP)
    with tenancy.tenant_context(alpha):
        report = json.loads(ImportJob.objects.get(pk=jid).report)
    assert (report["valid"], report["duplicates"], report["invalid"]) == (1, 2, 0)


def test_invalid_rows_reported_without_values(client_alpha, alpha):
    rows = [
        ["Fehler", "Franz", "31.02.1980", "", "", "", "", "kein-mail"],
        ["", "OhneNachname", "", "", "", "", "", ""],
        ["Zeitreise", "Zora", "01.01.2999", "", "", "", "", ""],
        GOOD_ROWS[0],
    ]
    jid = job_id(upload(client_alpha, xlsx(rows)))
    map_columns(client_alpha, jid, PATIENT_MAP)
    with tenancy.tenant_context(alpha):
        raw = ImportJob.objects.get(pk=jid).report
    report = json.loads(raw)
    assert (report["valid"], report["invalid"]) == (1, 3)
    assert [e["row"] for e in report["errors"]] == [2, 3, 4]
    for value in ("Fehler", "Franz", "kein-mail", "OhneNachname", "Zeitreise", "Zora", "1980", "2999"):
        assert value not in raw  # Protokoll enthält nie Zellwerte
    page = client_alpha.get(f"/import/{jid}/vorschau/").content.decode()
    assert "Zeile 2" in page and "Franz" not in page


def test_only_valid_rows_are_imported(client_alpha, alpha):
    rows = [["Fehler", "Franz", "31.02.1980", "", "", "", "", ""], GOOD_ROWS[0]]
    jid = job_id(upload(client_alpha, xlsx(rows)))
    map_columns(client_alpha, jid, PATIENT_MAP)
    client_alpha.post(f"/import/{jid}/ausfuehren/")
    with tenancy.tenant_context(alpha):
        assert [p.last_name for p in Patient.objects.all()] == ["Muster"]


def test_cancel_wipes_data(client_alpha, alpha):
    jid = job_id(upload(client_alpha, xlsx(GOOD_ROWS)))
    map_columns(client_alpha, jid, PATIENT_MAP)
    assert client_alpha.post(f"/import/{jid}/abbrechen/").status_code == 302
    with tenancy.tenant_context(alpha):
        job = ImportJob.objects.get(pk=jid)
        assert job.status == "cancelled" and job.payload == ""
        assert Patient.objects.count() == 0
    assert client_alpha.post(f"/import/{jid}/ausfuehren/").status_code == 302  # nichts passiert
    with tenancy.tenant_context(alpha):
        assert Patient.objects.count() == 0


def test_mapping_validation(client_alpha):
    jid = job_id(upload(client_alpha, xlsx(GOOD_ROWS)))
    assert map_columns(client_alpha, jid, {"first_name": 1}).status_code == 200  # Nachname fehlt
    assert map_columns(client_alpha, jid, {"last_name": 0, "first_name": 0}).status_code == 200  # Spalte doppelt
    assert map_columns(client_alpha, jid, {"last_name": 0, "first_name": 99}).status_code == 200  # ungültiger Index


# --- Datenschutz ---

def test_payload_is_encrypted_at_rest_and_file_not_stored(client_alpha, alpha):
    jid = job_id(upload(client_alpha, xlsx(GOOD_ROWS)))
    with connections["t_alpha"].cursor() as cur:
        cur.execute("select payload from importer_importjob where id=%s", [jid])
        raw = cur.fetchone()[0]
    assert raw.startswith("v1:") and "Muster" not in raw
    with connections["t_alpha"].cursor() as cur:
        cur.execute("select name from sqlite_master where type='table' and name like '%file%'")
        assert cur.fetchall() == []


def test_uploads_never_use_temp_files(settings):
    assert settings.FILE_UPLOAD_HANDLERS == ["django.core.files.uploadhandler.MemoryFileUploadHandler"]


def test_purge_command_discards_stale_jobs(client_alpha, alpha):
    jid = job_id(upload(client_alpha, xlsx(GOOD_ROWS)))
    with tenancy.tenant_context(alpha):
        ImportJob.objects.filter(pk=jid).update(created_at=timezone.now() - timedelta(hours=30))
    call_command("purge_imports")
    with tenancy.tenant_context(alpha):
        job = ImportJob.objects.get(pk=jid)
        assert job.status == "cancelled" and job.payload == ""


# --- Dokumentation importieren ---

ENTRY_HEADER = ["Nachname", "Vorname", "Geburtsdatum", "Datum", "Art", "Text"]
ENTRY_MAP = {"last_name": 0, "first_name": 1, "birth_date": 2, "date": 3, "kind": 4, "text": 5}


def test_entries_import_links_to_patients_and_creates_versions(client_alpha, alpha):
    with tenancy.tenant_context(alpha):
        Patient.objects.create(first_name="Maria", last_name="Muster", birth_date="1980-12-24")
    rows = [
        ["Muster", "Maria", "24.12.1980", "10.01.2025", "Behandlung", "Mobilisation"],
        ["Muster", "Maria", "24.12.1980", "17.01.2025", "", "Kontrolle"],  # Art leer = Verlauf
        ["Unbekannt", "Uwe", "", "10.01.2025", "Befund", "x"],
        ["Muster", "Maria", "24.12.1980", "10.01.2025", "Quatsch", "x"],
    ]
    jid = job_id(upload(client_alpha, xlsx(rows, ENTRY_HEADER), profile="entries"))
    map_columns(client_alpha, jid, ENTRY_MAP)
    client_alpha.post(f"/import/{jid}/ausfuehren/")
    with tenancy.tenant_context(alpha):
        entries = {e.date.isoformat(): e for e in Eintrag.objects.all()}
        assert set(entries) == {"2025-01-10", "2025-01-17"}
        assert entries["2025-01-10"].kind == "behandlung" and entries["2025-01-10"].current.text == "Mobilisation"
        assert entries["2025-01-17"].kind == "verlauf"
        report = json.loads(ImportJob.objects.get(pk=jid).report)
        assert (report["imported"], report["invalid"]) == (2, 2)
        assert entries["2025-01-10"].referral_id is None  # verbraucht keine Einheiten


def test_entries_import_is_idempotent_and_detects_ambiguity(client_alpha, alpha):
    with tenancy.tenant_context(alpha):
        Patient.objects.create(first_name="Maria", last_name="Muster", birth_date="1980-12-24")
        Patient.objects.create(first_name="Maria", last_name="Muster", birth_date="1990-01-01")
    row = ["Muster", "Maria", "24.12.1980", "10.01.2025", "Verlauf", "Text"]
    ambiguous = ["Muster", "Maria", "", "10.01.2025", "Verlauf", "Text"]
    for expected in (1, 1):  # zweiter Lauf legt nichts doppelt an
        jid = job_id(upload(client_alpha, xlsx([row, ambiguous], ENTRY_HEADER), profile="entries"))
        map_columns(client_alpha, jid, ENTRY_MAP)
        client_alpha.post(f"/import/{jid}/ausfuehren/")
        with tenancy.tenant_context(alpha):
            assert Eintrag.objects.count() == expected


# --- Dateigrenzen ---

def test_rejects_wrong_extension_corrupt_and_empty_files(client_alpha):
    assert upload(client_alpha, SimpleUploadedFile("daten.txt", b"abc")).status_code == 200
    assert upload(client_alpha, SimpleUploadedFile("daten.xlsx", b"kein zip")).status_code == 200
    assert upload(client_alpha, xlsx([])).status_code == 200  # nur Kopfzeile
    assert upload(client_alpha, SimpleUploadedFile("leer.csv", b"")).status_code == 200


def test_rejects_too_large_and_zip_bomb(client_alpha):
    assert upload(client_alpha, SimpleUploadedFile("gross.csv", b"a;b\n" + b"x;y\n" * (5 * 1024 * 256))).status_code == 200
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/payload.bin", b"\0" * (60 * 1024 * 1024))
    assert len(bomb.getvalue()) < 5 * 1024 * 1024
    response = upload(client_alpha, SimpleUploadedFile("bombe.xlsx", bomb.getvalue()))
    assert response.status_code == 200 and "entpackt" in response.content.decode()


def test_rejects_too_many_rows(client_alpha):
    csv = "Nachname;Vorname\n" + "\n".join(f"N{i};V{i}" for i in range(5001))
    assert upload(client_alpha, SimpleUploadedFile("viele.csv", csv.encode())).status_code == 200


def test_csv_with_semicolons_and_cp1252(client_alpha, alpha):
    csv = "Nachname;Vorname;Geburtsdatum\nMüller;Jörg;01.02.1970\n".encode("cp1252")
    jid = job_id(upload(client_alpha, SimpleUploadedFile("daten.csv", csv)))
    map_columns(client_alpha, jid, {"last_name": 0, "first_name": 1, "birth_date": 2})
    client_alpha.post(f"/import/{jid}/ausfuehren/")
    with tenancy.tenant_context(alpha):
        p = Patient.objects.get()
        assert (p.last_name, p.first_name, p.birth_date) == ("Müller", "Jörg", "1970-02-01")


def test_formulas_are_not_evaluated_and_html_is_escaped(client_alpha, alpha):
    rows = [
        ["=HYPERLINK(\"http://evil\")", "Formel", "", "", "", "", "", ""],  # Formel: kein Wert, Zeile wird abgelehnt
        ["<script>alert(1)</script>", "Skript", "", "", "", "", "", ""],
    ]
    jid = job_id(upload(client_alpha, xlsx(rows)))
    map_columns(client_alpha, jid, PATIENT_MAP)
    with tenancy.tenant_context(alpha):
        report = json.loads(ImportJob.objects.get(pk=jid).report)
    assert (report["valid"], report["invalid"]) == (1, 1)
    client_alpha.post(f"/import/{jid}/ausfuehren/")
    body = client_alpha.get("/patients/").content.decode()
    assert "<script>alert(1)" not in body and "&lt;script&gt;" in body


# --- Mandantentrennung und Zugriff ---

def test_other_tenant_cannot_see_or_run_foreign_import(client_alpha, client_beta, alpha, beta):
    jid = job_id(upload(client_alpha, xlsx(GOOD_ROWS)))
    map_columns(client_alpha, jid, PATIENT_MAP)
    with tenancy.tenant_context(beta):
        assert ImportJob.objects.count() == 0
    for method, path in (("get", f"/import/{jid}/vorschau/"), ("get", f"/import/{jid}/zuordnung/"),
                         ("post", f"/import/{jid}/ausfuehren/"), ("post", f"/import/{jid}/abbrechen/")):
        assert getattr(client_beta, method)(path).status_code == 404
    with tenancy.tenant_context(alpha):
        assert ImportJob.objects.get(pk=jid).status == "checked"
        assert Patient.objects.count() == 0


def test_import_writes_only_into_own_database(client_beta, alpha, beta):
    jid = job_id(upload(client_beta, xlsx(GOOD_ROWS)))
    map_columns(client_beta, jid, PATIENT_MAP)
    client_beta.post(f"/import/{jid}/ausfuehren/")
    with tenancy.tenant_context(alpha):
        assert Patient.objects.count() == 0
    with tenancy.tenant_context(beta):
        assert Patient.objects.count() == 2


def test_requires_login_and_template_download(client_alpha):
    from django.test import Client

    assert Client().get("/import/").status_code == 302
    response = client_alpha.get("/import/vorlage/patients/")
    assert response.status_code == 200 and response["Content-Type"].endswith("spreadsheetml.sheet")
    from openpyxl import load_workbook

    header = [c.value for c in next(load_workbook(io.BytesIO(response.content)).active.iter_rows())]
    assert header[:2] == ["Vorname", "Nachname"]
    assert client_alpha.get("/import/vorlage/unbekannt/").status_code == 302


def test_importjob_protocol_cannot_be_deleted(in_alpha):
    job = ImportJob.objects.create(profile="patients")
    with pytest.raises(RuntimeError):
        job.delete()
