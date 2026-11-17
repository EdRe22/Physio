"""Verwaltungsbereich für den Eigentümer. Enthält nur Konten-, Einrichtungs- und Systemfunktionen, nie Patientendaten."""
import re
from dataclasses import dataclass
from datetime import date

from django.conf import settings
from django.db import connections

from accounts.models import Therapist
from core import keystore, tenancy
from core.models import Tenant


@dataclass
class Step:
    title: str
    detail: str
    done: bool
    url_name: str = ""
    action: str = "Einrichten"


def slug_from_username(username: str) -> str:
    slug = re.sub(r"[^a-z0-9_]", "_", username.lower())
    if not slug[:1].isalpha():
        slug = "t" + slug
    return slug[:32]


def active_colleagues():
    """Aktive Konten der Kollegen (ohne den Eigentümer)."""
    return Therapist.objects.filter(is_active=True, tenant__is_active=True, tenant__purged_on__isnull=True).exclude(role=Therapist.Role.OWNER)


def setup_steps(request_user) -> list[Step]:
    """Prüfliste für die Einrichtung der Praxis. Liest nur Praxisdaten und das Rechnungsprofil des Eigentümers."""
    from praxis import services as rent
    from praxis.models import PraxisProfil
    from raeume.models import Raum
    from rechnungen import services as billing

    today = date.today()
    profile = PraxisProfil.objects.first()
    colleagues = list(active_colleagues().select_related("tenant"))
    without_lease = [c.tenant.name for c in colleagues if rent.lease_for(c.tenant, today.year, today.month) is None]
    invoice_profile = billing.get_profile()
    active_total = len(colleagues) + 1

    return [
        Step("Praxisprofil für Mietabrechnungen", "Name, Anschrift, IBAN und der vom Steuerberater bestätigte Steuerhinweis.",
             bool(profile and profile.ready), "rent_profile"),
        Step("Eigenes Rechnungsprofil", "Damit Sie selbst Honorarnoten ausstellen können (Steuerhinweis bestätigt).",
             bool(invoice_profile and invoice_profile.ready_to_issue), "profile_edit"),
        Step("Räume anlegen", "Mindestens ein aktiver Behandlungsraum für den Raumplan.",
             Raum.objects.filter(active=True).exists(), "raeume_manage"),
        Step("Kollegen anlegen", "Für jeden Kollegen ein Konto mit eigener, getrennter Datenbank.",
             bool(colleagues), "verwaltung_user_new", "Therapeut anlegen"),
        Step("Mietverhältnisse", ("Für jeden Kollegen ist ein gültiges Mietverhältnis hinterlegt." if colleagues and not without_lease
                                 else ("Es fehlt ein Mietverhältnis für: " + ", ".join(without_lease)) if without_lease
                                 else "Sobald Kollegen angelegt sind, hier die monatliche Miete festlegen."),
             bool(colleagues) and not without_lease, "lease_list"),
        Step("Notfallvertretung möglich", "Der Notfallzugriff braucht mindestens drei aktive Therapeuten (Vier-Augen-Prinzip).",
             active_total >= 3, "verwaltung_users", "Konten ansehen"),
    ]


def system_status() -> list[tuple[str, str, str]]:
    """Zeilen (Bezeichnung, Wert, Ampel: ok/warn/bad) für die Systemübersicht."""
    engine = connections.databases["default"]["ENGINE"]
    rows = []
    if settings.PHYSIO_KEYFILE:
        rows.append(("Master-Schlüssel", "entsperrt" if keystore.is_unlocked() else "GESPERRT", "ok" if keystore.is_unlocked() else "bad"))
    else:
        rows.append(("Master-Schlüssel", "keine Schlüsseldatei (nur Entwicklung)", "warn" if settings.DEV else "bad"))
    rows.append(("Betriebsart", "Entwicklung (Fake-Daten)" if settings.DEV else "Produktion", "warn" if settings.DEV else "ok"))
    rows.append(("Datenbank", "PostgreSQL" if "postgresql" in engine else "SQLite", "ok" if "postgresql" in engine else "warn"))
    rows.append(("Zeitgesteuerte Aufgaben", "Zugangswort gesetzt" if len(settings.PHYSIO_JOB_TOKEN) >= 32 else "nicht eingerichtet",
                 "ok" if len(settings.PHYSIO_JOB_TOKEN) >= 32 else "warn"))
    rows.append(("Mandanten", f"{Tenant.objects.filter(is_active=True).count()} aktiv, "
                              f"{Tenant.objects.filter(is_active=False, purged_on__isnull=True).count()} ausgeschieden (Aufbewahrung)", "ok"))
    return rows


def verify_all_audit_chains(actor) -> tuple[int, list[str]]:
    """Prüft das Protokoll jedes aktiven Mandanten. Rückgabe: (Anzahl geprüft, Namen mit Fehlern)."""
    from audit.models import verify_chain

    bad, checked = [], 0
    for tenant in Tenant.objects.filter(is_active=True, purged_on__isnull=True):
        with tenancy.tenant_context(tenant, actor):
            ok, _ = verify_chain()
        checked += 1
        if not ok:
            bad.append(tenant.name)
    return checked, bad
