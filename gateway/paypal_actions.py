"""Guarded PayPal tools: the gateway side.

PayPal's official MCP server (@paypal/mcp, built on @paypal/agent-toolkit) lets an agent create orders, refunds,
invoices or subscriptions with no per-call limit. The PayPal guard (`paypal_guard/`) sits in front of it: read-only
tools pass through; every other tool becomes a request here, held for the person, who approves the exact tool and
arguments (with their passkey once they have one). The guard may then run that call once, and reports the result.
"""

from datetime import timedelta
from typing import Any

from sqlalchemy import select

from gateway.canonical import canonical_json, sha256_hex
from gateway.db.models import LogEntry, PaypalAction
from gateway.ledger import chain
from gateway.service import Gateway, GatewayError, NotFound, new_id, now
from gateway.signing.passkeys import new_statement

# Tools of PayPal's agent toolkit that only read. Everything else changes money or the account.
READ_ONLY_PREFIXES = ("get_", "list_", "show_", "search_")
READ_ONLY = {"generate_invoice_number", "get_merchant_insights"}
CLAIM_WINDOW = timedelta(minutes=30)


def is_read_only(tool: str) -> bool:
    return tool in READ_ONLY or tool.startswith(READ_ONLY_PREFIXES)


def args_hash(tool: str, arguments: dict[str, Any]) -> str:
    return sha256_hex(canonical_json({"tool": tool, "arguments": arguments}))


def summary(tool: str, a: dict[str, Any]) -> str:
    """One plain sentence for the person (the exact arguments are shown next to it)."""
    def amount(x: Any) -> str:
        if isinstance(x, dict) and "value" in x:
            return f"{x.get('value')} {x.get('currency_code', '')}".strip()
        return str(x) if x is not None else ""
    if tool == "create_refund":
        return f"Give a customer {amount(a.get('amount')) or 'their full payment'} back (refund of payment {a.get('capture_id', '?')})"
    if tool == "create_product":
        return f"Add the product \"{a.get('name', '?')}\" to your PayPal catalog"
    if tool == "create_order":
        items = a.get("items") or []
        total = sum(float(i.get("itemCost", 0)) * float(i.get("quantity", 1)) for i in items if isinstance(i, dict))
        return (f"Create a PayPal order for {len(items)} item(s), {total:.2f} {a.get('currencyCode', '')}".strip()
                if items else "Create a PayPal order")
    if tool == "pay_order":
        return f"Pay (capture) PayPal order {a.get('order_id') or a.get('orderId', '?')}"
    if tool in ("send_invoice", "send_invoice_reminder"):
        return f"Send invoice {a.get('invoice_id', '?')} to the recipient"
    if tool == "create_invoice":
        try:
            to = a["primary_recipients"][0]["billing_info"]["email_address"]
            return f"Create an invoice for {to}"
        except (KeyError, IndexError, TypeError):
            return "Create an invoice"
    if tool == "accept_dispute_claim":
        return f"Accept dispute {a.get('dispute_id', '?')} (refunds the buyer)"
    if tool.startswith("create_subscription") or tool == "create_subscription":
        return "Start a subscription (recurring payments)"
    return tool.replace("_", " ").capitalize()


MONEY_TOOLS = ("refund", "pay_order", "create_order", "dispute", "subscription", "send_invoice")


def impact(tool: str) -> str:
    """Does money move? Said plainly next to the decision."""
    if any(k in tool for k in MONEY_TOOLS):
        return "money"
    return "account"


class PaypalActions:
    def __init__(self, gw: Gateway, ttl: timedelta = CLAIM_WINDOW):
        self.gw = gw
        self.ttl = ttl

    def _view(self, a: PaypalAction, for_agent: bool = False) -> dict[str, Any]:
        status = a.status
        if status == "pending_approval" and a.approval_expires_at and now() > a.approval_expires_at:
            status = "approval_expired"
        view = {"action_id": a.id, "agent_id": a.agent_id, "tool": a.tool, "arguments": a.arguments,
                "args_hash": a.args_hash, "summary": summary(a.tool, a.arguments), "impact": impact(a.tool), "reason": a.reason,
                "status": status, "created_at": a.created_at.isoformat(),
                "approval_expires_at": a.approval_expires_at.isoformat() if a.approval_expires_at else None,
                "decided_by": a.decided_by, "user_signed": bool(a.user_signature), "result": a.result,
                "approval_url": f"{self.gw.web_base_url}/?open={a.id}"}
        if for_agent:
            view.pop("result", None)
        return view

    async def _get(self, s, action_id: str, lock: bool = False) -> PaypalAction:
        q = select(PaypalAction).where(PaypalAction.id == action_id)
        if lock:
            q = q.with_for_update()
        a = (await s.execute(q)).scalar_one_or_none()
        if a is None:
            raise NotFound(f"unknown PayPal action {action_id}")
        return a

    async def request(self, agent_id: str | None, tool: str, arguments: dict[str, Any],
                      reason: str | None = None) -> dict[str, Any]:
        if is_read_only(tool):
            raise GatewayError(f"{tool} only reads: the guard runs it directly")
        async with self.gw.sessions.begin() as s:
            if agent_id and (stopped := await self.gw._agent_stopped(s, agent_id)):
                raise GatewayError(stopped)
            a = PaypalAction(id=new_id("ppa"), agent_id=agent_id, tool=tool, arguments=arguments,
                             args_hash=args_hash(tool, arguments), reason=(reason or "")[:500] or None,
                             status="pending_approval", approval_expires_at=now() + self.ttl)
            s.add(a)
            await s.flush()
            await chain.append(s, "paypal_action_requested",
                               {"action_id": a.id, "agent_id": agent_id, "tool": tool, "args_hash": a.args_hash,
                                "arguments": arguments, "reason": a.reason})
            return self._view(a, for_agent=True)

    async def get(self, action_id: str, for_agent: bool = False) -> dict[str, Any]:
        async with self.gw.sessions() as s:
            view = self._view(await self._get(s, action_id), for_agent)
            if not for_agent:
                # Its steps in the audit log, for the person's timeline.
                q = (select(LogEntry).where(LogEntry.payload["action_id"].as_string() == action_id)
                     .order_by(LogEntry.seq))
                view["history"] = [{"seq": e.seq, "at": e.at.isoformat(), "event": e.event}
                                   for e in (await s.execute(q)).scalars()]
            return view

    async def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        async with self.gw.sessions() as s:
            rows = (await s.execute(select(PaypalAction).order_by(PaypalAction.created_at.desc()).limit(limit))).scalars()
            return [self._view(a) for a in rows]

    async def approval_options(self, action_id: str, user: str) -> dict[str, Any]:
        async with self.gw.sessions() as s:
            a = await self._get(s, action_id)
            statement = new_statement("paypal_action_approval", action_id=a.id, tool=a.tool,
                                      args_hash=a.args_hash, summary=summary(a.tool, a.arguments))
            return await self.gw._approval_options(s, user, f"ppa:{a.id}", statement)

    async def decide(self, action_id: str, user: str, approve: bool, expected_hash: str,
                     credential: dict[str, Any] | None = None) -> dict[str, Any]:
        async with self.gw.sessions.begin() as s:
            a = await self._get(s, action_id, lock=True)
            if self._view(a)["status"] != "pending_approval":
                raise GatewayError(f"this request is {self._view(a)['status']}")
            if expected_hash != a.args_hash:
                raise GatewayError("the arguments changed since you looked: reload")
            record = None
            if approve:
                record = await self.gw._user_signature(s, user, f"ppa:{a.id}", credential,
                                                       expected={"action_id": a.id, "args_hash": a.args_hash})
            a.status = "approved" if approve else "rejected"
            a.decided_by, a.decided_at, a.user_signature = user, now(), record
            if approve:  # time to run it after approval
                a.approval_expires_at = now() + self.ttl
            await chain.append(s, "paypal_action_approved" if approve else "paypal_action_rejected",
                               {"action_id": a.id, "by": user, "args_hash": a.args_hash,
                                "signature_hash": sha256_hex(canonical_json(record)) if record else None})
            return self._view(a)

    async def claim(self, action_id: str, agent_id: str | None, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """The guard is about to run the call: only once, only these exact arguments, only before expiry."""
        async with self.gw.sessions.begin() as s:
            a = await self._get(s, action_id, lock=True)
            if agent_id and a.agent_id != agent_id:
                raise GatewayError("this request belongs to a different agent")
            if a.status != "approved":
                raise GatewayError(f"not approved (status: {self._view(a)['status']})")
            if a.approval_expires_at and now() > a.approval_expires_at:
                a.status = "approval_expired"
                raise GatewayError("the approval expired; ask again")
            if args_hash(tool, arguments) != a.args_hash:
                raise GatewayError("these are not the approved arguments")
            a.status = "executing"
            await chain.append(s, "paypal_action_executing", {"action_id": a.id, "args_hash": a.args_hash})
            return self._view(a, for_agent=True)

    async def report(self, action_id: str, agent_id: str | None, ok: bool, result: dict[str, Any]) -> dict[str, Any]:
        async with self.gw.sessions.begin() as s:
            a = await self._get(s, action_id, lock=True)
            if agent_id and a.agent_id != agent_id:
                raise GatewayError("this request belongs to a different agent")
            if a.status != "executing":
                raise GatewayError(f"nothing to report (status: {a.status})")
            a.status, a.result = ("done" if ok else "failed"), result
            await chain.append(s, "paypal_action_done" if ok else "paypal_action_failed",
                               {"action_id": a.id, "result_hash": sha256_hex(canonical_json(result))})
            return self._view(a, for_agent=True)

    async def waiting(self) -> list[dict[str, Any]]:
        async with self.gw.sessions() as s:
            rows = (await s.execute(select(PaypalAction).where(
                PaypalAction.status == "pending_approval", PaypalAction.approval_expires_at > now())
                .order_by(PaypalAction.created_at.desc()))).scalars()
            return [{"kind": "paypal_action", "id": a.id, "path": f"/?open={a.id}",
                     "title": f"PayPal action: {a.tool}", "text": summary(a.tool, a.arguments),
                     "created_at": a.created_at.isoformat()} for a in rows]
