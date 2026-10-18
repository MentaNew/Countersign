"""Agent-side guard hooks (defense in depth; the gateway enforces everything on its own).

Keeps the agent on the gateway path:
- no direct PayPal tools, API calls or credentials,
- no calls to the user's approval API,
- no request_purchase before the agent has seen its intent become active.

Used two ways:
- Claude Code command hook:  python3 guard.py pre|post   (hook JSON on stdin)
- Claude Agent SDK callbacks: see run_agent.py (imports `check_pre` / `record_post`)

Standard library only and Python 3.9 compatible, so the hook runs with any python3.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

GATEWAY_PREFIX = "mcp__guardrail__"

_PAYPAL_HOST = re.compile(r"paypal\.com|paypalobjects\.com", re.IGNORECASE)
_PAYPAL_API = re.compile(r"/v[12]/(checkout|payments|oauth2|vault|customer/disputes)", re.IGNORECASE)
_SECRETS = re.compile(r"(^|[\s/\"'=])\.env(\b|$)|keys/|\.pem\b|PAYPAL_CLIENT|GROQ_API_KEY|AGENT_API_KEY|USER_PASSWORD", re.IGNORECASE)
_USER_API = re.compile(r"/api/(login|intents/[^/\s]+/decision|purchases/[^/\s]+/decision)")


def check_pre(tool_name: str, tool_input: dict[str, Any], state: dict[str, Any]) -> str | None:
    """Return a reason to deny the call, or None to let it through."""
    name = tool_name or ""
    blob = json.dumps(tool_input or {})

    if "paypal" in name.lower() and not name.startswith(GATEWAY_PREFIX):
        return ("Direct PayPal tools are not available to the agent. All payments go through "
                "the guardrail gateway (request_purchase).")

    if name in ("Bash", "WebFetch", "WebSearch") or name.startswith("mcp__") and not name.startswith(GATEWAY_PREFIX):
        if _PAYPAL_HOST.search(blob) or _PAYPAL_API.search(blob):
            return "Calling PayPal directly is not allowed. Use the gateway's request_purchase tool."
        if _USER_API.search(blob):
            return ("Approvals belong to the user, in the gateway's web UI. Give the user the "
                    "review or approval URL instead.")

    if name in ("Bash", "Read", "Grep", "Glob", "Edit", "Write") and _SECRETS.search(blob):
        return "Credentials and signing keys are off limits to the agent."

    if name == GATEWAY_PREFIX + "request_purchase":
        intent_id = (tool_input or {}).get("intent_id")
        if intent_id not in state.get("active_intents", []):
            return (f"Intent {intent_id} is not known to be active yet. Call confirm_intent, have "
                    "the user approve it at review_url, then call get_intent_status until it "
                    "reports status 'active'.")
    return None


def _payloads(tool_response: Any) -> list[dict[str, Any]]:
    """MCP results reach hooks in a few shapes; collect every JSON object we can find."""
    found: list[dict[str, Any]] = []

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            found.append(x)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, str) and x.lstrip().startswith("{"):
            try:
                walk(json.loads(x))
            except ValueError:
                pass

    walk(tool_response)
    return found


def record_post(tool_name: str, tool_response: Any, state: dict[str, Any]) -> None:
    """Remember intents the gateway reported as active."""
    if tool_name not in (GATEWAY_PREFIX + "get_intent_status", GATEWAY_PREFIX + "confirm_intent"):
        return
    for p in _payloads(tool_response):
        if p.get("status") == "active" and isinstance(p.get("intent_id"), str):
            active = state.setdefault("active_intents", [])
            if p["intent_id"] not in active:
                active.append(p["intent_id"])


# --------------------------------------------------------------- command hook

def _state_file(session_id: str) -> Path:
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")) / ".guardrail-hook-state"
    root.mkdir(exist_ok=True)
    return root / f"{re.sub(r'[^A-Za-z0-9_-]', '_', session_id or 'default')}.json"


def main(mode: str) -> int:
    event = json.load(sys.stdin)
    path = _state_file(event.get("session_id", ""))
    state = json.loads(path.read_text()) if path.exists() else {}
    if mode == "pre":
        reason = check_pre(event.get("tool_name", ""), event.get("tool_input") or {}, state)
        if reason:
            json.dump({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                              "permissionDecision": "deny",
                                              "permissionDecisionReason": reason}}, sys.stdout)
    elif mode == "post":
        record_post(event.get("tool_name", ""), event.get("tool_response"), state)
        path.write_text(json.dumps(state))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "pre"))
