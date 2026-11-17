from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.utils import timezone


class LockoutBackend(ModelBackend):
    """Passwort-Login mit Sperre nach zu vielen Fehlversuchen (Schutz vor Durchprobieren)."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        User = get_user_model()
        if username is None:
            username = kwargs.get(User.USERNAME_FIELD)
        try:
            user = User._default_manager.get_by_natural_key(username)
        except User.DoesNotExist:
            User().set_password(password or "")  # gleiche Laufzeit, kein Hinweis auf Existenz
            return None

        now = timezone.now()
        if user.locked_until and user.locked_until > now:
            return None

        authenticated = super().authenticate(request, username=username, password=password, **kwargs)
        if authenticated:
            if user.failed_logins or user.locked_until:
                User.objects.filter(pk=user.pk).update(failed_logins=0, locked_until=None)
            return authenticated

        failures = user.failed_logins + 1
        update = {"failed_logins": failures}
        if failures >= settings.LOGIN_MAX_FAILURES:
            update = {"failed_logins": 0, "locked_until": now + timedelta(minutes=settings.LOGIN_LOCK_MINUTES)}
        User.objects.filter(pk=user.pk).update(**update)
        return None
