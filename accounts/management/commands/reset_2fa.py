from django.core.management.base import BaseCommand, CommandError
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.models import Therapist
from audit.models import log as audit_log
from core import tenancy
from core.management.commands._keys import require_key


class Command(BaseCommand):
    help = ("Setzt den zweiten Faktor eines Benutzers zurück (verlorenes Handy). Nur nach persönlicher Identitätsprüfung! "
            "Der Benutzer richtet beim nächsten Login einen neuen Faktor ein, alle seine Sitzungen werden beendet.")

    def add_arguments(self, parser):
        parser.add_argument("username")

    def handle(self, *args, username, **opts):
        from django.contrib.sessions.models import Session

        try:
            user = Therapist.objects.select_related("tenant").get(username=username)
        except Therapist.DoesNotExist:
            raise CommandError("Benutzer nicht gefunden.")
        removed = TOTPDevice.objects.filter(user=user).delete()[0]
        for session in Session.objects.all():  # Sitzungen des Benutzers beenden
            if str(session.get_decoded().get("_auth_user_id")) == str(user.pk):
                session.delete()
        if user.tenant_id and user.tenant.is_active and not user.tenant.purged_on:
            require_key()
            with tenancy.tenant_context(user.tenant, tenancy.Actor(user_id=None, label="system:admin")):
                audit_log("auth.2fa_reset", object_type="user", object_id=str(user.pk))
        self.stdout.write(self.style.SUCCESS(
            f"Zweiter Faktor von {username} zurückgesetzt ({removed} Eintrag/Einträge). Neues Einrichten beim nächsten Login. "
            "Bitte das Einrichten gemeinsam mit der Person durchführen."))
