"""What changes when a service runs on a public host (Render) instead of a laptop:
keys come from secret settings instead of files, database URLs need our driver, login and MCP
calls are rate limited, logs are one JSON object per line, and placeholder secrets are refused."""

import base64
import json
import logging
import time
from collections import defaultdict, deque
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Receive, Scope, Send

PLACEHOLDER = "change-me"


def db_url(url: str) -> str:
    """Hosts hand out `postgres://` or `postgresql://`; SQLAlchemy needs the psycopg driver."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def pem_bytes(value: str) -> bytes:
    """A PEM key from a secret setting: pasted as is (real or `\\n` line breaks) or base64."""
    v = value.strip()
    if v.startswith("-----BEGIN"):
        return v.replace("\\n", "\n").encode() + b"\n"
    return base64.b64decode(v)


def is_public(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return host not in ("localhost", "127.0.0.1", "::1", "")


def refuse_placeholders(public_url: str, **secrets: str) -> None:
    """On a public host, never start with an example password or key."""
    if not is_public(public_url):
        return
    weak = [name for name, v in secrets.items() if not v or PLACEHOLDER in v or len(v) < 16]
    if weak:
        raise RuntimeError(f"refusing to start on {public_url}: set strong values for "
                           f"{', '.join(n.upper() for n in weak)} (16+ characters)")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"at": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"), "level": record.levelname,
               "logger": record.name, "msg": record.getMessage()}
        if record.exc_info:
            out["error"] = self.formatException(record.exc_info)
        return json.dumps(out)


def setup_logging(fmt: str) -> None:
    if fmt != "json":
        logging.basicConfig(level=logging.INFO)
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)


class RateLimit:
    """Sliding-window limits per client address, plus an overall cap per rule so rotating
    addresses does not help a password guesser. In memory: one process, resets on restart.

    rules: (path prefix, max requests per client, max overall, window seconds)."""

    def __init__(self, app: ASGIApp, rules: list[tuple[str, int, int, float]]):
        self.app = app
        self.rules = rules
        self.hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def _allow(self, key: tuple[str, str], limit: int, window: float, now: float) -> bool:
        q = self.hits[key]
        while q and q[0] <= now - window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("method") != "OPTIONS":
            path = scope.get("path", "")
            rule = next((r for r in self.rules if path.startswith(r[0])), None)
            if rule:
                prefix, per_client, overall, window = rule
                client = (scope.get("client") or ("?", 0))[0]
                now = time.monotonic()
                if not (self._allow((prefix, client), per_client, window, now)
                        and self._allow((prefix, "*"), overall, window, now)):
                    await send({"type": "http.response.start", "status": 429,
                                "headers": [(b"content-type", b"application/json"),
                                            (b"retry-after", str(int(window)).encode())]})
                    await send({"type": "http.response.body",
                                "body": b'{"detail":"too many requests, slow down"}'})
                    return
        await self.app(scope, receive, send)


class SecurityHeaders:
    """Standard protective headers on every response: no content sniffing, no framing (stops
    click-jacking of approval screens), no referrer leaks, API answers never cached, and HTTPS
    only (HSTS) when served over HTTPS."""

    def __init__(self, app: ASGIApp, hsts: bool = False):
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        api = scope.get("path", "").startswith(("/api/", "/desk/api/", "/mcp"))

        async def with_headers(message):
            if message["type"] == "http.response.start":
                h = list(message.get("headers") or [])
                h += [(b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"DENY"),
                      (b"referrer-policy", b"no-referrer"),
                      (b"content-security-policy", b"frame-ancestors 'none'")]
                if api:
                    h.append((b"cache-control", b"no-store"))
                if self.hsts:
                    h.append((b"strict-transport-security", b"max-age=31536000"))
                message["headers"] = h
            await send(message)

        await self.app(scope, receive, with_headers)
