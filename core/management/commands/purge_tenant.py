from datetime import date

from django.core.management.base import BaseCommand, CommandError

from core.models import Tenant
from praxis import offboarding


class Command(BaseCommand):
    help = "Löscht die Daten eines ausgeschiedenen Mandanten nach Fristende endgültig (Datenbank, Belege, Datenschlüssel)."

    def add_arguments(self, parser):
        parser.add_argument("slug")
        parser.add_argument("--confirm", required=True, help="Kürzel zur Bestätigung wiederholen")

    def handle(self, *args, slug, confirm, **opts):
        if slug != confirm:
            raise CommandError("Bestätigung stimmt nicht mit dem Kürzel überein.")
        try:
            tenant = Tenant.objects.get(slug=slug)
        except Tenant.DoesNotExist:
            raise CommandError("Mandant nicht gefunden.")
        try:
            offboarding.purge(tenant, date.today())
        except offboarding.OffboardingError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(
            f"{slug} gelöscht. Ältere Sicherungen enthalten die Daten noch bis zu ihrem turnusgemäßen Austausch."))
