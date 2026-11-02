"""AP2 v0.2 mandates emitted per purchase, verified with AP2's dispute rules (subset)."""

import copy

import pytest

from gateway.ap2 import verify_bundle
from gateway.domain import CartLineRequest as L
from gateway.signing.jws import decode_jwt, sd_hash, sha256_b64url
from tests.conftest import active_intent


async def bought(gw, paypal, captured=True):
    if captured:
        paypal.status = "COMPLETED"  # vault-style payment: captured at creation
    iid = await active_intent(gw)
    out = await gw.request_purchase(iid, "papermill-supply", [L(sku="PM-A4-500W", qty=10, item=0)])
    return out, (await gw.get_receipt(out["receipt_id"]))["receipt"]


@pytest.mark.anyio
async def test_receipt_carries_valid_ap2_mandates(gw, paypal):
    out, r = await bought(gw, paypal)
    b = r["ap2"]
    claims = {k: decode_jwt(b[k].split("~")[0])[1] for k in
              ("open_checkout_mandate", "open_payment_mandate", "checkout_mandate", "payment_mandate")}
    assert [claims[k]["vct"] for k in claims] == ["mandate.checkout.open.1", "mandate.payment.open.1",
                                                  "mandate.checkout.1", "mandate.payment.1"]
    cm, pm = claims["checkout_mandate"], claims["payment_mandate"]
    assert cm["checkout_hash"] == sha256_b64url(cm["checkout_jwt"]) == pm["transaction_id"]
    assert pm["payment_amount"] == {"amount": 3890, "currency": "USD"}  # minor units
    assert decode_jwt(b["payment_receipt"])[1]["reference"] == sd_hash(b["payment_mandate"])
    v = await gw.verify_receipt(out["receipt_id"])
    assert v["valid"] and v["ap2"]["valid"] and len(v["ap2"]["checks"]) >= 11


@pytest.mark.anyio
async def test_ap2_checks_catch_tampering(gw, paypal):
    _, r = await bought(gw, paypal)
    gw_jwk = gw.ap2_signer.jwk
    m_jwk = r["cart_mandate"]["merchant_signature"]["jwk"]
    assert verify_bundle(r["ap2"], gw_jwk, m_jwk)["valid"]

    swapped = copy.deepcopy(r["ap2"])  # payment mandate from another purchase
    _, r2 = await bought(gw, paypal)
    swapped["payment_mandate"] = r2["ap2"]["payment_mandate"]
    out = verify_bundle(swapped, gw_jwk, m_jwk)
    failed = [c["name"] for c in out["checks"] if not c["ok"]]
    assert not out["valid"] and any("transaction_id" in f for f in failed)

    other_merchant_key = gw.catalog.signer("northside-office").jwk
    out = verify_bundle(r["ap2"], gw_jwk, other_merchant_key)
    assert not out["valid"] and any("Checkout JWT signed by the merchant" in c["name"]
                                    for c in out["checks"] if not c["ok"])


@pytest.mark.anyio
async def test_no_payment_receipt_until_paypal_captures(gw, paypal):
    _, r = await bought(gw, paypal, captured=False)
    assert r["ap2"]["payment_receipt"] is None  # order awaits the buyer at PayPal
    assert verify_bundle(r["ap2"], gw.ap2_signer.jwk,
                         r["cart_mandate"]["merchant_signature"]["jwk"])["valid"]
