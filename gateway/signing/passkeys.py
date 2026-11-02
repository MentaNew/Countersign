"""User signatures with passkeys (WebAuthn).

When the user approves an Intent Mandate or a held Cart Mandate, the gateway builds an
*approval statement* (what exactly is being approved) and asks the user's device to sign it.
The WebAuthn challenge is the SHA-256 of that statement, so the device's signature binds to it.

The resulting record (statement + device signature + device public key) goes into receipts and
can be re-verified by anyone with `verify_user_signature`, without trusting the gateway: the
gateway never holds the private key, so it cannot produce such a signature itself.
"""

import hashlib
import json
import secrets
import time
from typing import Any

from cryptography.exceptions import InvalidSignature
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.decode_credential_public_key import decode_credential_public_key
from webauthn.helpers.decoded_public_key_to_cryptography import decoded_public_key_to_cryptography
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)
from webauthn.helpers.verify_signature import verify_signature

from gateway.canonical import canonical_json

CHALLENGE_TTL = 300  # seconds


class PasskeyError(Exception):
    pass


def statement_challenge(statement: dict[str, Any]) -> bytes:
    """The WebAuthn challenge for an approval statement: SHA-256 of its canonical JSON."""
    return hashlib.sha256(canonical_json(statement)).digest()


def new_statement(kind: str, **fields: Any) -> dict[str, Any]:
    return {"kind": kind, **fields, "nonce": secrets.token_hex(16),
            "issued_at": int(time.time())}


class Passkeys:
    """Registration and approval ceremonies for one relying party (the gateway's web app)."""

    def __init__(self, rp_id: str, origin: str, rp_name: str = "Spend Guardrail"):
        self.rp_id = rp_id
        self.origin = origin
        self.rp_name = rp_name
        # Pending ceremonies (single process). Keyed by user or by approval subject.
        self._pending: dict[str, tuple[float, Any]] = {}

    def _put(self, key: str, value: Any) -> None:
        now = time.time()
        # Drop expired ceremonies: anyone can start a passkey sign-in, few finish it.
        if len(self._pending) > 100:
            for k in [k for k, (exp, _) in self._pending.items() if exp < now]:
                del self._pending[k]
        if len(self._pending) > 10_000:
            raise PasskeyError("too many pending passkey requests; try again in a minute")
        self._pending[key] = (now + CHALLENGE_TTL, value)

    def _take(self, key: str) -> Any:
        exp, value = self._pending.pop(key, (0, None))
        if value is None or time.time() > exp:
            raise PasskeyError("no pending request, or it expired; start again")
        return value

    # ------------------------------------------------------------ registration

    def registration_options(self, user: str, existing: list[str]) -> dict[str, Any]:
        opts = generate_registration_options(
            rp_id=self.rp_id, rp_name=self.rp_name, user_name=user,
            user_id=hashlib.sha256(user.encode()).digest()[:16],
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
            exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(c))
                                 for c in existing],
        )
        self._put(f"reg:{user}", opts.challenge)
        return json.loads(options_to_json(opts))

    def verify_registration(self, user: str, credential: dict[str, Any]) -> dict[str, Any]:
        challenge = self._take(f"reg:{user}")
        try:
            v = verify_registration_response(
                credential=credential, expected_challenge=challenge, expected_rp_id=self.rp_id,
                expected_origin=self.origin, require_user_verification=True,
            )
        except WebAuthnException as e:
            raise PasskeyError(f"registration failed: {e}") from e
        return {
            "credential_id": bytes_to_base64url(v.credential_id),
            "public_key": bytes_to_base64url(v.credential_public_key),
            "sign_count": v.sign_count,
            "aaguid": v.aaguid,
        }

    # ---------------------------------------------------------------- approval

    def approval_options(self, subject: str, statement: dict[str, Any],
                         credential_ids: list[str]) -> dict[str, Any]:
        if not credential_ids:
            raise PasskeyError("no passkey registered")
        statement = {**statement, "rp_id": self.rp_id}
        opts = generate_authentication_options(
            rp_id=self.rp_id, challenge=statement_challenge(statement),
            allow_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(c))
                               for c in credential_ids],
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        self._put(f"approve:{subject}", statement)
        return {"options": json.loads(options_to_json(opts)), "statement": statement}

    def verify_approval(self, subject: str, credential: dict[str, Any],
                        stored: dict[str, Any]) -> tuple[dict[str, Any], int]:
        """Check the device's signature over the pending statement. Returns the signature
        record for receipts and the new sign count."""
        statement = self._take(f"approve:{subject}")
        try:
            v = verify_authentication_response(
                credential=credential, expected_challenge=statement_challenge(statement),
                expected_rp_id=self.rp_id, expected_origin=self.origin,
                credential_public_key=base64url_to_bytes(stored["public_key"]),
                credential_current_sign_count=stored["sign_count"],
                require_user_verification=True,
            )
        except WebAuthnException as e:
            raise PasskeyError(f"passkey check failed: {e}") from e
        resp = credential["response"]
        record = {
            "type": "webauthn",
            "statement": statement,
            "credential_id": stored["credential_id"],
            "public_key": stored["public_key"],
            "authenticator_data": resp["authenticatorData"],
            "client_data_json": resp["clientDataJSON"],
            "signature": resp["signature"],
            "rp_id": self.rp_id,
            "origin": self.origin,
            "user_verified": v.user_verified,
        }
        return record, v.new_sign_count


def verify_user_signature(record: dict[str, Any] | None,
                          expected: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Re-check a passkey approval from the record alone (no gateway state needed).

    `expected` (optional) lists statement fields that must match, e.g. {"cart_hash": ...},
    so a valid signature over a *different* cart is rejected.
    """
    if not record or record.get("type") != "webauthn":
        return False, "no passkey signature"
    try:
        statement = record["statement"]
        for k, v in (expected or {}).items():
            if statement.get(k) != v:
                return False, f"signed statement is about a different {k}"
        client_data_raw = base64url_to_bytes(record["client_data_json"])
        client_data = json.loads(client_data_raw)
        if client_data.get("type") != "webauthn.get":
            return False, "not an approval signature"
        want = bytes_to_base64url(statement_challenge(statement))
        if client_data.get("challenge") != want:
            return False, "signature is not over this statement"
        if client_data.get("origin") != record.get("origin"):
            return False, "origin mismatch"
        auth_data = base64url_to_bytes(record["authenticator_data"])
        if auth_data[:32] != hashlib.sha256(record["rp_id"].encode()).digest():
            return False, "signed for a different site"
        flags = auth_data[32]
        if not flags & 0x01:
            return False, "user was not present"
        decoded = decode_credential_public_key(base64url_to_bytes(record["public_key"]))
        verify_signature(
            public_key=decoded_public_key_to_cryptography(decoded),
            signature_alg=decoded.alg,
            signature=base64url_to_bytes(record["signature"]),
            data=auth_data + hashlib.sha256(client_data_raw).digest(),
        )
    except (InvalidSignature, KeyError, ValueError, WebAuthnException) as e:
        return False, f"invalid signature ({type(e).__name__})"
    return True, ("signed on the user's device, user verified" if flags & 0x04
                  else "signed on the user's device")
