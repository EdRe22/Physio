"""Envelope-Verschlüsselung.

- Master-Schlüssel (vom Hardware-Token) schützt die Datenschlüssel der Mandanten.
- Jeder Mandant hat einen eigenen 256-Bit-Datenschlüssel (AES-256-GCM).
- Format der Chiffre: "v1:" + base64(nonce || ciphertext+tag)
"""
import base64
import os

from cryptography.exceptions import InvalidTag  # noqa: F401  (re-export für Aufrufer)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

PREFIX = "v1:"
_WRAP_AAD = b"physio/dek/v1"
_FIELD_AAD = b"physio/field/v1"


def master_key() -> bytes:
    from . import keystore

    if keystore.current() is not None:
        return keystore.current()
    if settings.PHYSIO_KEYFILE:  # Produktion: kein Klartext-Schlüssel aus der Umgebung
        raise ImproperlyConfigured("Master-Schlüssel ist gesperrt (noch nicht entsperrt).")
    raw = settings.PHYSIO_MASTER_KEY_B64
    if not raw:
        raise ImproperlyConfigured("Master-Schlüssel nicht entsperrt (PHYSIO_MASTER_KEY).")
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise ImproperlyConfigured("Master-Schlüssel muss 32 Byte lang sein.")
    return key


def new_data_key() -> bytes:
    return AESGCM.generate_key(bit_length=256)


def _seal(key: bytes, plaintext: bytes, aad: bytes) -> str:
    nonce = os.urandom(12)
    return PREFIX + base64.b64encode(nonce + AESGCM(key).encrypt(nonce, plaintext, aad)).decode()


def _open(key: bytes, token: str, aad: bytes) -> bytes:
    if not token.startswith(PREFIX):
        raise ValueError("Unbekanntes Chiffre-Format.")
    blob = base64.b64decode(token[len(PREFIX):])
    return AESGCM(key).decrypt(blob[:12], blob[12:], aad)


def wrap_key(data_key: bytes) -> str:
    return _seal(master_key(), data_key, _WRAP_AAD)


def unwrap_key(wrapped: str) -> bytes:
    return _open(master_key(), wrapped, _WRAP_AAD)


def encrypt(data_key: bytes, text: str) -> str:
    return _seal(data_key, text.encode("utf-8"), _FIELD_AAD)


def decrypt(data_key: bytes, token: str) -> str:
    return _open(data_key, token, _FIELD_AAD).decode("utf-8")


_FILE_AAD = b"physio/file/v1"


def encrypt_bytes(data_key: bytes, data: bytes) -> bytes:
    """Verschlüsselt Dateiinhalte (Belege): nonce(12) || ciphertext+tag."""
    nonce = os.urandom(12)
    return nonce + AESGCM(data_key).encrypt(nonce, data, _FILE_AAD)


def decrypt_bytes(data_key: bytes, blob: bytes) -> bytes:
    return AESGCM(data_key).decrypt(blob[:12], blob[12:], _FILE_AAD)


def blind_index(data_key: bytes, text: str) -> str:
    """Schlüsselabhängiger Hash für Dublettenprüfung ohne Klartext (HMAC-SHA256, pro Mandant)."""
    import hashlib
    import hmac

    return hmac.new(data_key, b"physio/blind/v1:" + text.encode("utf-8"), hashlib.sha256).hexdigest()
