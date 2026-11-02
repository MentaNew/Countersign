"""Gateway entry point: MCP for the agent at /mcp, REST for the user at /api, PayPal redirects
at /paypal. Run: uv run uvicorn gateway.main:app --port 8000"""

import asyncio
import contextlib
import json
import logging
from datetime import timedelta
from functools import partial
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
from gateway.hosting import (
    RateLimit,
    SecurityHeaders,
    pem_bytes,
    refuse_placeholders,
    setup_logging,
)
from gateway.ledger.anchor import request_timestamp
from gateway.logins import LoginSessions
from gateway.mcp_tools import create_mcp
from gateway.paypal.client import PayPalClient
from gateway.service import Gateway
from gateway.signing.jws import EcSigner
from gateway.signing.keys import Signer
from gateway.signing.passkeys import Passkeys
from gateway.verifier.factory import build_verifiers

log = logging.getLogger("gateway")
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
        signer=(Signer.from_pem(pem_bytes(s.signing_key_pem)) if s.signing_key_pem
                else Signer.from_file(s.signing_key_path)),
        ap2_signer=(EcSigner.from_pem(pem_bytes(s.ap2_signing_key_pem)) if s.ap2_signing_key_pem
                    else EcSigner.from_file(s.ap2_signing_key_path)),
        catalog=HttpCatalog(s.merchant_base_url),
        verifier=verifier,
        injection=injection,
        paypal=paypal,
        public_base_url=s.public_base_url,
        web_base_url=s.web_base_url,
        vault_id=s.paypal_vault_id,
        approval_ttl=timedelta(minutes=s.approval_ttl_minutes),
        merchant_names=merchant_names(),
        passkeys=Passkeys(s.webauthn_rp_id, s.webauthn_origin or s.web_base_url),
        require_passkey=s.require_passkey,
        timestamper=partial(request_timestamp,
                            tsa_urls=tuple(u.strip() for u in s.anchor_tsa_urls.split(",") if u.strip())),
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
    setup_logging(s.log_format)
    refuse_placeholders(s.public_base_url, agent_api_key=s.agent_api_key,
                        user_password=s.user_password)
    engine = engine or make_engine(s.database_url)
    gw = build_gateway(s, make_sessionmaker(engine), **gateway_overrides)
    mcp = create_mcp(gw)
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/", stateless_http=True, json_response=True,
        transport_security=mcp_transport_security(s.public_base_url),
    )

    async def anchor_loop() -> None:
        while True:
            await asyncio.sleep(s.anchor_interval_seconds)
            try:
                a = await gw.anchor_now()
                if a:
                    log.info("log anchored at seq %s by %s", a["seq"], a["tsa_name"])
            except Exception as e:  # noqa: BLE001 - never let anchoring take the gateway down
                log.warning("anchoring failed: %s", e)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        await create_schema(engine)
        await gw.ensure_default_agent(s.agent_api_key)
        task = asyncio.create_task(anchor_loop()) if s.anchor_interval_seconds > 0 else None
        async with mcp.session_manager.run():
            yield
        if task:
            task.cancel()

    app = FastAPI(title="Agent Spend Guardrail gateway", lifespan=lifespan)
    app.state.gateway = gw
    # Per client per minute, and overall: password guessing, and a runaway agent loop.
    app.add_middleware(RateLimit, rules=[("/api/login", 10, 60, 60.0), ("/mcp", 120, 600, 60.0),
                                         ("/api/", 300, 3000, 60.0)])
    app.add_middleware(SecurityHeaders, hsts=s.public_base_url.startswith("https://"))
    app.add_middleware(
        CORSMiddleware, allow_origins=[s.web_base_url], allow_methods=["*"], allow_headers=["*"]
    )
    logins = LoginSessions(gw.sessions, timedelta(minutes=s.session_idle_minutes),
                           timedelta(hours=s.session_max_hours))
    app.state.logins = logins
    app.include_router(build_router(gw, s.user_password, make_user_dependency(logins), logins,
                                    secure_cookie=s.web_base_url.startswith("https://")))
    app.include_router(build_paypal_router(gw))
    app.mount("/mcp", AgentKeyMiddleware(mcp_app, gw.authenticate_agent))

    @app.get("/.well-known/jwks.json")
    def jwks() -> dict:
        """Public keys that sign this gateway's AP2 mandates."""
        return gw.jwks()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def __getattr__(name: str):
    # `uvicorn gateway.main:app` builds the app lazily so importing this module has no side effects.
    if name == "app":
        return create_app()
    raise AttributeError(name)
