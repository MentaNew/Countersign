"""Run the shopping agent on the Claude Agent SDK, with the guard hooks as in-process callbacks.

    uv sync --extra agent
    uv run python adapters/claude/run_agent.py "Restock 10 reams of A4 paper, max $60, ask me above $40"

Uses your Claude Code login (or ANTHROPIC_API_KEY). The agent works in demo/agent-workspace,
which provides the guarded-purchase skill; it reaches the gateway only through MCP.
"""

import asyncio
import sys
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from adapters.claude.guard import check_pre, record_post
from gateway.config import get_settings

WORKSPACE = Path(__file__).resolve().parents[2] / "demo" / "agent-workspace"
state: dict[str, Any] = {}


async def pre_tool(input_data, tool_use_id, context):
    reason = check_pre(input_data["tool_name"], input_data.get("tool_input") or {}, state)
    if reason:
        print(f"   [guard] denied {input_data['tool_name']}: {reason}")
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                       "permissionDecision": "deny",
                                       "permissionDecisionReason": reason}}
    return {}


async def post_tool(input_data, tool_use_id, context):
    record_post(input_data["tool_name"], input_data.get("tool_response"), state)
    return {}


async def main(prompt: str) -> None:
    s = get_settings()
    options = ClaudeAgentOptions(
        cwd=str(WORKSPACE),
        setting_sources=["project"],  # loads the workspace's skill and CLAUDE.md
        mcp_servers={
            "guardrail": {
                "type": "http",
                "url": f"{s.public_base_url}/mcp/",
                "headers": {"Authorization": f"Bearer {s.agent_api_key}"},
            }
        },
        allowed_tools=["mcp__guardrail__*", "Bash(curl -s http://localhost:8100*)", "Skill"],
        disallowed_tools=["Write", "Edit"],
        hooks={
            "PreToolUse": [HookMatcher(hooks=[pre_tool])],
            "PostToolUse": [HookMatcher(hooks=[post_tool])],
        },
        max_turns=40,
    )
    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    print(f"\nagent: {block.text}")
                elif isinstance(block, ToolUseBlock):
                    print(f"   -> {block.name} {str(block.input)[:160]}")
        elif isinstance(msg, ResultMessage):
            print(f"\n[done] turns={msg.num_turns} cost=${(msg.total_cost_usd or 0):.4f}")


if __name__ == "__main__":
    asyncio.run(main(" ".join(sys.argv[1:]) or
                     "Restock 10 reams of A4 copy paper for the office from papermill-supply or "
                     "northside-office. Max $60 total, ask me above $40, valid for a week."))
