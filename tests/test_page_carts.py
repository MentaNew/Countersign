"""Carts read from a normal shop's checkout by the checkout guard (P2.1): weaker evidence,
always passkey-approved, shipping and tax in the budget, a signed permit instead of a PayPal
order created by the gateway."""

import base64

import pytest

from gateway.domain import PageCart
from gateway.service import GatewayError
from gateway.signing.keys import verify_with_public_key
from tests.conftest import paper_mandate
from tests.test_passkeys import enroll, pgw  # noqa: F401 - fixture reused

pytestmark = pytest.mark.anyio
SHOP = "https://www.greenleaf-paper.example"
PNG = base64.b64encode(b"\x89PNG fake screenshot").decode()


def page_cart(price="3.50", qty=10, shipping="4.99", tax="2.80", total=None, page=None):
    lines_total = float(price) * qty
    total = total or f"{lines_total + float(shipping) + float(tax):.2f}"
    return PageCart.model_validate({
        "shop_url": SHOP, "page_url": page or f"{SHOP}/checkout",
        "lines": [{"title": "A4 copy paper, 500 sheets, white", "qty": qty, "item": 0,
                   "unit_price": {"amount": price, "currency": "USD"},
                   "product_url": f"{SHOP}/products/a4-paper"}],
        "shipping": {"amount": shipping, "currency": "USD"},
        "tax": {"amount": tax, "currency": "USD"},
        "total": {"amount": total, "currency": "USD"},
        "reader": "shopify-cart-json", "screenshot_png_b64": PNG,
    })


async def browser_agent(gw, kind="browser"):
    a = await gw.create_agent("demo-user", f"{kind} agent", None, kind=kind)
    return a["agent_id"]


async def intent(gw, agent_id, **kw):
    r = await gw.confirm_intent("10 reams of A4 paper from Greenleaf, max $60",
                                paper_mandate(merchants=["greenleaf-paper.example"], **kw),
                                agent_id)
    await gw.decide_intent(r["intent_id"], "demo-user", True)
    return r["intent_id"]


async def test_small_page_cart_still_needs_the_passkey_then_gets_a_permit(pgw):  # noqa: F811
    aid = await browser_agent(pgw)
    iid = await intent(pgw, aid, approval_above=None)  # no ask-me line at all
    held = await pgw.request_page_purchase(iid, page_cart(), aid)
    assert held["status"] == "pending_approval" and held["total"] == "42.79"
    assert held["reasons"][0].startswith("cart read from the shop's page")
    assert held["merchant_id"] == "greenleaf-paper.example"

    with pytest.raises(GatewayError, match="needs a passkey"):  # none registered yet
        await pgw.decide_purchase(held["purchase_id"], "demo-user", True, held["cart_hash"])
    dev = await enroll(pgw)
    with pytest.raises(GatewayError, match="signed with your passkey"):
        await pgw.decide_purchase(held["purchase_id"], "demo-user", True, held["cart_hash"])
    opts = await pgw.purchase_approval_options(held["purchase_id"], "demo-user")
    st = opts["statement"]
    assert st["cart_source"] == "page" and st["charges"]["shipping"] == "4.99"
    assert st["screenshot_sha256"] and st["total"] == "42.79"
    done = await pgw.decide_purchase(held["purchase_id"], "demo-user", True, held["cart_hash"],
                                     dev.sign(opts["options"]))
    assert done["status"] == "authorized" and done["paypal_order_id"] is None
    assert pgw.paypal.orders == []  # the shop's own checkout takes the payment
    permit = done["checkout_permit"]
    assert permit["permit"]["shop"] == "greenleaf-paper.example"
    assert permit["permit"]["total"] == "42.79"
    assert verify_with_public_key(pgw.signer.public_key_b64, permit["permit"], permit["signature"])

    v = await pgw.verify_receipt(done["receipt_id"])
    assert v["valid"], v
    cart = v["receipt"]["cart_mandate"]
    assert cart["cart_source"] == "page" and cart["page_evidence"]["reader"] == "shopify-cart-json"
    assert v["merchant_signature"]["valid"] is None
    assert "not signed by the shop" in v["merchant_signature"]["detail"]
    assert v["receipt"]["payment"]["status"] == "pending"
    full = await pgw.get_purchase(held["purchase_id"])
    assert full["page_screenshot_png_b64"] == PNG


async def test_only_browser_agents_may_submit_page_carts(gw):
    aid = await browser_agent(gw, kind="mcp")
    iid = await intent(gw, aid)
    with pytest.raises(GatewayError, match="only the checkout guard"):
        await gw.request_page_purchase(iid, page_cart(), aid)


async def test_shipping_and_tax_count_toward_the_budget(gw):
    aid = await browser_agent(gw)
    iid = await intent(gw, aid)  # max 60 USD
    out = await gw.request_page_purchase(iid, page_cart(price="5.00", shipping="9.00",
                                                        tax="4.00"), aid)  # 50 + 13
    assert out["decision"] == "block" and any("budget" in r for r in out["reasons"])


async def test_totals_that_do_not_add_up_or_offsite_checkout_are_blocked(gw):
    aid = await browser_agent(gw)
    iid = await intent(gw, aid)
    out = await gw.request_page_purchase(iid, page_cart(total="30.00"), aid)
    assert out["decision"] == "block" and any("page_cart_adds_up" in r for r in out["reasons"])
    out = await gw.request_page_purchase(
        iid, page_cart(page="https://greenleaf-paper.example.evil.test/checkout"), aid)
    assert out["decision"] == "block"
    assert any("checkout_on_shop_site" in r for r in out["reasons"])


async def test_shop_not_in_the_permission_is_blocked(gw):
    aid = await browser_agent(gw)
    r = await gw.confirm_intent("paper", paper_mandate(), aid)  # papermill / northside only
    await gw.decide_intent(r["intent_id"], "demo-user", True)
    out = await gw.request_page_purchase(r["intent_id"], page_cart(), aid)
    assert out["decision"] == "block" and any("merchant_allowed" in r for r in out["reasons"])


async def approved_page_purchase(gw):
    aid = await browser_agent(gw)
    iid = await intent(gw, aid)
    held = await gw.request_page_purchase(iid, page_cart(), aid)
    dev = await enroll(gw)
    opts = await gw.purchase_approval_options(held["purchase_id"], "demo-user")
    await gw.decide_purchase(held["purchase_id"], "demo-user", True, held["cart_hash"],
                             dev.sign(opts["options"]))
    return aid, held["purchase_id"]


def paypal_order(status="COMPLETED", value="42.79"):
    async def get_order(order_id):
        return {"id": order_id, "status": status,
                "purchase_units": [{"amount": {"value": value, "currency_code": "USD"},
                                    "payments": {"captures": [{"id": "CAP123"}]}}]}
    return get_order


async def test_payment_checked_with_paypal_is_signed_and_on_the_receipt(pgw):  # noqa: F811
    aid, pid = await approved_page_purchase(pgw)
    pgw.paypal.get_order = paypal_order()
    out = await pgw.confirm_page_payment(pid, "5O190127TN364715T",
                                         f"{SHOP}/checkout/return?token=5O190127TN364715T", aid)
    assert out["status"] == "captured" and out["paypal_order_id"] == "5O190127TN364715T"
    conf = out["payment_confirmation"]["confirmation"]
    assert conf["checked_with_paypal"] and conf["capture_id"] == "CAP123"
    v = await pgw.verify_receipt(out["receipt_id"])
    assert v["valid"] and v["payment_confirmation"]["signature_valid"]
    again = await pgw.confirm_page_payment(pid, "OTHERORDER123", f"{SHOP}/x", aid)
    assert again["paypal_order_id"] == "5O190127TN364715T"  # recorded once


async def test_payment_for_another_amount_or_unfinished_is_refused(pgw):  # noqa: F811
    aid, pid = await approved_page_purchase(pgw)
    pgw.paypal.get_order = paypal_order(value="99.00")
    with pytest.raises(GatewayError, match="not the approved 42.79"):
        await pgw.confirm_page_payment(pid, "5O190127TN364715T", f"{SHOP}/done", aid)
    pgw.paypal.get_order = paypal_order(status="APPROVED")
    with pytest.raises(GatewayError, match="not completed"):
        await pgw.confirm_page_payment(pid, "5O190127TN364715T", f"{SHOP}/done", aid)
    with pytest.raises(GatewayError, match="not on greenleaf"):
        await pgw.confirm_page_payment(pid, "5O190127TN364715T", "https://evil.test/done", aid)


async def test_order_invisible_to_the_gateway_is_recorded_as_reported(pgw):  # noqa: F811
    aid, pid = await approved_page_purchase(pgw)

    async def not_ours(order_id):
        raise RuntimeError("404 RESOURCE_NOT_FOUND")
    pgw.paypal.get_order = not_ours
    out = await pgw.confirm_page_payment(pid, "5O190127TN364715T", f"{SHOP}/done", aid)
    assert out["status"] == "payment_reported"
    assert out["payment_confirmation"]["confirmation"]["checked_with_paypal"] is False


async def test_seller_desk_rechecks_the_payment_signature_itself(pgw):  # noqa: F811
    from dispute_desk.receipts import check_payment_confirmation

    aid, pid = await approved_page_purchase(pgw)
    pgw.paypal.get_order = paypal_order()
    out = await pgw.confirm_page_payment(pid, "5O190127TN364715T", f"{SHOP}/done", aid)
    pay = (await pgw.verify_receipt(out["receipt_id"]))["payment_confirmation"]
    assert check_payment_confirmation(pay, pgw.signer.public_key_b64)["signature_valid"]
    forged = {**pay, "amount": "1.00"}
    assert not check_payment_confirmation(forged, pgw.signer.public_key_b64)["signature_valid"]
