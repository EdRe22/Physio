from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core import tenancy
from core.models import Tenant
from importer.models import ImportJob


class Command(BaseCommand):
    help = "Verwirft liegengebliebene Import-Zwischendaten (älter als 24 Stunden) in allen Mandanten."

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=int, default=24)

    def handle(self, *args, hours, **opts):
        cutoff = timezone.now() - timedelta(hours=hours)
        actor = tenancy.Actor(user_id=None, label="system:purge_imports")
        for tenant in Tenant.objects.all():
            with tenancy.tenant_context(tenant, actor):
                stale = ImportJob.objects.filter(
                    status__in=[ImportJob.Status.UPLOADED, ImportJob.Status.CHECKED], created_at__lt=cutoff
                )
                count = 0
                for job in stale:
                    job.status = ImportJob.Status.CANCELLED
                    job.finished_at = timezone.now()
                    job.wipe()
                    job.save()
                    count += 1
            self.stdout.write(f"{tenant.slug}: {count} verworfen")
