"""Optionales automatisches Entsperren beim Start des Servers über systemd-Credentials.

Aktiv nur, wenn systemd dem Dienst die Passphrase als Credential bereitstellt (`LoadCredentialEncrypted=`, Einrichtung:
`docs/pi/auto-unlock.sh`). Die Passphrase steht damit weder in der Umgebungsdatei noch in den Umgebungsvariablen;
`CREDENTIALS_DIRECTORY` nennt nur den Ordner. Ohne Credential bleibt alles wie bisher (Entsperren unter /entsperren/).

Bewusste Abwägung (SECURITY.md): Wer den ganzen Pi oder ein Abbild der Karte besitzt, kann die Passphrase gewinnen.

Aufgerufen nur aus `config/wsgi.py` — Verwaltungsbefehle fragen weiterhin nach der Passphrase.
"""
import logging
import os
from pathlib import Path

from django.conf import settings

from . import keystore

CREDENTIAL_NAME = "physio-passphrase"
log = logging.getLogger(__name__)


def try_unlock() -> bool:
    """Entsperrt, falls ein Credential vorhanden ist. Wirft nie: Bei Fehlern startet die App gesperrt."""
    directory = os.environ.get("CREDENTIALS_DIRECTORY")
    if not directory or not settings.PHYSIO_KEYFILE or keystore.is_unlocked():
        return False
    credential = Path(directory) / CREDENTIAL_NAME
    if not credential.exists():
        return False
    try:
        passphrase = credential.read_text(encoding="utf-8").rstrip("\r\n")
        keystore.unlock_unattended(settings.PHYSIO_KEYFILE, passphrase)
    except (OSError, UnicodeError, keystore.KeyUnlockError) as exc:
        # Nur der Fehlertyp bzw. unsere eigene Meldung: nie Inhalte ins Protokoll.
        reason = str(exc) if isinstance(exc, keystore.KeyUnlockError) else type(exc).__name__
        log.warning("Automatisches Entsperren fehlgeschlagen (%s). Bitte unter /entsperren/ freigeben.", reason)
        return False
    log.warning("Master-Schlüssel beim Start automatisch entsperrt (systemd-Credential).")
    return True
