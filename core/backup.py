"""Verschlüsselte Gesamtsicherung (Praxis-DB, alle Mandanten-DBs, Belege) und Wiederherstellung.

Die Sicherung ist ein einziges .pbk-Archiv: tar, verschlüsselt mit AES-256-GCM, Schlüssel per scrypt aus einer
eigenen Backup-Passphrase (nicht der Master-Schlüssel, damit ein Backup-Dienstleister nie Zugriff auf Klartext
bekäme, selbst wenn er die Passphrase hätte: die Feldverschlüsselung liegt zusätzlich darunter).

SQLite: konsistente Kopie über die SQLite-Backup-API. PostgreSQL: pg_dump (Custom-Format), ungetestet.
Größe: das Archiv wird im Speicher verarbeitet (für eine Praxis mit wenigen GB ausreichend, Grenze 2 GB).
"""
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from django.conf import settings
from django.db import connections

from . import tenancy
from .models import Tenant

MAGIC = b"PHYSIOBK1"
_AAD = b"physio/backup/v1"
MAX_BYTES = 2 * 1024**3
MIN_PASSPHRASE = 14


class BackupError(Exception):
    pass


def _key(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode("utf-8"))


def _db_jobs():
    """(Name im Archiv, Alias) für Praxis-DB und alle Mandanten-DBs."""
    jobs = [("default", "default")]
    for tenant in Tenant.objects.all().order_by("slug"):
        jobs.append((f"tenant_{tenant.slug}", tenancy.register_db(tenant.slug)))
    return jobs


def _dump(alias, dest: Path):
    cfg = connections.databases[alias]
    if "sqlite3" in cfg["ENGINE"]:
        src = sqlite3.connect(str(cfg["NAME"]))
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
    elif "postgresql" in cfg["ENGINE"]:
        env = {**os.environ, "PGPASSWORD": cfg.get("PASSWORD", "")}
        subprocess.run(["pg_dump", "-Fc", "-h", cfg["HOST"], "-p", str(cfg["PORT"]), "-U", cfg["USER"], "-f", str(dest), cfg["NAME"]],
                       check=True, env=env)
    else:
        raise BackupError("Nicht unterstützte Datenbank.")


def create_backup(out_path, passphrase: str) -> dict:
    if len(passphrase) < MIN_PASSPHRASE:
        raise BackupError(f"Die Backup-Passphrase muss mindestens {MIN_PASSPHRASE} Zeichen lang sein.")
    if Path(out_path).exists():
        raise BackupError("Die Zieldatei existiert bereits.")
    manifest = {"version": 1, "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "files": {}}
    buffer = io.BytesIO()
    with tempfile.TemporaryDirectory() as tmp, tarfile.open(fileobj=buffer, mode="w") as tar:
        def add(name, path):
            data = Path(path).read_bytes()
            manifest["files"][name] = hashlib.sha256(data).hexdigest()
            info = tarfile.TarInfo(name)
            info.size, info.mtime = len(data), int(time.time())
            tar.addfile(info, io.BytesIO(data))

        for name, alias in _db_jobs():
            ext = "sqlite3" if "sqlite3" in connections.databases[alias]["ENGINE"] else "dump"
            target = Path(tmp) / f"{name}.{ext}"
            _dump(alias, target)
            add(f"db/{name}.{ext}", target)
        beleg_root = Path(settings.BELEG_DIR)
        if beleg_root.exists():
            for file in sorted(p for p in beleg_root.rglob("*") if p.is_file()):
                add(f"belege/{file.relative_to(beleg_root).as_posix()}", file)
        raw_manifest = json.dumps(manifest, indent=1).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size, info.mtime = len(raw_manifest), int(time.time())
        tar.addfile(info, io.BytesIO(raw_manifest))
    plain = buffer.getvalue()
    if len(plain) > MAX_BYTES:
        raise BackupError("Die Sicherung ist größer als 2 GB, bitte Verfahren anpassen.")
    salt, nonce = os.urandom(16), os.urandom(12)
    blob = MAGIC + salt + nonce + AESGCM(_key(passphrase, salt)).encrypt(nonce, plain, _AAD)
    fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(blob)
    return manifest


def restore_backup(file_path, passphrase: str, target_dir) -> dict:
    """Entpackt und prüft eine Sicherung in ein NEUES, leeres Verzeichnis (überschreibt nie Livedaten)."""
    target = Path(target_dir)
    if target.exists() and any(target.iterdir()):
        raise BackupError("Das Zielverzeichnis muss leer sein. Wiederherstellung überschreibt nie vorhandene Daten.")
    blob = Path(file_path).read_bytes()
    if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + 28:
        raise BackupError("Keine gültige Sicherungsdatei.")
    salt, nonce, ct = blob[9:25], blob[25:37], blob[37:]
    try:
        plain = AESGCM(_key(passphrase, salt)).decrypt(nonce, ct, _AAD)
    except InvalidTag:
        raise BackupError("Passphrase falsch oder Sicherung beschädigt.")
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(plain), mode="r") as tar:
        members = {m.name: m for m in tar.getmembers()}
        if "manifest.json" not in members:
            raise BackupError("Manifest fehlt.")
        manifest = json.loads(tar.extractfile(members["manifest.json"]).read())
        if set(manifest["files"]) != set(members) - {"manifest.json"}:
            raise BackupError("Inhalt stimmt nicht mit dem Manifest überein.")
        for name, digest in manifest["files"].items():
            member = members[name]
            dest = (target / name).resolve()
            if not member.isfile() or target.resolve() not in dest.parents:
                raise BackupError("Unzulässiger Eintrag in der Sicherung.")
            data = tar.extractfile(member).read()
            if hashlib.sha256(data).hexdigest() != digest:
                raise BackupError(f"Prüfsumme falsch: {name}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
    for db in (target / "db").glob("*.sqlite3"):
        conn = sqlite3.connect(str(db))
        try:
            if conn.execute("pragma integrity_check").fetchone()[0] != "ok":
                raise BackupError(f"Datenbank beschädigt: {db.name}")
        finally:
            conn.close()
    return manifest
