"""HTTP surface: the agent key reaches MCP only, the user login reaches approvals only."""

import httpx
import pytest

from gateway.config import Settings
from gateway.main import create_app
from gateway.verifier.stub import StubInjectionDetector, StubVerifier
from tests.conftest import CATALOG, FakePayPal

pytestmark = pytest.mark.anyio

AGENT = {"Authorization": "Bearer agent-secret"}
MCP_HEADERS = {**AGENT, "Accept": "application/json, text/event-stream",
               "Content-Type": "application/json", "MCP-Protocol-Version": "2025-06-18"}


@pytest.fixture
async def client(engine, tmp_path):
    from gateway.catalog.client import DictCatalog

    s = Settings(_env_file=None, agent_api_key="agent-secret", user_password="user-secret",
                 verifier="stub", signing_key_path=str(tmp_path / "k.pem"))
    app = create_app(s, engine=engine, catalog=DictCatalog(CATALOG), paypal=FakePayPal(),
                     verifier=StubVerifier(), injection=StubInjectionDetector())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
            yield c


async def mcp_call(c, method, params=None, id_=1):
    r = await c.post("/mcp/", headers=MCP_HEADERS,
                     json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}})
    assert r.status_code == 200, r.text
    return r.json()


async def test_mcp_requires_agent_key(client):
    r = await client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401


async def test_mcp_lists_tools_without_any_approval_tool(client):
    tools = {t["name"] for t in (await mcp_call(client, "tools/list"))["result"]["tools"]}
    assert {"confirm_intent", "request_purchase", "get_purchase_status"} <= tools
    assert not any("approve" in t or "decision" in t for t in tools)


async def test_agent_key_cannot_approve(client):
    out = await mcp_call(client, "tools/call", {"name": "confirm_intent", "arguments": {
        "user_text": "paper", "items": [{"description": "A4 paper", "qty": 10}],
        "max_total": {"amount": "60", "currency": "USD"}, "merchants": ["papermill-supply"],
        "expires_at": "2099-01-01T00:00:00Z"}})
    intent_id = out["result"]["structuredContent"]["intent_id"]
    r = await client.post(f"/api/intents/{intent_id}/decision", json={"approve": True}, headers=AGENT)
    assert r.status_code == 401

    tok = (await client.post("/api/login", json={"password": "user-secret"})).json()["token"]
    r = await client.post(f"/api/intents/{intent_id}/decision", json={"approve": True},
                          headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and r.json()["status"] == "active"

    out = await mcp_call(client, "tools/call", {"name": "request_purchase", "arguments": {
        "intent_id": intent_id, "merchant_id": "papermill-supply",
        "lines": [{"sku": "PM-A4-500W", "qty": 10, "item": 0}]}})
    res = out["result"]["structuredContent"]
    assert res["decision"] == "approve" and res["status"] == "ordered"  # $38.90, no ask-me line
    receipt = (await client.get(f"/api/receipts/{res['receipt_id']}/verify")).json()
    assert receipt["valid"]


async def test_wrong_password(client):
    assert (await client.post("/api/login", json={"password": "nope"})).status_code == 401
