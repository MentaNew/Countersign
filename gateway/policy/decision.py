"""Combine hard checks, the semantic verdict and the injection score into one decision."""

from dataclasses import asdict, dataclass
from decimal import Decimal

from gateway.domain import CheckResult, InjectionVerdict, Mandate, SemanticVerdict, Verdict


@dataclass(frozen=True)
class Thresholds:
    min_match: float = 0.80  # escalate if P(matches) is below this
    max_extra_items: float = 0.50  # escalate if P(cart has items the user did not ask for) is above
    max_injection: float = 0.50  # escalate if any merchant text looks like instructions to the agent


def decide(
    mandate: Mandate,
    total: Decimal,
    hard: list[CheckResult],
    semantic: SemanticVerdict | None,
    injection: InjectionVerdict | None,
    t: Thresholds = Thresholds(),
) -> Verdict:
    reasons: list[str] = []
    failed = [c for c in hard if not c.passed]
    if failed:
        reasons = [f"{c.name}: {c.detail}" if c.detail else c.name for c in failed]
        decision = "block"
    else:
        if semantic is None:
            reasons.append("semantic check unavailable")
        else:
            if semantic.match.get("matches", 0.0) < t.min_match:
                reasons.append(
                    f"cart may not match the intent (P(matches)={semantic.match.get('matches', 0):.2f})"
                )
            if semantic.extra_items > t.max_extra_items:
                reasons.append(f"cart may contain unrequested items (P={semantic.extra_items:.2f})")
        if injection and injection.score > t.max_injection:
            reasons.append(
                f"merchant text the agent used looks like instructions to it (score={injection.score:.2f})"
            )
        if mandate.approval_above and total > mandate.approval_above.amount:
            reasons.append(f"total {total:.2f} is above the ask-me threshold {mandate.approval_above}")
        decision = "escalate" if reasons else "approve"
        if decision == "approve":
            reasons.append("all checks passed")
    return Verdict(
        decision=decision,
        hard_checks=hard,
        semantic=semantic,
        injection=injection,
        thresholds=asdict(t),
        reasons=reasons,
    )
