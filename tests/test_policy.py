from datetime import UTC, datetime, timedelta
from decimal import Decimal

from gateway.domain import InjectionVerdict, Money, ResolvedLine, SemanticVerdict
from gateway.policy.decision import decide
from gateway.policy.hard_checks import SpendState, cart_total, run_hard_checks
from tests.conftest import paper_mandate

NOW = datetime.now(UTC)


def line(sku="PM-A4-500W", price="3.89", qty=10, item=0, cur="USD", title="A4 paper"):
    return ResolvedLine(sku=sku, title=title, unit_price=Money(amount=price, currency=cur),
                        qty=qty, item=item)


def failed(results):
    return {c.name for c in results if not c.passed}


def test_in_policy_cart_passes_all_checks():
    assert failed(run_hard_checks(paper_mandate(), "papermill-supply", [line()], SpendState(), NOW)) == set()


def test_each_hard_check_fires():
    m = paper_mandate()
    cases = {
        "budget": ([line(price="6.90")], "papermill-supply", SpendState()),
        "merchant_allowed": ([line()], "quickbuy-outlet", SpendState()),
        "currency": ([line(cur="EUR")], "papermill-supply", SpendState()),
        "lines_in_intent": ([line(), line(sku="PM-GIFT-25", price="1", qty=1, item=None)],
                            "papermill-supply", SpendState()),
        "quantity": ([line(qty=11, price="1")], "papermill-supply", SpendState()),
        "non_empty_cart": ([], "papermill-supply", SpendState()),
        "purchase_count": ([line(qty=1)], "papermill-supply", SpendState(purchases=5)),
    }
    for name, (lines, merchant, state) in cases.items():
        assert name in failed(run_hard_checks(m, merchant, lines, state, NOW)), name


def test_cumulative_budget_and_quantity_use_prior_spend():
    m = paper_mandate()
    state = SpendState(spent=Decimal("45.00"), purchases=1, qty_by_item={0: 8})
    out = failed(run_hard_checks(m, "papermill-supply", [line(qty=5)], state, NOW))
    assert {"budget", "quantity"} <= out


def test_expired_intent_blocks():
    m = paper_mandate(expires_at=NOW - timedelta(seconds=1))
    assert "not_expired" in failed(run_hard_checks(m, "papermill-supply", [line()], SpendState(), NOW))


def test_unit_price_cap():
    m = paper_mandate(items=[{"description": "A4 paper", "qty": 10,
                              "max_unit_price": {"amount": "3.50", "currency": "USD"}}])
    assert "unit_price" in failed(run_hard_checks(m, "papermill-supply", [line()], SpendState(), NOW))


def _sem(p_match=0.95, extra=0.02):
    rest = 1 - p_match
    return SemanticVerdict(match={"matches": p_match, "partial": rest, "mismatch": 0.0},
                           extra_items=extra, model="t")


def test_decision_rules():
    m = paper_mandate()
    ok = run_hard_checks(m, "papermill-supply", [line()], SpendState(), NOW)
    total = cart_total([line()])
    assert decide(m, total, ok, _sem(), None).decision == "approve"
    assert decide(m, total, ok, _sem(p_match=0.4), None).decision == "escalate"
    assert decide(m, total, ok, _sem(extra=0.9), None).decision == "escalate"
    inj = InjectionVerdict(score=0.97, per_text=[0.97], model="t")
    assert decide(m, total, ok, _sem(), inj).decision == "escalate"
    assert decide(m, Decimal("52.00"), ok, _sem(), None).decision == "escalate"  # above $40 ask-me
    bad = run_hard_checks(m, "quickbuy-outlet", [line()], SpendState(), NOW)
    v = decide(m, total, bad, _sem(), None)
    assert v.decision == "block" and any("merchant_allowed" in r for r in v.reasons)


def test_summary_is_plain_language():
    s = paper_mandate().summary()
    assert "10 x A4 copy paper" in s and "60.00 USD" in s and "40.00 USD" in s
