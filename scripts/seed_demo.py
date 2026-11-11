"""Fill a fresh (or reset) database with the judge-mode story, so the Inbox is not empty.

It does what a person does in the web app, through the same API: sign in with the password, let the
demo agent ask for its permission, approve it, then let the demo agent send five carts. The gateway
decides each cart exactly as it would for any agent (one order, two held for you, two blocked).

Needs DEMO_MODE=true on the gateway. If a passkey is already registered, approving needs your device:
the script then stops after the request and tells you to approve it in the web app, and you can run
it again with --carts-only.

    uv run python scripts/seed_demo.py --gateway https://spend-guardrail-gw.onrender.com
    (asks for the password; or set SEED_PASSWORD)
"""

import argparse
import getpass
import os
import sys
import time

import httpx

SCENARIOS = ["in_policy", "ask_me", "poisoned", "over_budget", "wrong_shop"]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--gateway", default=os.environ.get("GATEWAY_URL", "http://localhost:8000"))
    p.add_argument("--carts-only", action="store_true", help="use the newest active demo permission")
    a = p.parse_args()
    password = os.environ.get("SEED_PASSWORD") or getpass.getpass("Web app password: ")

    # Free hosting may be asleep: the first request can take up to a minute.
    http = httpx.Client(base_url=a.gateway.rstrip("/"), timeout=120)
    r = http.post("/api/login", json={"password": password})
    if r.status_code != 200:
        sys.exit(f"sign-in failed ({r.status_code}): {r.text[:200]}")
    http.headers["Authorization"] = f"Bearer {r.json()['token']}"

    state = http.get("/api/demo")
    if state.status_code == 404:
        sys.exit("judge mode is off: set DEMO_MODE=true on the gateway and redeploy")
    state.raise_for_status()

    if a.carts_only:
        active = [x for x in state.json()["permissions"] if x["status"] == "active"]
        if not active:
            sys.exit("no active demo permission: approve it in the web app first")
        intent_id = active[0]["intent_id"]
    else:
        intent = http.post("/api/demo/permission").json()
        intent_id = intent["intent_id"]
        print(f"demo agent asked for permission {intent_id}")
        d = http.post(f"/api/intents/{intent_id}/decision", json={"approve": True})
        if d.status_code != 200:
            sys.exit(f"could not approve it here ({d.text[:160]}).\nApprove it in the web app "
                     f"(Inbox > Permissions), then run: python scripts/seed_demo.py --carts-only")
        print("approved it (as you, with your password)")

    for sc in SCENARIOS:
        r = http.post("/api/demo/buy", json={"intent_id": intent_id, "scenario": sc})
        out = r.json()
        print(f"  {sc:12} -> {out.get('decision') or out.get('status') or out.get('detail')}")
        time.sleep(1)
    print("done: open the web app's Inbox")


if __name__ == "__main__":
    main()
