"""Week-1 PayPal sandbox checks (architecture doc §5, checks 1, 2 and 4).

Run from the repo root with credentials in .env:

    uv run python spikes/paypal_spikes.py token
    uv run python spikes/paypal_spikes.py order          # check 1a + 4: buyer-approved order, custom_id round-trip
    uv run python spikes/paypal_spikes.py vault          # check 1b: vault on first purchase, then charge without buyer
    uv run python spikes/paypal_spikes.py void           # check 4: authorize then void
    uv run python spikes/paypal_spikes.py disputes       # check 2: list disputes on the business account
    uv run python spikes/paypal_spikes.py simulate --url https://<tunnel>/webhooks/paypal

Each run writes raw responses to spikes/out/ so findings can go into docs/week1-findings.md.
Approve links must be opened while logged in as the sandbox *personal* (buyer) account.
"""

import argparse
import json
import sys
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gateway.config import get_settings

OUT = Path(__file__).parent / "out"
RETURN_URL = "https://example.com/return"
CANCEL_URL = "https://example.com/cancel"


def client() -> httpx.Client:
    s = get_settings()
    if not (s.paypal_client_id and s.paypal_client_secret):
        sys.exit("Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET in .env")
    token = httpx.post(
        f"{s.paypal_base_url}/v1/oauth2/token",
        auth=(s.paypal_client_id, s.paypal_client_secret),
        data={"grant_type": "client_credentials"},
    )
    token.raise_for_status()
    return httpx.Client(
        base_url=s.paypal_base_url,
        headers={"Authorization": f"Bearer {token.json()['access_token']}"},
        timeout=30,
    )


def call(c: httpx.Client, method: str, path: str, name: str, **kw) -> dict:
    headers = {"PayPal-Request-Id": str(uuid.uuid4()), "Prefer": "return=representation"}
    r = c.request(method, path, headers=headers, **kw)
    body = r.json() if r.content else {}
    OUT.mkdir(exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps({"status": r.status_code, "body": body}, indent=2))
    print(f"{method} {path} -> {r.status_code}  (saved spikes/out/{name}.json)")
    if r.status_code >= 400:
        print(json.dumps(body, indent=2))
    return body


def approve_link(order: dict) -> str | None:
    return next(
        (l["href"] for l in order.get("links", []) if l["rel"] in ("approve", "payer-action")), None
    )


def wait_for_buyer(order: dict) -> None:
    link = approve_link(order)
    if not link:
        print("No approve link: order did not need buyer approval.")
        return
    print(f"\nOpen as the sandbox buyer and approve:\n  {link}")
    input("Press Enter once approved (the redirect to example.com is expected)... ")


def purchase_unit(amount: str = "12.50") -> dict:
    return {
        "reference_id": "spike",
        "custom_id": "rcp_spike_001",  # check 4: does a receipt id survive on the order?
        "invoice_id": f"inv-{uuid.uuid4().hex[:10]}",
        "description": "A4 paper, 500 sheets x1 (spike)",
        "amount": {"currency_code": "USD", "value": amount},
    }


def cmd_token(_):
    client()
    print("OAuth token OK")


def cmd_order(_):
    c = client()
    order = call(c, "POST", "/v2/checkout/orders", "order_create", json={
        "intent": "CAPTURE",
        "purchase_units": [purchase_unit()],
        "payment_source": {"paypal": {"experience_context": {
            "return_url": RETURN_URL, "cancel_url": CANCEL_URL, "user_action": "PAY_NOW"}}},
    })
    wait_for_buyer(order)
    call(c, "POST", f"/v2/checkout/orders/{order['id']}/capture", "order_capture", json={})
    got = call(c, "GET", f"/v2/checkout/orders/{order['id']}", "order_get")
    pu = got.get("purchase_units", [{}])[0]
    print(f"custom_id round-trip: {pu.get('custom_id')!r}, invoice_id: {pu.get('invoice_id')!r}")


def cmd_vault(_):
    c = client()
    print("Step 1: first purchase with store_in_vault (needs Vault enabled on the sandbox app).")
    order = call(c, "POST", "/v2/checkout/orders", "vault_first_create", json={
        "intent": "CAPTURE",
        "purchase_units": [purchase_unit()],
        "payment_source": {"paypal": {
            "attributes": {"vault": {
                "store_in_vault": "ON_SUCCESS", "usage_type": "MERCHANT", "customer_type": "CONSUMER"}},
            "experience_context": {"return_url": RETURN_URL, "cancel_url": CANCEL_URL},
        }},
    })
    wait_for_buyer(order)
    cap = call(c, "POST", f"/v2/checkout/orders/{order['id']}/capture", "vault_first_capture", json={})
    vault = cap.get("payment_source", {}).get("paypal", {}).get("attributes", {}).get("vault", {})
    vault_id = vault.get("id")
    print(f"vault: {vault}")
    if not vault_id:
        sys.exit("No vault id returned: check 1b failed (or vault still pending; see saved JSON).")

    print("\nStep 2: second purchase with vault_id and no buyer interaction.")
    second = call(c, "POST", "/v2/checkout/orders", "vault_second_create", json={
        "intent": "CAPTURE",
        "purchase_units": [purchase_unit("9.99")],
        "payment_source": {"paypal": {"vault_id": vault_id}},
    })
    print(f"second order status: {second.get('status')}  approve link: {approve_link(second)}")
    if second.get("status") == "APPROVED":
        call(c, "POST", f"/v2/checkout/orders/{second['id']}/capture", "vault_second_capture", json={})
    print("Check 1 passes if the second order reached COMPLETED without opening a link.")


def cmd_void(_):
    c = client()
    order = call(c, "POST", "/v2/checkout/orders", "void_create", json={
        "intent": "AUTHORIZE",
        "purchase_units": [purchase_unit()],
        "payment_source": {"paypal": {"experience_context": {
            "return_url": RETURN_URL, "cancel_url": CANCEL_URL}}},
    })
    wait_for_buyer(order)
    auth = call(c, "POST", f"/v2/checkout/orders/{order['id']}/authorize", "void_authorize", json={})
    auth_id = auth["purchase_units"][0]["payments"]["authorizations"][0]["id"]
    call(c, "POST", f"/v2/payments/authorizations/{auth_id}/void", "void_void")
    got = call(c, "GET", f"/v2/payments/authorizations/{auth_id}", "void_get")
    print(f"authorization status after void: {got.get('status')}")


def cmd_disputes(_):
    c = client()
    body = call(c, "GET", "/v1/customer/disputes", "disputes_list", params={"page_size": 20})
    for d in body.get("items", []):
        print(d.get("dispute_id"), d.get("status"), d.get("reason"), d.get("dispute_amount"))
    if not body.get("items"):
        print("No disputes yet. Create one: log in at sandbox.paypal.com as the buyer, open a\n"
              "captured transaction from `order`, Report a problem. Then re-run this.")


def cmd_simulate(args):
    c = client()
    call(c, "POST", "/v1/notifications/simulate-event", "simulate_dispute_created", json={
        "url": args.url, "event_type": "CUSTOMER.DISPUTE.CREATED", "resource_version": "1.0"})
    print("Simulated events are mock payloads; label them as simulated in the demo.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("token", "order", "vault", "void", "disputes"):
        sub.add_parser(name)
    sim = sub.add_parser("simulate")
    sim.add_argument("--url", required=True, help="public URL that receives the webhook")
    args = p.parse_args()
    globals()[f"cmd_{args.cmd}"](args)


if __name__ == "__main__":
    main()
