"""Live decisions keep the exact model exchanges (request + raw response text) for later
inspection; the audit log carries their hash; the agent does not see them."""

import json

import httpx
import pytest
import respx

from gateway.canonical import canonical_json
from gateway.domain import CartLineRequest as L
from gateway.signing.keys import sha256_hex
from gateway.verifier.groq import GROQ_URL, GroqClient, GroqVerifier
from tests.conftest import active_intent

pytestmark = pytest.mark.anyio

ANSWER = {"p_matches": 0.97, "p_partial": 0.02, "p_mismatch": 0.01, "p_extra_items": 0.0,
          "flagged_skus": [], "rationale": "A4 white copy paper, as asked."}
RAW = json.dumps({"id": "chatcmpl-test", "model": "openai/gpt-oss-20b", "choices": [{
    "index": 0, "message": {"role": "assistant", "content": json.dumps(ANSWER),
                            "reasoning": "Requested A4 paper; cart line is A4 paper."}}],
    "usage": {"prompt_tokens": 500, "completion_tokens": 60}})


@respx.mock
async def test_live_decision_keeps_the_exact_model_reply(gw):
    respx.post(GROQ_URL).mock(return_value=httpx.Response(200, text=RAW))
    gw.verifier = GroqVerifier(GroqClient("gsk_test"), "openai/gpt-oss-20b")
    iid = await active_intent(gw)
    out = await gw.request_purchase(iid, "papermill-supply", [L(sku="PM-A4-500W", qty=10, item=0)])
    assert "model_exchanges" not in out  # the agent's view

    full = await gw.get_purchase(out["purchase_id"])
    [ex] = full["model_exchanges"]
    assert ex["response_text"] == RAW and ex["status"] == 200
    assert ex["request"]["model"] == "openai/gpt-oss-20b"
    assert "gsk_test" not in json.dumps(ex)  # the API key is never recorded

    log = await gw.get_log(purchase_id=out["purchase_id"])
    decision = next(e for e in log if e["event"] == "purchase_decision")
    assert decision["payload"]["model_exchanges_sha256"] == sha256_hex(canonical_json([ex]))
