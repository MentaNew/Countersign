"""A failing or slow model check must hold the cart for a person, never pass it or crash."""

import asyncio

import pytest

from gateway.domain import CartLineRequest as L
from gateway.policy import evaluate
from tests.conftest import active_intent

pytestmark = pytest.mark.anyio


class Broken:
    async def evaluate(self, mandate, lines):
        raise RuntimeError("Groq is down")

    async def score(self, texts):
        raise ConnectionError("Groq is down")


class Slow:
    async def evaluate(self, mandate, lines):
        await asyncio.sleep(5)


async def test_verifier_down_holds_an_otherwise_approvable_cart(gw, paypal):
    gw.verifier = Broken()
    iid = await active_intent(gw)
    out = await gw.request_purchase(iid, "papermill-supply", [L(sku="PM-A4-500W", qty=4, item=0)])
    assert out["decision"] == "escalate" and out["status"] == "pending_approval"
    assert any("semantic check unavailable (RuntimeError)" in r for r in out["reasons"])
    assert paypal.orders == []


async def test_injection_screen_down_holds_the_cart(gw, paypal):
    gw.injection = Broken()
    iid = await active_intent(gw)
    out = await gw.request_purchase(iid, "papermill-supply", [L(sku="PM-A4-500W", qty=4, item=0)],
                                    ["Great paper."])
    assert out["decision"] == "escalate" and paypal.orders == []


async def test_timeout_holds_the_cart(gw, paypal, monkeypatch):
    monkeypatch.setattr(evaluate, "CHECK_TIMEOUT", 0.05)
    gw.verifier = Slow()
    iid = await active_intent(gw)
    out = await gw.request_purchase(iid, "papermill-supply", [L(sku="PM-A4-500W", qty=4, item=0)])
    assert out["decision"] == "escalate" and any("TimeoutError" in r for r in out["reasons"])


async def test_a_rule_block_stays_a_block_when_models_fail(gw):
    gw.verifier, gw.injection = Broken(), Broken()
    iid = await active_intent(gw)
    out = await gw.request_purchase(iid, "quickbuy-outlet", [L(sku="QB-A4-500", qty=1, item=0)],
                                    ["text"])
    assert out["decision"] == "block"


@pytest.mark.anyio
async def test_repeat_order_wording_is_held_even_when_the_model_approves():
    from datetime import UTC, datetime

    from gateway.domain import Money, ResolvedLine
    from gateway.policy.decision import Thresholds
    from gateway.policy.evaluate import evaluate_cart
    from gateway.policy.hard_checks import SpendState
    from gateway.verifier.stub import StubInjectionDetector
    from tests.conftest import paper_mandate

    class Approves:
        async def evaluate(self, mandate, lines):
            from gateway.domain import SemanticVerdict
            return SemanticVerdict(match={"matches": 1.0, "partial": 0.0, "mismatch": 0.0},
                                   extra_items=0.0, model="fake", rationale="same product")

    line = ResolvedLine(sku="PM-A4-SUB", title="A4 Copy Paper, 500 sheets (Subscribe & Save, every 4 weeks)",
                        unit_price=Money(amount="3.50", currency="USD"), qty=2, item=0)
    v = await evaluate_cart(paper_mandate(approval_above=None), "papermill-supply", [line],
                            SpendState(), datetime.now(UTC), Approves(), StubInjectionDetector(),
                            [], Thresholds())
    assert v.decision == "escalate" and "repeat orders" in v.reasons[0]
