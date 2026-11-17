from datetime import date

from django.core.management.base import BaseCommand, CommandError

from core.models import Tenant
from praxis import offboarding

from ._keys import require_key


class Command(BaseCommand):
    help = "Therapeut scheidet aus: Konto und Mandant sperren, Mietverhältnis beenden, Aufbewahrungsfrist berechnen."

    def add_arguments(self, parser):
        parser.add_argument("slug")
        parser.add_argument("--export-confirmed", action="store_true",
                            help="Der Therapeut hat seinen Gesamtexport erhalten (Pflicht)")

    def handle(self, *args, slug, export_confirmed, **opts):
        try:
            tenant = Tenant.objects.get(slug=slug)
        except Tenant.DoesNotExist:
            raise CommandError("Mandant nicht gefunden.")
        require_key()
        try:
            end = offboarding.offboard(tenant, date.today(), export_confirmed)
        except offboarding.OffboardingError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(
            f"{slug} gesperrt. Aufbewahrung bis {end:%d.%m.%Y}, danach `purge_tenant {slug} --confirm {slug}`."))
