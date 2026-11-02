"""Per-agent keys, spending caps and kill switches (P1.5)."""

from decimal import Decimal

import httpx
import pytest

from gateway.config import Settings
from gateway.domain import CartLineRequest as L
from gateway.main import create_app
from gateway.service import AgentDenied, GatewayError, NotFound
from gateway.verifier.stub import StubInjectionDetector, StubVerifier
from tests.conftest import CATALOG, FakePayPal, paper_mandate
from tests.test_passkeys import enroll, pgw  # noqa: F401 - fixture reused

pytestmark = pytest.mark.anyio
PAPER = [L(sku="PM-A4-500W", qty=10, item=0)]  # 38.90 USD, approved by policy
HELD = [L(sku="NO-A4-500W-PREM", qty=10, item=0)]  # above the ask-me line: held


async def new_agent(gw, name="shopper", cap=None):
    a = await gw.create_agent("demo-user", name, cap)
    return a["agent_id"], a["key"]


async def intent_for(gw, agent_id):
    r = await gw.confirm_intent("Buy 10 reams of A4 paper, max $60", paper_mandate(), agent_id)
    await gw.decide_intent(r["intent_id"], "demo-user", approve=True)
    return r["intent_id"]


async def test_key_is_shown_once_and_only_its_hash_is_stored(gw):
    aid, key = await new_agent(gw)
    assert key.startswith("agk_") and await gw.authenticate_agent(key) == aid
    listed = (await gw.list_agents())["agents"]
    assert [a["agent_id"] for a in listed] == [aid] and "key" not in listed[0]
    with pytest.raises(AgentDenied, match="unknown agent key"):
        await gw.authenticate_agent(key + "x")


async def test_revoked_agent_is_refused_and_its_held_cart_cannot_be_approved(gw):
    aid, key = await new_agent(gw)
    held = await gw.request_purchase(await intent_for(gw, aid), "northside-office", HELD,
                                     agent_id=aid)
    assert held["status"] == "pending_approval"
    await gw.revoke_agent(aid, "demo-user")
    with pytest.raises(AgentDenied, match="revoked"):
        await gw.authenticate_agent(key)
    with pytest.raises(GatewayError, match="not pending_approval"):
        await gw.decide_purchase(held["purchase_id"], "demo-user", True, held["cart_hash"])
    assert (await gw.get_purchase(held["purchase_id"]))["error"] == "agent shopper was revoked"
    events = [e["event"] for e in await gw.get_log()]
    assert "agent_revoked" in events


async def test_stop_all_agents_then_resume(gw):
    aid, key = await new_agent(gw)
    iid = await intent_for(gw, aid)
    held = await gw.request_purchase(iid, "northside-office", HELD, agent_id=aid)
    state = await gw.stop_all_agents("demo-user")
    assert state["paused"]
    with pytest.raises(AgentDenied, match="all agents are stopped"):
        await gw.authenticate_agent(key)
    assert (await gw.get_purchase(held["purchase_id"]))["status"] == "blocked"
    # Even a direct call (bypassing the middleware) is blocked by the gateway's own checks.
    out = await gw.request_purchase(iid, "papermill-supply", PAPER, agent_id=aid)
    assert out["decision"] == "block" and "agent_active" in " ".join(out["reasons"])
    r = await gw.confirm_intent("more paper", paper_mandate(), aid)
    with pytest.raises(GatewayError, match="cannot approve"):
        await gw.decide_intent(r["intent_id"], "demo-user", True)
    assert not (await gw.resume_agents("demo-user"))["paused"]
    assert await gw.authenticate_agent(key) == aid


async def test_spend_cap_counts_only_that_agents_purchases(gw):
    aid, _ = await new_agent(gw, cap=Decimal(50))
    first = await gw.request_purchase(await intent_for(gw, aid), "papermill-supply", PAPER,
                                      agent_id=aid)
    assert first["decision"] == "approve"
    second = await gw.request_purchase(await intent_for(gw, aid), "papermill-supply", PAPER,
                                       agent_id=aid)
    assert second["decision"] == "block"
    assert any("agent_spend_cap" in r and "38.90 of its 50.00" in r for r in second["reasons"])
    other, _ = await new_agent(gw, "other")
    ok = await gw.request_purchase(await intent_for(gw, other), "papermill-supply", PAPER,
                                   agent_id=other)
    assert ok["decision"] == "approve"
    assert (await gw.list_agents())["agents"][0]["spent_30d"] == "38.90"


async def test_an_agent_cannot_use_or_read_another_agents_permission(gw):
    a, _ = await new_agent(gw, "a")
    b, _ = await new_agent(gw, "b")
    iid = await intent_for(gw, a)
    with pytest.raises(NotFound):
        await gw.wait_intent(iid, 0, agent_id=b)
    with pytest.raises(NotFound):
        await gw.agent_log(iid, b)
    out = await gw.request_purchase(iid, "papermill-supply", PAPER, agent_id=b)
    assert out["decision"] == "block"
    assert any("intent_agent_match" in r for r in out["reasons"])


async def test_creating_an_agent_and_resuming_need_the_passkey(pgw):  # noqa: F811
    dev = await enroll(pgw)
    with pytest.raises(GatewayError, match="signed with your passkey"):
        await pgw.create_agent("demo-user", "shopper", Decimal(25))
    opts = await pgw.agent_creation_options("demo-user", "shopper", Decimal(25))
    assert opts["statement"]["kind"] == "agent_creation"
    assert opts["statement"]["spend_cap"] == "25.00"
    # Signing for a 25 USD cap cannot create an agent with a bigger one.
    with pytest.raises(GatewayError, match="different spend_cap"):
        await pgw.create_agent("demo-user", "shopper", Decimal(2500), "USD",
                               dev.sign(opts["options"]))
    opts = await pgw.agent_creation_options("demo-user", "shopper", Decimal(25))
    a = await pgw.create_agent("demo-user", "shopper", Decimal(25), "USD",
                               dev.sign(opts["options"]))
    assert a["user_signed"] and a["spend_cap"] == "25.00"
    # Stopping and revoking never need the passkey; resuming does.
    await pgw.stop_all_agents("demo-user")
    await pgw.revoke_agent(a["agent_id"], "demo-user")
    with pytest.raises(GatewayError, match="signed with your passkey"):
        await pgw.resume_agents("demo-user")
    opts = await pgw.resume_options("demo-user")
    assert not (await pgw.resume_agents("demo-user", dev.sign(opts["options"])))["paused"]


# ------------------------------------------------------------------ over HTTP


@pytest.fixture
async def http(engine, tmp_path):
    from gateway.catalog.client import DictCatalog

    s = Settings(_env_file=None, agent_api_key="agent-secret", user_password="user-secret",
                 verifier="stub", signing_key_path=str(tmp_path / "k.pem"),
                 anchor_interval_seconds=0)
    app = create_app(s, engine=engine, catalog=DictCatalog(CATALOG), paypal=FakePayPal(),
                     verifier=StubVerifier(), injection=StubInjectionDetector())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
            tok = (await c.post("/api/login", json={"password": "user-secret"})).json()["token"]
            c.user = {"Authorization": f"Bearer {tok}"}
            yield c


async def call(c, key, name, arguments):
    r = await c.post("/mcp/", headers={
        "Authorization": f"Bearer {key}", "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json", "MCP-Protocol-Version": "2025-06-18"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": name, "arguments": arguments}})
    return r


INTENT = {"user_text": "paper", "items": [{"description": "A4 paper", "qty": 10}],
          "max_total": {"amount": "60", "currency": "USD"}, "merchants": ["papermill-supply"],
          "expires_at": "2099-01-01T00:00:00Z"}


async def test_each_agent_key_reaches_mcp_as_itself(http):
    listed = (await http.get("/api/agents", headers=http.user)).json()
    default = listed["agents"][0]
    assert default["name"].startswith("default") and not listed["paused"]

    made = (await http.post("/api/agents", json={"name": "browser bot", "spend_cap": "30"},
                            headers=http.user)).json()
    r = await call(http, made["key"], "confirm_intent", INTENT)
    iid = r.json()["result"]["structuredContent"]["intent_id"]
    assert (await http.get(f"/api/intents/{iid}", headers=http.user)).json()["agent_id"] == \
        made["agent_id"]
    # The default agent (AGENT_API_KEY) cannot see the other agent's permission.
    r = await call(http, "agent-secret", "get_intent_status", {"intent_id": iid, "wait_seconds": 0})
    assert r.json()["result"]["isError"]

    await http.post("/api/agents/stop", headers=http.user)
    r = await call(http, made["key"], "get_intent_status", {"intent_id": iid, "wait_seconds": 0})
    assert r.status_code == 403 and "stopped" in r.json()["error"]
    await http.post("/api/agents/resume", json={}, headers=http.user)
    await http.post(f"/api/agents/{made['agent_id']}/revoke", headers=http.user)
    assert (await call(http, made["key"], "get_intent_status", {"intent_id": iid})).status_code == 403
    assert (await call(http, "agent-secret", "confirm_intent", INTENT)).status_code == 200
    # The agent key never reaches the agent controls.
    r = await http.post("/api/agents/stop", headers={"Authorization": "Bearer agent-secret"})
    assert r.status_code == 401


async def test_changing_the_env_key_retires_the_old_one(gw):
    await gw.ensure_default_agent("old-key")
    await gw.ensure_default_agent("new-key")
    await gw.ensure_default_agent("new-key")  # restarting changes nothing
    with pytest.raises(AgentDenied, match="revoked"):
        await gw.authenticate_agent("old-key")
    assert await gw.authenticate_agent("new-key")
    assert len((await gw.list_agents())["agents"]) == 2
