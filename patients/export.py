"""Datenexport: Kopie der Patientendaten (Art. 15/20 DSGVO), Patientenübergabe und Gesamtexport beim Praxisende.

Enthält Gesundheitsdaten im Klartext. Aufrufer protokollieren im Audit-Log und liefern mit `Cache-Control: no-store`
aus. Dateinamen im Archiv enthalten nur IDs, keine Namen.
"""
import io
import json
import zipfile

from .models import Patient


def _d(value):
    return value.isoformat() if value else None


def patient_bundle(patient) -> dict:
    return {
        "patient": {
            "id": patient.pk, "vorname": patient.first_name, "nachname": patient.last_name,
            "geburtsdatum": patient.birth_date, "versicherungsnummer": patient.insurance_no, "versicherung": patient.insurer,
            "adresse": patient.address, "telefon": patient.phone, "email": patient.email,
            "archiviert_am": _d(patient.archived_at), "angelegt_am": _d(patient.created_at),
        },
        "einwilligungen": [
            {"art": c.kind, "erteilt_am": _d(c.granted_at), "widerrufen_am": _d(c.revoked_at)} for c in patient.consents.all()
        ],
        "zuweisungen": [
            {"id": r.pk, "arzt": r.doctor, "diagnose": r.diagnosis, "einheiten": r.units_total,
             "gueltig_ab": _d(r.valid_from), "gueltig_bis": _d(r.valid_until)}
            for r in patient.referrals.all()
        ],
        "dokumentation": [
            {"id": e.pk, "art": e.kind, "datum": _d(e.date), "zuweisung": e.referral_id,
             "versionen": [{"version": v.version, "text": v.text, "erstellt": _d(v.created_at)} for v in e.versions.all()]}
            for e in patient.entries.order_by("date", "id")
        ],
        "honorarnoten": [
            {"nummer": i.number, "art": i.kind, "status": i.status, "betrag_cent": i.total_cents}
            for i in patient.invoices.exclude(number__isnull=True)
        ],
    }


def patient_text(bundle: dict) -> str:
    p = bundle["patient"]
    lines = [f"PATIENT {p['nachname']} {p['vorname']} (ID {p['id']})", f"Geburtsdatum: {p['geburtsdatum']}",
             f"Versicherung: {p['versicherung']} {p['versicherungsnummer']}", f"Adresse: {p['adresse']}",
             f"Telefon: {p['telefon']}  E-Mail: {p['email']}", ""]
    for r in bundle["zuweisungen"]:
        lines.append(f"ZUWEISUNG {r['arzt']} | {r['diagnose']} | {r['einheiten']} Einheiten | ab {r['gueltig_ab']} bis {r['gueltig_bis']}")
    lines.append("")
    for e in bundle["dokumentation"]:
        lines.append(f"--- {e['art'].upper()} am {e['datum']} ---")
        for v in e["versionen"]:
            lines.append(f"[Version {v['version']}, {v['erstellt']}]\n{v['text']}\n")
    return "\n".join(lines)


def _add_patient(archive, folder, patient):
    bundle = patient_bundle(patient)
    archive.writestr(f"{folder}/patient.json", json.dumps(bundle, ensure_ascii=False, indent=1))
    archive.writestr(f"{folder}/patient.txt", patient_text(bundle))


def patient_zip(patient) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        _add_patient(archive, "patient", patient)
    return buffer.getvalue()


def tenant_zip() -> bytes:
    """Alle Patienten (auch archivierte) und alle ausgestellten Honorarnoten des aktiven Mandanten."""
    from rechnungen.models import Honorarnote
    from rechnungen.pdf import render_pdf

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for patient in Patient.objects.all().order_by("pk"):
            _add_patient(archive, f"patienten/{patient.pk}", patient)
        for invoice in Honorarnote.objects.exclude(number__isnull=True).order_by("number"):
            archive.writestr(f"honorarnoten/{invoice.number}.pdf", render_pdf(invoice))
        archive.writestr("LIESMICH.txt", "Gesamtexport. Enthält Gesundheitsdaten im Klartext: sicher aufbewahren, "
                                          "nicht per E-Mail versenden, nach der Übergabe sicher löschen.\n")
    return buffer.getvalue()
