"""Jede Schreiboperation auf Patientendaten wird automatisch protokolliert (nicht vergessbar)."""
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from audit.models import log

from .models import Einwilligung, Patient


def _register(model):
    @receiver(post_save, sender=model, weak=False)
    def _saved(sender, instance, created, **kwargs):
        log("create" if created else "update", instance)

    @receiver(post_delete, sender=model, weak=False)
    def _deleted(sender, instance, **kwargs):
        log("delete", instance)


for _model in (Patient, Einwilligung):
    _register(_model)
