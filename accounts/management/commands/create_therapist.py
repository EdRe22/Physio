import getpass

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from accounts.models import Therapist
from core.models import Tenant


class Command(BaseCommand):
    help = "Legt einen Therapeuten-Benutzer für einen bestehenden Mandanten an (Passwort wird abgefragt)."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("tenant_slug")
        parser.add_argument("--owner", action="store_true", help="Rolle Eigentümer")
        parser.add_argument("--email", default="")

    def handle(self, *args, username, tenant_slug, owner, email, **opts):
        try:
            tenant = Tenant.objects.get(slug=tenant_slug)
        except Tenant.DoesNotExist:
            raise CommandError("Mandant nicht gefunden.")
        password = getpass.getpass("Passwort: ")
        try:
            validate_password(password)
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages))
        if password != getpass.getpass("Passwort (Wiederholung): "):
            raise CommandError("Passwörter stimmen nicht überein.")
        Therapist.objects.create_user(
            username=username, email=email, password=password, tenant=tenant,
            role=Therapist.Role.OWNER if owner else Therapist.Role.THERAPIST,
        )
        self.stdout.write(self.style.SUCCESS("Benutzer angelegt. Der zweite Faktor wird beim ersten Login eingerichtet."))
