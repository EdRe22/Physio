from django.core.management.base import BaseCommand, CommandError

from audit.models import verify_chain
from core import tenancy
from core.models import Tenant


class Command(BaseCommand):
    help = "Prüft die Hash-Kette des Audit-Logs aller Mandanten (z. B. nach Wiederherstellung oder per Cron). Exit-Code 1 bei Bruch."

    def handle(self, *args, **opts):
        broken = []
        for tenant in Tenant.objects.all().order_by("slug"):
            with tenancy.tenant_context(tenant):
                ok, detail = verify_chain()
            self.stdout.write(f"{tenant.slug}: {'OK' if ok else 'KETTE GEBROCHEN'}")
            if not ok:
                broken.append(tenant.slug)
        if broken:
            raise CommandError("Audit-Kette gebrochen bei: " + ", ".join(broken))
