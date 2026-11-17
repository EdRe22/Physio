"""Anlegen eines Mandanten samt eigener Datenbank. Gemeinsam genutzt von Kommandozeile und Verwaltungsbereich."""
from django.core.management import call_command
from django.db import connection, connections

from core import tenancy
from core.models import Tenant


class ProvisionError(Exception):
    pass


def create_tenant_database(slug: str) -> str:
    """Macht die Datenbank ansprechbar und legt sie bei PostgreSQL an. Rückgabe: Alias."""
    alias = tenancy.register_db(slug)
    if "postgresql" in connections.databases[alias]["ENGINE"]:
        with connection.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{tenancy.db_name_for(slug)}"')
    return alias


def create_tenant(slug: str, name: str) -> Tenant:
    if Tenant.objects.filter(slug=slug).exists():
        raise ProvisionError("Dieses Kürzel ist bereits vergeben.")
    try:
        alias = create_tenant_database(slug)
        tenant = Tenant.provision(slug, name)
        call_command("migrate", database=alias, verbosity=0)
    except ProvisionError:
        raise
    except Exception as exc:  # z. B. fehlende Datenbankrechte; Details gehören ins Serverprotokoll, nicht in die Oberfläche
        raise ProvisionError("Die Datenbank des Mandanten konnte nicht angelegt werden. Bitte die technische Betreuung informieren "
                             f"(Fehlerart: {type(exc).__name__}).") from exc
    return tenant
