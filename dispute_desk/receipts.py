"""Fetch and check a buyer's authorization receipt the way any seller could: over the gateway's
public endpoints, verifying the signature ourselves with the gateway's public key."""

from typing import Any, Protocol

import httpx

from gateway.signing.keys import verify_with_public_key  # signature check only, no gateway data


class Receipts(Protocol):
    async def check(self, receipt_id: str) -> dict[str, Any]: ...


class GatewayReceipts:
    def __init__(self, gateway_public_url: str, pinned_public_key: str = "",
                 transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(base_url=gateway_public_url.rstrip("/"), timeout=15,
                                       transport=transport)
        self.base_url = gateway_public_url.rstrip("/")
        self.pinned = pinned_public_key

    async def check(self, receipt_id: str) -> dict[str, Any]:
        out: dict[str, Any] = {"receipt_id": receipt_id, "found": False,
                               "verify_url": f"{self.base_url}/api/receipts/{receipt_id}/verify"}
        try:
            r = await self._http.get(f"/api/receipts/{receipt_id}")
            if r.status_code == 404:
                out["error"] = "receipt not found at the buyer's gateway"
                return out
            r.raise_for_status()
            signed = r.json()
            key = (await self._http.get("/api/public-key")).json()
            report = (await self._http.get(f"/api/receipts/{receipt_id}/verify")).json()
        except (httpx.HTTPError, ValueError) as e:
            out["error"] = f"gateway unreachable: {e}"
            return out

        public_key = self.pinned or key.get("public_key", "")
        out.update(
            found=True,
            receipt=signed["receipt"],
            key_id=key.get("key_id"),
            key_pinned=bool(self.pinned),
            key_matches_pin=(not self.pinned) or self.pinned == key.get("public_key"),
            # Checked here, independently of the gateway's own answer:
            signature_valid=verify_with_public_key(public_key, signed["receipt"], signed["signature"]),
            # Reported by the gateway (only it can re-hash its private log):
            gateway_report={k: report.get(k) for k in
                            ("valid", "chain_valid", "log_anchor_valid", "chain_entries")},
        )
        return out
