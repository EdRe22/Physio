"""Versiegelter Master-Schlüssel und Entsperr-Ablauf beim Systemstart.

Der Master-Schlüssel liegt nie im Klartext auf der Platte. Er steht in einer Schlüsseldatei, verschlüsselt mit
einem Schlüssel, der aus Passphrase (und optional einer Schlüsseldatei, z. B. von einem USB-Stick) per scrypt
abgeleitet wird. Nach dem Start ist die App gesperrt, bis der Eigentümer sie entsperrt. Der entschlüsselte
Master-Schlüssel lebt nur im Arbeitsspeicher dieses Prozesses (deshalb genau ein Serverprozess, siehe DEPLOYMENT.md).

Eine Wiederherstellungsdatei enthält denselben Master-Schlüssel unter einer zweiten Passphrase und gehört
getrennt in den Tresor (Ersatz bei Verlust von Passphrase oder Token).

Schnittstelle für ein echtes Hardware-Token (z. B. YubiKey HMAC-Challenge-Response): ein weiterer Faktor wird
wie `keyfile` in die Ableitung eingemischt. Das ist noch NICHT angebunden.
"""
import base64
import hashlib
import json
import os
import threading
import time

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

_AAD = b"physio/masterkey-file/v1"
_PARAMS = {"n": 2**15, "r": 8, "p": 1}
MIN_PASSPHRASE = 14

_lock = threading.Lock()
_key = None
_failures = 0
_blocked_until = 0.0


class KeyUnlockError(Exception):
    """Entsperren fehlgeschlagen (Meldung ohne Details, damit nichts über den Schlüssel verraten wird)."""


def _kek(passphrase: str, salt: bytes, params: dict, keyfile: bytes) -> bytes:
    secret = passphrase.encode("utf-8")
    if keyfile:
        secret += b"\0" + hashlib.sha256(keyfile).digest()
    return Scrypt(salt=salt, length=32, n=params["n"], r=params["r"], p=params["p"]).derive(secret)


def seal(master: bytes, passphrase: str, keyfile: bytes = b"", params: dict | None = None) -> str:
    if len(master) != 32:
        raise ValueError("Master-Schlüssel muss 32 Byte lang sein.")
    if len(passphrase) < MIN_PASSPHRASE:
        raise ValueError(f"Die Passphrase muss mindestens {MIN_PASSPHRASE} Zeichen lang sein.")
    params = params or _PARAMS
    salt, nonce = os.urandom(16), os.urandom(12)
    ct = AESGCM(_kek(passphrase, salt, params, keyfile)).encrypt(nonce, master, _AAD)
    return json.dumps({
        "v": 1, "kdf": "scrypt", **params, "keyfile": bool(keyfile),
        "salt": base64.b64encode(salt).decode(), "nonce": base64.b64encode(nonce).decode(),
        "ct": base64.b64encode(ct).decode(),
    })


def open_sealed(blob: str, passphrase: str, keyfile: bytes = b"") -> bytes:
    try:
        data = json.loads(blob)
        params = {k: int(data[k]) for k in ("n", "r", "p")}
        if params["n"] > 2**20 or params["r"] > 16 or params["p"] > 4:
            raise ValueError  # Schutz vor manipulierten Parametern (Speicherbombe)
        if data["keyfile"] and not keyfile:
            raise KeyUnlockError("Schlüsseldatei erforderlich.")
        salt, nonce, ct = (base64.b64decode(data[k]) for k in ("salt", "nonce", "ct"))
        return AESGCM(_kek(passphrase, salt, params, keyfile if data["keyfile"] else b"")).decrypt(nonce, ct, _AAD)
    except KeyUnlockError:
        raise
    except (InvalidTag, ValueError, KeyError, TypeError):
        raise KeyUnlockError("Entsperren fehlgeschlagen.")


# --- Zustand im Prozess ---

def current():
    return _key


def is_unlocked() -> bool:
    return _key is not None


def lock():
    global _key
    with _lock:
        _key = None


def retry_wait() -> int:
    return max(0, int(_blocked_until - time.monotonic() + 0.999))


def unlock(path, passphrase: str, keyfile: bytes = b"") -> None:
    """Entsperrt mit Bremse gegen Raten: ab dem 3. Fehlversuch verdoppelt sich die Wartezeit (max. 15 Minuten)."""
    global _key, _failures, _blocked_until
    with _lock:
        if retry_wait():
            raise KeyUnlockError(f"Zu viele Versuche, bitte {retry_wait()} Sekunden warten.")
        try:
            with open(path, encoding="utf-8") as fh:
                blob = fh.read()
            _key = open_sealed(blob, passphrase, keyfile)
            _failures = 0
        except OSError:
            raise KeyUnlockError("Schlüsseldatei nicht lesbar.")
        except KeyUnlockError:
            _failures += 1
            if _failures >= 3:
                _blocked_until = time.monotonic() + min(900, 5 * 2 ** (_failures - 3))
            raise


def reset_for_tests():
    global _key, _failures, _blocked_until
    _key, _failures, _blocked_until = None, 0, 0.0
