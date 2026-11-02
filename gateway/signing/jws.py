"""Minimal ES256 JWTs and SD-JWTs (RFC 7515/7519, SD-JWT), enough for AP2 v0.2 mandates and
merchant Checkout JWTs. AP2 requires a non-deterministic scheme (ECDSA) for the Checkout JWT,
so these use P-256 rather than the gateway's Ed25519 receipt key."""

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)


class JwtError(Exception):
    pass


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sha256_b64url(text: str) -> str:
    """AP2's hash encoding (e.g. checkout_hash): base64url(SHA-256(value))."""
    return b64url(hashlib.sha256(text.encode()).digest())


def _compact_json(obj: Any) -> bytes:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True).encode()


class EcSigner:
    """A P-256 key that signs ES256 JWTs and publishes itself as a JWK."""

    def __init__(self, key: ec.EllipticCurvePrivateKey):
        self._key = key
        nums = key.public_key().public_numbers()
        self.jwk = {"kty": "EC", "crv": "P-256",
                    "x": b64url(nums.x.to_bytes(32, "big")),
                    "y": b64url(nums.y.to_bytes(32, "big"))}
        # RFC 7638 thumbprint as key id
        self.kid = b64url(hashlib.sha256(_compact_json(
            {"crv": "P-256", "kty": "EC", "x": self.jwk["x"], "y": self.jwk["y"]})).digest())[:16]
        self.jwk = {**self.jwk, "kid": self.kid, "alg": "ES256", "use": "sig"}

    @classmethod
    def ephemeral(cls) -> "EcSigner":
        return cls(ec.generate_private_key(ec.SECP256R1()))

    @classmethod
    def from_pem(cls, pem: bytes) -> "EcSigner":
        key = serialization.load_pem_private_key(pem, password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise TypeError("not an EC private key")
        return cls(key)

    @classmethod
    def from_file(cls, path: str | Path) -> "EcSigner":
        path = Path(path)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            key = ec.generate_private_key(ec.SECP256R1())
            path.touch(mode=0o600)
            path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                               serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
            return cls(key)
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise TypeError(f"{path} is not an EC private key")
        return cls(key)

    def sign_jwt(self, payload: dict[str, Any], typ: str = "JWT") -> str:
        header = {"alg": "ES256", "kid": self.kid, "typ": typ}
        signing_input = f"{b64url(_compact_json(header))}.{b64url(_compact_json(payload))}"
        der = self._key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        return f"{signing_input}.{b64url(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"

    def sign_sd_jwt(self, payload: dict[str, Any]) -> str:
        """An SD-JWT with every claim disclosed in the clear (no selective disclosure)."""
        return self.sign_jwt({**payload, "_sd_alg": "sha-256"}, typ="dc+sd-jwt") + "~"


def _public_key(jwk: dict[str, Any]) -> ec.EllipticCurvePublicKey:
    if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        raise JwtError("unsupported key (expected EC P-256)")
    return ec.EllipticCurvePublicNumbers(
        int.from_bytes(b64url_decode(jwk["x"]), "big"),
        int.from_bytes(b64url_decode(jwk["y"]), "big"), ec.SECP256R1()).public_key()


def decode_jwt(token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """(header, payload) without verifying."""
    try:
        h, p, _ = token.split(".")
        return json.loads(b64url_decode(h)), json.loads(b64url_decode(p))
    except ValueError as e:
        raise JwtError(f"malformed JWT ({e})") from e


def verify_jwt(token: str, jwk: dict[str, Any]) -> dict[str, Any]:
    """Verify an ES256 JWT with `jwk`; returns the payload or raises JwtError."""
    header, payload = decode_jwt(token)
    if header.get("alg") != "ES256":
        raise JwtError(f"unexpected alg {header.get('alg')}")
    signing_input, _, sig = token.rpartition(".")
    raw = b64url_decode(sig)
    if len(raw) != 64:
        raise JwtError("bad ES256 signature length")
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    try:
        _public_key(jwk).verify(der, signing_input.encode(), ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as e:
        raise JwtError("signature does not verify") from e
    return payload


def verify_sd_jwt(token: str, jwk: dict[str, Any]) -> dict[str, Any]:
    """Verify the issuer-signed part of an SD-JWT (disclosures, if any, are not used here)."""
    issuer_jwt = token.split("~", 1)[0]
    payload = verify_jwt(issuer_jwt, jwk)
    if payload.get("_sd"):
        raise JwtError("selectively disclosed claims are not supported by this verifier")
    return payload


def sd_hash(token: str) -> str:
    """Hash of a presented SD-JWT, as AP2 receipts reference it (same as `sd_hash`)."""
    return sha256_b64url(token)
