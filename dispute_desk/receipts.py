"""Fetch and check a buyer's authorization receipt the way any seller could: over the gateway's
public endpoints, verifying the signature ourselves with the gateway's public key."""

from typing import Any, Protocol

import httpx

from gateway.ap2 import verify_bundle as verify_ap2_bundle  # signature + hash math only
from gateway.catalog.offers import merchant_signature_check, same_key  # signature math only
from gateway.ledger.proof import verify_receipt_proof  # hash + timestamp math only
from gateway.signing.keys import verify_with_public_key  # signature check only, no gateway data


class Receipts(Protocol):
    async def check(self, receipt_id: str) -> dict[str, Any]: ...


class GatewayReceipts:
    def __init__(self, gateway_public_url: str, pinned_public_key: str = "",
                 transport: httpx.AsyncBaseTransport | None = None,
                 shop_keys=None):
        self._http = httpx.AsyncClient(base_url=gateway_public_url.rstrip("/"), timeout=70,
                                       transport=transport)
        self.base_url = gateway_public_url.rstrip("/")
        self.pinned = pinned_public_key
        # async (merchant_id) -> the shop's own published public key, or None
        self.shop_keys = shop_keys

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
            jwks = (await self._http.get("/.well-known/jwks.json")).json()
            proof_resp = await self._http.get(f"/api/receipts/{receipt_id}/proof")
            proof = proof_resp.json() if proof_resp.status_code == 200 else {"anchored": False}
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
            # Outside witness, checked here from public data; the proof itself is kept as the
            # seller's own copy, so a later rewrite on the buyer's side would still be caught.
            witness=verify_receipt_proof(proof, signed["receipt"].get("log_hash")),
            proof=proof,
            merchant=await self._merchant_check(signed["receipt"]),
        )
        # Payment at the shop's own checkout (page carts): the gateway's signed confirmation,
        # its signature checked here with the same public key.
        if report.get("payment_confirmation"):
            out["payment_confirmation"] = check_payment_confirmation(
                report["payment_confirmation"], public_key)
        # AP2 v0.2 dispute verification, with keys this desk fetched itself: the gateway's
        # published JWKS and the shop's own published key.
        shop_jwk = None
        if self.shop_keys:
            try:
                shop_jwk = await self.shop_keys(signed["receipt"].get("merchant_id"))
            except (httpx.HTTPError, ValueError, KeyError):
                shop_jwk = None
        gw_jwk = (jwks.get("keys") or [{}])[0]
        out["ap2"] = verify_ap2_bundle(signed["receipt"].get("ap2"), gw_jwk,
                                       shop_jwk or (out["merchant"].get("jwk") or {}))
        return out

    async def _merchant_check(self, receipt: dict[str, Any]) -> dict[str, Any]:
        """The cart's merchant signature, checked here, and compared with the key the shop
        itself publishes (the seller knows its own key)."""
        out = merchant_signature_check(receipt)
        if out.get("present") and self.shop_keys:
            try:
                published = await self.shop_keys(receipt.get("merchant_id"))
            except (httpx.HTTPError, ValueError, KeyError):
                published = None
            out["matches_published_key"] = (same_key(published, out.get("jwk"))
                                             if published else None)
        return out


def http_shop_keys(merchant_base_url: str):
    client = httpx.AsyncClient(base_url=merchant_base_url.rstrip("/"), timeout=70)

    async def get(merchant_id: str) -> str | None:
        r = await client.get(f"/{merchant_id}/.well-known/agent-merchant.json")
        return r.json()["jwk"] if r.status_code == 200 else None

    return get



def check_payment_confirmation(pay: dict[str, Any], public_key: str) -> dict[str, Any]:
    """The gateway's signed record of a payment at the shop's own checkout, checked here."""
    body = {k: v for k, v in pay.items() if k not in ("signature", "signature_valid")}
    ok = bool(pay.get("signature")) and verify_with_public_key(public_key, body, pay["signature"])
    return {**body, "signature_valid": ok}
