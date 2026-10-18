"""Two principals with separate credentials: the agent (MCP only) and the user (approvals)."""

import hashlib
import hmac
import secrets

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.types import ASGIApp, Receive, Scope, Send

DEMO_USER = "demo-user"
_bearer = HTTPBearer(auto_error=False)


def user_token(password: str) -> str:
    """Session token for the demo user, derived from the password. Never equals the agent key."""
    return hmac.new(password.encode(), b"guardrail-user-session", hashlib.sha256).hexdigest()


def make_user_dependency(password: str):
    expected = user_token(password)

    def require_user(cred: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
        if not cred or not secrets.compare_digest(cred.credentials, expected):
            raise HTTPException(401, "user login required")
        return DEMO_USER

    return require_user


class AgentKeyMiddleware:
    """Guards the MCP mount: requests must carry `Authorization: Bearer <AGENT_API_KEY>`."""

    def __init__(self, app: ASGIApp, agent_key: str):
        self.app = app
        self.expected = f"Bearer {agent_key}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            auth = dict(scope.get("headers") or []).get(b"authorization", b"")
            if not secrets.compare_digest(auth, self.expected):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": b'{"error":"agent key required"}'})
                return
        await self.app(scope, receive, send)
