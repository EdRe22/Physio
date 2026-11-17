"""Ausscheiden eines Therapeuten / Praxisende: Sperren, Aufbewahrungsfrist, Löschung.

Ablauf:
1. Der Therapeut zieht seinen Gesamtexport (Patientenübergabe, eigene Kopie).
2. `offboard`: Konto und Mandant werden gesperrt, das Fristende wird berechnet. Die Daten bleiben verschlüsselt liegen.
3. Nach Fristende `purge`: Datenbank, Belege und der Datenschlüssel werden vernichtet (Crypto-Shredding).
   Ältere Sicherungen enthalten den (mit dem Master-Schlüssel geschützten) Datenschlüssel noch. Die Löschung ist
   erst vollständig, wenn diese Sicherungen turnusgemäß ausgetauscht wurden (Aufbewahrungsdauer der Sicherungen dokumentieren).

Fristen (VOR Echtbetrieb rechtlich bestätigen lassen):
- Dokumentation: 10 Jahre ab dem letzten Eintrag (Annahme MTD-Gesetz, nicht verifiziert)
- Honorarnoten und Buchhaltung: 7 Jahre (BAO § 132)
"""
import shutil
from datetime import date
from pathlib import Path

from django.conf import settings
from django.db import connections, transaction

from accounts.models import Therapist
from core import tenancy
from rechnungen.services import BillingError

from .models import Mietverhaeltnis

RETENTION_DOC_YEARS = 10
RETENTION_TAX_YEARS = 7


class OffboardingError(BillingError):
    pass


def add_years(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 29. Februar
        return day.replace(year=day.year + years, day=28)


def retention_end(today: date) -> date:
    """Fristende aus den Daten des aktiven Mandanten (der spätere von Dokumentation, Honorarnoten, Buchungen)."""
    from buchhaltung.models import Buchung
    from patients.models import Eintrag
    from rechnungen.models import Honorarnote

    ends = [today]
    last_doc = Eintrag.objects.order_by("-date").values_list("date", flat=True).first()
    if last_doc:
        ends.append(add_years(last_doc, RETENTION_DOC_YEARS))
    last_invoice = Honorarnote.objects.exclude(issue_date__isnull=True).order_by("-issue_date").values_list("issue_date", flat=True).first()
    if last_invoice:
        ends.append(add_years(last_invoice, RETENTION_TAX_YEARS))
    last_booking = Buchung.objects.order_by("-date").values_list("date", flat=True).first()
    if last_booking:
        ends.append(add_years(last_booking, RETENTION_TAX_YEARS))
    return max(ends)


def offboard(tenant, today: date, export_confirmed: bool) -> date:
    """Sperrt Mandant und Benutzer, beendet Mietverhältnisse und setzt das Fristende. Aufruf mit entsperrtem Schlüssel."""
    if not export_confirmed:
        raise OffboardingError("Bitte zuerst bestätigen, dass der Therapeut seinen Gesamtexport erhalten hat.")
    if tenant.offboarded_on:
        raise OffboardingError("Dieser Mandant wurde bereits ausgeschieden.")
    with tenancy.tenant_context(tenant, tenancy.Actor(user_id=None, label="system:offboarding")):
        end = retention_end(today)
    with transaction.atomic():
        tenant.is_active = False
        tenant.offboarded_on, tenant.retain_until = today, end
        tenant.save(update_fields=["is_active", "offboarded_on", "retain_until"])
        Therapist.objects.filter(tenant=tenant).update(is_active=False)
        for lease in Mietverhaeltnis.objects.filter(tenant=tenant).filter(valid_until__isnull=True):
            lease.valid_until = today
            lease.save()
    return end


def purge(tenant, today: date) -> None:
    """Vernichtet die Daten eines ausgeschiedenen Mandanten nach Fristende. Nicht rückgängig zu machen."""
    if tenant.purged_on:
        raise OffboardingError("Bereits gelöscht.")
    if not tenant.offboarded_on or not tenant.retain_until:
        raise OffboardingError("Der Mandant ist nicht ausgeschieden.")
    if today < tenant.retain_until:
        raise OffboardingError(f"Aufbewahrungsfrist läuft bis {tenant.retain_until:%d.%m.%Y}.")
    alias = tenancy.register_db(tenant.slug)
    cfg = connections.databases[alias]
    connections[alias].close()
    if "sqlite3" in cfg["ENGINE"]:
        path = Path(str(cfg["NAME"]))
        for suffix in ("", "-wal", "-shm", "-journal"):
            Path(str(path) + suffix).unlink(missing_ok=True)
    elif "postgresql" in cfg["ENGINE"]:
        with connections["default"].cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{cfg["NAME"]}"')
    shutil.rmtree(Path(settings.BELEG_DIR) / tenant.slug, ignore_errors=True)
    connections.databases.pop(alias, None)
    tenant.wrapped_key = ""  # Crypto-Shredding: ohne diesen Schlüssel sind übrig gebliebene Reste unlesbar
    tenant.purged_on = today
    tenant.save(update_fields=["wrapped_key", "purged_on"])
