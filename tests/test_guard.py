import json
import subprocess
import sys
from pathlib import Path

from adapters.claude.guard import check_pre, record_post

GUARD = Path(__file__).resolve().parent.parent / "adapters" / "claude" / "guard.py"


def test_blocks_direct_paypal_and_secrets():
    assert check_pre("mcp__paypal__create_order", {}, {})
    assert check_pre("Bash", {"command": "curl https://api-m.sandbox.paypal.com/v2/checkout/orders"}, {})
    assert check_pre("Bash", {"command": "cat ../../.env"}, {})
    assert check_pre("Read", {"file_path": "/repo/keys/gateway_ed25519.pem"}, {})
    assert check_pre("Bash", {"command": "curl -X POST localhost:8000/api/purchases/pur_1/decision"}, {})
    assert check_pre("Bash", {"command": "curl -s http://localhost:8100/papermill-supply"}, {}) is None


def test_request_purchase_needs_active_intent():
    state: dict = {}
    args = {"intent_id": "int_1", "merchant_id": "m", "lines": []}
    assert check_pre("mcp__guardrail__request_purchase", args, state)
    record_post("mcp__guardrail__get_intent_status",
                [{"type": "text", "text": json.dumps({"intent_id": "int_1", "status": "pending_user"})}], state)
    assert check_pre("mcp__guardrail__request_purchase", args, state)
    record_post("mcp__guardrail__get_intent_status", {"intent_id": "int_1", "status": "active"}, state)
    assert check_pre("mcp__guardrail__request_purchase", args, state) is None


def test_command_hook_protocol(tmp_path):
    def run(mode, event):
        return subprocess.run([sys.executable, str(GUARD), mode], input=json.dumps(event),
                              capture_output=True, text=True, check=True,
                              env={"CLAUDE_PROJECT_DIR": str(tmp_path)}).stdout

    out = json.loads(run("pre", {"session_id": "s1", "tool_name": "mcp__guardrail__request_purchase",
                                 "tool_input": {"intent_id": "int_9"}}))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    run("post", {"session_id": "s1", "tool_name": "mcp__guardrail__get_intent_status",
                 "tool_response": {"structuredContent": {"intent_id": "int_9", "status": "active"}}})
    assert run("pre", {"session_id": "s1", "tool_name": "mcp__guardrail__request_purchase",
                       "tool_input": {"intent_id": "int_9"}}) == ""
