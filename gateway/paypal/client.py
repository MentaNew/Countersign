"""PayPal REST client (Orders v2). Only the gateway holds these credentials."""

import time
import uuid
from decimal import Decimal
from typing import Protocol

import httpx

from gateway.domain import ResolvedLine


class PayPalError(Exception):
    def __init__(self, status: int, body: dict):
        super().__init__(f"PayPal {status}: {body.get('name')} {body.get('message')}")
        self.status = status
        self.body = body


class PayPal(Protocol):
    async def create_order(
        self,
        *,
        lines: list[ResolvedLine],
        currency: str,
        merchant_name: str,
        custom_id: str,
        invoice_id: str,
        return_url: str,
        cancel_url: str,
        vault_id: str = "",
    ) -> dict: ...

    async def capture_order(self, order_id: str) -> dict: ...

    async def get_order(self, order_id: str) -> dict: ...


def order_body(
    lines: list[ResolvedLine],
    currency: str,
    merchant_name: str,
    custom_id: str,
    invoice_id: str,
    return_url: str,
    cancel_url: str,
    vault_id: str = "",
) -> dict:
    total = sum((ln.line_total for ln in lines), Decimal(0))
    if vault_id:
        source = {"paypal": {"vault_id": vault_id}}
    else:
        source = {
            "paypal": {
                "experience_context": {
                    "brand_name": "Agent Spend Guardrail",
                    "user_action": "PAY_NOW",
                    "shipping_preference": "NO_SHIPPING",
                    "return_url": return_url,
                    "cancel_url": cancel_url,
                }
            }
        }
    return {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "reference_id": invoice_id,
                "custom_id": custom_id,
                "invoice_id": invoice_id,
                "description": f"Agent purchase at {merchant_name}"[:127],
                "amount": {
                    "currency_code": currency,
                    "value": f"{total:.2f}",
                    "breakdown": {"item_total": {"currency_code": currency, "value": f"{total:.2f}"}},
                },
                "items": [
                    {
                        "name": ln.title[:127],
                        "sku": ln.sku[:127],
                        "quantity": str(ln.qty),
                        "unit_amount": {
                            "currency_code": currency,
                            "value": f"{ln.unit_price.amount:.2f}",
                        },
                        "category": "PHYSICAL_GOODS",
                    }
                    for ln in lines
                ],
            }
        ],
        "payment_source": source,
    }


class PayPalClient:
    def __init__(self, base_url: str, client_id: str, client_secret: str):
        if not (client_id and client_secret):
            raise ValueError("PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET are not set")
        self._auth = (client_id, client_secret)
        self._http = httpx.AsyncClient(base_url=base_url, timeout=30)
        self._token: str | None = None
        self._token_exp = 0.0

    async def _bearer(self) -> str:
        if not self._token or time.time() > self._token_exp - 60:
            r = await self._http.post(
                "/v1/oauth2/token", auth=self._auth, data={"grant_type": "client_credentials"}
            )
            r.raise_for_status()
            j = r.json()
            self._token, self._token_exp = j["access_token"], time.time() + j["expires_in"]
        return self._token

    async def _call(self, method: str, path: str, **kw) -> dict:
        headers = {
            "Authorization": f"Bearer {await self._bearer()}",
            "PayPal-Request-Id": kw.pop("request_id", None) or str(uuid.uuid4()),
            "Prefer": "return=representation",
        }
        r = await self._http.request(method, path, headers=headers, **kw)
        body = r.json() if r.content else {}
        if r.status_code >= 400:
            raise PayPalError(r.status_code, body)
        return body

    async def create_order(self, *, lines, currency, merchant_name, custom_id, invoice_id,
                           return_url, cancel_url, vault_id="") -> dict:
        body = order_body(lines, currency, merchant_name, custom_id, invoice_id,
                          return_url, cancel_url, vault_id)
        # Idempotent per purchase: retrying never creates a second order.
        return await self._call("POST", "/v2/checkout/orders", json=body, request_id=invoice_id)

    async def capture_order(self, order_id: str) -> dict:
        return await self._call(
            "POST", f"/v2/checkout/orders/{order_id}/capture", json={},
            request_id=f"capture-{order_id}",
        )

    async def get_order(self, order_id: str) -> dict:
        return await self._call("GET", f"/v2/checkout/orders/{order_id}")


def payer_action_url(order: dict) -> str | None:
    return next(
        (l["href"] for l in order.get("links", []) if l.get("rel") in ("payer-action", "approve")),
        None,
    )
