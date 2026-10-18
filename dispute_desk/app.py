"""Seller-side dispute desk server (separate from the buyer's gateway).

    uv run uvicorn dispute_desk.app:app --port 8200

Doors: /desk/api (seller staff, own login), /webhooks/paypal (PayPal servers)."""

import contextlib
import hashlib
import hmac
import secrets

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dispute_desk.config import DeskSettings, get_desk_settings
from dispute_desk.models import create_desk_schema
from dispute_desk.paypal import DisputesClient
from dispute_desk.receipts import GatewayReceipts
from dispute_desk.service import Desk, DeskError, NotFound

SELLER = "seller-staff"
_bearer = HTTPBearer(auto_error=False)


def seller_token(password: str) -> str:
    return hmac.new(password.encode(), b"dispute-desk-seller-session", hashlib.sha256).hexdigest()


class Login(BaseModel):
    password: str


class ImportBody(BaseModel):
    dispute_id: str


class Fulfillment(BaseModel):
    carrier: str = ""
    tracking_number: str = ""


class Submit(BaseModel):
    action: str
    note: str
    offer_amount: str | None = None


def build_desk(s: DeskSettings, sessions, **overrides) -> Desk:
    paypal = (DisputesClient(s.paypal_base_url, s.paypal_client_id, s.paypal_client_secret)
              if s.paypal_client_id and s.paypal_client_secret else None)
    kw = {"sessions": sessions, "paypal": paypal,
          "receipts": GatewayReceipts(s.gateway_public_url, s.gateway_public_key),
          "webhook_id": s.paypal_webhook_id,
          "allow_unverified_webhooks": s.desk_allow_unverified_webhooks}
    kw.update(overrides)
    return Desk(**kw)


def create_desk_app(s: DeskSettings | None = None, engine=None, **overrides) -> FastAPI:
    s = s or get_desk_settings()
    engine = engine or create_async_engine(s.database_url, pool_pre_ping=True)
    desk = build_desk(s, async_sessionmaker(engine, expire_on_commit=False), **overrides)
    expected = seller_token(s.seller_password)

    def require_seller(cred: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
        if not cred or not secrets.compare_digest(cred.credentials, expected):
            raise HTTPException(401, "seller login required")
        return SELLER

    seller = Depends(require_seller)

    async def run(coro):
        try:
            return await coro
        except NotFound as e:
            raise HTTPException(404, str(e))
        except DeskError as e:
            raise HTTPException(409, str(e))

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        await create_desk_schema(engine)
        yield

    app = FastAPI(title="Dispute desk (seller side)", lifespan=lifespan)
    app.state.desk = desk

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/desk/api/login")
    async def login(body: Login):
        if not secrets.compare_digest(body.password, s.seller_password):
            raise HTTPException(401, "wrong password")
        return {"token": expected, "user": SELLER}

    @app.get("/desk/api/disputes")
    async def disputes(_: str = seller):
        return await desk.list()

    @app.post("/desk/api/disputes/import")
    async def import_dispute(body: ImportBody, _: str = seller):
        return await run(desk.import_dispute(body.dispute_id.strip()))

    @app.get("/desk/api/disputes/{dispute_id}")
    async def dispute(dispute_id: str, _: str = seller):
        return await run(desk.get(dispute_id))

    @app.post("/desk/api/disputes/{dispute_id}/refresh")
    async def refresh(dispute_id: str, _: str = seller):
        return await run(desk.refresh(dispute_id))

    @app.post("/desk/api/disputes/{dispute_id}/fulfillment")
    async def fulfillment(dispute_id: str, body: Fulfillment, _: str = seller):
        data = body.model_dump() if body.tracking_number.strip() else None
        return await run(desk.refresh(dispute_id, fulfillment=data or {}))

    @app.post("/desk/api/disputes/{dispute_id}/submit")
    async def submit(dispute_id: str, body: Submit, who: str = seller):
        return await run(desk.submit(dispute_id, who, body.action, body.note, body.offer_amount))

    # PayPal servers call this; authenticated by PayPal's webhook signature, not by a login.
    @app.post("/webhooks/paypal")
    async def webhook(request: Request):
        event = await request.json()
        try:
            return await desk.handle_webhook(dict(request.headers), event)
        except DeskError as e:
            raise HTTPException(400, str(e))

    return app


def __getattr__(name: str):
    if name == "app":
        return create_desk_app()
    raise AttributeError(name)
