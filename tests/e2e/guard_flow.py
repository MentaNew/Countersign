"""End-to-end check of the checkout guard (P2.1) on an ordinary web shop, in headless Chrome:
the agent fills a cart, the PayPal step is blocked, the guard reads the cart and totals itself,
the user approves with a passkey (software passkey here), payment unlocks, and changing the
cart locks it again. The agent's side goes through the guard's real MCP tools.

Throwaway stack on the TEST database (never your real one):

    TESTDB=postgresql+psycopg://guardrail:guardrail@localhost:5433/guardrail_test
    uv run python tests/e2e/browser_flow.py --reset-db $TESTDB
    PAYPAL_CLIENT_ID= uv run uvicorn demo.webshop.app:app --port 8300 &     # no real PayPal order
    DATABASE_URL=$TESTDB VERIFIER=stub ANCHOR_INTERVAL_SECONDS=0 \\
      PUBLIC_BASE_URL=http://localhost:8001 WEB_BASE_URL=http://localhost:5174 \\
      uv run uvicorn gateway.main:app --port 8001 &
    uv run --extra guard python tests/e2e/guard_flow.py <screenshot-dir>

Needs Google Chrome. Opens PayPal's sandbox site once (after approval) to show it is reachable.
"""

import asyncio
import base64
import contextlib
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from playwright.async_api import Error as PlaywrightError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from functools import lru_cache

from checkout_guard.browser import CheckoutGuard
from checkout_guard.gateway_client import GatewayClient
from checkout_guard.rules import host_of
from checkout_guard.server import build_server
from gateway.config import get_settings
from tests.soft_passkey import SoftPasskey

GW, ORIGIN, SHOP = "http://localhost:8001", "http://localhost:5174", "http://greenleaf.localhost:8300"
MERCHANT = "http://localhost:8100"  # demo merchants (one product page carries a planted injection)


@lru_cache
def user() -> dict:
    """Sign in once through the API; scripts send the session token as a Bearer token."""
    r = httpx.post(f"{GW}/api/login", json={"password": get_settings().user_password}, timeout=30)
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['token']}"}


def api(method: str, path: str, **body) -> dict:
    r = httpx.request(method, f"{GW}{path}", headers=user(), json=body or None, timeout=60)
    r.raise_for_status()
    return r.json()


async def run(out: Path) -> int:
    failures = 0

    def check(label: str, ok: bool, extra: str = "") -> None:
        nonlocal failures
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {label} {extra}")

    dev = SoftPasskey("localhost", ORIGIN)
    api("POST", "/api/passkeys/register", credential=dev.register(
        api("POST", "/api/passkeys/register/options")), label="e2e soft passkey")
    terms = {"name": "browser bot", "spend_cap": "100", "kind": "browser"}
    opts = api("POST", "/api/agents/options", **terms)
    agent = api("POST", "/api/agents", **terms, webauthn=dev.sign(opts["options"]))
    check("browser agent created with passkey", agent["kind"] == "browser")

    gateway = GatewayClient(GW, agent["key"])
    guard = CheckoutGuard(gateway, headless=True)
    mcp = build_server(guard, gateway)

    async def tool(name: str, **args) -> dict:
        res = await mcp.call_tool(name, args)
        data = getattr(res, "structuredContent", None) or getattr(res, "structured_content", None)
        if getattr(res, "isError", False) or getattr(res, "is_error", False):
            raise RuntimeError(str(res.content))
        return data.get("result", data) if isinstance(data, dict) else data

    async def shot(name: str) -> None:
        page = await guard.page()
        (out / f"{name}.png").write_bytes(await page.screenshot(full_page=True))

    def ref(snap: dict, *, text: str = "", name: str = "") -> str:
        for el in snap["elements"]:
            if (text and text in el["text"]) or (name and el["name"] == name):
                return el["ref"]
        raise AssertionError(f"no element {text or name} on {snap['url']}")

    try:
        it = await tool("confirm_intent", user_text="10 reams of A4 paper from Greenleaf, max $60",
                        items=[{"description": "A4 copy paper, 500 sheets", "qty": 10}],
                        max_total={"amount": "60", "currency": "USD"},
                        merchants=["greenleaf.localhost"],
                        expires_at=(datetime.now(UTC) + timedelta(days=1)).isoformat())
        o = api("POST", f"/api/intents/{it['intent_id']}/approval-options")
        api("POST", f"/api/intents/{it['intent_id']}/decision", approve=True,
            webauthn=dev.sign(o["options"]))
        st = await tool("get_intent_status", intent_id=it["intent_id"], wait_seconds=0)
        check("permission active", st["status"] == "active")

        snap = await tool("open_page", url=f"{SHOP}/products/a4-copy-paper")
        await tool("type_text", ref=ref(snap, name="qty"), text="10")
        snap = await tool("click", ref=ref(snap, text="Add to cart"))
        snap = await tool("click", ref=ref(snap, text="Check out"))
        await tool("type_text", ref=ref(snap, name="email"), text="buyer@example.com")
        check("on the shop's checkout page", "/checkout" in snap["url"], snap["url"])
        await shot("1-checkout")

        clicking = asyncio.create_task(tool("click", ref=ref(snap, text="Pay with PayPal")))
        page, title = await guard.page(), ""
        for _ in range(20):  # the guard shows its "payment locked" page for a moment
            await asyncio.sleep(0.15)
            with contextlib.suppress(PlaywrightError):  # the page may be mid-navigation
                title = await page.title()
            if "Payment locked" in title:
                break
        check("a readable 'payment locked' page instead of Chrome's error", "Payment locked" in title)
        await shot("1b-locked")
        snap = await clicking
        check("PayPal step blocked before approval", "blocked" in snap
              and host_of(snap["url"]) == "greenleaf.localhost", snap["url"])
        page = await guard.page()
        check("guard identifies itself as an AI agent",
              "AI shopping agent" in await page.evaluate("navigator.userAgent"))

        held = await tool("checkout_ready", intent_id=it["intent_id"], line_items=[0])
        check("guard read the cart and totals; held for passkey",
              held["status"] == "pending_approval" and held["total"] == "42.79",
              f"{held['status']} {held['total']}")
        check("unsigned reason shown", held["reasons"][0].startswith("cart read from the shop"))

        full = api("GET", f"/api/purchases/{held['purchase_id']}")
        (out / "2-guard-screenshot.png").write_bytes(
            base64.b64decode(full["page_screenshot_png_b64"]))
        await shot("2b-waiting")
        o = api("POST", f"/api/purchases/{held['purchase_id']}/approval-options")
        api("POST", f"/api/purchases/{held['purchase_id']}/decision", approve=True,
            cart_hash=held["cart_hash"], webauthn=dev.sign(o["options"]))
        for _ in range(40):  # the guard notices the approval by itself
            if guard.unlocked():
                break
            await asyncio.sleep(0.5)
        check("payment unlocked by itself after the approval (no agent call)", guard.unlocked())
        await asyncio.sleep(0.5)
        await shot("2c-unlocked")
        done = await tool("wait_for_approval", purchase_id=held["purchase_id"], wait_seconds=0)
        check("approved; guard checked the permit and unlocked payment",
              done["status"] == "authorized" and "unlocked" in done.get("payment", ""),
              done.get("payment", ""))

        v = api("GET", f"/api/receipts/{done['receipt_id']}/verify")
        check("receipt valid and labelled as a page cart",
              v["valid"] and v["receipt"]["cart_mandate"]["cart_source"] == "page")

        snap = await tool("page_snapshot")
        snap = await tool("click", ref=ref(snap, text="Pay with PayPal"))
        check("after approval the PayPal step opens", "paypal.com" in host_of(snap["url"]),
              host_of(snap["url"]))
        await shot("3-paypal")

        # The person pays in PayPal (sandbox login, not scripted here); PayPal then sends the
        # browser back to the shop with the order id, which is what the guard watches for.
        page = await guard.page()
        await page.goto(f"{SHOP}/checkout/return?token=E2EORDER12345678&PayerID=E2EPAYER")
        paid = await tool("confirm_payment", purchase_id=held["purchase_id"])
        check("payment recorded from PayPal's return to the shop",
              paid["paypal_order_id"] == "E2EORDER12345678"
              and paid["status"] in ("captured", "payment_reported"), paid["status"])
        check("payment locked again once recorded", not guard.unlocked())
        v = api("GET", f"/api/receipts/{done['receipt_id']}/verify")
        check("receipt carries the signed payment confirmation",
              v["valid"] and v["payment_confirmation"]["signature_valid"])

        snap = await tool("open_page", url=f"{SHOP}/products/premium-pens")
        await tool("click", ref=ref(snap, text="Add to cart"))
        snap = await tool("open_page", url=f"{SHOP}/checkout")
        snap = await tool("click", ref=ref(snap, text="Pay with PayPal"))
        check("locked again: PayPal blocked", "blocked" in snap)

        # A shop outside the permission, with text aimed at AI agents: both are flagged in the browser.
        snap = await tool("open_page", url=f"{MERCHANT}/papermill-supply/PM-A4-500W-DEAL")
        w = snap.get("warnings", {})
        check("other shop and agent-aimed text are flagged", "shop" in w and "agent_bait" in w, str(list(w)))
        await shot("4-warnings")
    finally:
        await guard.close()
        await gateway.aclose()
    print(f"\n{'all checks passed' if not failures else f'{failures} check(s) failed'}; "
          f"screenshots in {out}")
    return failures


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "guard-shots")
    out.mkdir(parents=True, exist_ok=True)
    sys.exit(asyncio.run(run(out)))
