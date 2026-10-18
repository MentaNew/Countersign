"""MCP tools exposed to the (untrusted) shopping agent. There is deliberately no tool that
approves an intent or a cart: those live behind the user's login in the REST API."""

from datetime import datetime
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from gateway.domain import CartLineRequest, Mandate, MandateItem, Money
from gateway.service import Gateway, GatewayError

INSTRUCTIONS = """Payment gateway for agent purchases. Workflow:
1. Gather from the user what to buy (items, quantities), the total budget, allowed merchants,
   the amount above which they want to be asked, and when the permission expires.
2. Call confirm_intent. Give the user the review_url and wait with get_intent_status until the
   intent is active. You cannot pay before that.
3. Shop. Call request_purchase with the intent_id, the merchant, SKUs and quantities, and for
   each line the index of the requested item it fulfils. Pass any merchant text you relied on
   (descriptions, reviews, banners) in context_texts.
4. If the decision is escalate, give the user the approval_url and wait with
   get_purchase_status. If it is block, tell the user why; do not try to work around it.
The gateway takes prices from the merchant catalog and builds the PayPal order itself."""


def create_mcp(gw: Gateway) -> MCPServer:
    mcp = MCPServer("agent-spend-guardrail", instructions=INSTRUCTIONS)

    async def guarded(coro) -> dict[str, Any]:
        try:
            return await coro
        except GatewayError as e:
            raise ToolError(str(e)) from e

    @mcp.tool()
    async def confirm_intent(
        user_text: str = Field(description="The user's request, in their words"),
        items: list[MandateItem] = Field(description="What to buy: description and quantity"),
        max_total: Money = Field(description="Total budget across all purchases"),
        merchants: list[str] = Field(description="Merchant ids the user allows"),
        expires_at: datetime = Field(description="When this permission ends (ISO 8601 with tz)"),
        approval_above: Money | None = Field(
            default=None, description="Ask the user before any purchase above this amount"
        ),
        max_purchases: int = Field(default=5, description="Maximum number of separate purchases"),
    ) -> dict[str, Any]:
        """Propose a purchase intent for the user to approve. Returns intent_id and review_url."""
        try:
            mandate = Mandate(items=items, max_total=max_total, merchants=merchants,
                              expires_at=expires_at, approval_above=approval_above,
                              max_purchases=max_purchases)
        except ValueError as e:
            raise ToolError(f"invalid intent: {e}") from e
        return await guarded(gw.confirm_intent(user_text, mandate))

    @mcp.tool()
    async def get_intent_status(
        intent_id: str,
        wait_seconds: float = Field(default=30, description="Wait up to this long (max 60) "
                                    "for the user to decide"),
    ) -> dict[str, Any]:
        """Status of an intent: pending_user, active, rejected."""
        return await guarded(gw.wait_intent(intent_id, wait_seconds))

    @mcp.tool()
    async def request_purchase(
        intent_id: str,
        merchant_id: str,
        lines: list[CartLineRequest] = Field(
            description="SKUs and quantities; set `item` to the index of the requested item "
            "each line fulfils"
        ),
        context_texts: list[str] = Field(
            default_factory=list,
            description="Merchant text you relied on (product descriptions, reviews, banners)",
        ),
    ) -> dict[str, Any]:
        """Ask the gateway to buy this cart. Returns decision approve / block / escalate."""
        return await guarded(gw.request_purchase(intent_id, merchant_id, lines, context_texts))

    @mcp.tool()
    async def get_purchase_status(
        purchase_id: str,
        wait_seconds: float = Field(default=30, description="Wait up to this long (max 60) "
                                    "for the user's decision on an escalated cart"),
    ) -> dict[str, Any]:
        """Status of a purchase request."""
        return await guarded(gw.wait_purchase(purchase_id, wait_seconds))

    @mcp.tool()
    async def verify_receipt(receipt_id: str) -> dict[str, Any]:
        """Check an authorization receipt's signature and its anchor in the tamper-evident log."""
        return await guarded(gw.verify_receipt(receipt_id))

    @mcp.tool()
    async def get_log(intent_id: str) -> list[dict[str, Any]]:
        """Read-only audit log entries for one intent."""
        return await guarded(gw.get_log(intent_id=intent_id))

    return mcp
