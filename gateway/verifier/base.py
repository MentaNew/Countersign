"""Verifier interfaces. Implementations see only the structured mandate and the resolved cart,
never the agent's reasoning; the injection detector sees one merchant text at a time."""

from typing import Protocol

from gateway.domain import InjectionVerdict, Mandate, ResolvedLine, SemanticVerdict


class Verifier(Protocol):
    async def evaluate(self, mandate: Mandate, lines: list[ResolvedLine]) -> SemanticVerdict: ...


class InjectionDetector(Protocol):
    async def score(self, texts: list[str]) -> InjectionVerdict: ...


def verifier_state(mandate: Mandate, lines: list[ResolvedLine]) -> dict:
    """The only state a semantic verifier receives. Prices, totals and quantities are left out
    on purpose: arithmetic and counting belong to the hard checks (which also allow a request
    to be split over several purchases)."""
    return {
        "requested_items": [
            {"index": i, "description": it.description} for i, it in enumerate(mandate.items)
        ],
        "cart_lines": [
            {"sku": ln.sku, "title": ln.title, "claimed_item_index": ln.item} for ln in lines
        ],
    }
