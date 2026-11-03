"""Judge one cart: policy checks, then the semantic and injection checks, then the decision.
Shared by the gateway and the evaluation runner, so eval scores describe the real gateway."""

import asyncio
import logging
from datetime import datetime
from decimal import Decimal

from gateway.domain import CheckResult, Mandate, ResolvedLine, Verdict
from gateway.policy.decision import Thresholds, decide
from gateway.policy.hard_checks import (
    SpendState,
    cart_total,
    recurring_lines,
    run_hard_checks,
    unit_findings,
)
from gateway.verifier.base import InjectionDetector, Verifier

log = logging.getLogger("gateway.evaluate")
CHECK_TIMEOUT = 30.0  # seconds; a slow or failing model check holds the cart, never passes it


async def _guarded(coro, what: str, notes: list[str]):
    """Run a model check; on error or timeout return None and say so (fail closed)."""
    try:
        return await asyncio.wait_for(coro, CHECK_TIMEOUT)
    except Exception as e:  # noqa: BLE001 - any failure must hold the cart, not crash or pass
        log.warning("%s failed: %s", what, e)
        notes.append(f"{what} unavailable ({type(e).__name__}); held for a person instead")
        return None


async def evaluate_cart(
    mandate: Mandate,
    merchant_id: str,
    lines: list[ResolvedLine],
    state: SpendState,
    now: datetime,
    verifier: Verifier,
    injection: InjectionDetector,
    context_texts: list[str],
    thresholds: Thresholds,
    pre_checks: list[CheckResult] | None = None,
    charges: Decimal = Decimal(0),
) -> Verdict:
    hard = [*(pre_checks or []),
            *run_hard_checks(mandate, merchant_id, lines, state, now, charges)]
    hard_ok = all(c.passed for c in hard)
    notes: list[str] = []
    # Injection screening runs even on blocked carts: it is part of the evidence.
    inj = (await _guarded(injection.score(context_texts), "injection screen", notes)
           if context_texts else None)
    # The semantic check is skipped when a rule already blocks (saves a model call).
    semantic = (await _guarded(verifier.evaluate(mandate, lines), "semantic check", notes)
                if hard_ok else None)
    verdict = decide(mandate, cart_total(lines) + charges, hard, semantic, inj, thresholds)
    if verdict.decision != "block":
        held = unit_findings(mandate, lines, state)
        if held:
            reasons = [r for r in verdict.reasons if r != "all checks passed"]
            verdict = verdict.model_copy(update={"decision": "escalate",
                                                 "reasons": [*held, *reasons]})
    repeat = recurring_lines(mandate, lines)
    if repeat and verdict.decision == "approve":
        verdict = verdict.model_copy(update={
            "decision": "escalate",
            "reasons": [(f"a line signs you up for repeat orders (subscription, autoship or "
                        f"auto-renewal), which you did not ask for: {', '.join(repeat)}")]})
    elif repeat and verdict.decision == "escalate":
        verdict = verdict.model_copy(update={"reasons": [
            *verdict.reasons, f"repeat-order wording in: {', '.join(repeat)}"]})
    if notes and verdict.decision != "block":
        # A missing check never counts as a pass: hold for the human.
        reasons = [r for r in verdict.reasons if r not in ("all checks passed",
                                                         "semantic check unavailable")]
        verdict = verdict.model_copy(update={"decision": "escalate", "reasons": [*notes, *reasons]})
    elif notes:
        verdict = verdict.model_copy(update={"reasons": [*verdict.reasons, *notes]})
    return verdict
