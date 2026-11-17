from django.core.management.base import BaseCommand

from core import tenancy
from core.models import Tenant
from termine.reminders import send_due_reminders


class Command(BaseCommand):
    help = "Sendet fällige Terminerinnerungen für alle aktiven Mandanten (täglich per Cron/systemd-Timer)."

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=int, default=36, help="Termine, die in den nächsten N Stunden beginnen")

    def handle(self, *args, hours, **opts):
        actor = tenancy.Actor(user_id=None, label="system:reminders")
        for tenant in Tenant.objects.filter(is_active=True):
            user = getattr(tenant, "user", None)
            with tenancy.tenant_context(tenant, actor):
                counts = send_due_reminders(tenant.name, reply_to=getattr(user, "email", "") or "", hours=hours)
            self.stdout.write(f"{tenant.slug}: {counts}")
