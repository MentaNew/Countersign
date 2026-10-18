from gateway.config import Settings
from gateway.verifier.base import InjectionDetector, Verifier


def build_verifiers(s: Settings) -> tuple[Verifier, InjectionDetector]:
    if s.verifier == "stub":
        from gateway.verifier.stub import StubInjectionDetector, StubVerifier

        return StubVerifier(), StubInjectionDetector()
    if s.verifier == "jev":
        raise NotImplementedError("Jev verifier: waiting for API access (see docs/week1-findings.md)")
    from gateway.verifier.groq import GroqClient, GroqInjectionDetector, GroqVerifier

    client = GroqClient(s.groq_api_key)
    return (
        GroqVerifier(client, s.llm_verifier_model),
        GroqInjectionDetector(client, s.llm_verifier_model, s.injection_guard_model),
    )
