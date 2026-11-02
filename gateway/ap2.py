"""AP2 v0.2 mandates for each purchase (focused subset).

Spec: google-agentic-commerce/AP2 `docs/ap2/specification.md` (v0.2, commit e1ea56d, 2026-04-29).
Emitted per purchase, as SD-JWTs signed by the gateway acting as the user's Trusted Surface:

- open Checkout Mandate  (vct mandate.checkout.open.1) with `checkout.allowed_merchants`
- open Payment Mandate   (vct mandate.payment.open.1)  with `payment.amount_range`,
  `payment.allowed_payees`
- closed Checkout Mandate (vct mandate.checkout.1): the merchant's ES256 `checkout_jwt` and
  `checkout_hash` = base64url(SHA-256(checkout_jwt))
- closed Payment Mandate  (vct mandate.payment.1): `transaction_id` = checkout_hash, payee,
  amount in minor units, payment instrument (PayPal)
- Payment Receipt (JWT): `reference` = hash of the closed Payment Mandate, PayPal ids

Deliberately not implemented (stated wherever these are shown): the delegate SD-JWT key-binding
chain (closed mandates here are signed with the same key the open mandates name in `cnf`,
instead of a Key Binding JWT), selective disclosure, and merchant-issued Checkout Receipts.
The user's own approval is a passkey signature carried as an extension claim.
"""

import time
from datetime import datetime
from decimal import Decimal
from typing import Any

from gateway.canonical import canonical_json, sha256_hex
from gateway.signing.jws import (
    EcSigner,
    JwtError,
    decode_jwt,
    sd_hash,
    sha256_b64url,
    verify_jwt,
    verify_sd_jwt,
)

SPEC = "AP2 v0.2 (google-agentic-commerce/AP2 e1ea56d, 2026-04-29)"
EXT = "https://spend-guardrail.dev/ap2"  # collision-resistant prefix for our extension claims
PAYPAL_INSTRUMENT = {"id": "paypal-wallet", "type": "paypal",
                     "description": "PayPal wallet (sandbox)"}
NOT_IMPLEMENTED = ["delegate SD-JWT key binding (closed mandates are signed by the cnf key)",
                   "selective disclosure", "merchant-issued Checkout Receipts"]


def minor(amount: str | Decimal) -> int:
    return int((Decimal(str(amount)) * 100).quantize(Decimal(1)))


def merchant_obj(merchant_id: str, names: dict[str, str]) -> dict[str, str]:
    return {"id": merchant_id, "name": names.get(merchant_id, merchant_id)}


def build_bundle(signer: EcSigner, *, intent_id: str, mandate: dict[str, Any],
                 intent_approved_at: int, user_signature: dict | None,
                 merchant_id: str, merchant_names: dict[str, str], checkout_jwt: str,
                 total: str, currency: str, paypal_order_id: str,
                 paypal_capture_id: str | None, gateway_url: str) -> dict[str, Any]:
    now = int(time.time())
    exp = int(_iso_to_epoch(mandate["expires_at"]))
    user_auth = ({"type": "webauthn", "record_hash": sha256_hex(canonical_json(user_signature))}
                 if user_signature else {"type": "password_session"})
    cnf = {"jwk": signer.jwk}
    open_checkout = signer.sign_sd_jwt({
        "vct": "mandate.checkout.open.1", "iat": intent_approved_at, "exp": exp, "cnf": cnf,
        "constraints": [{"type": "checkout.allowed_merchants",
                         "allowed": [merchant_obj(m, merchant_names) for m in mandate["merchants"]]}],
        f"{EXT}/intent_id": intent_id, f"{EXT}/user_authorization": user_auth,
    })
    open_payment = signer.sign_sd_jwt({
        "vct": "mandate.payment.open.1", "iat": intent_approved_at, "exp": exp, "cnf": cnf,
        "payment_instrument": PAYPAL_INSTRUMENT,
        "constraints": [
            {"type": "payment.amount_range", "currency": mandate["max_total"]["currency"],
             "max": minor(mandate["max_total"]["amount"])},
            {"type": "payment.allowed_payees",
             "allowed": [merchant_obj(m, merchant_names) for m in mandate["merchants"]]},
        ],
        f"{EXT}/intent_id": intent_id, f"{EXT}/user_authorization": user_auth,
    })
    checkout_hash = sha256_b64url(checkout_jwt)
    offer_exp = decode_jwt(checkout_jwt)[1].get("exp", now + 900)
    checkout_mandate = signer.sign_sd_jwt({
        "vct": "mandate.checkout.1", "iat": now, "exp": offer_exp,
        "checkout_jwt": checkout_jwt, "checkout_hash": checkout_hash,
        f"{EXT}/open_mandate_hash": sd_hash(open_checkout),
    })
    payment_mandate = signer.sign_sd_jwt({
        "vct": "mandate.payment.1", "iat": now, "exp": offer_exp,
        "transaction_id": checkout_hash,
        "payee": merchant_obj(merchant_id, merchant_names),
        "payment_amount": {"amount": minor(total), "currency": currency},
        "payment_instrument": PAYPAL_INSTRUMENT,
        f"{EXT}/open_mandate_hash": sd_hash(open_payment),
    })
    payment_receipt = None
    if paypal_capture_id:  # only once PayPal has actually captured the payment
        payment_receipt = signer.sign_jwt({
            "status": "Success", "iss": gateway_url, "iat": now,
            "reference": sd_hash(payment_mandate), "payment_id": paypal_order_id,
            "psp_confirmation_id": paypal_capture_id,
            f"{EXT}/relayed_from": "PayPal Orders v2 capture",
        })
    return {"spec": SPEC, "not_implemented": NOT_IMPLEMENTED, "signer_kid": signer.kid,
            "open_checkout_mandate": open_checkout, "open_payment_mandate": open_payment,
            "checkout_mandate": checkout_mandate, "payment_mandate": payment_mandate,
            "payment_receipt": payment_receipt}


def _iso_to_epoch(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


def verify_bundle(bundle: dict[str, Any] | None, gateway_jwk: dict[str, Any],
                  merchant_jwk: dict[str, Any]) -> dict[str, Any]:
    """AP2 v0.2 'Dispute' verification (subset), from the bundle and two public keys only."""
    if not bundle:
        return {"present": False, "valid": None, "checks": []}
    checks: list[dict[str, Any]] = []

    def check(name: str, fn) -> Any:
        try:
            result = fn()
            ok = result is not False
        except (JwtError, KeyError, TypeError, ValueError) as e:
            result, ok = None, False
            name = f"{name} ({e})"
        checks.append({"name": name, "ok": ok})
        return result if ok else None

    oc = check("Open Checkout Mandate signed by the Trusted Surface (mandate.checkout.open.1)",
               lambda: _vct(verify_sd_jwt(bundle["open_checkout_mandate"], gateway_jwk),
                            "mandate.checkout.open.1"))
    op = check("Open Payment Mandate signed by the Trusted Surface (mandate.payment.open.1)",
               lambda: _vct(verify_sd_jwt(bundle["open_payment_mandate"], gateway_jwk),
                            "mandate.payment.open.1"))
    cm = check("Checkout Mandate signed (mandate.checkout.1)",
               lambda: _vct(verify_sd_jwt(bundle["checkout_mandate"], gateway_jwk),
                            "mandate.checkout.1"))
    pm = check("Payment Mandate signed (mandate.payment.1)",
               lambda: _vct(verify_sd_jwt(bundle["payment_mandate"], gateway_jwk),
                            "mandate.payment.1"))
    checkout = None
    if cm:
        checkout = check("Checkout JWT signed by the merchant (ES256)",
                         lambda: verify_jwt(cm["checkout_jwt"], merchant_jwk))
        check("checkout_hash recomputed from checkout_jwt",
              lambda: cm["checkout_hash"] == sha256_b64url(cm["checkout_jwt"]))
    if oc and cm:
        check("Closed mandates signed by the key named in the open mandate (cnf)",
              lambda: oc["cnf"]["jwk"].get("x") == gateway_jwk.get("x"))
        check("Constraint checkout.allowed_merchants",
              lambda: _constraint(oc, "checkout.allowed_merchants") and checkout and any(
                  m["id"] == checkout.get("merchant_id")
                  for m in _constraint(oc, "checkout.allowed_merchants")["allowed"]))
    if pm and cm:
        check("Payment Mandate bound to this checkout (transaction_id = checkout_hash)",
              lambda: pm["transaction_id"] == cm["checkout_hash"])
        if checkout and checkout.get("total"):
            check("Payment amount equals the merchant's checkout total",
                  lambda: pm["payment_amount"] == {"amount": minor(checkout["total"]["amount"]),
                                                   "currency": checkout["total"]["currency"]})
    if op and pm:
        rng = _constraint(op, "payment.amount_range")
        check("Constraint payment.amount_range",
              lambda: rng and rng["currency"] == pm["payment_amount"]["currency"]
              and pm["payment_amount"]["amount"] <= rng["max"])
        payees = _constraint(op, "payment.allowed_payees")
        check("Constraint payment.allowed_payees",
              lambda: payees and any(p["id"] == pm["payee"]["id"] for p in payees["allowed"]))
    if bundle.get("payment_receipt") and pm:
        rc = check("Payment Receipt signed", lambda: verify_jwt(bundle["payment_receipt"],
                                                                gateway_jwk))
        if rc:
            check("Payment Receipt references this Payment Mandate",
                  lambda: rc["reference"] == sd_hash(bundle["payment_mandate"]))
    return {"present": True, "valid": all(c["ok"] for c in checks), "checks": checks,
            "spec": bundle.get("spec"), "not_implemented": bundle.get("not_implemented"),
            "has_payment_receipt": bool(bundle.get("payment_receipt"))}


def _vct(payload: dict[str, Any], expected: str) -> dict[str, Any]:
    if payload.get("vct") != expected:
        raise ValueError(f"vct is {payload.get('vct')}, expected {expected}")
    return payload


def _constraint(mandate: dict[str, Any], ctype: str) -> dict[str, Any] | None:
    return next((c for c in mandate.get("constraints", []) if c.get("type") == ctype), None)
