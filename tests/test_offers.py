"""Merchant-signed cart offers: the merchant attests titles and prices; the gateway pins its key."""

import copy
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from gateway.catalog.client import price_lines
from gateway.catalog.offers import merchant_signature_check, sign_offer, verify_offer
from gateway.db.models import Purchase
from gateway.domain import CartLineRequest as L
from gateway.signing.jws import EcSigner
from tests.conftest import CATALOG, active_intent

PAPER = CATALOG["papermill-supply"]["products"]


def signed(signer=None, lines=(("PM-A4-500W", 4),)):
    signer = signer or EcSigner.ephemeral()
    offer = price_lines("papermill-supply", PAPER, [{"sku": s, "qty": q} for s, q in lines])
    return signer, sign_offer(signer, offer)


def test_verify_offer_catches_every_kind_of_change():
    signer, so = signed()
    pk = signer.jwk
    assert verify_offer(so, pk, "papermill-supply", [("PM-A4-500W", 4)])[0]
    bad = copy.deepcopy(so)
    bad["offer"]["lines"][0]["unit_price"]["amount"] = "0.01"  # edit the unsigned copy
    assert verify_offer(bad, pk, "papermill-supply")[1] == "offer copy differs from what the merchant signed"
    forged = copy.deepcopy(so)
    h, p_, sig = forged["checkout_jwt"].split(".")
    forged["checkout_jwt"] = f"{h}.{p_}.{sig[:-4]}AAAA"
    assert verify_offer(forged, pk, "papermill-supply")[1] == "merchant signature does not verify"
    assert verify_offer(so, pk, "northside-office")[1] == "offer is for a different merchant"
    assert verify_offer(so, pk, "papermill-supply", [("PM-A4-500W", 5)])[1] == \
        "offer lines differ from the cart requested"
    later = datetime.now(UTC) + timedelta(hours=1)
    assert verify_offer(so, pk, "papermill-supply", now=later)[1] == "merchant offer expired"
    _impostor, so2 = signed()
    assert verify_offer(so2, pk, "papermill-supply")[1] == \
        "offer signed with a key that is not the merchant's"


@pytest.mark.anyio
async def test_receipt_carries_a_verifiable_merchant_signature(gw):
    iid = await active_intent(gw)
    out = await gw.request_purchase(iid, "papermill-supply", [L(sku="PM-A4-500W", qty=10, item=0)])
    checks = {c["name"]: c for c in out["verdict"]["hard_checks"]}
    assert checks["merchant_signed_cart"]["passed"]
    assert "pinned on first use" in checks["merchant_signed_cart"]["detail"]
    v = await gw.verify_receipt(out["receipt_id"])
    assert v["valid"] and v["merchant_signature"]["valid"]
    r = copy.deepcopy(v["receipt"])
    r["lines"][0]["unit_price"]["amount"] = "1.00"  # paid less than the merchant offered
    assert merchant_signature_check(r)["detail"] == "paid lines differ from the merchant's signed offer"


@pytest.mark.anyio
async def test_a_changed_merchant_key_blocks_the_purchase(gw, paypal):
    iid = await active_intent(gw)
    await gw.request_purchase(iid, "papermill-supply", [L(sku="PM-A4-500W", qty=2, item=0)])
    gw.catalog.signers["papermill-supply"] = EcSigner.ephemeral()  # key swapped after pinning
    out = await gw.request_purchase(iid, "papermill-supply", [L(sku="PM-A4-500W", qty=2, item=0)])
    assert out["decision"] == "block"
    assert any("key changed since it was pinned" in r for r in out["reasons"])
    assert len(paypal.orders) == 1


@pytest.mark.anyio
async def test_unknown_merchant_is_blocked(gw):
    iid = await active_intent(gw, merchants=["papermill-supply", "ghost-shop"])
    out = await gw.request_purchase(iid, "ghost-shop", [L(sku="X", qty=1, item=0)])
    assert out["decision"] == "block" and any("not a known merchant" in r for r in out["reasons"])


@pytest.mark.anyio
async def test_expired_offer_cannot_be_approved_later(gw, paypal):
    iid = await active_intent(gw)
    held = await gw.request_purchase(iid, "northside-office",
                                     [L(sku="NO-A4-500W-PREM", qty=10, item=0)])
    assert held["status"] == "pending_approval"
    async with gw.sessions.begin() as s:
        p = await s.get(Purchase, held["purchase_id"])
        offer = copy.deepcopy(p.merchant_offer)
        offer["offer"]["expires_at"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        await s.execute(update(Purchase).where(Purchase.id == p.id).values(merchant_offer=offer))
    done = await gw.decide_purchase(held["purchase_id"], "demo-user", True, held["cart_hash"])
    assert done["status"] == "blocked" and "offer expired" in done["error"]
    assert paypal.orders == []
