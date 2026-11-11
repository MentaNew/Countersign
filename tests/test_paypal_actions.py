"""Guarded PayPal tools (gateway side): a money-moving call to PayPal's own agent tools runs only after the
person approved these exact arguments, only once, and only before the approval expires."""

from datetime import timedelta

import pytest

from gateway.paypal_actions import PaypalActions, is_read_only, summary
from gateway.service import GatewayError
from tests.test_passkeys import enroll, pgw  # noqa: F401 - fixture reused

pytestmark = pytest.mark.anyio
REFUND = {"capture_id": "CAP123", "amount": {"value": "10.00", "currency_code": "USD"}}


def test_read_only_tools_and_plain_summaries():
    assert is_read_only("list_invoices") and is_read_only("get_order") and is_read_only("show_product_details")
    assert not is_read_only("create_refund") and not is_read_only("pay_order") and not is_read_only("send_invoice")
    assert summary("create_refund", REFUND) == "Refund 10.00 USD of capture CAP123"


async def test_approve_then_run_once_with_the_exact_arguments(gw):
    acts = PaypalActions(gw)
    req = await acts.request(None, "create_refund", REFUND, "customer asked")
    assert req["status"] == "pending_approval" and "result" not in req
    with pytest.raises(GatewayError, match="not approved"):
        await acts.claim(req["action_id"], None, "create_refund", REFUND)
    await acts.decide(req["action_id"], "demo-user", True, req["args_hash"])
    with pytest.raises(GatewayError, match="not the approved arguments"):
        await acts.claim(req["action_id"], None, "create_refund", {**REFUND, "amount": {"value": "999", "currency_code": "USD"}})
    await acts.claim(req["action_id"], None, "create_refund", REFUND)
    with pytest.raises(GatewayError):  # only once
        await acts.claim(req["action_id"], None, "create_refund", REFUND)
    done = await acts.report(req["action_id"], None, True, {"text": "{\"id\": \"R1\"}"})
    assert done["status"] == "done"
    log = [e["event"] for e in await gw.get_log()]
    for ev in ("paypal_action_requested", "paypal_action_approved", "paypal_action_executing", "paypal_action_done"):
        assert ev in log


async def test_rejected_expired_and_read_only_requests(gw):
    acts = PaypalActions(gw, ttl=timedelta(seconds=-1))  # already expired
    req = await acts.request(None, "send_invoice", {"invoice_id": "INV1"})
    assert (await acts.get(req["action_id"]))["status"] == "approval_expired"
    with pytest.raises(GatewayError):
        await acts.decide(req["action_id"], "demo-user", True, req["args_hash"])
    acts = PaypalActions(gw)
    req = await acts.request(None, "send_invoice", {"invoice_id": "INV2"})
    out = await acts.decide(req["action_id"], "demo-user", False, req["args_hash"])
    assert out["status"] == "rejected"
    with pytest.raises(GatewayError, match="only reads"):
        await acts.request(None, "list_invoices", {})


async def test_with_a_passkey_the_approval_must_be_signed(pgw):  # noqa: F811
    await enroll(pgw)
    acts = PaypalActions(pgw)
    req = await acts.request(None, "create_refund", REFUND)
    with pytest.raises(GatewayError, match="signed with your passkey"):
        await acts.decide(req["action_id"], "demo-user", True, req["args_hash"])
