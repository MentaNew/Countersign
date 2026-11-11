"""User-side REST API for the web UI: review and approve intents and carts, read the log,
verify receipts. Approval endpoints require the user's login, which the agent never has."""

import secrets
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel

from gateway.api.auth import DEMO_USER
from gateway.logins import COOKIE, LoginSessions
from gateway.paypal_actions import PaypalActions
from gateway.service import Gateway, GatewayError, NotFound


class Login(BaseModel):
    password: str


class ActionDecision(BaseModel):
    approve: bool
    args_hash: str
    webauthn: dict | None = None


class PasskeySignIn(BaseModel):
    nonce: str
    credential: dict


class Decision(BaseModel):
    approve: bool
    webauthn: dict | None = None  # passkey assertion from the browser


class CartDecision(BaseModel):
    approve: bool
    cart_hash: str
    webauthn: dict | None = None


class NewAgent(BaseModel):
    name: str
    spend_cap: Decimal | None = None  # per rolling 30 days; None = only the permissions' limits
    currency: str = "USD"
    kind: str = "mcp"  # "browser": shops on normal websites through the checkout guard
    webauthn: dict | None = None


class Signed(BaseModel):
    webauthn: dict | None = None


class PasskeyRegistration(BaseModel):
    credential: dict
    label: str | None = None


def build_router(gw: Gateway, user_password: str, require_user, logins: LoginSessions,
                 secure_cookie: bool, passkey_registration_open: bool = True) -> APIRouter:
    api = APIRouter(prefix="/api")
    user = Depends(require_user)

    async def start_session(request: Request, response: Response, method: str) -> dict:
        token, row = await logins.create(
            DEMO_USER, method, request.headers.get("user-agent"),
            request.client.host if request.client else None)
        response.set_cookie(COOKIE, token, httponly=True, secure=secure_cookie,
                            samesite="strict", path="/",
                            max_age=int(logins.max_age.total_seconds()))
        # The token is also returned for scripts (Bearer); the web app relies on the cookie.
        return {"user": DEMO_USER, "token": token, "session_id": row.id, "method": method,
                "expires_at": row.expires_at.isoformat()}

    async def run(coro):
        try:
            return await coro
        except GatewayError as e:
            raise HTTPException(404 if isinstance(e, NotFound) else 409, str(e))

    @api.post("/login")
    async def login(body: Login, request: Request, response: Response):
        if not secrets.compare_digest(body.password.encode(), user_password.encode()):
            raise HTTPException(401, "wrong password")
        return await start_session(request, response, "password")

    @api.post("/login/passkey/options")
    async def passkey_signin_options():
        return await run(gw.signin_options(DEMO_USER))

    @api.post("/login/passkey")
    async def passkey_signin(body: PasskeySignIn, request: Request, response: Response):
        try:
            await gw.verify_signin(DEMO_USER, body.nonce, body.credential)
        except GatewayError as e:
            raise HTTPException(401, str(e)) from e
        return await start_session(request, response, "passkey")

    @api.get("/session")
    async def session(request: Request, who: str = user):
        return {"user": who, "session_id": request.state.session_id}

    @api.post("/logout")
    async def logout(request: Request, response: Response, who: str = user):
        await logins.end(request.state.session_id, who)
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @api.get("/sessions")
    async def sessions(request: Request, who: str = user):
        return await logins.list(who, request.state.session_id)

    @api.post("/sessions/{session_id}/end")
    async def end_session(session_id: str, who: str = user):
        if not await logins.end(session_id, who):
            raise HTTPException(404, "no such active session")
        return {"ok": True}

    @api.post("/sessions/end-others")
    async def end_other_sessions(request: Request, who: str = user):
        return {"ended": await logins.end_all(who, keep=request.state.session_id)}

    @api.get("/intents")
    async def intents(_: str = user):
        return await gw.list_intents()

    @api.get("/intents/{intent_id}")
    async def intent(intent_id: str, _: str = user):
        return await run(gw.get_intent(intent_id))

    @api.post("/intents/{intent_id}/decision")
    async def decide_intent(intent_id: str, body: Decision, who: str = user):
        return await run(gw.decide_intent(intent_id, who, body.approve, body.webauthn))

    @api.post("/intents/{intent_id}/revoke")
    async def revoke_intent(intent_id: str, who: str = user):
        return await run(gw.revoke_intent(intent_id, who))

    @api.post("/intents/{intent_id}/approval-options")
    async def intent_approval_options(intent_id: str, who: str = user):
        return await run(gw.intent_approval_options(intent_id, who))

    actions = PaypalActions(gw)

    @api.get("/waiting")
    async def waiting(_: str = user):
        return [*(await gw.waiting()), *(await actions.waiting())]

    @api.get("/paypal-actions")
    async def paypal_actions(_: str = user):
        return await actions.recent()

    @api.get("/paypal-actions/{action_id}")
    async def paypal_action(action_id: str, _: str = user):
        return await run(actions.get(action_id))

    @api.post("/paypal-actions/{action_id}/approval-options")
    async def paypal_action_options(action_id: str, who: str = user):
        return await run(actions.approval_options(action_id, who))

    @api.post("/paypal-actions/{action_id}/decision")
    async def paypal_action_decision(action_id: str, body: ActionDecision, who: str = user):
        return await run(actions.decide(action_id, who, body.approve, body.args_hash, body.webauthn))

    @api.get("/purchases")
    async def purchases(intent_id: str | None = None, _: str = user):
        return await gw.list_purchases(intent_id)

    @api.get("/purchases/{purchase_id}")
    async def purchase(purchase_id: str, _: str = user):
        return await run(gw.get_purchase(purchase_id))

    @api.post("/purchases/{purchase_id}/decision")
    async def decide_purchase(purchase_id: str, body: CartDecision, who: str = user):
        return await run(gw.decide_purchase(purchase_id, who, body.approve, body.cart_hash,
                                            body.webauthn))

    @api.post("/purchases/{purchase_id}/approval-options")
    async def purchase_approval_options(purchase_id: str, who: str = user):
        return await run(gw.purchase_approval_options(purchase_id, who))

    @api.get("/passkeys")
    async def passkeys(who: str = user):
        return await gw.passkey_status(who)

    def registration_open() -> None:
        if not passkey_registration_open:
            raise HTTPException(403, "Adding passkeys is turned off on this shared demo: everyone signs in with "
                                     "the same password, so one visitor's passkey would lock the others out.")

    @api.post("/passkeys/register/options")
    async def passkey_options(who: str = user):
        registration_open()
        return await run(gw.passkey_registration_options(who))

    @api.post("/passkeys/register")
    async def passkey_register(body: PasskeyRegistration, who: str = user):
        registration_open()
        return await run(gw.register_passkey(who, body.credential, body.label))

    @api.get("/agents")
    async def agents(_: str = user):
        return await gw.list_agents()

    @api.post("/agents/options")
    async def agent_options(body: NewAgent, who: str = user):
        return await run(gw.agent_creation_options(who, body.name, body.spend_cap, body.currency,
                                                   body.kind))

    @api.post("/agents")
    async def create_agent(body: NewAgent, who: str = user):
        return await run(gw.create_agent(who, body.name, body.spend_cap, body.currency,
                                         body.webauthn, body.kind))

    @api.post("/agents/{agent_id}/revoke")
    async def revoke_agent(agent_id: str, who: str = user):
        return await run(gw.revoke_agent(agent_id, who))

    @api.post("/agents/stop")
    async def stop_agents(who: str = user):
        return await run(gw.stop_all_agents(who))

    @api.post("/agents/resume/options")
    async def resume_options(who: str = user):
        return await run(gw.resume_options(who))

    @api.post("/agents/resume")
    async def resume_agents(body: Signed, who: str = user):
        return await run(gw.resume_agents(who, body.webauthn))

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

    @api.get("/receipts/{receipt_id}/proof")
    async def receipt_proof(receipt_id: str):
        return await run(gw.receipt_proof(receipt_id))

    @api.get("/anchors")
    async def anchors():
        return await gw.list_anchors()

    @api.get("/anchors/{anchor_id}.tsr")
    async def anchor_token(anchor_id: int):
        token = await run(gw.anchor_token(anchor_id))
        return Response(token, media_type="application/timestamp-reply",
                        headers={"Content-Disposition": f'attachment; filename="anchor-{anchor_id}.tsr"'})

    @api.post("/log/anchor")
    async def anchor_now(_: str = user):
        return await run(gw.anchor_now()) or {"anchored": False, "reason": "nothing new to anchor"}

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


class DemoBuy(BaseModel):
    intent_id: str
    scenario: str


def build_demo_router(demo, require_user) -> APIRouter:
    """Judge mode (DEMO_MODE=true). Without it these routes do not exist (404)."""
    api = APIRouter(prefix="/api/demo")
    user = Depends(require_user)

    async def run(coro):
        try:
            return await coro
        except GatewayError as e:
            raise HTTPException(409, str(e))

    @api.get("")
    async def state(_: str = user):
        return await run(demo.state())

    @api.post("/permission")
    async def propose(_: str = user):
        return await run(demo.propose())

    @api.post("/buy")
    async def buy(body: DemoBuy, _: str = user):
        return await run(demo.buy(body.intent_id, body.scenario))

    return api
