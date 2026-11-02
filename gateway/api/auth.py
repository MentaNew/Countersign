"""Two principals with separate credentials: the agent (MCP only) and the user (approvals)."""

import json
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from fastapi.security import HTTPBearer
from starlette.types import ASGIApp, Receive, Scope, Send

from gateway.logins import make_session_dependency
from gateway.service import AgentDenied

DEMO_USER = "demo-user"
# The agent making the current MCP call (set by AgentKeyMiddleware).
current_agent: ContextVar[str | None] = ContextVar("current_agent", default=None)
_bearer = HTTPBearer(auto_error=False)


# The person's sign-in check lives with the session store (shared with the seller desk).
make_user_dependency = make_session_dependency


class AgentKeyMiddleware:
    """Guards the MCP mount. Each request must carry `Authorization: Bearer <agent key>`; the key
    is looked up on every call, so a revoked agent or the stop-all switch takes effect at once.
    The agent's id is put in `current_agent` for the MCP tools."""

    def __init__(self, app: ASGIApp, authenticate: Callable[[str], Awaitable[str]]):
        self.app = app
        self.authenticate = authenticate

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        auth = dict(scope.get("headers") or []).get(b"authorization", b"").decode("latin-1")
        status, error = 401, "agent key required"
        if auth.startswith("Bearer ") and len(auth) > 7:
            try:
                agent_id = await self.authenticate(auth[7:])
            except AgentDenied as e:
                status, error = 403 if str(e) != "unknown agent key" else 401, str(e)
            else:
                token = current_agent.set(agent_id)
                try:
                    await self.app(scope, receive, send)
                finally:
                    current_agent.reset(token)
                return
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": json.dumps({"error": error}).encode()})
