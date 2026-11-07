"""End-to-end check of the real web app in headless Chrome with a virtual authenticator
(Chrome's simulated Touch ID): register a passkey, approve a permission and a held cart with it,
witness the log with a real timestamp authority, then open the receipt.

Run against a throwaway stack on the TEST database (so no test passkey lands on your account):

    TESTDB=postgresql+psycopg://guardrail:guardrail@localhost:5433/guardrail_test
    uv run python tests/e2e/browser_flow.py --reset-db $TESTDB   # pytest leaves tampered rows
    uv run uvicorn demo.merchant.app:app --port 8101 &
    DATABASE_URL=$TESTDB VERIFIER=stub PAYPAL_VAULT_ID= ANCHOR_INTERVAL_SECONDS=0 \\
      PUBLIC_BASE_URL=http://localhost:8001 MERCHANT_BASE_URL=http://localhost:8101 \\
      WEB_BASE_URL=http://localhost:5174 uv run uvicorn gateway.main:app --port 8001 &
    (cd web && GATEWAY_URL=http://localhost:8001 npx vite --port 5174 --strictPort &)
    uv run python tests/e2e/browser_flow.py <screenshot-dir>

Needs Google Chrome. Makes one real call to the timestamp authority and creates sandbox
PayPal orders (no buyer action needed for the held cart: it stays "order created").
"""

import asyncio
import base64
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from functools import lru_cache

from gateway.config import get_settings

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
GW, WEB = "http://localhost:8001", "http://localhost:5174"
s = get_settings()


@lru_cache
def user() -> dict:
    """Sign in once through the API; scripts send the session token as a Bearer token."""
    r = httpx.post(f"{GW}/api/login", json={"password": get_settings().user_password}, timeout=30)
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['token']}"}
AGENT = {"Authorization": f"Bearer {s.agent_api_key}", "Content-Type": "application/json",
         "Accept": "application/json, text/event-stream"}


def tool(name: str, **arguments) -> dict:
    r = httpx.post(f"{GW}/mcp/", headers=AGENT, timeout=60, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments}})
    return r.json()["result"]["structuredContent"]


async def run(out_dir: Path) -> int:
    failures = 0
    clean = httpx.get(f"{GW}/api/log/verify", headers=user()).json()
    if not clean["valid"]:
        print("The test database has a broken log (pytest's tamper tests leave one). Stop the "
              "gateway, run with --reset-db <TESTDB>, start it again.")
        return 1

    def check(label: str, ok: bool, extra: str = "") -> None:
        nonlocal failures
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {label} {extra}")

    profile = tempfile.mkdtemp()
    chrome = subprocess.Popen([CHROME, "--headless=new", "--remote-debugging-port=9338",
                               f"--user-data-dir={profile}", "--window-size=1280,1600",
                               "--hide-scrollbars", "about:blank"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                tabs = json.load(urllib.request.urlopen("http://127.0.0.1:9338/json"))
                break
            except OSError:
                time.sleep(0.2)
        ws_url = next(t for t in tabs if t["type"] == "page")["webSocketDebuggerUrl"]
        async with websockets.connect(ws_url, max_size=80_000_000) as ws:
            n = 0

            async def cmd(method: str, **params):
                nonlocal n
                n += 1
                await ws.send(json.dumps({"id": n, "method": method, "params": params}))
                while True:
                    r = json.loads(await ws.recv())
                    if r.get("id") == n:
                        return r.get("result", {})

            async def go(path: str, wait: float = 2.5):
                await cmd("Page.navigate", url=WEB + path)
                await asyncio.sleep(wait)

            async def click(text: str) -> bool:
                """Click the first button containing `text`, waiting up to 8 s for it to render."""
                js = ("(() => { const b=[...document.querySelectorAll('button')]"
                      f".find(x=>x.textContent.includes({json.dumps(text)}) && !x.disabled); if(!b) return false;"
                      " b.click(); return true })()")
                for _ in range(40):
                    if (await cmd("Runtime.evaluate", expression=js))["result"]["value"]:
                        return True
                    await asyncio.sleep(0.2)
                return False

            async def shot(name: str):
                r = await cmd("Page.captureScreenshot", format="png", captureBeyondViewport=True)
                (out_dir / f"{name}.png").write_bytes(base64.b64decode(r["data"]))

            await cmd("Page.enable")
            await go("/", 1.5)
            # Sign in inside the page, so the browser holds the HttpOnly session cookie.
            await cmd("Runtime.evaluate", awaitPromise=True, expression=(
                "fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},"
                f"body:JSON.stringify({{password:{json.dumps(s.user_password)}}})}}).then(r=>r.status)"))
            await go("/", 1.5)
            await cmd("WebAuthn.enable")
            await cmd("WebAuthn.addVirtualAuthenticator", options={
                "protocol": "ctap2", "transport": "internal", "hasResidentKey": True,
                "hasUserVerification": True, "isUserVerified": True,
                "automaticPresenceSimulation": True})

            await go("/security")
            check("passkey button found", await click("Add a passkey"))
            await asyncio.sleep(3)
            await shot("1-security")
            check("passkey required after enrolment",
                  httpx.get(f"{GW}/api/passkeys", headers=user()).json()["required"])

            intent = tool("confirm_intent",
                          user_text="Restock 10 reams of A4 paper, max 60 USD, ask me above 40",
                          items=[{"description": "A4 copy paper, 500 sheets, white", "qty": 10}],
                          max_total={"amount": "60", "currency": "USD"},
                          merchants=["papermill-supply", "northside-office"],
                          approval_above={"amount": "40", "currency": "USD"},
                          expires_at=(datetime.now(UTC) + timedelta(days=7)).isoformat())
            await go(f"/intents/{intent['intent_id']}")
            await click("Approve with your passkey")
            await asyncio.sleep(3)
            await shot("2-intent")
            st = tool("get_intent_status", intent_id=intent["intent_id"], wait_seconds=0)
            check("intent approved with passkey", st["status"] == "active" and st.get("user_signed"))

            held = tool("request_purchase", intent_id=intent["intent_id"],
                        merchant_id="northside-office",
                        lines=[{"sku": "NO-A4-500W-PREM", "qty": 10, "item": 0}])
            check("cart held for the human", held["status"] == "pending_approval")
            await go(f"/purchases/{held['purchase_id']}")
            await click("Approve with your passkey")
            await asyncio.sleep(4)
            await shot("3-cart")
            done = tool("get_purchase_status", purchase_id=held["purchase_id"], wait_seconds=0)
            check("cart approved and ordered", done["status"] in ("ordered", "captured"),
                  done["status"])

            anchor = httpx.post(f"{GW}/api/log/anchor", headers=user(), timeout=60).json()
            check("log witnessed by a timestamp authority", "id" in anchor,
                  str(anchor.get("tsa_name")))
            v = httpx.get(f"{GW}/api/receipts/{done['receipt_id']}/verify").json()
            check("receipt valid", v["valid"])
            check("both device signatures valid",
                  all(x["valid"] for x in v["user_signatures"].values())
                  and len(v["user_signatures"]) == 2)
            check("receipt witnessed", bool(v["witness"].get("valid")), str(v["witness"]))
            check("AP2 v0.2 mandates verify", bool(v.get("ap2", {}).get("valid")),
                  f"{sum(c['ok'] for c in v.get('ap2', {}).get('checks', []))} checks")
            check("shop's signature over prices and lines valid",
                  bool(v.get("merchant_signature", {}).get("valid")),
                  str(v.get("merchant_signature", {}).get("key_id")))
            await go(f"/receipts/{done['receipt_id']}")
            await shot("4-receipt")
            await go("/log")
            await shot("5-log")

            # Agents: create one with the passkey, use its key, stop all, turn back on.
            await go("/agents")
            fill = ("(() => { const set = Object.getOwnPropertyDescriptor("
                    "HTMLInputElement.prototype, 'value').set;"
                    " for (const [label, v] of [['Agent name','e2e bot'],['Spending cap','20']]) {"
                    " const el = document.querySelector(`input[aria-label='${label}']`);"
                    " set.call(el, v); el.dispatchEvent(new Event('input', {bubbles: true})) } })()")
            await cmd("Runtime.evaluate", expression=fill)
            await asyncio.sleep(0.3)
            check("create-agent button found", await click("Create agent"))
            await asyncio.sleep(3)
            key = (await cmd("Runtime.evaluate", expression=(
                "document.querySelector('code.secret')?.textContent ?? ''")))["result"]["value"]
            made = next((a for a in httpx.get(f"{GW}/api/agents", headers=user()).json()["agents"]
                         if a["name"] == "e2e bot" and a["status"] == "active"), {})
            check("agent created, signed with passkey, key shown once",
                  key.startswith("agk_") and made.get("user_signed") and made.get("spend_cap") == "20.00")
            bot = {**AGENT, "Authorization": f"Bearer {key}"}

            def bot_call() -> int:
                return httpx.post(f"{GW}/mcp/", headers=bot, timeout=30, json={
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "get_intent_status",
                               "arguments": {"intent_id": "int_none", "wait_seconds": 0}}}
                    ).status_code

            check("new agent's key reaches MCP", bot_call() == 200)
            await shot("6-agents")
            await click("I copied it")
            check("stop button found", await click("Stop all agents"))
            await asyncio.sleep(1.5)
            check("stopped agent refused on its next call", bot_call() == 403)
            await shot("7-stopped")
            check("resume button found", await click("Turn agents back on"))
            await asyncio.sleep(3)
            check("agents back on after passkey signature", bot_call() == 200)
            log = [e["event"] for e in httpx.get(f"{GW}/api/log", headers=user()).json()]
            check("agent events in the log",
                  {"agent_created", "agents_stopped", "agents_resumed"} <= set(log))
    finally:
        chrome.terminate()
        chrome.wait(10)
        shutil.rmtree(profile, ignore_errors=True)  # Chrome profiles are large; don't leave them
    print(f"\n{'all checks passed' if not failures else f'{failures} check(s) failed'}; "
          f"screenshots in {out_dir}")
    return failures


async def reset_db(url: str) -> None:
    from gateway.db.session import create_schema, make_engine

    assert url.rsplit("/", 1)[1].endswith("_test"), "refusing to reset a non-test database"
    engine = make_engine(url)
    await create_schema(engine, drop=True)
    await engine.dispose()


if __name__ == "__main__":
    if sys.argv[1:2] == ["--reset-db"]:
        asyncio.run(reset_db(sys.argv[2]))
        sys.exit(0)
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "e2e-shots")
    out.mkdir(parents=True, exist_ok=True)
    sys.exit(asyncio.run(run(out)))
