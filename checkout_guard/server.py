"""MCP server (stdio) for a *browser* shopping agent. The agent gets a browser it can drive on
normal shops, and the gateway's permission tools, but no gateway key and no way to pay on its
own: the guard holds the key, reads the cart itself, and opens payment pages only for the cart
the user approved with their passkey.

Run by the agent's host, e.g. in Claude Code's MCP config:
    command: uv run --extra guard python -m checkout_guard
    env: GUARD_GATEWAY_URL=http://localhost:8000  GUARD_AGENT_KEY=<a "browser" agent's key>
Optional: GUARD_HEADLESS=1, GUARD_BROWSER_CHANNEL=chrome.

Limit: the agent must not be able to read this process's environment (no shell access to the
guard's machine, or hooks that forbid it); otherwise it could take the key and skip the guard.
"""

import os
from datetime import datetime
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from checkout_guard.browser import CheckoutGuard, GuardError
from checkout_guard.gateway_client import GatewayClient, GatewayError
from gateway.domain import MandateItem, Money

INSTRUCTIONS = """Shop on normal websites for the user, through a guarded browser.
1. Agree with the user what to buy, the budget, the shops (their web addresses, e.g.
   greenleaf.example.com), and when the permission ends. Call confirm_intent, give them the
   review_url, and wait with get_intent_status until it is active.
2. Shop: open_page, then click / type_text / choose using the element refs from the snapshot.
   Fill the cart, then go to the shop's checkout page (delivery details are fine; never card
   or bank details, the guard refuses them).
3. On the checkout page call checkout_ready with, for each cart line in order, the index of the
   requested item it fulfils. The guard reads the cart and totals itself and sends them to the
   gateway. The user approves with their passkey; wait with wait_for_approval.
4. Once approved, payment is unlocked for that exact cart: click the shop's PayPal button. The
   person logs in to PayPal and confirms. Changing the cart locks payment again.
5. When PayPal has sent the person back to the shop, call confirm_payment: the payment is
   recorded on the purchase and payment locks again.
Payment pages are blocked before approval; do not try to work around it."""


def build_server(guard: CheckoutGuard, gateway: GatewayClient) -> MCPServer:
    mcp = MCPServer("checkout-guard", instructions=INSTRUCTIONS)

    async def guarded(coro) -> Any:
        try:
            return await coro
        except (GuardError, GatewayError) as e:
            raise ToolError(str(e)) from e

    def remember(intent: dict[str, Any]) -> dict[str, Any]:
        """The guard warns (in the browser) when the agent shops outside the permitted shops."""
        shops = (intent.get("mandate") or {}).get("merchants")
        if shops:
            guard.allowed_shops = list(shops)
        return intent

    @mcp.tool()
    async def confirm_intent(
        user_text: str = Field(description="The user's request, in their words"),
        items: list[MandateItem] = Field(description="What to buy: description and quantity"),
        max_total: Money = Field(description="Total budget, shipping and tax included"),
        merchants: list[str] = Field(description="Shop addresses the user allows, e.g. "
                                     "greenleaf.example.com"),
        expires_at: datetime = Field(description="When this permission ends (ISO 8601 with tz)"),
        approval_above: Money | None = None,
        max_purchases: int = 5,
    ) -> dict[str, Any]:
        """Propose a purchase permission for the user to approve. Returns intent_id, review_url."""
        return remember(await guarded(gateway.call(
            "confirm_intent", user_text=user_text,
            items=[i.model_dump(mode="json") for i in items],
            max_total=max_total.model_dump(mode="json"),
            merchants=[m.lower().removeprefix("https://").removeprefix("www.").strip("/")
                       for m in merchants],
            expires_at=expires_at.isoformat(), max_purchases=max_purchases,
            approval_above=approval_above.model_dump(mode="json") if approval_above else None)))

    @mcp.tool()
    async def get_intent_status(intent_id: str, wait_seconds: float = 30) -> dict[str, Any]:
        """Status of a permission: pending_user, active, rejected."""
        return remember(await guarded(gateway.call("get_intent_status", intent_id=intent_id,
                                                   wait_seconds=wait_seconds)))

    @mcp.tool()
    async def open_page(url: str) -> dict[str, Any]:
        """Open a web page. Returns its text and the elements you can use (refs)."""
        return await guarded(guard.open(url))

    @mcp.tool()
    async def page_snapshot() -> dict[str, Any]:
        """The current page's text and elements again."""
        return await guarded(guard.snapshot())

    @mcp.tool()
    async def click(ref: str) -> dict[str, Any]:
        """Click an element by its ref from the last snapshot. Returns the new snapshot."""
        return await guarded(guard.click(ref))

    @mcp.tool()
    async def type_text(ref: str, text: str) -> dict[str, Any]:
        """Type into a field (never card, bank or password fields)."""
        return await guarded(guard.type_text(ref, text))

    @mcp.tool()
    async def choose(ref: str, option: str) -> dict[str, Any]:
        """Pick an option in a drop-down by its visible text."""
        return await guarded(guard.choose(ref, option))

    @mcp.tool()
    async def checkout_ready(
        intent_id: str,
        line_items: list[int | None] = Field(
            description="For each cart line, in order, the index of the requested item it "
            "fulfils (null if it is not one of them)"),
        context_texts: list[str] = Field(
            default_factory=list,
            description="Shop text you relied on (product descriptions, banners)"),
    ) -> dict[str, Any]:
        """On the shop's checkout page: the guard reads the cart and totals and asks the
        gateway. Returns the decision; held carts need the user's passkey approval."""
        async def run() -> dict[str, Any]:
            cart = await guard.read_cart()
            if len(line_items) != len(cart["lines"]):
                raise GuardError(f"the cart has {len(cart['lines'])} lines; give one item index "
                                 "per line")
            for ln, idx in zip(cart["lines"], line_items, strict=True):
                ln["item"] = idx
            out = await gateway.call("submit_page_cart", intent_id=intent_id, cart=cart,
                                     context_texts=context_texts)
            out["read_by_guard"] = {k: cart[k] for k in ("lines", "shipping", "tax", "total",
                                                         "page_url")}
            if out.get("status") in ("pending_approval", "authorized") and out.get("purchase_id"):
                await guard.show(mode="waiting", text="⏳ Waiting for your approval in Spend Guardrail",
                                 sub=f"cart {guard.cart_total} · payment 🔒 locked")
                guard.watch(out["purchase_id"])
                out["note"] = ("The guard unlocks payment by itself as soon as the person approves; "
                               "wait_for_approval tells you when.")
            return out
        return await guarded(run())

    @mcp.tool()
    async def wait_for_approval(purchase_id: str, wait_seconds: float = 60) -> dict[str, Any]:
        """Wait for the user's decision on a held cart. When approved, payment is unlocked."""
        async def run() -> dict[str, Any]:
            p = await gateway.call("get_purchase_status", purchase_id=purchase_id,
                                   wait_seconds=wait_seconds)
            if p.get("status") == "authorized" and p.get("checkout_permit"):
                p["payment"] = await guard.unlock(p)
                p["next"] = ("Click the shop's PayPal button; the person completes payment in "
                             "PayPal. Do not change the cart.")
            return p
        return await guarded(run())

    @mcp.tool()
    async def confirm_payment(purchase_id: str) -> dict[str, Any]:
        """After the person paid in PayPal and PayPal sent them back to the shop: the guard
        reports the PayPal order to the gateway, which records it on the purchase. Payment
        then locks again."""
        async def run() -> dict[str, Any]:
            report = await guard.payment_report()
            out = await gateway.call("confirm_page_payment", purchase_id=purchase_id, **report)
            await guard.lock()
            return out
        return await guarded(run())

    return mcp


def main() -> None:
    gateway = GatewayClient(os.environ.get("GUARD_GATEWAY_URL", "http://localhost:8000"),
                            os.environ["GUARD_AGENT_KEY"])
    guard = CheckoutGuard(gateway, headless=os.environ.get("GUARD_HEADLESS") == "1",
                          channel=os.environ.get("GUARD_BROWSER_CHANNEL", "chrome"))
    build_server(guard, gateway).run()
