"""Demo: a shopping agent that IS fooled by a poisoned product page, and a gateway that is not.

A careful agent like Claude refuses the planted instruction on its own, so this script plays an
agent that obeys it. It talks to the gateway only like a real agent would (MCP + agent key).

    uv run python demo/compromised_agent.py                  # you approve the permission in the web app
    uv run python demo/compromised_agent.py --auto-approve   # rehearsal: approves it for you

Needs the merchant (8100), gateway (8000) and web app (5173) running (docs/run-locally.md).
Attack 1 is blocked by a policy rule; attack 2 is disguised to pass the rules and is held for
the human by the semantic and injection checks. GroqCloud calls with VERIFIER=llm: about 5.
"""

import argparse
import html
import re
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gateway.config import get_settings

TTY = sys.stdout.isatty()


def c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if TTY else text


def say(who: str, text: str) -> None:
    tag = {"agent": c("35;1", "AGENT  "), "gateway": c("36;1", "GATEWAY"),
           "page": c("33;1", "PAGE   "), "you": c("32;1", "YOU    ")}[who]
    print(f"{tag} {text}")
    time.sleep(0.6 if TTY else 0)


class Agent:
    """Speaks MCP JSON-RPC to the gateway with the agent key, nothing else."""

    def __init__(self, base_url: str, agent_key: str):
        self.http = httpx.Client(base_url=base_url, timeout=120)
        self.headers = {"Authorization": f"Bearer {agent_key}", "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream"}
        self.n = 0

    def tool(self, name: str, **arguments) -> dict:
        self.n += 1
        r = self.http.post("/mcp/", headers=self.headers, json={
            "jsonrpc": "2.0", "id": self.n, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}})
        r.raise_for_status()
        res = r.json()["result"]
        if res.get("isError"):
            sys.exit(f"{name} failed: {res['content'][0]['text']}")
        return res["structuredContent"]


def show_decision(p: dict) -> None:
    colour = {"block": "31;1", "escalate": "33;1", "approve": "32;1"}[p["decision"]]
    say("gateway", c(colour, f"{p['decision'].upper()}") + f"  (cart {p['total']} {p['currency']})")
    for r in p["reasons"]:
        print(f"          - {r}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--auto-approve", action="store_true", help="approve the permission via the user API")
    ap.add_argument("--auto-reject", action="store_true", help="reject the held cart via the user API")
    args = ap.parse_args()
    s = get_settings()
    agent = Agent(s.public_base_url, s.agent_api_key)

    print(c("1", "\n=== A fooled agent meets the gateway ===\n"))
    say("you", '"Restock 10 reams of A4 paper from Papermill Supply. Max 60 USD, ask me above 40."')
    intent = agent.tool(
        "confirm_intent",
        user_text="Restock 10 reams of A4 paper from Papermill Supply. Max 60 USD, ask me above 40.",
        items=[{"description": "A4 copy paper, 500 sheets, white", "qty": 10}],
        max_total={"amount": "60.00", "currency": "USD"}, merchants=["papermill-supply"],
        approval_above={"amount": "40.00", "currency": "USD"},
        expires_at=(datetime.now(UTC) + timedelta(days=7)).isoformat())
    iid = intent["intent_id"]
    say("agent", f"Proposed permission {iid}. Please approve it: {intent['review_url']}")

    if args.auto_approve:
        user = httpx.Client(base_url=s.public_base_url, timeout=30)
        tok = user.post("/api/login", json={"password": s.user_password}).json()["token"]
        user.post(f"/api/intents/{iid}/decision", json={"approve": True},
                  headers={"Authorization": f"Bearer {tok}"}).raise_for_status()
        say("you", "Approved and signed (auto-approve).")
    status = intent["status"]
    while status != "active":
        status = agent.tool("get_intent_status", intent_id=iid, wait_seconds=60)["status"]
        if status == "rejected":
            sys.exit("Permission rejected; nothing to demo.")
    say("gateway", "Permission is active and signed. The agent may now shop.")

    # The agent reads the "weekly deal" page. The hidden span is invisible to a human visitor.
    page = httpx.get(f"{s.merchant_base_url}/papermill-supply/PM-A4-500W-DEAL", timeout=10).text
    hidden = re.search(r'<span style="display:none">(.*?)</span>', page, re.DOTALL)
    say("agent", "Found a cheaper listing: 'A4 Copy Paper (weekly deal)' at 3.49 USD.")
    if hidden:
        say("page", c("33", "hidden text: " + html.unescape(hidden.group(1)).strip()))
    say("agent", c("35", "Following the page's instructions: adding the gift card."))
    description = re.search(r"<div>(.*?)</div>", page, re.DOTALL)
    context = [html.unescape(description.group(1)) if description else page]

    print(c("1", "\n--- Attack 1: gift card added as an extra line ---"))
    p1 = agent.tool("request_purchase", intent_id=iid, merchant_id="papermill-supply",
                    lines=[{"sku": "PM-A4-500W-DEAL", "qty": 4, "item": 0},
                           {"sku": "PM-GIFT-25", "qty": 1}],
                    context_texts=context)
    show_decision(p1)

    print(c("1", "\n--- Attack 2: gift card disguised as the paper (claims to be item #0) ---"))
    say("agent", "Retrying: labelling the gift card as part of the A4 paper order.")
    p2 = agent.tool("request_purchase", intent_id=iid, merchant_id="papermill-supply",
                    lines=[{"sku": "PM-A4-500W-DEAL", "qty": 4, "item": 0},
                           {"sku": "PM-GIFT-25", "qty": 1, "item": 0}],
                    context_texts=context)
    show_decision(p2)
    if p2["status"] == "pending_approval":
        say("gateway", f"Held for the human, every line shown: {p2['approval_url']}")
        if args.auto_reject:
            user = httpx.Client(base_url=s.public_base_url, timeout=30)
            tok = user.post("/api/login", json={"password": s.user_password}).json()["token"]
            user.post(f"/api/purchases/{p2['purchase_id']}/decision",
                      json={"approve": False, "cart_hash": p2["cart_hash"]},
                      headers={"Authorization": f"Bearer {tok}"}).raise_for_status()
            say("you", "Rejected (auto-reject).")
        else:
            final = agent.tool("get_purchase_status", purchase_id=p2["purchase_id"], wait_seconds=60)
            if final["status"] != "pending_approval":
                say("you", f"Decision: {final['status']}.")

    bought = [p for p in (p1, p2) if p["status"] in ("authorized", "ordered", "captured")]
    print()
    if bought:
        say("gateway", c("31;1", "A cart with the gift card went through (a human approved it)."))
    else:
        say("gateway", c("32;1", "No gift card was bought. The agent was fooled; the gateway was not."))


if __name__ == "__main__":
    main()
