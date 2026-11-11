"""MCP server (stdio) that sits between an agent and PayPal's official MCP server (@paypal/mcp).

The agent sees PayPal's own tools, unchanged. Read-only tools (get_, list_, show_, search_) are passed straight
through. Every other tool (orders, payments, refunds, invoices, subscriptions, dispute decisions) is held: the guard
asks the gateway, the person approves these exact arguments in the web app (with their passkey once they have one),
and only then does the guard run the call, once, and record PayPal's answer in the audit log.

The guard holds the PayPal credentials and the agent's gateway key; the agent holds neither.

Run by the agent's host (see demo/paypal-agent-workspace/.mcp.json):
    GUARD_GATEWAY_URL=http://localhost:8000 GUARD_AGENT_KEY=<an agent key>
    PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET (sandbox; read from .env too)
    uv run python -m paypal_guard
Optional: PAYPAL_MCP_COMMAND (default "npx -y @paypal/mcp@1.8.1 --tools=all"), GUARD_WAIT_SECONDS (default 90).
"""

import contextlib
import os
import shlex
from collections.abc import AsyncIterator
from typing import Any

import httpx
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from checkout_guard.gateway_client import GatewayClient, GatewayError
from gateway.paypal_actions import args_hash, is_read_only

DEFAULT_COMMAND = "npx -y @paypal/mcp@1.8.1 --tools=all"
NOTE = ("\n\n[Guarded] This PayPal tool moves money or changes the account: the person must approve these exact "
        "arguments first. The call waits for their decision; if it is still pending, call it again with the same "
        "arguments to continue.")


async def sandbox_token(client_id: str, secret: str, base: str) -> str:
    async with httpx.AsyncClient(base_url=base, timeout=30) as http:
        r = await http.post("/v1/oauth2/token", auth=(client_id, secret), data={"grant_type": "client_credentials"})
        r.raise_for_status()
        return r.json()["access_token"]


def text(msg: str, error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=msg)], is_error=error)


class PaypalGuard:
    def __init__(self, gateway: GatewayClient, upstream: ClientSession, wait_seconds: float = 90):
        self.gateway = gateway
        self.upstream = upstream
        self.wait = wait_seconds
        self.pending: dict[str, str] = {}  # args hash -> action id (resume on a repeated call)

    async def list_tools(self) -> list[types.Tool]:
        tools = (await self.upstream.list_tools()).tools
        out = []
        for t in tools:
            if not is_read_only(t.name):
                t = t.model_copy(update={"description": (t.description or "") + NOTE})
            out.append(t)
        return out

    async def call(self, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        if is_read_only(name):
            return await self.upstream.call_tool(name, arguments)
        key = args_hash(name, arguments)
        try:
            action_id = self.pending.get(key)
            if action_id is None:
                req = await self.gateway.call("request_paypal_action", tool=name, arguments=arguments)
                action_id = req["action_id"]
                self.pending[key] = action_id
            view = await self.gateway.call("get_paypal_action", action_id=action_id, wait_seconds=self.wait)
            status = view["status"]
            if status == "pending_approval":
                return text(f"Held for the person's approval: {view['approval_url']} . Nothing was sent to "
                            f"PayPal. Call {name} again with the same arguments to continue once they decide.")
            if status != "approved":
                self.pending.pop(key, None)
                return text(f"Not done: the person's decision is '{status}'. Nothing was sent to PayPal.", True)
            await self.gateway.call("claim_paypal_action", action_id=action_id, tool=name, arguments=arguments)
            self.pending.pop(key, None)
        except GatewayError as e:
            return text(f"Refused by the gateway: {e}. Nothing was sent to PayPal.", True)
        result = await self.upstream.call_tool(name, arguments)
        body = "\n".join(c.text for c in result.content if isinstance(c, types.TextContent))[:4000]
        await self.gateway.call("report_paypal_action", action_id=action_id, ok=not result.is_error,
                                result={"text": body})
        return result


def build_server(guard_ref: dict[str, PaypalGuard], lifespan) -> Server:
    async def on_list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=await guard_ref["guard"].list_tools())

    async def on_call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        return await guard_ref["guard"].call(params.name, dict(params.arguments or {}))

    return Server("paypal-guard", instructions=(
        "PayPal's own tools, guarded: read-only tools run directly; tools that move money wait for the person's "
        "approval of the exact arguments. Never try to reach PayPal another way."),
        lifespan=lifespan, on_list_tools=on_list_tools, on_call_tool=on_call_tool)


def main() -> None:
    import anyio

    from gateway.config import get_settings

    s = get_settings()
    gateway = GatewayClient(os.environ.get("GUARD_GATEWAY_URL", "http://localhost:8000"), os.environ["GUARD_AGENT_KEY"])
    command = shlex.split(os.environ.get("PAYPAL_MCP_COMMAND", DEFAULT_COMMAND))
    ref: dict[str, PaypalGuard] = {}

    @contextlib.asynccontextmanager
    async def lifespan(_server) -> AsyncIterator[dict[str, Any]]:
        token = await sandbox_token(s.paypal_client_id, s.paypal_client_secret, s.paypal_base_url)
        params = StdioServerParameters(command=command[0], args=command[1:], env={
            **os.environ, "PAYPAL_ACCESS_TOKEN": token, "PAYPAL_ENVIRONMENT": "SANDBOX"})
        async with stdio_client(params) as (r, w), ClientSession(r, w) as upstream:
            await upstream.initialize()
            ref["guard"] = PaypalGuard(gateway, upstream, float(os.environ.get("GUARD_WAIT_SECONDS", "90")))
            yield {}

    server = build_server(ref, lifespan)

    async def run() -> None:
        async with stdio_server() as (r, w):
            await server.run(r, w, server.create_initialization_options())

    anyio.run(run)


if __name__ == "__main__":
    main()

