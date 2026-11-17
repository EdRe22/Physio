"""Test-Einstellungen: nur Fake-Daten, In-Memory-Datenbanken, zwei feste Test-Mandanten."""
import os

os.environ["PHYSIO_DEV"] = "1"

from .settings import *  # noqa: E402,F401,F403

DATABASES = {
    "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
    "t_alpha": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
    "t_beta": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
}
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

BELEG_DIR = Path(tempfile.mkdtemp(prefix="physio_test_belege_"))
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
