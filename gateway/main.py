"""Gateway entry point: MCP for the agent at /mcp, REST for the user at /api, PayPal redirects
at /paypal. Run: uv run uvicorn gateway.main:app --port 8000"""

import contextlib
import json
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mcp.server.transport_security import TransportSecuritySettings

from gateway.api.auth import AgentKeyMiddleware, make_user_dependency
from gateway.api.routes import build_paypal_router, build_router
from gateway.catalog.client import HttpCatalog
from gateway.config import Settings, get_settings
from gateway.db.session import create_schema, make_engine, make_sessionmaker
from gateway.mcp_tools import create_mcp
from gateway.paypal.client import PayPalClient
from gateway.service import Gateway
from gateway.signing.keys import Signer
from gateway.verifier.factory import build_verifiers

CATALOG_FILE = Path(__file__).resolve().parent.parent / "demo" / "merchant" / "catalog.json"


def merchant_names() -> dict[str, str]:
    if not CATALOG_FILE.exists():
        return {}
    return {k: v["name"] for k, v in json.loads(CATALOG_FILE.read_text()).items()}


def build_gateway(s: Settings, sessions, **overrides) -> Gateway:
    verifier, injection = build_verifiers(s)
    paypal = (
        PayPalClient(s.paypal_base_url, s.paypal_client_id, s.paypal_client_secret)
        if s.paypal_client_id and s.paypal_client_secret else None
    )
    kw = dict(
        sessions=sessions,
        signer=Signer.from_file(s.signing_key_path),
        catalog=HttpCatalog(s.merchant_base_url),
        verifier=verifier,
        injection=injection,
        paypal=paypal,
        public_base_url=s.public_base_url,
        web_base_url=s.web_base_url,
        vault_id=s.paypal_vault_id,
        approval_ttl=timedelta(minutes=s.approval_ttl_minutes),
        merchant_names=merchant_names(),
    )
    kw.update(overrides)
    return Gateway(**kw)


def mcp_transport_security(public_base_url: str) -> TransportSecuritySettings:
    """DNS-rebinding protection: accept local hosts plus the configured public host."""
    hosts = ["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*", "[::1]", "[::1]:*"]
    public = urlsplit(public_base_url).netloc
    if public and public not in hosts:
        hosts.append(public)
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts)


def create_app(s: Settings | None = None, engine=None, **gateway_overrides) -> FastAPI:
    s = s or get_settings()
    engine = engine or make_engine(s.database_url)
    gw = build_gateway(s, make_sessionmaker(engine), **gateway_overrides)
    mcp = create_mcp(gw)
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/", stateless_http=True, json_response=True,
        transport_security=mcp_transport_security(s.public_base_url),
    )

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        await create_schema(engine)
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="Agent Spend Guardrail gateway", lifespan=lifespan)
    app.state.gateway = gw
    app.add_middleware(
        CORSMiddleware, allow_origins=[s.web_base_url], allow_methods=["*"], allow_headers=["*"]
    )
    app.include_router(build_router(gw, s.user_password, make_user_dependency(s.user_password)))
    app.include_router(build_paypal_router(gw))
    app.mount("/mcp", AgentKeyMiddleware(mcp_app, s.agent_api_key))

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def __getattr__(name: str):
    # `uvicorn gateway.main:app` builds the app lazily so importing this module has no side effects.
    if name == "app":
        return create_app()
    raise AttributeError(name)
