"""Offline verifier for tests and for running the demo without any model API."""

import re

from gateway.domain import InjectionVerdict, Mandate, ResolvedLine, SemanticVerdict

_STOP = {"x", "of", "the", "a", "and", "with", "for", "per", "pack"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP and len(w) > 1}


class StubVerifier:
    """Without a fixed verdict: a crude word-overlap check, so offline demos still flag a line
    that shares no words with the item it claims to be (a gift card claimed as paper). It will
    not catch subtle variants (US Letter vs A4); that needs the real verifier.
    With a fixed verdict (tests): returns it."""

    def __init__(self, verdict: SemanticVerdict | None = None):
        self.verdict = verdict
        self.calls = 0

    async def evaluate(self, mandate: Mandate, lines: list[ResolvedLine]) -> SemanticVerdict:
        self.calls += 1
        if self.verdict is not None:
            return self.verdict
        flagged = []
        for ln in lines:
            target = mandate.items[ln.item].description if ln.item is not None and \
                0 <= ln.item < len(mandate.items) else ""
            if not (_words(ln.title) & _words(target)):
                flagged.append(ln.sku)
        if not flagged:
            return SemanticVerdict(match={"matches": 0.95, "partial": 0.04, "mismatch": 0.01},
                                   extra_items=0.02, model="stub")
        share = len(flagged) / max(len(lines), 1)
        return SemanticVerdict(
            match={"matches": 0.05, "partial": round(0.9 * (1 - share), 2),
                   "mismatch": round(0.95 - 0.9 * (1 - share), 2)},
            extra_items=0.9, model="stub", flagged_skus=flagged,
            rationale="Offline word-overlap check: these lines share no words with the item "
                      "they claim to be.",
        )


class StubInjectionDetector:
    """Flags texts containing any of the given markers."""

    def __init__(self, markers: tuple[str, ...] = ("assistant:", "ai shopping assistant")):
        self.markers = markers

    async def score(self, texts: list[str]) -> InjectionVerdict:
        per = [0.99 if any(m in t.lower() for m in self.markers) else 0.01 for t in texts]
        return InjectionVerdict(score=max(per, default=0.0), per_text=per, model="stub")
