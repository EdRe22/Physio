from django.core.management.base import BaseCommand, CommandError

from core import provision


class Command(BaseCommand):
    help = "Legt einen Mandanten (Therapeut) mit eigener Datenbank und eigenem Schlüssel an."

    def add_arguments(self, parser):
        parser.add_argument("slug")
        parser.add_argument("name")

    def handle(self, *args, slug, name, **opts):
        try:
            provision.create_tenant(slug, name)
        except provision.ProvisionError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(f"Mandant {slug} angelegt (Datenbank t_{slug})."))
