from django.core.management.base import BaseCommand, CommandError

from accounts.models import Therapist
from core import tenancy
from praxis import services


class Command(BaseCommand):
    help = "Stellt die Mietabrechnungen eines Monats (JJJJ-MM) für alle Kollegen aus (z. B. per Cron am Monatsersten)."

    def add_arguments(self, parser):
        parser.add_argument("period")

    def handle(self, *args, period, **options):
        owner = Therapist.objects.filter(role="owner", tenant__isnull=False).select_related("tenant").first()
        if owner is None:
            raise CommandError("Kein Eigentümer mit Mandant vorhanden.")
        with tenancy.tenant_context(owner.tenant):
            created, skipped = services.issue_all(period)
        self.stdout.write(f"{len(created)} ausgestellt, {len(skipped)} übersprungen")
        for name, reason in skipped:
            self.stdout.write(f"  {name}: {reason}")
