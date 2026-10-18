"""User-side REST API for the web UI: review and approve intents and carts, read the log,
verify receipts. Approval endpoints require the user's login, which the agent never has."""

import secrets

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from gateway.api.auth import DEMO_USER, user_token
from gateway.service import Gateway, GatewayError, NotFound


class Login(BaseModel):
    password: str


class Decision(BaseModel):
    approve: bool


class CartDecision(BaseModel):
    approve: bool
    cart_hash: str


def build_router(gw: Gateway, user_password: str, require_user) -> APIRouter:
    api = APIRouter(prefix="/api")
    user = Depends(require_user)

    async def run(coro):
        try:
            return await coro
        except GatewayError as e:
            raise HTTPException(404 if isinstance(e, NotFound) else 409, str(e))

    @api.post("/login")
    async def login(body: Login):
        if not secrets.compare_digest(body.password, user_password):
            raise HTTPException(401, "wrong password")
        return {"token": user_token(user_password), "user": DEMO_USER}

    @api.get("/intents")
    async def intents(_: str = user):
        return await gw.list_intents()

    @api.get("/intents/{intent_id}")
    async def intent(intent_id: str, _: str = user):
        return await run(gw.get_intent(intent_id))

    @api.post("/intents/{intent_id}/decision")
    async def decide_intent(intent_id: str, body: Decision, who: str = user):
        return await run(gw.decide_intent(intent_id, who, body.approve))

    @api.get("/purchases")
    async def purchases(intent_id: str | None = None, _: str = user):
        return await gw.list_purchases(intent_id)

    @api.get("/purchases/{purchase_id}")
    async def purchase(purchase_id: str, _: str = user):
        return await run(gw.get_purchase(purchase_id))

    @api.post("/purchases/{purchase_id}/decision")
    async def decide_purchase(purchase_id: str, body: CartDecision, who: str = user):
        return await run(gw.decide_purchase(purchase_id, who, body.approve, body.cart_hash))

    @api.get("/log")
    async def log(intent_id: str | None = None, purchase_id: str | None = None, _: str = user):
        return await gw.get_log(intent_id, purchase_id)

    @api.get("/log/verify")
    async def log_verify(_: str = user):
        return await gw.verify_log()

    # Public: anyone holding a receipt id (seller, dispute analyst) can verify it.
    @api.get("/receipts/{receipt_id}")
    async def receipt(receipt_id: str):
        return await run(gw.get_receipt(receipt_id))

    @api.get("/receipts/{receipt_id}/verify")
    async def receipt_verify(receipt_id: str):
        return await gw.verify_receipt(receipt_id)

    @api.get("/public-key")
    async def public_key():
        return {"key_id": gw.signer.key_id, "public_key": gw.signer.public_key_b64,
                "alg": "Ed25519", "canonicalization": "JSON, sorted keys, no whitespace, UTF-8"}

    return api


def build_paypal_router(gw: Gateway) -> APIRouter:
    r = APIRouter(prefix="/paypal")

    @r.get("/return")
    async def paypal_return(token: str):
        # PayPal appends ?token=<order id>&PayerID=... after the buyer approves.
        try:
            view = await gw.on_paypal_return(token)
        except GatewayError as e:
            raise HTTPException(404, str(e))
        return RedirectResponse(f"{gw.web_base_url}/purchases/{view['purchase_id']}")

    @r.get("/cancel")
    async def paypal_cancel(token: str = ""):
        return RedirectResponse(f"{gw.web_base_url}/")

    return r
