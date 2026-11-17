from django.apps import AppConfig


class TermineConfig(AppConfig):
    name = "termine"
    verbose_name = "Termine"

    def ready(self):
        from django.db.models.signals import post_delete, post_save

        from audit.models import log

        from .models import Termin

        def saved(sender, instance, created, **kwargs):
            log("create" if created else "update", instance)

        def deleted(sender, instance, **kwargs):
            log("delete", instance)

        post_save.connect(saved, sender=Termin, weak=False, dispatch_uid="termin_saved")
        post_delete.connect(deleted, sender=Termin, weak=False, dispatch_uid="termin_deleted")
