from django.core.management import call_command
from django.core.management.base import BaseCommand

from core import tenancy
from core.models import Tenant


class Command(BaseCommand):
    help = "Wendet Migrationen auf alle Mandanten-Datenbanken an (nach jedem Release)."

    def handle(self, *args, **opts):
        for tenant in Tenant.objects.all():
            alias = tenancy.register_db(tenant.slug)
            call_command("migrate", database=alias, verbosity=0)
            self.stdout.write(f"{alias}: ok")
