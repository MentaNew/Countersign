"""The guard's line to the gateway: MCP tool calls with the browser agent's key (which only the
guard holds), plus the gateway's public key to check checkout permits."""

import itertools
from typing import Any

import httpx


class GatewayError(Exception):
    pass


class GatewayClient:
    def __init__(self, base_url: str, agent_key: str, timeout: float = 90):
        self.base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)
        self._headers = {"Authorization": f"Bearer {agent_key}",
                         "Accept": "application/json, text/event-stream",
                         "Content-Type": "application/json"}
        self._ids = itertools.count(1)
        self._public_key: str | None = None

    async def call(self, tool: str, **arguments: Any) -> dict[str, Any]:
        r = await self._http.post("/mcp/", headers=self._headers, json={
            "jsonrpc": "2.0", "id": next(self._ids), "method": "tools/call",
            "params": {"name": tool, "arguments": arguments}})
        if r.status_code != 200:
            raise GatewayError(f"gateway refused ({r.status_code}): {r.text[:300]}")
        result = r.json().get("result") or {}
        if result.get("isError"):
            text = " ".join(c.get("text", "") for c in result.get("content", []))
            raise GatewayError(text or "gateway error")
        return result.get("structuredContent") or {}

    async def public_key(self) -> str:
        if self._public_key is None:
            r = await self._http.get("/api/public-key")
            r.raise_for_status()
            self._public_key = r.json()["public_key"]
        return self._public_key

    async def aclose(self) -> None:
        await self._http.aclose()
