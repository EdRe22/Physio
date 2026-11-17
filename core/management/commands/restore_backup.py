import getpass
import os

from django.core.management.base import BaseCommand, CommandError

from core import backup


class Command(BaseCommand):
    help = "Entpackt und prüft eine Sicherung in ein leeres Verzeichnis (spielt nie direkt in den Livebetrieb zurück)."

    def add_arguments(self, parser):
        parser.add_argument("file")
        parser.add_argument("target")

    def handle(self, *args, file, target, **opts):
        passphrase = os.environ.get("PHYSIO_BACKUP_PASSPHRASE") or getpass.getpass("Backup-Passphrase: ")
        try:
            manifest = backup.restore_backup(file, passphrase, target)
        except backup.BackupError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(
            f"{len(manifest['files'])} Dateien nach {target} wiederhergestellt und geprüft (Prüfsummen, Datenbankintegrität). "
            "Dateien aus db/ und belege/ anschließend bei gestopptem System an die Livepfade kopieren."))
