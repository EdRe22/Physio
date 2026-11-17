from django.core.management.base import BaseCommand

from core import tenancy
from core.models import Tenant
from rechnungen.mailing import send_auto_reminders


class Command(BaseCommand):
    help = "Erstellt und versendet automatische Zahlungserinnerungen/1. Mahnungen (nur Mandanten mit aktivierter Einstellung; täglich)."

    def handle(self, *args, **opts):
        actor = tenancy.Actor(user_id=None, label="system:auto_reminders")
        for tenant in Tenant.objects.filter(is_active=True):
            with tenancy.tenant_context(tenant, actor):
                counts = send_auto_reminders()
            self.stdout.write(f"{tenant.slug}: {counts}")
