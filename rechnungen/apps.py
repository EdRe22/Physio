from django.apps import AppConfig


class RechnungenConfig(AppConfig):
    name = "rechnungen"
    verbose_name = "Honorarnoten"

    def ready(self):
        from django.db.models.signals import post_delete, post_save

        from audit.models import log

        from .models import Honorarnote, Leistung, Mahnung, Position, Rechnungsprofil, Zahlung

        def saved(sender, instance, created, **kwargs):
            log("create" if created else "update", instance)

        def deleted(sender, instance, **kwargs):
            log("delete", instance)

        for model in (Rechnungsprofil, Leistung, Honorarnote, Position, Zahlung, Mahnung):
            post_save.connect(saved, sender=model, weak=False, dispatch_uid=f"{model.__name__}_saved")
            post_delete.connect(deleted, sender=model, weak=False, dispatch_uid=f"{model.__name__}_deleted")
