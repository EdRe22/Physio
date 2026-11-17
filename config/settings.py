"""Einstellungen der Physio-Praxis-App.

Grundsatz: Fehlt etwas Sicherheitsrelevantes (Secret, Master-Schlüssel), startet die
App nicht. Entwicklungs-Vorgaben gelten nur mit PHYSIO_DEV=1 und nur mit Fake-Daten.
"""
import base64
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
DEV = os.environ.get("PHYSIO_DEV") == "1"
DEBUG = DEV and os.environ.get("PHYSIO_DEBUG", "1") == "1"


def _require(name, dev_default=None):
    value = os.environ.get(name)
    if value:
        return value
    if DEV and dev_default is not None:
        return dev_default
    raise ImproperlyConfigured(f"Umgebungsvariable {name} fehlt.")


SECRET_KEY = _require("PHYSIO_SECRET_KEY", "dev-only-insecure-secret-key-change-me-0123456789")
ALLOWED_HOSTS = [h for h in os.environ.get("PHYSIO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h]

# Master-Schlüssel (Base64, 32 Byte). In Produktion kommt er vom Hardware-Token des
# Eigentümers beim Systemstart und liegt nie im Klartext auf der Platte.
PHYSIO_KEYFILE = os.environ.get("PHYSIO_KEYFILE") or None  # versiegelter Master-Schlüssel (siehe core/keystore.py)
PHYSIO_MASTER_KEY_B64 = os.environ.get("PHYSIO_MASTER_KEY") or (
    base64.b64encode(b"\x01" * 32).decode() if DEV else None
)
if not DEV and PHYSIO_MASTER_KEY_B64 and not PHYSIO_KEYFILE and os.environ.get("PHYSIO_ALLOW_ENV_KEY") != "1":
    raise ImproperlyConfigured(
        "Produktion: Master-Schlüssel nicht aus der Umgebung lesen. PHYSIO_KEYFILE setzen "
        "(oder bewusst PHYSIO_ALLOW_ENV_KEY=1, nicht empfohlen)."
    )

PHYSIO_TRUST_PROXY = os.environ.get("PHYSIO_TRUST_PROXY") == "1"  # nginx auf Loopback setzt X-Forwarded-For (siehe core/net.py)
PHYSIO_JOB_TOKEN = os.environ.get("PHYSIO_JOB_TOKEN", "")  # Cron-Schnittstelle, siehe core/jobs.py

DATA_DIR = Path(os.environ.get("PHYSIO_DATA_DIR", BASE_DIR / "data"))

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_otp",
    "django_otp.plugins.otp_totp",
    "core",
    "accounts",
    "audit",
    "patients",
    "termine",
    "importer",
    "rechnungen",
    "buchhaltung",
    "praxis",
    "notfall",
    "raeume",
    "hilfe",
    "verwaltung",
    "einstellungen",
]

# Diese Apps liegen ausschließlich in der Mandanten-Datenbank des jeweiligen Therapeuten.
TENANT_APP_LABELS = {"audit", "patients", "termine", "importer", "rechnungen", "buchhaltung"}

# Belege (verschlüsselte Dateien, pro Mandant ein Unterordner). Muss ins Backup!
BELEG_DIR = Path(os.environ.get("PHYSIO_BELEG_DIR", DATA_DIR / "belege"))

# Uploads bleiben im Arbeitsspeicher und landen nie unverschlüsselt als Temp-Datei auf der Platte.
FILE_UPLOAD_HANDLERS = ["django.core.files.uploadhandler.MemoryFileUploadHandler"]
FILE_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024

AUTH_USER_MODEL = "accounts.Therapist"
AUTHENTICATION_BACKENDS = ["accounts.backends.LockoutBackend"]
DATABASE_ROUTERS = ["core.router.TenantRouter"]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "uebersicht"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "core.middleware.LockedMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.SecurityHeadersMiddleware",
    "core.middleware.TwoFactorRequiredMiddleware",
    "core.middleware.TenantMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "hilfe.context.help_link",
                "verwaltung.context.theme",
            ]
        },
    }
]

# Praxis-Datenbank ("default"): Benutzer, Mandanten, Praxisbereich (keine Patientendaten).
# Pro Therapeut entsteht zur Laufzeit eine eigene Datenbank "t_<slug>" (siehe core.tenancy).
if os.environ.get("PHYSIO_DB_ENGINE") == "postgres":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("PHYSIO_DB_NAME", "physio_praxis"),
            "USER": os.environ["PHYSIO_DB_USER"],
            "PASSWORD": os.environ["PHYSIO_DB_PASSWORD"],
            "HOST": os.environ.get("PHYSIO_DB_HOST", "localhost"),
            "PORT": os.environ.get("PHYSIO_DB_PORT", "5432"),
        }
    }
else:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": DATA_DIR / "praxis.sqlite3"}}
TENANT_DB_PREFIX = "physio_t_"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "de-at"
TIME_ZONE = "Europe/Vienna"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Sitzungen und Cookies
SESSION_COOKIE_AGE = 30 * 60
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Strict"
CSRF_COOKIE_SAMESITE = "Strict"
SESSION_COOKIE_SECURE = not DEV
CSRF_COOKIE_SECURE = not DEV
SECURE_SSL_REDIRECT = False  # TLS wird vom Reverse-Proxy beendet, App nur per VPN erreichbar
SECURE_HSTS_SECONDS = 0 if DEV else 31536000
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# E-Mail (Terminerinnerungen): EU-basierter SMTP-Dienst mit AV-Vertrag, nur TLS.
if os.environ.get("PHYSIO_EMAIL_HOST"):
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = os.environ["PHYSIO_EMAIL_HOST"]
    EMAIL_PORT = int(os.environ.get("PHYSIO_EMAIL_PORT", "587"))
    EMAIL_HOST_USER = os.environ.get("PHYSIO_EMAIL_USER", "")
    EMAIL_HOST_PASSWORD = os.environ.get("PHYSIO_EMAIL_PASSWORD", "")
    EMAIL_USE_TLS = True
elif DEV:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
else:
    EMAIL_BACKEND = "django.core.mail.backends.dummy.EmailBackend"  # ohne Konfiguration wird nichts versendet
DEFAULT_FROM_EMAIL = os.environ.get("PHYSIO_FROM_EMAIL", "praxis@example.invalid")

# Zweiter Faktor und Sperre nach Fehlversuchen
OTP_TOTP_ISSUER = "Physio Praxisgemeinschaft"
LOGIN_MAX_FAILURES = 5
LOGIN_LOCK_MINUTES = 15
