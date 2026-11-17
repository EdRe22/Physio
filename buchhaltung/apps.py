from django.apps import AppConfig


class BuchhaltungConfig(AppConfig):
    name = "buchhaltung"
    verbose_name = "Buchhaltung"

    def ready(self):
        from django.db.models.signals import post_delete, post_save

        from audit.models import log
        from rechnungen.models import Zahlung

        from . import services
        from .models import BankTransaktion, Beleg, Buchung, Kategorie

        def saved(sender, instance, created, **kwargs):
            log("create" if created else "update", instance)

        def deleted(sender, instance, **kwargs):
            log("delete", instance)

        for model in (Kategorie, Beleg, Buchung, BankTransaktion):
            post_save.connect(saved, sender=model, weak=False, dispatch_uid=f"{model.__name__}_saved_bh")
            post_delete.connect(deleted, sender=model, weak=False, dispatch_uid=f"{model.__name__}_deleted_bh")

        def payment_saved(sender, instance, created, **kwargs):
            if created:
                services.on_payment_recorded(instance)

        post_save.connect(payment_saved, sender=Zahlung, weak=False, dispatch_uid="zahlung_to_buchung")
