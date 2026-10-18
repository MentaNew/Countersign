"""Offline verifier for tests and for running the demo without any model API."""

from gateway.domain import InjectionVerdict, Mandate, ResolvedLine, SemanticVerdict


class StubVerifier:
    """Returns a fixed verdict (all matches by default)."""

    def __init__(self, verdict: SemanticVerdict | None = None):
        self.verdict = verdict or SemanticVerdict(
            match={"matches": 0.95, "partial": 0.04, "mismatch": 0.01},
            extra_items=0.02,
            model="stub",
        )
        self.calls = 0

    async def evaluate(self, mandate: Mandate, lines: list[ResolvedLine]) -> SemanticVerdict:
        self.calls += 1
        return self.verdict


class StubInjectionDetector:
    """Flags texts containing any of the given markers."""

    def __init__(self, markers: tuple[str, ...] = ("assistant:", "ai shopping assistant")):
        self.markers = markers

    async def score(self, texts: list[str]) -> InjectionVerdict:
        per = [0.99 if any(m in t.lower() for m in self.markers) else 0.01 for t in texts]
        return InjectionVerdict(score=max(per, default=0.0), per_text=per, model="stub")
