"""Resolve agent-supplied SKUs to merchant catalog data. The gateway never trusts agent prices."""

from typing import Protocol

import httpx

from gateway.domain import CartLineRequest, Money, ResolvedLine


class UnknownProduct(Exception):
    pass


class Catalog(Protocol):
    async def lookup(self, merchant_id: str, sku: str) -> dict: ...


class HttpCatalog:
    def __init__(self, base_url: str):
        self._http = httpx.AsyncClient(base_url=base_url, timeout=10)

    async def lookup(self, merchant_id: str, sku: str) -> dict:
        r = await self._http.get(f"/api/{merchant_id}/products/{sku}")
        if r.status_code == 404:
            raise UnknownProduct(f"{merchant_id}/{sku}")
        r.raise_for_status()
        return r.json()


class DictCatalog:
    """In-memory catalog in the demo merchant's catalog.json shape (tests, eval)."""

    def __init__(self, data: dict):
        self._data = data

    async def lookup(self, merchant_id: str, sku: str) -> dict:
        for p in self._data.get(merchant_id, {}).get("products", []):
            if p["sku"] == sku:
                return p
        raise UnknownProduct(f"{merchant_id}/{sku}")


async def resolve_lines(
    catalog: Catalog, merchant_id: str, lines: list[CartLineRequest]
) -> list[ResolvedLine]:
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
