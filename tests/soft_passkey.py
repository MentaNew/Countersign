"""A software passkey for tests: produces the same WebAuthn registration and assertion
messages a browser and Touch ID would (attestation "none", ES256)."""

import hashlib
import json
import os
import struct

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.encode_cbor import encode_cbor


class SoftPasskey:
    def __init__(self, rp_id: str, origin: str):
        self.rp_id, self.origin = rp_id, origin
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(16)
        self.count = 0

    def _cose_public_key(self) -> bytes:
        nums = self.key.public_key().public_numbers()
        return encode_cbor({1: 2, 3: -7, -1: 1, -2: nums.x.to_bytes(32, "big"),
                            -3: nums.y.to_bytes(32, "big")})

    def _client_data(self, kind: str, challenge_b64: str) -> bytes:
        return json.dumps({"type": kind, "challenge": challenge_b64, "origin": self.origin,
                           "crossOrigin": False}).encode()

    def register(self, options: dict) -> dict:
        rp_hash = hashlib.sha256(self.rp_id.encode()).digest()
        attested = (bytes(16) + struct.pack(">H", len(self.credential_id)) + self.credential_id
                    + self._cose_public_key())
        auth_data = rp_hash + bytes([0x01 | 0x04 | 0x40]) + struct.pack(">I", self.count) + attested
        att = encode_cbor({"fmt": "none", "attStmt": {}, "authData": auth_data})
        cid = bytes_to_base64url(self.credential_id)
        return {"id": cid, "rawId": cid, "type": "public-key", "response": {
            "clientDataJSON": bytes_to_base64url(self._client_data("webauthn.create", options["challenge"])),
            "attestationObject": bytes_to_base64url(att), "transports": ["internal"]},
            "clientExtensionResults": {}}

    def sign(self, options: dict, origin: str | None = None) -> dict:
        self.count += 1
        rp_hash = hashlib.sha256(self.rp_id.encode()).digest()
        auth_data = rp_hash + bytes([0x01 | 0x04]) + struct.pack(">I", self.count)
        client = json.dumps({"type": "webauthn.get", "challenge": options["challenge"],
                             "origin": origin or self.origin, "crossOrigin": False}).encode()
        sig = self.key.sign(auth_data + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        cid = bytes_to_base64url(self.credential_id)
        return {"id": cid, "rawId": cid, "type": "public-key", "response": {
            "clientDataJSON": bytes_to_base64url(client),
            "authenticatorData": bytes_to_base64url(auth_data),
            "signature": bytes_to_base64url(sig), "userHandle": None},
            "clientExtensionResults": {}}


__all__ = ["SoftPasskey", "base64url_to_bytes"]
