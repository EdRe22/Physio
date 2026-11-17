from django.core.management.base import BaseCommand, CommandError

from accounts import admin_ops
from accounts.models import Therapist
from core.management.commands._keys import require_key


class Command(BaseCommand):
    help = ("Setzt den zweiten Faktor eines Benutzers zurück (verlorenes Handy). Nur nach persönlicher Identitätsprüfung! "
            "Der Benutzer richtet beim nächsten Login einen neuen Faktor ein, alle seine Sitzungen werden beendet.")

    def add_arguments(self, parser):
        parser.add_argument("username")

    def handle(self, *args, username, **opts):
        try:
            user = Therapist.objects.select_related("tenant").get(username=username)
        except Therapist.DoesNotExist:
            raise CommandError("Benutzer nicht gefunden.")
        if user.tenant_id and user.tenant.is_active and not user.tenant.purged_on:
            require_key()
        removed = admin_ops.reset_2fa(user, "system:admin")
        self.stdout.write(self.style.SUCCESS(
            f"Zweiter Faktor von {username} zurückgesetzt ({removed} Eintrag/Einträge). Neues Einrichten beim nächsten Login. "
            "Bitte das Einrichten gemeinsam mit der Person durchführen."))
