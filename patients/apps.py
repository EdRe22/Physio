from django.apps import AppConfig


class PatientsConfig(AppConfig):
    name = "patients"
    verbose_name = "Patienten"

    def ready(self):
        from . import signals  # noqa: F401
