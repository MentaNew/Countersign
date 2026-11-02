"""Merchant-signed cart offers, as AP2 v0.2 Checkout JWTs.

The merchant prices a requested cart and signs it as an ES256 JWT (AP2 requires a
non-deterministic signature for the Checkout JWT). The gateway checks it against the merchant's
key (a JWK pinned the first time it is seen), so titles and prices are attested by the merchant.
The signed offer travels in the receipt and in the AP2 Checkout Mandate, so anyone, including the
seller, can re-check it offline.
"""

import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from gateway.signing.jws import EcSigner, JwtError, verify_jwt
from gateway.signing.keys import verify_with_public_key

OFFER_TTL = timedelta(minutes=30)  # at least as long as a held cart waits for the human


def build_offer(merchant_id: str, priced: list[dict[str, Any]], unavailable: list[str],
                now: datetime | None = None) -> dict[str, Any]:
    """`priced`: [{sku, title, unit_price: {amount, currency}, qty}] in the requested order."""
    now = now or datetime.now(UTC)
    currencies = {ln["unit_price"]["currency"] for ln in priced}
    total = sum((Decimal(ln["unit_price"]["amount"]) * ln["qty"] for ln in priced), Decimal(0))
    return {
        "type": "cart_offer",
        "offer_id": f"off_{secrets.token_hex(8)}",
        "merchant_id": merchant_id,
        "lines": priced,
        "unavailable": unavailable,
        "total": ({"amount": f"{total:.2f}", "currency": currencies.pop()}
                  if len(currencies) == 1 else None),
        "issued_at": now.isoformat(),
        "expires_at": (now + OFFER_TTL).isoformat(),
        "iat": int(now.timestamp()),
        "exp": int((now + OFFER_TTL).timestamp()),
        "iss": merchant_id,
    }


def sign_offer(signer: EcSigner, offer: dict[str, Any]) -> dict[str, Any]:
    return {"checkout_jwt": signer.sign_jwt(offer), "offer": offer,
            "key_id": signer.kid, "jwk": signer.jwk}


def merchant_profile(merchant_id: str, name: str, signer: EcSigner, base_url: str) -> dict[str, Any]:
    """Published at /{merchant_id}/.well-known/agent-merchant.json."""
    base = base_url.rstrip("/")
    return {"merchant_id": merchant_id, "name": name, "website": f"{base}/{merchant_id}",
            "key_id": signer.kid, "jwk": signer.jwk, "jwks": {"keys": [signer.jwk]},
            "offers_url": f"{base}/api/{merchant_id}/cart-offers"}


def same_key(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    return bool(a and b and a.get("x") == b.get("x") and a.get("y") == b.get("y")
                and a.get("crv") == b.get("crv"))


def verify_offer(signed: dict[str, Any], jwk: dict[str, Any], merchant_id: str,
                 requested: list[tuple[str, int]] | None = None,
                 now: datetime | None = None, check_expiry: bool = True) -> tuple[bool, str]:
    """Check a signed offer. `requested` = [(sku, qty)] the agent asked for, in order."""
    try:
        if not same_key(signed.get("jwk"), jwk):
            return False, "offer signed with a key that is not the merchant's"
        try:
            offer = verify_jwt(signed["checkout_jwt"], jwk)
        except JwtError:
            return False, "merchant signature does not verify"
        if signed.get("offer") is not None and signed["offer"] != offer:
            return False, "offer copy differs from what the merchant signed"
        if offer.get("type") != "cart_offer" or offer.get("merchant_id") != merchant_id:
            return False, "offer is for a different merchant"
        if check_expiry and datetime.fromisoformat(offer["expires_at"]) < (now or datetime.now(UTC)):
            return False, "merchant offer expired"
        if requested is not None:
            offered = [(ln["sku"], ln["qty"]) for ln in offer["lines"]]
            wanted = [(sku, qty) for sku, qty in requested if sku not in offer["unavailable"]]
            if offered != wanted:
                return False, "offer lines differ from the cart requested"
        total = sum((Decimal(ln["unit_price"]["amount"]) * ln["qty"] for ln in offer["lines"]),
                    Decimal(0))
        if offer.get("total") and Decimal(offer["total"]["amount"]) != total:
            return False, "offer total does not add up"
    except (KeyError, TypeError, ValueError) as e:
        return False, f"malformed offer ({type(e).__name__})"
    return True, "signed by the merchant (ES256 Checkout JWT)"


def merchant_signature_check(receipt: dict[str, Any]) -> dict[str, Any]:
    """Re-check the merchant's signed offer carried by a receipt, and that it matches the lines
    that were paid. Expiry is not re-checked: the offer was valid when the purchase was made."""
    cart = receipt.get("cart_mandate") or {}
    signed = cart.get("merchant_signature")
    if not signed and cart.get("cart_source") == "page":
        return {"present": False, "valid": None,
                "detail": "not signed by the shop: the checkout guard read this cart from the "
                          "shop's own checkout page, and the user approved it with a passkey"}
    if not signed:
        return {"present": False, "valid": None, "detail": "receipt predates merchant-signed carts"}
    if "checkout_jwt" in signed:
        ok, detail = verify_offer(signed, signed.get("jwk") or {}, receipt.get("merchant_id"),
                                  check_expiry=False)
        offer = signed.get("offer") or {}
    else:  # Oct 21 morning format: Ed25519 signature over the offer
        offer = signed.get("offer") or {}
        ok = verify_with_public_key(signed.get("public_key", ""), offer, signed.get("signature", ""))
        detail = "signed by the merchant (Ed25519, earlier format)" if ok else \
            "merchant signature does not verify"
    paid = [(ln["sku"], ln["qty"], ln["unit_price"]["amount"]) for ln in receipt.get("lines", [])]
    offered = [(ln["sku"], ln["qty"], ln["unit_price"]["amount"]) for ln in offer.get("lines", [])]
    if ok and paid != offered:
        ok, detail = False, "paid lines differ from the merchant's signed offer"
    return {"present": True, "valid": ok, "detail": detail, "key_id": signed.get("key_id"),
            "jwk": signed.get("jwk"), "public_key": signed.get("public_key"),
            "offer_id": offer.get("offer_id")}
