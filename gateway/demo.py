"""Judge mode (DEMO_MODE=true): a scripted "demo agent" that judges drive from the web app.

The person (the judge) plays themselves: they approve the demo agent's permission and decide on held carts in
the normal screens. The demo agent only does what any agent can do through MCP: propose a permission and ask to
buy, with real carts priced and signed by the demo shops. Nothing is approved on the judge's behalf.
"""

import html
import re
import secrets
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import select

from gateway.db.models import Agent, Intent
from gateway.domain import CartLineRequest as L
from gateway.domain import Mandate
from gateway.service import Gateway, GatewayError, agent_key_hash, now

DEMO_AGENT = "Demo agent (judge mode)"
DEMO_REQUEST = "Restock 10 reams of A4 copy paper (500 sheets, white) from Papermill Supply or Northside Office. " \
               "Max 60 USD in total, ask me above 40 USD."

# Each scenario is a cart a real agent could send. What happens next is decided by the gateway, not by this file.
SCENARIOS: dict[str, dict[str, Any]] = {
    "in_policy": {"label": "Buy 2 reams, in policy", "merchant": "papermill-supply",
                  "lines": [L(sku="PM-A4-500W", qty=2, item=0)],
                  "expect": "approved automatically, PayPal sandbox order created"},
    "ask_me": {"label": "Premium paper, above your ask-me line", "merchant": "northside-office",
               "lines": [L(sku="NO-A4-500W-PREM", qty=8, item=0)],
               "expect": "held for you (41.60 USD is above 40)"},
    "poisoned": {"label": "Follow a poisoned product page", "merchant": "papermill-supply",
                 "lines": [L(sku="PM-A4-500W-DEAL", qty=3, item=0), L(sku="PM-GIFT-25", qty=1, item=0)],
                 "page": "/papermill-supply/PM-A4-500W-DEAL",
                 "expect": "held: a gift card disguised as paper, and hidden instructions on the page"},
    "over_budget": {"label": "Recycled paper, over budget", "merchant": "papermill-supply",
                    "lines": [L(sku="PM-A4-500W-ECO", qty=9, item=0)],
                    "expect": "blocked by the budget rule"},
    "wrong_shop": {"label": "A shop you did not allow", "merchant": "quickbuy-outlet",
                   "lines": [L(sku="QB-A4-500", qty=5, item=0)],
                   "expect": "blocked: shop not in your permission"},
    "wrong_variant": {"label": "US Letter instead of A4", "merchant": "papermill-supply",
                      "lines": [L(sku="PM-LTR-500W", qty=4, item=0)],
                      "expect": "held by the AI check (needs VERIFIER=llm; the offline checker misses it)"},
}


class Demo:
    def __init__(self, gw: Gateway, merchant_base_url: str):
        self.gw = gw
        self.merchant_base_url = merchant_base_url.rstrip("/")

    async def agent_id(self) -> str:
        """The demo agent's row (created once). Its key is random and never shown: only judge mode uses it."""
        async with self.gw.sessions.begin() as s:
            row = (await s.execute(select(Agent).where(Agent.name == DEMO_AGENT))).scalars().first()
            if row is None:
                key = secrets.token_urlsafe(32)
                row = Agent(id=f"agt_demo{secrets.token_hex(4)}", name=DEMO_AGENT, key_hash=agent_key_hash(key),
                            key_hint=key[:8], kind="mcp", created_by="judge mode", status="active")
                s.add(row)
                await s.flush()
            # Same off switches as any agent: revoked, its cap, or "Stop all agents".
            stopped = await self.gw._agent_stopped(s, row.id, row)
            if stopped:
                raise GatewayError(f"the demo agent can't act: {stopped}")
            row.last_used_at = now()
            return row.id

    async def state(self) -> dict[str, Any]:
        agent = await self.agent_id()
        async with self.gw.sessions() as s:
            rows = (await s.execute(select(Intent).where(Intent.agent_id == agent)
                                    .order_by(Intent.created_at.desc()).limit(5))).scalars().all()
        return {"enabled": True, "agent_id": agent, "agent_name": DEMO_AGENT, "request": DEMO_REQUEST,
                "verifier": self.gw.verifier.__class__.__name__,
                "permissions": [{"intent_id": i.id, "status": i.status, "created_at": i.created_at.isoformat()}
                                for i in rows],
                "scenarios": [{"id": k, "label": v["label"], "expect": v["expect"]} for k, v in SCENARIOS.items()]}

    async def propose(self) -> dict[str, Any]:
        agent = await self.agent_id()
        mandate = Mandate.model_validate({
            "items": [{"description": "A4 copy paper, 500 sheets, white", "qty": 10}],
            "max_total": {"amount": "60.00", "currency": "USD"},
            "merchants": ["papermill-supply", "northside-office"],
            "approval_above": {"amount": "40.00", "currency": "USD"},
            "expires_at": (now() + timedelta(days=7)).isoformat(), "max_purchases": 5})
        return await self.gw.confirm_intent(DEMO_REQUEST, mandate, agent)

    async def buy(self, intent_id: str, scenario: str) -> dict[str, Any]:
        sc = SCENARIOS.get(scenario)
        if sc is None:
            raise GatewayError(f"unknown scenario {scenario}")
        agent = await self.agent_id()
        context: list[str] = []
        if sc.get("page"):
            # Like the fooled agent: read the shop's product page and pass its text along.
            async with httpx.AsyncClient(timeout=70) as http:
                page = (await http.get(self.merchant_base_url + sc["page"])).text
            m = re.search(r"<div>(.*?)</div>", page, re.DOTALL)
            context = [html.unescape(m.group(1)) if m else page]
        return await self.gw.request_purchase(intent_id, sc["merchant"], sc["lines"], context, agent)
