import getpass
import os

from django.core.management.base import BaseCommand, CommandError

from core import backup


class Command(BaseCommand):
    help = "Erstellt eine verschlüsselte Gesamtsicherung (.pbk). Passphrase aus PHYSIO_BACKUP_PASSPHRASE oder Eingabe."

    def add_arguments(self, parser):
        parser.add_argument("out")

    def handle(self, *args, out, **opts):
        passphrase = os.environ.get("PHYSIO_BACKUP_PASSPHRASE") or getpass.getpass("Backup-Passphrase: ")
        try:
            manifest = backup.create_backup(out, passphrase)
        except backup.BackupError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(f"Sicherung {out}: {len(manifest['files'])} Dateien."))
