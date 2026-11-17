"""Verwaltungsvorgänge an Benutzerkonten. Gemeinsam genutzt von Kommandozeile und Verwaltungsbereich der App."""
import secrets

from django.contrib.sessions.models import Session
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.models import Therapist
from audit.models import log as audit_log
from core import tenancy

# Ohne leicht verwechselbare Zeichen (0/O, 1/l/I), damit das Startpasswort fehlerfrei abgetippt werden kann.
_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_password(length: int = 16) -> str:
    """Zufälliges Startpasswort (erfüllt Länge, Gemischtheit und Häufigkeitsregeln der Passwortprüfung)."""
    while True:
        pw = "".join(secrets.choice(_ALPHABET) for _ in range(length))
        if any(c.islower() for c in pw) and any(c.isupper() for c in pw) and any(c.isdigit() for c in pw):
            return pw


def end_sessions(user) -> None:
    """Beendet alle Sitzungen des Benutzers."""
    for session in Session.objects.all():
        if str(session.get_decoded().get("_auth_user_id")) == str(user.pk):
            session.delete()


def log_in_target_chain(user, action: str, actor_label: str, actor_id=None, ip: str = "") -> None:
    """Hält den Vorgang im Protokoll des Betroffenen fest (nur wenn dessen Mandant aktiv und der Schlüssel verfügbar ist)."""
    tenant = user.tenant
    if tenant and tenant.is_active and not tenant.purged_on:
        with tenancy.tenant_context(tenant, tenancy.Actor(user_id=actor_id, label=actor_label, ip=ip)):
            audit_log(action, object_type="user", object_id=str(user.pk))


def reset_2fa(user: Therapist, actor_label: str, actor_id=None, ip: str = "") -> int:
    """Entfernt den zweiten Faktor (verlorenes Handy) und beendet die Sitzungen. Rückgabe: Anzahl entfernter Geräte."""
    removed = TOTPDevice.objects.filter(user=user).delete()[0]
    end_sessions(user)
    log_in_target_chain(user, "auth.2fa_reset", actor_label, actor_id, ip)
    return removed


def reset_password(user: Therapist, actor_label: str, actor_id=None, ip: str = "") -> str:
    """Setzt ein neues Startpasswort. Der Benutzer muss es beim nächsten Login ändern. Rückgabe: das Passwort (nur jetzt sichtbar)."""
    password = generate_password()
    user.set_password(password)
    user.must_change_password = True
    user.failed_logins = 0
    user.locked_until = None
    user.save(update_fields=["password", "must_change_password", "failed_logins", "locked_until"])
    end_sessions(user)
    log_in_target_chain(user, "auth.password_reset", actor_label, actor_id, ip)
    return password
