"""The evaluation runner works offline, and the deterministic parts never regress: every
rule-violation case is blocked and no good cart is held, whatever the verifier."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))
import run_eval

pytestmark = pytest.mark.anyio


async def test_stub_eval_rules_and_no_false_alarms():
    out = await run_eval.run("stub", use_cache=False)
    m = run_eval.metrics(out["results"])
    assert m["n"] >= 150
    assert m["per_decision"]["block"]["recall"] == 1.0  # all hard_fail cases blocked
    a, b = m["by_category"]["hard_fail"].split("/")
    assert a == b
    assert m["false_alarms"] == []
    assert out["model_calls"] == 0
    assert {r["split"] for r in out["results"]} == {"development", "held-out"}


def test_wilson_interval():
    lo, hi = run_eval.wilson(0, 29)
    assert lo == 0 and 0.11 < hi < 0.13  # zero errors in 29 still allows up to ~12%
    lo, hi = run_eval.wilson(5, 13)
    assert 0.17 < lo < 0.19 and 0.63 < hi < 0.65
    assert run_eval.wilson(0, 0) is None


def test_every_case_is_well_formed():
    ids = set()
    for case in run_eval.load_cases():
        assert case["id"] not in ids
        ids.add(case["id"])
        assert case["expected"] in run_eval.DECISIONS
        run_eval.to_inputs(case)  # mandate and lines validate
