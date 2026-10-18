"""PayPal Disputes API client for the seller. Holds the seller's credentials, never the buyer's."""

import json
import time
import uuid
from typing import Protocol

import httpx


class PayPalError(Exception):
    def __init__(self, status: int, body: dict):
        super().__init__(f"PayPal {status}: {body.get('name')} {body.get('message')}")
        self.status = status
        self.body = body


class Disputes(Protocol):
    async def get_dispute(self, dispute_id: str) -> dict: ...
    async def list_disputes(self) -> list[dict]: ...
    async def provide_evidence(self, dispute_id: str, evidence_input: dict) -> dict: ...
    async def make_offer(self, dispute_id: str, body: dict) -> dict: ...
    async def accept_claim(self, dispute_id: str, body: dict) -> dict: ...
    async def verify_webhook(self, headers: dict[str, str], event: dict, webhook_id: str) -> bool: ...


class DisputesClient:
    def __init__(self, base_url: str, client_id: str, client_secret: str):
        if not (client_id and client_secret):
            raise ValueError("PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET are not set")
        self._auth = (client_id, client_secret)
        self._http = httpx.AsyncClient(base_url=base_url, timeout=30)
        self._token: str | None = None
        self._exp = 0.0

    async def _bearer(self) -> str:
        if not self._token or time.time() > self._exp - 60:
            r = await self._http.post(
                "/v1/oauth2/token", auth=self._auth, data={"grant_type": "client_credentials"}
            )
            r.raise_for_status()
            j = r.json()
            self._token, self._exp = j["access_token"], time.time() + j["expires_in"]
        return self._token

    async def _call(self, method: str, path: str, **kw) -> dict:
        headers = {"Authorization": f"Bearer {await self._bearer()}",
                   "PayPal-Request-Id": str(uuid.uuid4())}
        r = await self._http.request(method, path, headers=headers, **kw)
        body = r.json() if r.content else {}
        if r.status_code >= 400:
            raise PayPalError(r.status_code, body)
        return body

    async def get_dispute(self, dispute_id: str) -> dict:
        return await self._call("GET", f"/v1/customer/disputes/{dispute_id}")

    async def list_disputes(self) -> list[dict]:
        # Observed Oct 17: real sandbox cases can be missing from this list; fetch by id instead.
        return (await self._call("GET", "/v1/customer/disputes", params={"page_size": 50})).get(
            "items", []
        )

    async def provide_evidence(self, dispute_id: str, evidence_input: dict) -> dict:
        # multipart/form-data with a JSON part named "input" (documents could be added as files)
        files = {"input": (None, json.dumps(evidence_input), "application/json")}
        return await self._call("POST", f"/v1/customer/disputes/{dispute_id}/provide-evidence",
                                files=files)

    async def make_offer(self, dispute_id: str, body: dict) -> dict:
        return await self._call("POST", f"/v1/customer/disputes/{dispute_id}/make-offer", json=body)

    async def accept_claim(self, dispute_id: str, body: dict) -> dict:
        return await self._call("POST", f"/v1/customer/disputes/{dispute_id}/accept-claim",
                                json=body)

    async def verify_webhook(self, headers: dict[str, str], event: dict, webhook_id: str) -> bool:
        h = {k.lower(): v for k, v in headers.items()}
        body = {
            "auth_algo": h.get("paypal-auth-algo"),
            "cert_url": h.get("paypal-cert-url"),
            "transmission_id": h.get("paypal-transmission-id"),
            "transmission_sig": h.get("paypal-transmission-sig"),
            "transmission_time": h.get("paypal-transmission-time"),
            "webhook_id": webhook_id,
            "webhook_event": event,
        }
        if not all(body.values()):
            return False
        try:
            out = await self._call("POST", "/v1/notifications/verify-webhook-signature", json=body)
        except PayPalError:
            return False
        return out.get("verification_status") == "SUCCESS"
