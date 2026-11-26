import base64
import importlib
import json
import sqlite3
import sys

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, OperationalError, connections
from django.test import Client, override_settings

from audit.models import log
from core import autounlock, backup, crypto, keystore, tenancy
from core.management.commands.init_master_key import Command as InitKey
from tests.conftest import verified_client

FAST = {"n": 2**10, "r": 8, "p": 1}
PASS = "ein-sehr-langes-passwort-1"
MASTER = b"\x07" * 32


@pytest.fixture(autouse=True)
def _clean_keystore():
    keystore.reset_for_tests()
    yield
    keystore.reset_for_tests()


# --- Schlüsseldatei ---

def test_seal_roundtrip_and_wrong_secrets():
    blob = keystore.seal(MASTER, PASS, params=FAST)
    assert keystore.open_sealed(blob, PASS) == MASTER
    with pytest.raises(keystore.KeyUnlockError):
        keystore.open_sealed(blob, PASS + "x")
    with pytest.raises(keystore.KeyUnlockError):
        keystore.open_sealed("kein json", PASS)


def test_keyfile_as_second_factor():
    blob = keystore.seal(MASTER, PASS, keyfile=b"usb-secret", params=FAST)
    assert keystore.open_sealed(blob, PASS, b"usb-secret") == MASTER
    for wrong in (b"", b"anderes"):
        with pytest.raises(keystore.KeyUnlockError):
            keystore.open_sealed(blob, PASS, wrong)


def test_short_passphrase_and_bad_key_length_rejected():
    with pytest.raises(ValueError):
        keystore.seal(MASTER, "kurz", params=FAST)
    with pytest.raises(ValueError):
        keystore.seal(b"x" * 16, PASS, params=FAST)


def test_tampered_kdf_params_rejected():
    data = json.loads(keystore.seal(MASTER, PASS, params=FAST))
    data["n"] = 2**30  # würde Gigabytes Speicher verlangen
    with pytest.raises(keystore.KeyUnlockError):
        keystore.open_sealed(json.dumps(data), PASS)


def test_unlock_throttles_guessing(tmp_path):
    path = tmp_path / "k.json"
    path.write_text(keystore.seal(MASTER, PASS, params=FAST))
    for _ in range(3):
        with pytest.raises(keystore.KeyUnlockError):
            keystore.unlock(path, "falsch-falsch-falsch")
    with pytest.raises(keystore.KeyUnlockError, match="warten"):
        keystore.unlock(path, PASS)  # selbst die richtige Passphrase wird jetzt gebremst
    assert not keystore.is_unlocked()


def test_init_master_key_creates_two_files_with_distinct_passphrases(tmp_path):
    keyfile, recovery = tmp_path / "key", tmp_path / "recovery"
    master = base64.b64decode(InitKey.create(keyfile, recovery, PASS, PASS + "-tresor"))
    assert keystore.open_sealed(keyfile.read_text(), PASS) == master
    assert keystore.open_sealed(recovery.read_text(), PASS + "-tresor") == master
    with pytest.raises(keystore.KeyUnlockError):
        keystore.open_sealed(recovery.read_text(), PASS)
    with pytest.raises(FileExistsError):  # überschreibt nie einen vorhandenen Schlüssel
        InitKey.create(keyfile, tmp_path / "r2", PASS, PASS + "-b")


# --- Sperre beim Start ---

def test_locked_system_blocks_everything_until_unlocked(tmp_path):
    keyfile = tmp_path / "k.json"
    keyfile.write_text(keystore.seal(MASTER, PASS, params=FAST))
    with override_settings(PHYSIO_KEYFILE=str(keyfile)):
        client = Client()
        for path in ("/", "/patients/", "/login/", "/rechnungen/"):
            assert client.get(path).status_code == 503, path
        assert client.get("/entsperren/").status_code == 200
        bad = client.post("/entsperren/", {"passphrase": "falsch-falsch-falsch"})
        assert bad.status_code == 403 and not keystore.is_unlocked()
        keystore.reset_for_tests()
        ok = client.post("/entsperren/", {"passphrase": PASS})
        assert ok.status_code == 302 and keystore.is_unlocked()
        assert client.get("/login/").status_code == 200


def test_locked_key_is_not_taken_from_environment(tmp_path):
    with override_settings(PHYSIO_KEYFILE=str(tmp_path / "k.json")):
        with pytest.raises(ImproperlyConfigured):
            crypto.master_key()


def test_unlock_page_absent_without_keyfile():
    assert Client().get("/entsperren/").status_code == 404


def test_logged_in_user_can_open_unlock_page_while_locked(tmp_path, user_alpha):
    keyfile = tmp_path / "k.json"
    keyfile.write_text(keystore.seal(MASTER, PASS, params=FAST))
    client = verified_client(user_alpha)
    with override_settings(PHYSIO_KEYFILE=str(keyfile)):
        assert client.get("/entsperren/").status_code == 200  # TenantMiddleware darf nicht am gesperrten Schlüssel scheitern


# --- Automatisches Entsperren (systemd-Credential) ---

@pytest.fixture
def sealed(tmp_path, monkeypatch):
    keyfile = tmp_path / "k.json"
    keyfile.write_text(keystore.seal(MASTER, PASS, params=FAST))
    creds = tmp_path / "creds"
    creds.mkdir()
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(creds))
    with override_settings(PHYSIO_KEYFILE=str(keyfile)):
        yield creds / autounlock.CREDENTIAL_NAME


@pytest.mark.parametrize("content", [PASS, PASS + "\n"])
def test_auto_unlock_with_credential(sealed, content, caplog):
    sealed.write_text(content, newline="")
    assert autounlock.try_unlock() is True
    assert keystore.current() == MASTER
    assert Client().get("/login/").status_code == 200
    assert PASS not in caplog.text


@pytest.mark.parametrize("content", [b"falsch-falsch-falsch", b"", b"\xff\xfe\x00"])
def test_auto_unlock_with_bad_credential_stays_locked_without_throttle(sealed, content, caplog):
    sealed.write_bytes(content)
    for _ in range(5):  # z. B. Neustart-Schleife
        assert autounlock.try_unlock() is False
    assert not keystore.is_unlocked() and keystore.retry_wait() == 0
    assert "/entsperren/" in caplog.text and "falsch-falsch" not in caplog.text
    ok = Client().post("/entsperren/", {"passphrase": PASS})  # von Hand geht es weiterhin sofort
    assert ok.status_code == 302


def test_auto_unlock_does_nothing_without_credential(sealed, monkeypatch):
    assert autounlock.try_unlock() is False  # Ordner da, Datei fehlt
    sealed.write_text(PASS)
    monkeypatch.delenv("CREDENTIALS_DIRECTORY")
    assert autounlock.try_unlock() is False
    assert not keystore.is_unlocked()


def test_auto_unlock_ignores_passphrase_in_environment(sealed, monkeypatch):
    monkeypatch.setenv("PHYSIO_PASSPHRASE", PASS)
    monkeypatch.setenv(autounlock.CREDENTIAL_NAME, PASS)
    assert autounlock.try_unlock() is False and not keystore.is_unlocked()


def test_server_start_calls_auto_unlock(sealed):
    sealed.write_text(PASS)
    sys.modules.pop("config.wsgi", None)
    importlib.import_module("config.wsgi")
    assert keystore.current() == MASTER


# --- Audit-Trigger ---

def test_database_blocks_audit_update_and_delete_even_via_sql(in_alpha):
    log("x")
    with connections["t_alpha"].cursor() as cur:
        with pytest.raises((OperationalError, IntegrityError), match="append-only"):
            cur.execute("update audit_auditentry set action='y'")
    with connections["t_alpha"].cursor() as cur:
        with pytest.raises((OperationalError, IntegrityError), match="append-only"):
            cur.execute("delete from audit_auditentry")


def test_verify_audit_command(alpha, beta, capsys):
    for t in (alpha, beta):
        with tenancy.tenant_context(t):
            log("a")
    call_command("verify_audit")
    out = capsys.readouterr().out
    assert "alpha: OK" in out and "beta: OK" in out
    with connections["t_alpha"].cursor() as cur:
        cur.execute("drop trigger audit_no_update")
        cur.execute("update audit_auditentry set action='manipuliert'")
    with pytest.raises(CommandError, match="alpha"):
        call_command("verify_audit")


# --- Sicherung ---

def test_backup_roundtrip_and_integrity(tmp_path, alpha, beta, settings, monkeypatch):
    # In den Tests liegen die Mandanten-DBs in :memory:. Für die Sicherung legen wir echte Dateien an.
    settings.BELEG_DIR = tmp_path / "belege"
    (settings.BELEG_DIR / "alpha").mkdir(parents=True)
    (settings.BELEG_DIR / "alpha" / "x.bin").write_bytes(b"verschluesselt-egal")
    paths = {"default": tmp_path / "praxis.sqlite3", "t_alpha": tmp_path / "alpha.sqlite3", "t_beta": tmp_path / "beta.sqlite3"}
    for alias, path in paths.items():
        conn = sqlite3.connect(path)
        conn.execute("create table t (v text)")
        conn.execute("insert into t values (?)", (f"geheim-{alias}",))
        conn.commit()
        conn.close()
        monkeypatch.setitem(connections.databases[alias], "NAME", str(path))
    out = tmp_path / "sicherung.pbk"
    manifest = backup.create_backup(out, "backup-passphrase-123")
    assert {"db/default.sqlite3", "db/tenant_alpha.sqlite3", "db/tenant_beta.sqlite3", "belege/alpha/x.bin"} <= set(manifest["files"])
    raw = out.read_bytes()
    assert b"geheim-t_alpha" not in raw and b"SQLite format" not in raw  # nichts im Klartext
    target = tmp_path / "restore"
    backup.restore_backup(out, "backup-passphrase-123", target)
    restored = sqlite3.connect(target / "db" / "tenant_alpha.sqlite3").execute("select v from t").fetchone()[0]
    assert restored == "geheim-t_alpha"
    assert (target / "belege" / "alpha" / "x.bin").read_bytes() == b"verschluesselt-egal"
    with pytest.raises(backup.BackupError, match="leer"):
        backup.restore_backup(out, "backup-passphrase-123", target)  # überschreibt nie
    with pytest.raises(backup.BackupError, match="falsch"):
        backup.restore_backup(out, "falsche-passphrase-xyz", tmp_path / "r2")
    with pytest.raises(backup.BackupError, match="existiert"):
        backup.create_backup(out, "backup-passphrase-123")


def test_backup_rejects_short_passphrase_corruption_and_junk(tmp_path, alpha, settings):
    settings.BELEG_DIR = tmp_path / "belege"
    with pytest.raises(backup.BackupError, match="mindestens"):
        backup.create_backup(tmp_path / "x.pbk", "kurz")
    junk = tmp_path / "junk.pbk"
    junk.write_bytes(b"nichts")
    with pytest.raises(backup.BackupError, match="gültige"):
        backup.restore_backup(junk, "backup-passphrase-123", tmp_path / "t")
