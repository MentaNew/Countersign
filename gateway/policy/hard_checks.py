"""Deterministic policy checks. Plain code, never an LLM: money, counts and dates live here."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from gateway.domain import CheckResult, Mandate, ResolvedLine


@dataclass(frozen=True)
class SpendState:
    """What has already been authorised against this intent (excluding the current cart)."""

    spent: Decimal = Decimal(0)
    purchases: int = 0
    qty_by_item: dict[int, int] | None = None


def run_hard_checks(
    mandate: Mandate,
    merchant_id: str,
    lines: list[ResolvedLine],
    state: SpendState,
    now: datetime,
) -> list[CheckResult]:
    cur = mandate.max_total.currency
    total = sum((ln.line_total for ln in lines), Decimal(0))
    already = state.qty_by_item or {}
    results: list[CheckResult] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        results.append(CheckResult(name=name, passed=passed, detail=detail))

    check("non_empty_cart", bool(lines), "" if lines else "cart has no lines")

    check(
        "not_expired",
        now < mandate.expires_at,
        f"intent expired at {mandate.expires_at.isoformat()}" if now >= mandate.expires_at else "",
    )

    check(
        "merchant_allowed",
        merchant_id in mandate.merchants,
        "" if merchant_id in mandate.merchants else f"{merchant_id} is not in {mandate.merchants}",
    )

    bad_cur = sorted({ln.unit_price.currency for ln in lines if ln.unit_price.currency != cur})
    check("currency", not bad_cur, f"lines priced in {bad_cur}, intent is in {cur}" if bad_cur else "")

    unmapped = [
        ln.sku for ln in lines if ln.item is None or not (0 <= ln.item < len(mandate.items))
    ]
    check(
        "lines_in_intent",
        not unmapped,
        f"not part of the approved intent: {unmapped}" if unmapped else "",
    )

    qty: dict[int, int] = defaultdict(int)
    for ln in lines:
        if ln.item is not None and 0 <= ln.item < len(mandate.items):
            qty[ln.item] += ln.qty
    over = []
    for idx, n in qty.items():
        allowed = mandate.items[idx].qty
        used = already.get(idx, 0)
        if used + n > allowed:
            over.append(f"item {idx} ({mandate.items[idx].description}): {used}+{n} > {allowed}")
    check("quantity", not over, "; ".join(over))

    pricey = []
    for ln in lines:
        if ln.item is None or not (0 <= ln.item < len(mandate.items)):
            continue
        cap = mandate.items[ln.item].max_unit_price
        if cap and (ln.unit_price.currency != cap.currency or ln.unit_price.amount > cap.amount):
            pricey.append(f"{ln.sku} at {ln.unit_price} > {cap}")
    check("unit_price", not pricey, "; ".join(pricey))

    remaining = mandate.max_total.amount - state.spent
    check(
        "budget",
        total <= remaining,
        f"cart {total:.2f} {cur} > remaining {remaining:.2f} {cur} "
        f"(cap {mandate.max_total}, already spent {state.spent:.2f})"
        if total > remaining
        else f"cart {total:.2f} of remaining {remaining:.2f} {cur}",
    )

    check(
        "purchase_count",
        state.purchases < mandate.max_purchases,
        f"{state.purchases} purchases already made, limit {mandate.max_purchases}"
        if state.purchases >= mandate.max_purchases
        else "",
    )

    return results


def cart_total(lines: list[ResolvedLine]) -> Decimal:
    return sum((ln.line_total for ln in lines), Decimal(0))
