"""Import-Profile: Feldliste, Zuordnungshilfe, Prüfung und Anlegen.

Alle Fehlermeldungen nennen nur das Feld, nie den Zellwert (kein Personenbezug im Protokoll).
"""
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.db import transaction

from core import tenancy
from patients.models import Eintrag, EintragVersion, Patient

MAX_FIELD = 500
MAX_TEXT = 20000


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    required: bool = False
    synonyms: tuple = ()


def _norm(text):
    return re.sub(r"[^a-z0-9]", "", text.casefold().replace("ä", "a").replace("ö", "o").replace("ü", "u").replace("ß", "ss"))


def parse_date(raw):
    raw = raw.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


PATIENT_FIELDS = [
    Field("first_name", "Vorname", True, ("vorname", "firstname")),
    Field("last_name", "Nachname", True, ("nachname", "familienname", "name", "lastname")),
    Field("birth_date", "Geburtsdatum", False, ("geburtsdatum", "geb", "gebdatum", "geburtstag")),
    Field("insurance_no", "Versicherungsnummer", False, ("versicherungsnummer", "svnr", "svnummer", "svnr")),
    Field("insurer", "Versicherung", False, ("versicherung", "kasse", "krankenkasse", "versicherungstraeger")),
    Field("address", "Adresse", False, ("adresse", "anschrift", "strasse")),
    Field("phone", "Telefon", False, ("telefon", "tel", "handy", "mobil")),
    Field("email", "E-Mail", False, ("email", "mail", "emailadresse")),
]

ENTRY_FIELDS = [
    Field("last_name", "Nachname", True, ("nachname", "familienname", "name", "lastname")),
    Field("first_name", "Vorname", True, ("vorname", "firstname")),
    Field("birth_date", "Geburtsdatum (zur Zuordnung)", False, ("geburtsdatum", "geb", "gebdatum")),
    Field("date", "Datum", True, ("datum", "behandlungsdatum", "date")),
    Field("kind", "Art (Anamnese/Befund/Behandlung/Verlauf)", False, ("art", "typ", "kategorie")),
    Field("text", "Text", True, ("text", "eintrag", "notiz", "verlauf", "dokumentation", "bemerkung")),
]

KIND_BY_NAME = {"anamnese": "anamnese", "befund": "befund", "behandlung": "behandlung", "verlauf": "verlauf"}


def suggest_mapping(fields, headers):
    """Ordnet Spaltenüberschriften den Feldern anhand von Synonymen zu (nur Vorschlag)."""
    normalized = [_norm(h) for h in headers]
    mapping, used = {}, set()
    for f in fields:
        wanted = {_norm(f.label), *map(_norm, f.synonyms)}
        for index, header in enumerate(normalized):
            if index not in used and header in wanted:
                mapping[f.key] = index
                used.add(index)
                break
    return mapping


@dataclass
class Report:
    total: int = 0
    valid: int = 0
    imported: int = 0
    duplicates: int = 0
    invalid: int = 0
    errors: list = field(default_factory=list)

    def error(self, row, message):
        self.invalid += 1
        if len(self.errors) < 200:
            self.errors.append({"row": row, "message": message})

    def as_json(self):
        return json.dumps(self.__dict__, ensure_ascii=False)


def _value(row, mapping, key):
    index = mapping.get(key)
    if index is None or index >= len(row):
        return ""
    return row[index].strip()


def _identity(last, first, birth):
    return (last.strip().casefold(), first.strip().casefold(), birth or "")


# --- Patientenstamm ---

class PatientProfile:
    name = "patients"
    title = "Patientenstamm"
    fields = PATIENT_FIELDS

    def __init__(self):
        self.seen = {_identity(p.last_name, p.first_name, p.birth_date) for p in Patient.objects.all()}

    def validate(self, row, mapping):
        clean, errors = {}, []
        for f in self.fields:
            value = _value(row, mapping, f.key)
            if f.required and not value:
                errors.append(f"{f.label} fehlt")
            elif len(value) > MAX_FIELD:
                errors.append(f"{f.label} ist zu lang")
            clean[f.key] = value
        if clean["birth_date"]:
            parsed = parse_date(clean["birth_date"])
            if parsed is None:
                errors.append("Geburtsdatum ungültig (TT.MM.JJJJ erwartet)")
            elif parsed > date.today():
                errors.append("Geburtsdatum liegt in der Zukunft")
            else:
                clean["birth_date"] = parsed.isoformat()
        if clean["email"]:
            try:
                validate_email(clean["email"])
            except ValidationError:
                errors.append("E-Mail ungültig")
        return clean, errors

    def is_duplicate(self, clean):
        key = _identity(clean["last_name"], clean["first_name"], clean["birth_date"])
        if key in self.seen:
            return True
        self.seen.add(key)
        return False

    def create(self, clean):
        Patient.objects.create(**clean)


# --- Dokumentation / Verlauf ---

class EntryProfile:
    name = "entries"
    title = "Dokumentation (Verlauf)"
    fields = ENTRY_FIELDS

    def __init__(self):
        self.by_identity, self.by_name = {}, {}
        for p in Patient.objects.all():
            self.by_identity.setdefault(_identity(p.last_name, p.first_name, p.birth_date), []).append(p.pk)
            self.by_name.setdefault(_identity(p.last_name, p.first_name, ""), []).append(p.pk)
        # Bereits vorhandene Einträge, damit ein wiederholter Import nichts doppelt anlegt
        latest = {}
        for version in EintragVersion.objects.select_related("entry").order_by("version"):
            latest[version.entry_id] = version
        self.seen = {
            (v.entry.patient_id, v.entry.date, v.entry.kind, v.text) for v in latest.values()
        }

    def validate(self, row, mapping):
        clean, errors = {}, []
        for f in self.fields:
            value = _value(row, mapping, f.key)
            limit = MAX_TEXT if f.key == "text" else MAX_FIELD
            if f.required and not value:
                errors.append(f"{f.label} fehlt")
            elif len(value) > limit:
                errors.append(f"{f.label} ist zu lang")
            clean[f.key] = value
        birth = ""
        if clean["birth_date"]:
            parsed = parse_date(clean["birth_date"])
            if parsed is None:
                errors.append("Geburtsdatum ungültig (TT.MM.JJJJ erwartet)")
            else:
                birth = parsed.isoformat()
        clean["birth_date"] = birth
        if clean["date"]:
            parsed = parse_date(clean["date"])
            if parsed is None:
                errors.append("Datum ungültig (TT.MM.JJJJ erwartet)")
            elif parsed > date.today():
                errors.append("Datum liegt in der Zukunft")
            else:
                clean["date"] = parsed
        kind = KIND_BY_NAME.get(clean["kind"].strip().casefold(), "") if clean["kind"] else "verlauf"
        if not kind:
            errors.append("Art unbekannt")
        clean["kind"] = kind
        if not errors:
            matches = (
                self.by_identity.get(_identity(clean["last_name"], clean["first_name"], birth), [])
                if birth else self.by_name.get(_identity(clean["last_name"], clean["first_name"], ""), [])
            )
            if len(matches) == 0:
                errors.append("Kein passender Patient gefunden")
            elif len(matches) > 1:
                errors.append("Patient nicht eindeutig (Geburtsdatum ergänzen)")
            else:
                clean["patient_id"] = matches[0]
        return clean, errors

    def is_duplicate(self, clean):
        key = (clean["patient_id"], clean["date"], clean["kind"], clean["text"])
        if key in self.seen:
            return True
        self.seen.add(key)
        return False

    def create(self, clean):
        entry = Eintrag.objects.create(patient_id=clean["patient_id"], kind=clean["kind"], date=clean["date"])
        entry.add_version(clean["text"], author_id=None)


PROFILES = {"patients": PatientProfile, "entries": EntryProfile}


def process(job, commit):
    """Prüft (commit=False) oder importiert (commit=True). Gleicher Code für beide Wege."""
    data = json.loads(job.payload)
    mapping = {k: int(v) for k, v in json.loads(job.mapping).items()}
    profile = PROFILES[job.profile]()
    report = Report(total=len(data["rows"]))
    pending = []
    for number, row in enumerate(data["rows"], start=2):  # Zeile 1 ist die Kopfzeile
        clean, errors = profile.validate(row, mapping)
        if errors:
            report.error(number, "; ".join(errors))
        elif profile.is_duplicate(clean):
            report.duplicates += 1
        else:
            report.valid += 1
            pending.append(clean)
    if commit:
        with transaction.atomic(using=tenancy.require().alias):
            for clean in pending:
                profile.create(clean)
        report.imported = len(pending)
    return report
