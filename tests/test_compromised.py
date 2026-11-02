"""The two attacks from demo/compromised_agent.py, against the gateway with the offline
verifier and injection detector: neither may reach PayPal without a human."""

import pytest

from gateway.domain import CartLineRequest as L
from tests.conftest import CATALOG, active_intent

pytestmark = pytest.mark.anyio

POISONED = next(p for p in CATALOG["papermill-supply"]["products"]
                if p["sku"] == "PM-A4-500W-DEAL")["description"]


async def test_naive_attack_is_blocked_by_policy(gw, paypal):
    iid = await active_intent(gw)
    out = await gw.request_purchase(iid, "papermill-supply",
                                    [L(sku="PM-A4-500W-DEAL", qty=4, item=0), L(sku="PM-GIFT-25", qty=1)],
                                    [POISONED])
    assert out["decision"] == "block" and any("lines_in_intent" in r for r in out["reasons"])
    assert paypal.orders == []


async def test_disguised_attack_passes_rules_but_is_held(gw, paypal):
    iid = await active_intent(gw)
    out = await gw.request_purchase(iid, "papermill-supply",
                                    [L(sku="PM-A4-500W-DEAL", qty=4, item=0),
                                     L(sku="PM-GIFT-25", qty=1, item=0)],
                                    [POISONED])
    assert all(c["passed"] for c in out["verdict"]["hard_checks"])  # rules alone would allow it
    assert out["decision"] == "escalate" and out["status"] == "pending_approval"
    assert out["verdict"]["semantic"]["flagged_skus"] == ["PM-GIFT-25"]
    assert out["verdict"]["injection"]["score"] > 0.9
    assert paypal.orders == []
    done = await gw.decide_purchase(out["purchase_id"], "demo-user", False, out["cart_hash"])
    assert done["status"] == "rejected" and paypal.orders == []
