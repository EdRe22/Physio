"""Manipulationssicheres Audit-Log pro Mandant.

Jeder Eintrag enthält den Hash des Vorgängers (Hash-Kette). Änderungen oder Löschungen
einzelner Einträge werden von verify_chain() erkannt. Es werden nur IDs gespeichert,
niemals Patientendaten im Klartext.
"""
import datetime as dt
import hashlib
import json

from django.db import IntegrityError, models, transaction
from django.utils import timezone

from core import tenancy

GENESIS = "0" * 64


class AppendOnlyError(Exception):
    pass


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AppendOnlyError("Audit-Einträge sind unveränderlich.")

    def delete(self):
        raise AppendOnlyError("Audit-Einträge sind unveränderlich.")


def _hash(prev_hash, ts, actor_id, actor_label, ip, action, object_type, object_id):
    payload = json.dumps(
        [prev_hash, ts.astimezone(dt.timezone.utc).isoformat(), actor_id, actor_label, ip, action, object_type, object_id],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AuditEntry(models.Model):
    ts = models.DateTimeField()
    actor_id = models.BigIntegerField(null=True)  # bewusst kein FK: Benutzer liegen in der Praxis-DB
    actor_label = models.CharField(max_length=150)
    ip = models.CharField(max_length=45, blank=True)
    action = models.CharField(max_length=64)
    object_type = models.CharField(max_length=64, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    prev_hash = models.CharField(max_length=64, unique=True)
    entry_hash = models.CharField(max_length=64)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["id"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AppendOnlyError("Audit-Einträge sind unveränderlich.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyError("Audit-Einträge sind unveränderlich.")

    def compute_hash(self):
        return _hash(self.prev_hash, self.ts, self.actor_id, self.actor_label, self.ip,
                     self.action, self.object_type, self.object_id)


def log(action, obj=None, object_type="", object_id=""):
    """Schreibt einen Audit-Eintrag in die Datenbank des aktiven Mandanten."""
    ctx = tenancy.require()
    actor = tenancy.current_actor()
    if obj is not None:
        object_type = obj._meta.label_lower
        object_id = str(obj.pk)
    for _ in range(5):
        try:
            with transaction.atomic(using=ctx.alias):
                last = AuditEntry.objects.using(ctx.alias).order_by("-id").first()
                entry = AuditEntry(
                    ts=timezone.now(),
                    actor_id=actor.user_id if actor else None,
                    actor_label=actor.label if actor else "system",
                    ip=actor.ip if actor else "",
                    action=action,
                    object_type=object_type,
                    object_id=object_id,
                    prev_hash=last.entry_hash if last else GENESIS,
                )
                entry.entry_hash = entry.compute_hash()
                entry.save(using=ctx.alias)
            return entry
        except IntegrityError:  # paralleler Eintrag hat dieselbe Vorgänger-ID belegt
            continue
    raise RuntimeError("Audit-Eintrag konnte nicht geschrieben werden.")


def verify_chain():
    """Prüft die Kette des aktiven Mandanten. Rückgabe: (ok, id_des_ersten_fehlerhaften_eintrags)."""
    ctx = tenancy.require()
    prev = GENESIS
    for entry in AuditEntry.objects.using(ctx.alias).order_by("id").iterator():
        if entry.prev_hash != prev or entry.entry_hash != entry.compute_hash():
            return False, entry.pk
        prev = entry.entry_hash
    return True, None
