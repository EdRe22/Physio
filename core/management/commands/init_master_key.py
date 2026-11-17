import base64
import getpass
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.core.management.base import BaseCommand, CommandError

from core import keystore


class Command(BaseCommand):
    help = ("Erzeugt einen neuen Master-Schlüssel und versiegelt ihn (Schlüsseldatei + Wiederherstellungsdatei). "
            "NUR bei der Erstinstallation vor dem Anlegen der Mandanten. Der Klartext-Schlüssel wird nie angezeigt.")

    def add_arguments(self, parser):
        parser.add_argument("--keyfile", required=True, help="Pfad der Schlüsseldatei (PHYSIO_KEYFILE)")
        parser.add_argument("--recovery", required=True, help="Pfad der Wiederherstellungsdatei (in den Tresor!)")
        parser.add_argument("--usb-secret", help="Optionale Datei (z. B. auf USB-Stick/Token) als zusätzlicher Faktor")

    def handle(self, *args, keyfile, recovery, usb_secret=None, **opts):
        for path in (keyfile, recovery):
            if Path(path).exists():
                raise CommandError(f"{path} existiert bereits. Ein vorhandener Master-Schlüssel wird nie überschrieben.")
        extra = Path(usb_secret).read_bytes() if usb_secret else b""
        pw = getpass.getpass("Passphrase für den Betrieb (mind. 14 Zeichen): ")
        if pw != getpass.getpass("Wiederholen: "):
            raise CommandError("Eingaben stimmen nicht überein.")
        rpw = getpass.getpass("Passphrase für die Wiederherstellungsdatei (andere, für den Tresor): ")
        if rpw == pw:
            raise CommandError("Wiederherstellungs-Passphrase muss sich unterscheiden.")
        self.create(keyfile, recovery, pw, rpw, extra)
        self.stdout.write(self.style.SUCCESS(
            "Master-Schlüssel erzeugt und versiegelt. Wiederherstellungsdatei und Passphrase getrennt im Tresor ablegen. "
            "Ohne eine von beiden Dateien samt Passphrase sind ALLE Daten unwiederbringlich verloren."))

    @staticmethod
    def create(keyfile, recovery, passphrase, recovery_passphrase, extra=b""):
        master = AESGCM.generate_key(bit_length=256)
        for path, secret, kf in ((keyfile, passphrase, extra), (recovery, recovery_passphrase, b"")):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(keystore.seal(master, secret, kf))
        return base64.b64encode(master)  # nur für Tests; der Befehl gibt ihn nicht aus
