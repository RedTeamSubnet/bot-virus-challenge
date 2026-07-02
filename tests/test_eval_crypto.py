"""Wire-format contract for the browser-side /_eval submission.

The SDK encrypts the payload in the browser with a hybrid scheme and POSTs it to
/_eval as ``{"error": {"data": "<encKey>::<encIv>::<cipherText>"}}``. The server
decrypts it with the private ``vault_unlock`` wheel. This test pins that wire
format: it builds a blob exactly the way the JS SDK does (RSA-OAEP-SHA256 for the
AES key and IV, AES-256-CBC/PKCS7 for the body, base64 parts joined by ``::``)
and asserts the real decryptor recovers the plaintext.

If ``vault_unlock`` is not installed (local dev / CI without the private wheel),
the round trip is verified against the public ``cryptography`` decryptor instead,
so the format contract is still enforced.
"""

import os
import json
import base64

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

import re

# Must match api/core/constants/_regex.py ALPHANUM_CUSTOM_REGEX.
_DATA_REGEX = re.compile(r"^[0-9a-zA-Z_\-:+/=]+$")


def _gen_keypair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return priv, priv_pem


def _sdk_style_encrypt(plaintext: str, public_key) -> str:
    """Replicate the JS SDK's hybrid encryption -> '<encKey>::<encIv>::<ct>'."""
    aes_key = os.urandom(32)
    iv = os.urandom(16)

    _padder = PKCS7(128).padder()
    _padded = _padder.update(plaintext.encode()) + _padder.finalize()
    _enc = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).encryptor()
    _ct = _enc.update(_padded) + _enc.finalize()

    def _rsa(data: bytes) -> bytes:
        return public_key.encrypt(
            data,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )

    _b64 = lambda b: base64.b64encode(b).decode()
    return "::".join((_b64(_rsa(aes_key)), _b64(_rsa(iv)), _b64(_ct)))


def _payload() -> dict:
    return {
        "schemaVersion": "bv-runtime-1",
        "sessionId": "sess-1",
        "movements": [],
        "clicks": [],
        "mouseDowns": [],
        "mouseUps": [],
        "keydowns": [],
        "keyups": [],
        "scroll": [],
        "browserInfo": {"userAgent": "Chrome", "webdriver": False},
        "automation": {"webdriver": False},
        "sessionBinding": {"nonce": "nonce-abc123"},
    }


def test_blob_charset_is_eval_compatible() -> None:
    priv, _ = _gen_keypair()
    blob = _sdk_style_encrypt(json.dumps(_payload()), priv.public_key())
    assert _DATA_REGEX.match(blob), "blob must match the /_eval data charset"
    assert blob.count("::") == 2, "blob must be encKey::encIv::cipherText"


def test_vault_unlock_decrypts_sdk_style_blob() -> None:
    vault_unlock = pytest.importorskip(
        "vault_unlock", reason="private vault_unlock wheel not installed"
    )
    priv, priv_pem = _gen_keypair()
    original = _payload()
    blob = _sdk_style_encrypt(json.dumps(original), priv.public_key())

    plaintext = vault_unlock.decrypt_payload(
        encrypted_text=blob, private_key_pem=priv_pem
    )
    assert json.loads(plaintext) == original


def test_public_crypto_round_trip_matches() -> None:
    # Format contract enforced even without the private wheel.
    priv, _ = _gen_keypair()
    original = _payload()
    blob = _sdk_style_encrypt(json.dumps(original), priv.public_key())

    enc_key_b64, enc_iv_b64, ct_b64 = blob.split("::")

    def _rsa_dec(data: bytes) -> bytes:
        return priv.decrypt(
            data,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )

    aes_key = _rsa_dec(base64.b64decode(enc_key_b64))
    iv = _rsa_dec(base64.b64decode(enc_iv_b64))
    assert len(aes_key) == 32 and len(iv) == 16

    _dec = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).decryptor()
    _padded = _dec.update(base64.b64decode(ct_b64)) + _dec.finalize()
    _unpadder = PKCS7(128).unpadder()
    plaintext = (_unpadder.update(_padded) + _unpadder.finalize()).decode()

    assert json.loads(plaintext) == original
