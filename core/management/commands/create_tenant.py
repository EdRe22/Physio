from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, connections

from core import tenancy
from core.models import Tenant


class Command(BaseCommand):
    help = "Legt einen Mandanten (Therapeut) mit eigener Datenbank und eigenem Schlüssel an."

    def add_arguments(self, parser):
        parser.add_argument("slug")
        parser.add_argument("name")

    def handle(self, *args, slug, name, **opts):
        if Tenant.objects.filter(slug=slug).exists():
            raise CommandError("Mandant existiert bereits.")
        alias = tenancy.register_db(slug)
        engine = connections.databases[alias]["ENGINE"]
        if "postgresql" in engine:
            with connection.cursor() as cur:
                cur.execute(f'CREATE DATABASE "{tenancy.db_name_for(slug)}"')
        Tenant.provision(slug, name)
        call_command("migrate", database=alias, verbosity=0)
        self.stdout.write(self.style.SUCCESS(f"Mandant {slug} angelegt (Datenbank {alias})."))
