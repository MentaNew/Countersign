"""Seller-side dispute desk. The desk reaches the buyer's gateway only over its public HTTP
endpoints (routed in-process here), and PayPal is faked: no network."""

import copy
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from dispute_desk.app import DESK_COOKIE, create_desk_app
from dispute_desk.config import DeskSettings
from dispute_desk.models import create_desk_schema
from dispute_desk.packet import build_packet, evidence_plan
from dispute_desk.paypal import PayPalError
from dispute_desk.receipts import GatewayReceipts
from dispute_desk.service import Desk, DeskError
from gateway.catalog.client import DictCatalog
from gateway.config import Settings
from gateway.db.models import LogEntry
from gateway.domain import CartLineRequest as L
from gateway.main import create_app
from gateway.verifier.stub import StubInjectionDetector, StubVerifier
from tests.conftest import CATALOG, FakePayPal, paper_mandate

pytestmark = pytest.mark.anyio
NOW = datetime(2026, 10, 18, tzinfo=UTC)


def dispute_json(receipt_id="rcp_x", purchase_id="pur_x", gross="38.90", reason="UNAUTHORISED",
                 amount="38.90", sku="PM-A4-500W", dispute_id="PP-D-TEST1",
                 links=("provide_evidence", "make_offer", "accept_claim")):
    """Shape copied from a real sandbox case (PP-R-ELT-10193319, Oct 17)."""
    return {
        "dispute_id": dispute_id, "status": "WAITING_FOR_SELLER_RESPONSE", "reason": reason,
        "dispute_life_cycle_stage": "INQUIRY",
        "dispute_amount": {"currency_code": "USD", "value": amount},
        "seller_response_due_date": "2026-11-06T23:19:17.810Z",
        "disputed_transactions": [{
            "seller_transaction_id": "CAP123", "gross_amount": {"currency_code": "USD", "value": gross},
            "invoice_number": purchase_id, "custom": receipt_id,
            "buyer": {"email": "buyer@personal.example.com"},
            "items": [{"item_id": sku, "item_name": "A4 Copy Paper", "item_quantity": "10"}],
        }],
        "messages": [{"posted_by": "BUYER", "content": "I never authorised this"}],
        "evidences": [{"evidence_type": "PROOF_OF_FULFILLMENT", "source": "REQUESTED_FROM_SELLER"}],
        "links": [{"rel": r, "method": "POST", "href": f"https://x/{r}"} for r in links],
    }


class FakeDisputes:
    def __init__(self):
        self.cases: dict[str, dict] = {}
        self.sent: list[tuple[str, str, dict]] = []
        self.webhook_ok = False

    async def get_dispute(self, dispute_id):
        if dispute_id not in self.cases:
            raise PayPalError(404, {"name": "RESOURCE_NOT_FOUND", "message": "nope"})
        return copy.deepcopy(self.cases[dispute_id])

    async def list_disputes(self):
        return []

    async def provide_evidence(self, dispute_id, evidence_input):
        self.sent.append(("provide_evidence", dispute_id, evidence_input))
        return {"links": []}

    async def make_offer(self, dispute_id, body):
        self.sent.append(("make_offer", dispute_id, body))
        return {"links": []}

    async def accept_claim(self, dispute_id, body):
        self.sent.append(("accept_claim", dispute_id, body))
        return {"links": []}

    async def verify_webhook(self, headers, event, webhook_id):
        return self.webhook_ok


# ------------------------------------------------------------------ packet logic (pure)

def valid_check(**over):
    receipt = {"receipt_id": "rcp_x", "purchase_id": "pur_x", "total": "38.90", "currency": "USD",
               "lines": [{"sku": "PM-A4-500W", "title": "A4 Copy Paper", "qty": 10}],
               "user_text": "Buy 10 reams of A4", "approved_by_human": False,
               "approved_by": "policy", "issued_at": "2026-10-17T00:00:00", "intent_id": "int_x"}
    receipt.update(over)
    return {"found": True, "receipt": receipt, "signature_valid": True, "key_id": "k",
            "verify_url": "http://gw/api/receipts/rcp_x/verify",
            "gateway_report": {"valid": True}}


def test_unauthorised_with_valid_receipt_is_contested():
    p = build_packet(dispute_json(), valid_check(), now=NOW)
    assert p["recommendation"]["action"] == "contest" and p["recommendation"]["strength"] == "strong"
    assert all(c["ok"] for c in p["checks"])
    assert "rcp_x" in p["narrative"] and "signed spending permission" in p["narrative"]
    assert p["facts"]["days_left"] == 19 and p["requested_by_paypal"] == ["PROOF_OF_FULFILLMENT"]


def test_receipt_for_another_payment_does_not_count():
    p = build_packet(dispute_json(gross="99.00"), valid_check(), now=NOW)
    assert not next(e for e in p["evidence"] if e["kind"] == "authorization_receipt")["have"]
    assert p["recommendation"]["strength"] == "weak"


def test_not_received_small_amount_recommends_refund_unless_tracking():
    d = dispute_json(reason="MERCHANDISE_OR_SERVICE_NOT_RECEIVED", amount="3.50")
    p = build_packet(d, valid_check(), now=NOW)
    assert p["recommendation"]["action"] == "refund" and p["recommendation"]["paypal_call"] == "make_offer"
    assert any("does not prove delivery" in w for w in p["recommendation"]["why"])
    p = build_packet(d, valid_check(), {"carrier": "UPS", "tracking_number": "1Z999"}, now=NOW)
    assert p["recommendation"]["action"] == "contest" and "1Z999" in p["narrative"]


def test_no_receipt_still_builds_packet():
    p = build_packet(dispute_json(receipt_id=None), None, now=NOW)  # 38.90 > small-claim line
    assert p["authorization"] is None
    assert p["recommendation"] == {"action": "review", "paypal_call": None, "strength": "weak",
                                   "why": ["No verifiable authorization record for this payment."]}


# ------------------------------------------------------- integration with a real gateway

@pytest.fixture
async def stack(engine, tmp_path):
    """A real gateway app (buyer side) and a desk (seller side) sharing only HTTP."""
    gw_settings = Settings(_env_file=None, verifier="stub", signing_key_path=str(tmp_path / "k.pem"),
                           agent_api_key="a", user_password="u")
    fake_orders = FakePayPal()
    gw_app = create_app(gw_settings, engine=engine, catalog=DictCatalog(CATALOG), paypal=fake_orders,
                        verifier=StubVerifier(), injection=StubInjectionDetector())
    await create_desk_schema(engine, drop=True)
    async with gw_app.router.lifespan_context(gw_app):
        gw = gw_app.state.gateway
        r = await gw.confirm_intent("Buy 10 reams of A4", paper_mandate())
        await gw.decide_intent(r["intent_id"], "demo-user", True)
        purchase = await gw.request_purchase(r["intent_id"], "papermill-supply",
                                             [L(sku="PM-A4-500W", qty=10, item=0)])
        paypal = FakeDisputes()
        catalog = gw_app.state.gateway.catalog

        async def shop_keys(merchant_id):  # the seller knows its own shop's key
            return catalog.signer(merchant_id).jwk

        receipts = GatewayReceipts("http://gateway.test",
                                   transport=httpx.ASGITransport(app=gw_app), shop_keys=shop_keys)
        desk = Desk(async_sessionmaker(engine, expire_on_commit=False), paypal, receipts)
        yield gw, desk, paypal, purchase


async def test_desk_verifies_receipt_through_public_endpoints(stack):
    _gw, desk, paypal, purchase = stack
    paypal.cases["PP-D-1"] = dispute_json(receipt_id=purchase["receipt_id"],
                                          purchase_id=purchase["purchase_id"], dispute_id="PP-D-1")
    view = await desk.import_dispute("PP-D-1")
    assert view["has_valid_receipt"] and view["recommendation"] == "contest"
    check = view["receipt_check"]
    assert check["signature_valid"] and check["gateway_report"]["valid"]
    auth = view["packet"]["authorization"]
    assert auth["user_text"] == "Buy 10 reams of A4"
    assert auth["intent_terms"]["merchants"] and auth["gateway_checks"]["decision"] == "approve"
    assert "checked the cart against the buyer's signed request" in view["packet"]["narrative"]
    names = [c["name"] for c in view["packet"]["checks"] if c["ok"]]
    assert any(n.startswith("Prices and lines were signed by the shop") for n in names)
    assert "That key is the shop's own published key" in names
    assert any(n.startswith("AP2 v0.2 Checkout and Payment Mandates verify") for n in names)


async def test_tampered_buyer_log_weakens_the_packet(stack):
    gw, desk, paypal, purchase = stack
    paypal.cases["PP-D-2"] = dispute_json(receipt_id=purchase["receipt_id"],
                                          purchase_id=purchase["purchase_id"], dispute_id="PP-D-2")
    async with gw.sessions.begin() as s:
        await s.execute(update(LogEntry).where(LogEntry.seq == 1).values(payload={"x": 1}))
    view = await desk.import_dispute("PP-D-2")
    assert view["receipt_check"]["signature_valid"]  # the signature itself is still fine
    assert not view["has_valid_receipt"] and view["recommendation"] != "contest"


async def test_submit_only_what_paypal_allows(stack):
    _gw, desk, paypal, purchase = stack
    paypal.cases["PP-D-3"] = dispute_json(receipt_id=purchase["receipt_id"],
                                          purchase_id=purchase["purchase_id"], dispute_id="PP-D-3",
                                          links=("make_offer",))
    await desk.import_dispute("PP-D-3")
    with pytest.raises(DeskError, match="does not currently allow"):
        await desk.submit("PP-D-3", "seller", "provide_evidence", "note")
    view = await desk.submit("PP-D-3", "seller", "make_offer", "Refunding", "10.00")
    assert paypal.sent[-1][0] == "make_offer"
    assert paypal.sent[-1][2]["offer_amount"] == {"currency_code": "USD", "value": "10.00"}
    assert view["actions"][-1]["ok"]


async def test_evidence_includes_tracking(stack):
    _gw, desk, paypal, purchase = stack
    paypal.cases["PP-D-4"] = dispute_json(receipt_id=purchase["receipt_id"],
                                          purchase_id=purchase["purchase_id"], dispute_id="PP-D-4")
    await desk.import_dispute("PP-D-4")
    await desk.refresh("PP-D-4", {"carrier": "UPS", "tracking_number": "1Z999"})
    await desk.submit("PP-D-4", "seller", "provide_evidence", "See receipt.")
    kinds = [e["evidence_type"] for e in paypal.sent[-1][2]["evidences"]]
    assert kinds == ["PROOF_OF_FULFILLMENT", "OTHER"]


async def test_webhooks_need_a_valid_signature_unless_allowed(stack):
    _gw, desk, _paypal, _purchase = stack
    event = {"id": "WH-1", "event_type": "CUSTOMER.DISPUTE.CREATED",
             "resource": dispute_json(dispute_id="PP-SIM-1")}
    with pytest.raises(DeskError, match="signature"):
        await desk.handle_webhook({}, event)
    desk.allow_unverified = True
    out = await desk.handle_webhook({}, event)
    assert out == {"dispute_id": "PP-SIM-1", "source": "simulated", "verified": False}
    with pytest.raises(DeskError, match="simulated"):
        await desk.submit("PP-SIM-1", "seller", "accept_claim", "ok")


async def test_desk_tables_live_in_their_own_schema(engine, stack):
    async with engine.connect() as c:
        rows = (await c.execute(text(
            "select table_schema, table_name from information_schema.tables "
            "where table_schema in ('gateway','desk','public') order by 1, 2"))).all()
    schemas = {s for s, _ in rows}
    assert {"gateway", "desk"} <= schemas and "public" not in schemas


async def test_seller_login_is_separate_from_buyer_login(engine):
    s = DeskSettings(_env_file=None, seller_password="seller-secret", user_password="x")
    app = create_desk_app(s, engine=engine, paypal=FakeDisputes(),
                          receipts=GatewayReceipts("http://unused"))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://desk") as c,
    ):
        assert (await c.get("/desk/api/disputes")).status_code == 401
        buyer = {"Authorization": "Bearer ses-token-from-the-buyer-gateway"}
        assert (await c.get("/desk/api/disputes", headers=buyer)).status_code == 401
        r = await c.post("/desk/api/login", json={"password": "seller-secret"})
        cookie = r.headers["set-cookie"].lower()
        assert f"{DESK_COOKIE}=" in cookie and "httponly" in cookie and "path=/desk" in cookie
        tok = r.json()["token"]
        r = await c.get("/desk/api/disputes", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200
        assert (await c.get("/desk/api/disputes")).status_code == 200  # via the cookie
        # A change with the cookie alone (no app header) is refused.
        assert (await c.post("/desk/api/disputes/import", json={"dispute_id": "PP-X"})).status_code == 403
        assert (await c.post("/desk/api/logout", headers={"X-Guardrail-Request": "1"})).status_code == 200
        c.cookies.clear()
        r = await c.get("/desk/api/disputes", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 401  # signed out: the token no longer works
        assert (await c.post("/webhooks/paypal", json={"id": "x"})).status_code == 400


def test_money_is_decimal_safe():
    facts = build_packet(dispute_json(amount="0.10"), None, now=NOW)["facts"]
    assert Decimal(facts["disputed_amount"]) == Decimal("0.10")


def test_evidence_types_follow_paypal_rules_per_reason():
    """Regression: OTHER on a not-received case was refused (EVIDENCE_TYPE_IS_NOT_ALLOWED, Oct 18)."""
    ups = {"carrier": "UPS", "tracking_number": "1Z999"}
    nr = "MERCHANDISE_OR_SERVICE_NOT_RECEIVED"
    assert [e["evidence_type"] for e in evidence_plan(nr, ["PROOF_OF_FULFILLMENT"], None, "n")] \
        == ["PROOF_OF_FULFILLMENT"]
    with_tracking = evidence_plan(nr, [], ups, "n")
    assert len(with_tracking) == 1 and with_tracking[0]["evidence_info"]["tracking_info"][0][
        "tracking_number"] == "1Z999" and with_tracking[0]["notes"] == "n"
    nad = evidence_plan("MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED", [], ups, "n")
    assert [e["evidence_type"] for e in nad] == ["OTHER"]  # tracking not accepted for this reason
    assert [e["evidence_type"] for e in evidence_plan("UNAUTHORISED", [], ups, "n")] \
        == ["PROOF_OF_FULFILLMENT", "OTHER"]
    assert all("OTHER" != e["evidence_type"] for e in evidence_plan(nr, [], ups, "n"))


def test_no_suggestion_when_paypal_accepts_no_response():
    """Regression (Oct 19): a case under PayPal's own review offered no actions, yet the desk
    still suggested a refund."""
    d = dispute_json(reason="MERCHANDISE_OR_SERVICE_NOT_RECEIVED", amount="3.89", links=())
    d["status"] = "UNDER_REVIEW"
    p = build_packet(d, valid_check(), now=NOW)
    assert p["recommendation"]["action"] == "wait" and p["recommendation"]["paypal_call"] is None
    assert p["available_actions"] == []


def test_suggestion_never_names_an_action_paypal_does_not_offer():
    """Regression (Oct 19): 'not as described' inquiry offered no provide_evidence, but the desk
    suggested contesting."""
    d = dispute_json(reason="MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED", amount="15.00",
                     links=("accept_claim", "make_offer"))
    rec = build_packet(d, valid_check(), now=NOW)["recommendation"]
    assert rec["action"] == "evidence_not_accepted" and rec["paypal_call"] is None
    assert "escalate" in rec["why"][-1]
