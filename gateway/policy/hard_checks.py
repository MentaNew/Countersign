"""Deterministic policy checks. Plain code, never an LLM: money, counts and dates live here."""

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from typing import Any

from gateway.domain import CheckResult, Mandate, ResolvedLine
from gateway.policy.units import line_units


@dataclass(frozen=True)
class SpendState:
    """What has already been authorised against this intent (excluding the current cart)."""

    spent: Decimal = Decimal(0)
    purchases: int = 0
    # Already bought per requested item, in requested items (pack sizes applied, see units.py)
    qty_by_item: dict[int, Any] | None = None
    # Already bought per requested item, in packages as listed (defaults to qty_by_item)
    packs_by_item: dict[int, int] | None = None


def run_hard_checks(
    mandate: Mandate,
    merchant_id: str,
    lines: list[ResolvedLine],
    state: SpendState,
    now: datetime,
    charges: Decimal = Decimal(0),
) -> list[CheckResult]:
    """`charges`: shipping and tax minus discounts, when the shop adds them at checkout."""
    cur = mandate.max_total.currency
    total = sum((ln.line_total for ln in lines), Decimal(0)) + charges
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

    over = []
    for idx, u in item_units(mandate, lines, state).items():
        # Counted in requested items (pack sizes applied; a line without a readable pack size
        # counts one per unit bought). Over the request is blocked, except when only pack sizes
        # read from titles say so while the packages themselves fit: that is held for the person
        # instead (unit_findings), in case a title was misread.
        if u.units_total > u.allowed and not (u.all_known and u.packs_total <= u.allowed):
            over.append(f"item {idx} ({mandate.items[idx].description}): "
                        f"{u.prior_packs}+{u.packs} > {u.allowed}")
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


# Words that turn a one-off purchase into a repeating one (Oct 23 eval: the model read
# "Autoship: every 4 weeks" as the same product and approved it).
RECURRING = re.compile(
    r"\b(auto-?ship|auto-?renew\w*|auto-?replenish\w*|subscri(?:be|ption)s?|recurring"
    r"|every\s+\d+\s*(?:days?|weeks?|months?|years?)"
    r"|(?:weekly|monthly|yearly|annual)\s+(?:delivery|deliveries|shipments?|subscription))\b",
    re.IGNORECASE)


def recurring_lines(mandate: Mandate, lines: list[ResolvedLine]) -> list[str]:
    """Lines whose title signs the person up for repeat orders, unless what they asked for
    already says so (e.g. "monthly subscription"). Deterministic, so it does not depend on the
    model noticing."""
    asked = " ".join(it.description for it in mandate.items)
    return [ln.sku for ln in lines
            if RECURRING.search(ln.title) and not RECURRING.search(asked)]


@dataclass(frozen=True)
class ItemUnits:
    allowed: int  # requested quantity of the item
    packs: int  # packages in this cart, as listed
    units: Fraction  # this cart, in requested items (pack sizes applied)
    prior_packs: int
    prior_units: Fraction
    all_known: bool  # every line's pack size was read from its title

    @property
    def packs_total(self) -> int:
        return self.prior_packs + self.packs

    @property
    def units_total(self) -> Fraction:
        return self.prior_units + self.units


def item_units(mandate: Mandate, lines: list[ResolvedLine],
               state: SpendState) -> dict[int, ItemUnits]:
    units: dict[int, Fraction] = defaultdict(Fraction)
    packs: dict[int, int] = defaultdict(int)
    known: dict[int, bool] = defaultdict(lambda: True)
    for ln in lines:
        if ln.item is None or not (0 <= ln.item < len(mandate.items)):
            continue
        lu = line_units(mandate.items[ln.item].description, ln.title, ln.qty)
        units[ln.item] += lu.requested
        packs[ln.item] += ln.qty
        known[ln.item] = known[ln.item] and lu.known
    prior_u = state.qty_by_item or {}
    prior_p = state.packs_by_item if state.packs_by_item is not None else prior_u
    return {idx: ItemUnits(allowed=mandate.items[idx].qty, packs=packs[idx], units=units[idx],
                           prior_packs=int(prior_p.get(idx, 0)),
                           prior_units=Fraction(prior_u.get(idx, 0)),
                           all_known=known[idx])
            for idx in units}


def unit_findings(mandate: Mandate, lines: list[ResolvedLine], state: SpendState) -> list[str]:
    """Reasons to hold a cart for the person, from pack sizes read in code."""
    out = []
    for idx, u in item_units(mandate, lines, state).items():
        what = mandate.items[idx].description
        if u.all_known and u.units_total > u.allowed and u.packs_total <= u.allowed:
            out.append(f"more than you asked for: the pack sizes add up to "
                       f"{float(u.units_total):g} x \"{what}\", you asked for {u.allowed}")
        elif u.units < u.packs and u.packs_total == u.allowed and u.units_total < u.allowed:
            out.append(f"smaller packs than you asked for: {u.packs} pack(s), but only "
                       f"{float(u.units_total):g} of the {u.allowed} x \"{what}\" requested")
    return out
