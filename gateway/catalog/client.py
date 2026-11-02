"""Talk to merchants: signed cart offers and published keys. The gateway never trusts agent prices,
and with signed offers it does not have to trust its own lookups either: the merchant signs."""

from typing import Any, Protocol

import httpx

from gateway.catalog.offers import build_offer, merchant_profile, sign_offer
from gateway.domain import CartLineRequest, Money, ResolvedLine
from gateway.signing.jws import EcSigner


class UnknownProduct(Exception):
    pass


class UnknownMerchant(Exception):
    pass


class Catalog(Protocol):
    async def lookup(self, merchant_id: str, sku: str) -> dict: ...
    async def offer(self, merchant_id: str, lines: list[CartLineRequest]) -> dict[str, Any]: ...
    async def profile(self, merchant_id: str) -> dict[str, Any]: ...


class HttpCatalog:
    def __init__(self, base_url: str):
        # Generous: a sleeping free host (Render) takes about a minute to wake up.
        self._http = httpx.AsyncClient(base_url=base_url, timeout=70)

    async def lookup(self, merchant_id: str, sku: str) -> dict:
        r = await self._http.get(f"/api/{merchant_id}/products/{sku}")
        if r.status_code == 404:
            raise UnknownProduct(f"{merchant_id}/{sku}")
        r.raise_for_status()
        return r.json()

    async def offer(self, merchant_id: str, lines: list[CartLineRequest]) -> dict[str, Any]:
        r = await self._http.post(f"/api/{merchant_id}/cart-offers",
                                  json={"lines": [{"sku": ln.sku, "qty": ln.qty} for ln in lines]})
        if r.status_code == 404:
            raise UnknownMerchant(merchant_id)
        r.raise_for_status()
        return r.json()

    async def profile(self, merchant_id: str) -> dict[str, Any]:
        r = await self._http.get(f"/{merchant_id}/.well-known/agent-merchant.json")
        if r.status_code == 404:
            raise UnknownMerchant(merchant_id)
        r.raise_for_status()
        return r.json()


class DictCatalog:
    """In-memory merchants in the demo catalog.json shape (tests, eval). Each merchant signs
    offers with its own in-memory key, exactly like the demo merchant server."""

    def __init__(self, data: dict):
        self._data = data
        self.signers: dict[str, EcSigner] = {}

    def signer(self, merchant_id: str) -> EcSigner:
        return self.signers.setdefault(merchant_id, EcSigner.ephemeral())

    async def lookup(self, merchant_id: str, sku: str) -> dict:
        for p in self._data.get(merchant_id, {}).get("products", []):
            if p["sku"] == sku:
                return p
        raise UnknownProduct(f"{merchant_id}/{sku}")

    async def offer(self, merchant_id: str, lines: list[CartLineRequest]) -> dict[str, Any]:
        if merchant_id not in self._data:
            raise UnknownMerchant(merchant_id)
        return sign_offer(self.signer(merchant_id),
                          price_lines(merchant_id, self._data[merchant_id]["products"], lines))

    async def profile(self, merchant_id: str) -> dict[str, Any]:
        if merchant_id not in self._data:
            raise UnknownMerchant(merchant_id)
        return merchant_profile(merchant_id, self._data[merchant_id].get("name", merchant_id),
                                self.signer(merchant_id), "http://merchant.test")


def price_lines(merchant_id: str, products: list[dict], lines) -> dict[str, Any]:
    """Merchant side: price requested (sku, qty) lines from a product list into an offer."""
    by_sku = {p["sku"]: p for p in products}
    priced, unavailable = [], []
    for ln in lines:
        sku, qty = (ln.sku, ln.qty) if hasattr(ln, "sku") else (ln["sku"], ln["qty"])
        p = by_sku.get(sku)
        if p is None:
            unavailable.append(sku)
            continue
        priced.append({"sku": sku, "title": p["title"], "qty": qty,
                       "unit_price": {"amount": p["unit_price"], "currency": p["currency"]}})
    return build_offer(merchant_id, priced, unavailable)


def lines_from_offer(offer: dict[str, Any], requested: list[CartLineRequest]) -> list[ResolvedLine]:
    """Resolved lines come from the merchant's signed offer; the agent only supplies which
    requested item each line fulfils."""
    items = {ln.sku: ln.item for ln in requested}
    return [ResolvedLine(sku=ln["sku"], title=ln["title"],
                         unit_price=Money(amount=ln["unit_price"]["amount"],
                                          currency=ln["unit_price"]["currency"]),
                         qty=ln["qty"], item=items.get(ln["sku"]))
            for ln in offer["lines"]]


async def resolve_lines(
    catalog: Catalog, merchant_id: str, lines: list[CartLineRequest]
) -> list[ResolvedLine]:
    """Unsigned lookup (kept for tools that only browse)."""
    out = []
    for ln in lines:
        p = await catalog.lookup(merchant_id, ln.sku)
        out.append(
            ResolvedLine(
                sku=p["sku"],
                title=p["title"],
                unit_price=Money(amount=p["unit_price"], currency=p["currency"]),
                qty=ln.qty,
                item=ln.item,
            )
        )
    return out
