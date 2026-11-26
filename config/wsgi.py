import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
application = get_wsgi_application()

# Nur der Serverprozess: entsperrt, falls systemd die Passphrase als Credential bereitstellt (sonst /entsperren/).
from core.autounlock import try_unlock  # noqa: E402  (erst nach dem Django-Start importierbar)

try_unlock()
