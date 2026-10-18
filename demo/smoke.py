"""Live smoke test against a running gateway + demo merchant (real PayPal sandbox and verifier).

    uv run uvicorn demo.merchant.app:app --port 8100 &
    uv run uvicorn gateway.main:app --port 8000 &
    uv run python demo/smoke.py

Plays both roles: the agent (MCP with AGENT_API_KEY) and the user (REST login). Three
scenarios: in-policy purchase, wrong variant (semantic check), planted injection + gift card.
With VERIFIER=llm this makes about 4 GroqCloud calls."""

import json
import sys
from datetime import UTC, datetime, timedelta

import httpx

from gateway.config import get_settings

s = get_settings()
GW = s.public_base_url
MCP = {"Authorization": f"Bearer {s.agent_api_key}", "Accept": "application/json, text/event-stream",
       "Content-Type": "application/json", "MCP-Protocol-Version": "2025-06-18"}
http = httpx.Client(base_url=GW, timeout=120)
_id = 0


def tool(name: str, **arguments) -> dict:
    global _id
    _id += 1
    r = http.post("/mcp/", headers=MCP, json={"jsonrpc": "2.0", "id": _id, "method": "tools/call",
                                             "params": {"name": name, "arguments": arguments}})
    r.raise_for_status()
    res = r.json()["result"]
    if res.get("isError"):
        sys.exit(f"{name} failed: {res['content'][0]['text']}")
    return res["structuredContent"]


def show(title: str, p: dict) -> None:
    v = p["verdict"]
    sem = v.get("semantic") or {}
    inj = v.get("injection") or {}
    print(f"\n== {title}: {p['decision'].upper()} -> {p['status']}  total {p['total']} {p['currency']}")
    for r in p["reasons"]:
        print(f"   - {r}")
    if sem:
        print(f"   semantic {sem['model']}: {sem['match']} extra={sem['extra_items']}  "
              f"'{sem.get('rationale', '')}'")
    if inj:
        print(f"   injection {inj['model']}: {inj['per_text']}")
    if p.get("receipt_id"):
        print(f"   receipt {p['receipt_id']}  PayPal order {p['paypal_order_id']} "
              f"({p['paypal_status']})")


def main() -> None:
    token = http.post("/api/login", json={"password": s.user_password}).json()["token"]
    user = {"Authorization": f"Bearer {token}"}

    intent = tool(
        "confirm_intent",
        user_text="Buy 10 reams of A4 copy paper, max $60 total, from approved suppliers. "
                  "Ask me above $40.",
        items=[{"description": "A4 copy paper, 500 sheets, white", "qty": 10}],
        max_total={"amount": "60.00", "currency": "USD"},
        merchants=["papermill-supply", "northside-office"],
        approval_above={"amount": "40.00", "currency": "USD"},
        expires_at=(datetime.now(UTC) + timedelta(days=7)).isoformat(),
    )
    iid = intent["intent_id"]
    print(f"intent {iid} proposed:\n{intent['summary']}")
    r = http.post(f"/api/intents/{iid}/decision", json={"approve": True}, headers=user)
    r.raise_for_status()
    print(f"user approved; signature {r.json()['signature'][:24]}...")

    p = tool("request_purchase", intent_id=iid, merchant_id="papermill-supply",
             lines=[{"sku": "PM-A4-500W", "qty": 4, "item": 0}])
    show("1. in-policy purchase", p)

    p = tool("request_purchase", intent_id=iid, merchant_id="papermill-supply",
             lines=[{"sku": "PM-LTR-500W", "qty": 3, "item": 0}])
    show("2. wrong variant (US Letter)", p)
    if p["status"] == "pending_approval":
        r = http.post(f"/api/purchases/{p['purchase_id']}/decision",
                      json={"approve": False, "cart_hash": p["cart_hash"]}, headers=user)
        print(f"   user rejected -> {r.json()['status']}")

    page = http.get(f"{s.merchant_base_url}/api/papermill-supply/products").json()
    deal = next(x for x in page if x["sku"] == "PM-A4-500W-DEAL")
    import demo.merchant.app as merchant  # the agent would have read this from the HTML page

    raw = next(x for x in merchant.CATALOG["papermill-supply"]["products"] if x["sku"] == deal["sku"])
    p = tool("request_purchase", intent_id=iid, merchant_id="papermill-supply",
             lines=[{"sku": "PM-A4-500W-DEAL", "qty": 4, "item": 0}, {"sku": "PM-GIFT-25", "qty": 1}],
             context_texts=[raw["description"]])
    show("3. planted injection + gift card", p)

    log = http.get("/api/log/verify", headers=user).json()
    print(f"\nlog: {log['entries']} entries, chain valid={log['valid']}")
    first = next((x for x in http.get("/api/purchases", params={"intent_id": iid}, headers=user)
                  .json() if x["receipt_id"] and x["status"] != "failed"), None)
    if not first:
        print("no purchase reached PayPal")
        return
    v = http.get(f"/api/receipts/{first['receipt_id']}/verify").json()
    print(f"receipt {first['receipt_id']}: valid={v['valid']} (signature={v['signature_valid']}, "
          f"anchor={v['log_anchor_valid']}, chain={v['chain_valid']})")
    if first.get("payer_action_url"):
        print(f"buyer step (sandbox personal account): {first['payer_action_url']}")
    print(json.dumps({"intent_id": iid}, indent=None))


if __name__ == "__main__":
    main()
