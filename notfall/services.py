from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from audit.models import log as audit_log
from core import tenancy
from rechnungen.services import BillingError

from .models import DURATION, MIN_REASON, NotfallZugriff


class EmergencyError(BillingError):
    pass


def _in_target(grant, user, action):
    """Protokolliert im Audit-Log des Betroffenen (eigene Hash-Kette) mit dem handelnden Benutzer als Akteur."""
    actor = tenancy.Actor(user_id=user.pk, label=f"notfall:{user.get_username()}")
    with tenancy.tenant_context(grant.target, actor):
        audit_log(action, object_type="notfall", object_id=str(grant.pk))


def _notify(grant, text):
    target_user = getattr(grant.target, "user", None)
    if target_user and target_user.email:
        # Kein Patientenbezug, nur der Vorgang.
        send_mail("Notfallzugriff auf Ihre Daten", text, None, [target_user.email], fail_silently=True)


def request_access(user, target, reason):
    reason = (reason or "").strip()
    if not user.tenant_id:
        raise EmergencyError("Kein Mandant zugeordnet.")
    if target.pk == user.tenant_id:
        raise EmergencyError("Für die eigenen Daten ist kein Notfallzugriff nötig.")
    if not target.is_active or target.purged_on:
        raise EmergencyError("Dieser Mandant ist nicht aktiv.")
    if len(reason) < MIN_REASON:
        raise EmergencyError(f"Bitte eine nachvollziehbare Begründung angeben (mindestens {MIN_REASON} Zeichen).")
    if NotfallZugriff.objects.filter(target=target, requested_by=user, ended_at__isnull=True, expires_at__isnull=True).exists():
        raise EmergencyError("Es gibt bereits einen offenen Antrag.")
    with transaction.atomic():
        grant = NotfallZugriff.objects.create(target=target, requested_by=user, reason=reason)
        _in_target(grant, user, "notfall.request")
    _notify(grant, f"{user.get_username()} hat Notfallzugriff auf Ihre Daten beantragt. Es ist noch keine Freigabe erfolgt.")
    return grant


def can_approve(user, grant):
    return (user.tenant_id and user.tenant_id not in (grant.target_id, grant.requested_by.tenant_id)
            and user.pk != grant.requested_by_id)


def approve(user, grant, now=None):
    now = now or timezone.now()
    if grant.ended_at or grant.approved_at:
        raise EmergencyError("Dieser Antrag ist nicht mehr offen.")
    if not can_approve(user, grant):
        raise EmergencyError("Die Freigabe muss durch einen anderen Therapeuten erfolgen (weder Antragsteller noch Betroffener).")
    with transaction.atomic():
        grant.approved_by, grant.approved_at, grant.expires_at = user, now, now + DURATION
        grant.save()
        _in_target(grant, user, "notfall.approve")
    _notify(grant, f"Der Notfallzugriff auf Ihre Daten wurde freigegeben und gilt bis {grant.expires_at:%d.%m.%Y %H:%M} (24 Stunden).")
    return grant


def end(user, grant, now=None):
    """Widerruf durch Antragsteller, Freigebenden oder Betroffenen."""
    now = now or timezone.now()
    if grant.ended_at:
        raise EmergencyError("Bereits beendet.")
    if user.pk not in (grant.requested_by_id, grant.approved_by_id) and user.tenant_id != grant.target_id:
        raise EmergencyError("Keine Berechtigung.")
    grant.ended_at = now
    grant.save()
    _in_target(grant, user, "notfall.end")
    return grant


def visible_to(user):
    """Antragsteller/Freigebende sehen ihre Vorgänge, der Betroffene sieht alle Zugriffe auf seine Daten (Transparenz)."""
    from django.db.models import Q

    return NotfallZugriff.objects.filter(
        Q(requested_by=user) | Q(approved_by=user) | Q(target_id=user.tenant_id)
    ).select_related("target", "requested_by", "approved_by")


def open_for_approval(user):
    return [g for g in NotfallZugriff.objects.filter(approved_at__isnull=True, ended_at__isnull=True)
            .select_related("target", "requested_by") if can_approve(user, g)]


def usable_grant(user, pk, now=None):
    """Aktive Freigabe, die genau dieser Benutzer als Antragsteller nutzen darf."""
    grant = NotfallZugriff.objects.select_related("target", "requested_by").filter(pk=pk, requested_by=user).first()
    if grant is None or not grant.is_active(now):
        return None
    if not grant.target.is_active or grant.target.purged_on:
        return None
    return grant
