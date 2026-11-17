import pytest
from cryptography.exceptions import InvalidTag

from core import crypto


def test_roundtrip_and_randomised():
    key = crypto.new_data_key()
    a, b = crypto.encrypt(key, "Maria Muster"), crypto.encrypt(key, "Maria Muster")
    assert a != b  # zufälliger Nonce
    assert crypto.decrypt(key, a) == "Maria Muster"


def test_wrong_key_fails():
    token = crypto.encrypt(crypto.new_data_key(), "geheim")
    with pytest.raises(InvalidTag):
        crypto.decrypt(crypto.new_data_key(), token)


def test_tampering_detected():
    key = crypto.new_data_key()
    token = crypto.encrypt(key, "geheim")
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    with pytest.raises(Exception):
        crypto.decrypt(key, tampered)


def test_data_key_wrap_roundtrip_and_not_plain():
    key = crypto.new_data_key()
    wrapped = crypto.wrap_key(key)
    assert crypto.unwrap_key(wrapped) == key
    assert key.hex() not in wrapped
