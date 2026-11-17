import getpass
from pathlib import Path

from django.conf import settings
from django.core.management.base import CommandError

from core import keystore


def require_key():
    """Verwaltungsbefehle laufen in eigenen Prozessen: Schlüssel hier interaktiv entsperren (nie aus Dateien/Umgebung)."""
    if keystore.is_unlocked() or not settings.PHYSIO_KEYFILE:
        return
    passphrase = getpass.getpass("Passphrase zum Entsperren des Master-Schlüssels: ")
    extra = input("Pfad der Schlüsseldatei (leer, falls keine): ").strip()
    try:
        keystore.unlock(settings.PHYSIO_KEYFILE, passphrase, Path(extra).read_bytes() if extra else b"")
    except (keystore.KeyUnlockError, OSError) as exc:
        raise CommandError(f"Entsperren fehlgeschlagen: {exc}")
