from django.conf import settings

from . import tenancy


class TenantRouter:
    """Patienten-Apps gehen nur in die Datenbank des aktiven Mandanten, alles andere in "default"."""

    @staticmethod
    def _is_tenant_label(app_label):
        return app_label in settings.TENANT_APP_LABELS

    def _route(self, model, **hints):
        if self._is_tenant_label(model._meta.app_label):
            return tenancy.require().alias
        return "default"

    db_for_read = _route
    db_for_write = _route

    def allow_relation(self, obj1, obj2, **hints):
        # Keine Beziehungen zwischen Mandanten-DB und Praxis-DB (oder zwischen Mandanten).
        return obj1._state.db == obj2._state.db

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if self._is_tenant_label(app_label):
            return db != "default"
        return db == "default"
