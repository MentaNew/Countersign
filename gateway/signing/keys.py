"""Ed25519 signing for intents, approval tokens and receipts. The key never leaves the gateway."""

import base64
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from gateway.canonical import canonical_json, sha256_hex


class Signer:
    def __init__(self, private_key: Ed25519PrivateKey):
        self._key = private_key
        self._public = private_key.public_key()
        raw = self._public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.public_key_b64 = base64.b64encode(raw).decode()
        self.key_id = sha256_hex(raw)[:16]

    @classmethod
    def from_file(cls, path: str | Path) -> "Signer":
        """Load the key, creating it (mode 0600) on first run."""
        path = Path(path)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            key = Ed25519PrivateKey.generate()
            pem = key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            path.touch(mode=0o600)
            path.write_bytes(pem)
            return cls(key)
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise TypeError(f"{path} is not an Ed25519 private key")
        return cls(key)

    @classmethod
    def from_pem(cls, pem: bytes) -> "Signer":
        key = serialization.load_pem_private_key(pem, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise TypeError("SIGNING_KEY_PEM is not an Ed25519 private key")
        return cls(key)

    @classmethod
    def ephemeral(cls) -> "Signer":
        return cls(Ed25519PrivateKey.generate())

    def sign(self, obj: Any) -> str:
        return base64.b64encode(self._key.sign(canonical_json(obj))).decode()

    def verify(self, obj: Any, signature: str) -> bool:
        return verify_with_public_key(self.public_key_b64, obj, signature)


def verify_with_public_key(public_key_b64: str, obj: Any, signature: str) -> bool:
    """Lets a third party (seller, dispute analyst) check a signature with only the public key."""
    pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
    try:
        pub.verify(base64.b64decode(signature), canonical_json(obj))
        return True
    except (InvalidSignature, ValueError):
        return False
