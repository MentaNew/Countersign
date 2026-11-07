"""The gateway's core flow. MCP tools (agent side) and the REST API (user side) are thin
wrappers around this class; all enforcement happens here."""

import asyncio
import base64
import json
import re
import secrets
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.ap2 import build_bundle as build_ap2_bundle
from gateway.ap2 import verify_bundle as verify_ap2_bundle
from gateway.canonical import canonical_json, sha256_hex
from gateway.catalog.client import Catalog, UnknownMerchant, lines_from_offer
from gateway.catalog.offers import merchant_signature_check, same_key, verify_offer
from gateway.db.models import (
    Agent,
    Anchor,
    Control,
    Intent,
    LogEntry,
    MerchantKey,
    Passkey,
    Purchase,
    Receipt,
)
from gateway.domain import CartLineRequest, CheckResult, Mandate, PageCart, ResolvedLine
from gateway.ledger import chain
from gateway.ledger.anchor import request_timestamp, verify_token
from gateway.ledger.proof import verify_receipt_proof
from gateway.paypal.client import PayPal, PayPalError, payer_action_url
from gateway.policy.decision import Thresholds
from gateway.policy.evaluate import evaluate_cart
from gateway.policy.hard_checks import SpendState, cart_total, run_hard_checks
from gateway.policy.units import line_units
from gateway.signing.jws import EcSigner
from gateway.signing.keys import Signer
from gateway.signing.passkeys import (
    PasskeyError,
    Passkeys,
    new_statement,
    verify_user_signature,
)
from gateway.verifier.base import InjectionDetector, Verifier
from gateway.verifier.groq import recorder as model_recorder

SPENDING_STATUSES = ("authorized", "ordered", "captured", "payment_reported")


class GatewayError(Exception):
    """An error the caller should see verbatim (wrong state, tampered record)."""


class NotFound(GatewayError):
    pass


class AgentDenied(GatewayError):
    """The agent's key is unknown or revoked, or all agents are stopped."""


AGENT_CAP_WINDOW = timedelta(days=30)
PERMIT_TTL = timedelta(minutes=20)
UNSIGNED_REASON = ("cart read from the shop's page, not signed by the shop: "
                   "always needs your passkey")  # time to finish paying at the shop after approval
PAUSE_KEY = "agents_paused"


def agent_key_hash(key: str) -> str:
    return sha256_hex(key.encode())


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def now() -> datetime:
    return datetime.now(UTC)


async def recorded(coro) -> tuple[Any, list[dict[str, Any]]]:
    """Run a model-backed check while keeping every exchange with the model (exact request and
    raw response), so a decision can be examined later exactly as the model gave it."""
    exchanges: list[dict[str, Any]] = []
    token = model_recorder.set(exchanges)
    try:
        return await coro, exchanges
    finally:
        model_recorder.reset(token)


def shop_host(url: str) -> str:
    """Merchant id for a normal web shop: its host name, without "www."."""
    host = (urlsplit(url if "://" in url else f"https://{url}").hostname or "").lower()
    return host.removeprefix("www.")


def page_cart_checks(cart: PageCart, merchant_id: str, total: Decimal) -> list[CheckResult]:
    """Sanity checks on what the guard read: the money adds up, one currency, the checkout page
    is on the shop's own site."""
    cur = cart.total.currency
    currencies = {ln.unit_price.currency for ln in cart.lines} | {
        m.currency for m in (cart.shipping, cart.tax, cart.discount) if m}
    page = shop_host(cart.page_url)
    same_site = bool(merchant_id) and (page == merchant_id or page.endswith("." + merchant_id))
    adds_up = abs(total - cart.total.amount) <= Decimal("0.01")
    return [
        CheckResult(name="cart_source", passed=True,
                    detail=f"read from the shop's page by the checkout guard ({cart.reader}); "
                           "not signed by the shop"),
        CheckResult(name="page_cart_adds_up", passed=adds_up and currencies <= {cur},
                    detail=f"lines + shipping + tax - discount = {total:.2f}; page total "
                           f"{cart.total}" + ("" if currencies <= {cur} else
                                              f"; mixed currencies {sorted(currencies)}")),
        CheckResult(name="checkout_on_shop_site", passed=same_site,
                    detail=f"checkout page on {page or '?'}, shop {merchant_id or '?'}"),
    ]


def compute_cart_hash(intent_id: str, merchant_id: str, lines: list[ResolvedLine],
                      offer_id: str | None = None) -> str:
    body = {
        "intent_id": intent_id,
        "merchant_id": merchant_id,
        "lines": [ln.model_dump(mode="json") for ln in lines],
    }
    if offer_id:  # binds the user's approval to the merchant's signed offer too
        body["merchant_offer_id"] = offer_id
    return sha256_hex(canonical_json(body))


class Gateway:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        signer: Signer,
        catalog: Catalog,
        verifier: Verifier,
        injection: InjectionDetector,
        paypal: PayPal | None,
        *,
        public_base_url: str,
        web_base_url: str,
        vault_id: str = "",
        approval_ttl: timedelta = timedelta(minutes=30),
        thresholds: Thresholds = Thresholds(),
        merchant_names: dict[str, str] | None = None,
        passkeys: Passkeys | None = None,
        require_passkey: str = "auto",
        timestamper=None,
        token_verifier=None,
        ap2_signer: EcSigner | None = None,
    ):
        self.sessions = sessions
        self.signer = signer
        self.catalog = catalog
        self.verifier = verifier
        self.injection = injection
        self.paypal = paypal
        self.public_base_url = public_base_url.rstrip("/")
        self.web_base_url = web_base_url.rstrip("/")
        self.vault_id = vault_id
        self.approval_ttl = approval_ttl
        self.thresholds = thresholds
        self.merchant_names = merchant_names or {}
        self.passkeys = passkeys
        # "auto": required once the user has registered a passkey; "always"; "never" (scripts).
        self.require_passkey = require_passkey
        # External anchoring (RFC 3161). Injectable for tests.
        self.timestamper = timestamper or request_timestamp
        self.token_verifier = token_verifier or verify_token
        # ES256 key for AP2 v0.2 mandates (the gateway acting as the user's Trusted Surface).
        self.ap2_signer = ap2_signer or EcSigner.ephemeral()

    # ------------------------------------------------------------------ intents

    def _intent_signed_body(self, it: Intent) -> dict[str, Any]:
        return {
            "intent_id": it.id,
            "user_text": it.user_text,
            "mandate": it.mandate,
            "approved_by": it.approved_by,
            "approved_at": it.approved_at.astimezone(UTC).isoformat() if it.approved_at else None,
            "key_id": it.key_id,
            # Only present when the user signed with a passkey (older intents verify unchanged).
            **({"user_signature_hash": sha256_hex(canonical_json(it.user_signature))}
               if it.user_signature else {}),
        }

    async def confirm_intent(self, user_text: str, mandate: Mandate,
                             agent_id: str | None = None) -> dict[str, Any]:
        intent_id = new_id("int")
        async with self.sessions.begin() as s:
            it = Intent(id=intent_id, user_text=user_text, mandate=mandate.model_dump(mode="json"),
                        agent_id=agent_id)
            s.add(it)
            await s.flush()
            await chain.append(
                s, "intent_proposed",
                {"user_text": user_text, "mandate": it.mandate, "agent_id": agent_id},
                intent_id=intent_id,
            )
        return {
            "intent_id": intent_id,
            "status": "pending_user",
            "summary": mandate.summary(),
            "review_url": f"{self.web_base_url}/intents/{intent_id}",
            "next": "Ask the user to review and approve this intent at review_url, then call "
            "get_intent_status. Do not shop with payment until it is active.",
        }

    async def decide_intent(self, intent_id: str, user: str, approve: bool,
                            credential: dict[str, Any] | None = None) -> dict[str, Any]:
        async with self.sessions.begin() as s:
            it = await self._intent(s, intent_id, for_update=True)
            if it.status != "pending_user":
                raise GatewayError(f"intent is {it.status}, not pending_user")
            if approve:
                stopped = await self._agent_stopped(s, it.agent_id)
                if stopped:
                    raise GatewayError(f"cannot approve: {stopped}")
                record = await self._user_signature(s, user, f"intent:{it.id}", credential,
                                                    expected={"intent_id": it.id})
                it.status = "active"
                it.approved_by = user
                it.approved_at = now()
                it.key_id = self.signer.key_id
                it.user_signature = record
                it.signature = self.signer.sign(self._intent_signed_body(it))
                await chain.append(
                    s, "intent_approved",
                    {"approved_by": user, "signature": it.signature, "key_id": it.key_id,
                     "user_signed": record is not None,
                     "user_signature_hash": sha256_hex(canonical_json(record)) if record else None},
                    intent_id=it.id,
                )
            else:
                it.status = "rejected"
                await chain.append(s, "intent_rejected", {"by": user}, intent_id=it.id)
            return self._intent_view(it)

    async def intent_approval_options(self, intent_id: str, user: str) -> dict[str, Any]:
        """Start a passkey approval: the statement the user's device will sign."""
        async with self.sessions() as s:
            it = await self._intent(s, intent_id)
            if it.status != "pending_user":
                raise GatewayError(f"intent is {it.status}, not pending_user")
            mandate = Mandate.model_validate(it.mandate)
            statement = new_statement(
                "intent_mandate_approval", intent_id=it.id, user_text=it.user_text,
                summary=mandate.summary(), mandate_hash=sha256_hex(canonical_json(it.mandate)),
            )
            return await self._approval_options(s, user, f"intent:{it.id}", statement)

    # ---------------------------------------------------------------- passkeys

    async def _credentials(self, s: AsyncSession, user: str) -> list[Passkey]:
        return list((await s.execute(select(Passkey).where(Passkey.user == user))).scalars())

    async def passkey_status(self, user: str) -> dict[str, Any]:
        async with self.sessions() as s:
            creds = await self._credentials(s, user)
        return {
            "enabled": self.passkeys is not None,
            "required": self._required(len(creds)),
            "mode": self.require_passkey,
            "passkeys": [{"credential_id": c.credential_id[:12] + "…", "label": c.label,
                          "created_at": c.created_at.isoformat()} for c in creds],
        }

    def _required(self, n_credentials: int) -> bool:
        if self.passkeys is None or self.require_passkey == "never":
            return False
        return self.require_passkey == "always" or n_credentials > 0

    async def passkey_registration_options(self, user: str) -> dict[str, Any]:
        if not self.passkeys:
            raise GatewayError("passkeys are not configured")
        async with self.sessions() as s:
            existing = [c.credential_id for c in await self._credentials(s, user)]
        return self.passkeys.registration_options(user, existing)

    async def register_passkey(self, user: str, credential: dict[str, Any],
                               label: str | None = None) -> dict[str, Any]:
        if not self.passkeys:
            raise GatewayError("passkeys are not configured")
        try:
            reg = self.passkeys.verify_registration(user, credential)
        except PasskeyError as e:
            raise GatewayError(str(e)) from e
        async with self.sessions.begin() as s:
            s.add(Passkey(credential_id=reg["credential_id"], user=user,
                          public_key=reg["public_key"], sign_count=reg["sign_count"],
                          aaguid=reg["aaguid"], label=label or "passkey"))
            await chain.append(s, "passkey_registered",
                               {"user": user, "credential_id": reg["credential_id"],
                                "public_key": reg["public_key"]})
        return await self.passkey_status(user)

    async def _approval_options(self, s: AsyncSession, user: str, subject: str,
                                statement: dict[str, Any], force: bool = False) -> dict[str, Any]:
        creds = await self._credentials(s, user)
        if not self.passkeys or not creds:
            if force:
                raise GatewayError("this approval needs a passkey: add one on the Security page")
            return {"passkey": False, "required": self._required(len(creds))}
        try:
            out = self.passkeys.approval_options(subject, statement,
                                                 [c.credential_id for c in creds])
        except PasskeyError as e:
            raise GatewayError(str(e)) from e
        return {"passkey": True, "required": True, **out}

    async def _user_signature(self, s: AsyncSession, user: str, subject: str,
                              credential: dict[str, Any] | None,
                              expected: dict[str, Any], force: bool = False,
                              ) -> dict[str, Any] | None:
        """Verify the user's passkey signature for an approval, or refuse if one is required."""
        creds = await self._credentials(s, user)
        if credential is None:
            if force and not (self.passkeys and creds):
                raise GatewayError("this approval needs a passkey: add one on the Security page")
            if force or self._required(len(creds)):
                raise GatewayError("this approval must be signed with your passkey")
            return None
        if not self.passkeys:
            raise GatewayError("passkeys are not configured")
        stored = next((c for c in creds if c.credential_id == credential.get("id")), None)
        if not stored:
            raise GatewayError("unknown passkey")
        try:
            record, count = self.passkeys.verify_approval(
                subject, credential,
                {"credential_id": stored.credential_id, "public_key": stored.public_key,
                 "sign_count": stored.sign_count})
        except PasskeyError as e:
            raise GatewayError(str(e)) from e
        ok, reason = verify_user_signature(record, expected)
        if not ok:
            raise GatewayError(f"passkey signature rejected: {reason}")
        stored.sign_count = count
        return record

    async def signin_options(self, user: str) -> dict[str, Any]:
        """Passwordless sign-in: the device signs a one-time sign-in statement."""
        nonce = secrets.token_urlsafe(16)
        async with self.sessions() as s:
            out = await self._approval_options(
                s, user, f"signin:{nonce}", new_statement("sign_in", user=user, signin_id=nonce),
                force=True)
        return {**out, "nonce": nonce}

    async def verify_signin(self, user: str, nonce: str, credential: dict[str, Any]) -> None:
        async with self.sessions.begin() as s:
            await self._user_signature(s, user, f"signin:{nonce}", credential,
                                       expected={"signin_id": nonce}, force=True)

    async def get_intent(self, intent_id: str) -> dict[str, Any]:
        async with self.sessions() as s:
            return self._intent_view(await self._intent(s, intent_id))

    async def wait_intent(self, intent_id: str, wait_seconds: float = 0,
                          agent_id: str | None = None) -> dict[str, Any]:
        await self._check_owner(Intent, intent_id, agent_id)
        return await self._wait(self.get_intent, intent_id, wait_seconds, "pending_user")

    async def list_intents(self, limit: int = 50) -> list[dict[str, Any]]:
        async with self.sessions() as s:
            rows = (
                await s.execute(select(Intent).order_by(Intent.created_at.desc()).limit(limit))
            ).scalars()
            return [self._intent_view(r) for r in rows]

    def _intent_view(self, it: Intent) -> dict[str, Any]:
        mandate = Mandate.model_validate(it.mandate)
        return {
            "intent_id": it.id,
            "status": it.status,
            "user_text": it.user_text,
            "mandate": it.mandate,
            "summary": mandate.summary(),
            "created_at": it.created_at.isoformat(),
            "approved_by": it.approved_by,
            "approved_at": it.approved_at.isoformat() if it.approved_at else None,
            "signature": it.signature,
            "user_signed": it.user_signature is not None,
            "agent_id": it.agent_id,
        }

    async def _intent(self, s: AsyncSession, intent_id: str, for_update: bool = False) -> Intent:
        q = select(Intent).where(Intent.id == intent_id)
        if for_update:
            q = q.with_for_update()
        it = (await s.execute(q)).scalar_one_or_none()
        if not it:
            raise NotFound(f"unknown intent {intent_id}")
        return it

    async def revoke_intent(self, intent_id: str, user: str) -> dict[str, Any]:
        """Cancel a permission. Never needs a passkey: taking power away is always allowed.
        Carts still waiting under it are cancelled; nothing new can be bought with it."""
        async with self.sessions.begin() as s:
            it = await self._intent(s, intent_id, for_update=True)
            if it.status not in ("active", "pending_user"):
                raise GatewayError(f"intent is {it.status}")
            it.status = "revoked"
            held = (await s.execute(select(Purchase).where(
                Purchase.intent_id == intent_id, Purchase.status == "pending_approval")
                .with_for_update())).scalars().all()
            for p in held:
                p.status = "cancelled"
            await chain.append(s, "intent_revoked",
                               {"by": user, "cancelled_carts": [p.id for p in held]},
                               intent_id=intent_id)
        return await self.get_intent(intent_id)

    # ---------------------------------------------------------------- purchases

    async def _spend_state(self, s: AsyncSession, intent_id: str, exclude: str | None = None):
        rows = (
            await s.execute(
                select(Purchase).where(
                    Purchase.intent_id == intent_id, Purchase.status.in_(SPENDING_STATUSES)
                )
            )
        ).scalars()
        it = await s.get(Intent, intent_id)
        items = (it.mandate or {}).get("items", []) if it else []
        spent, n = Decimal(0), 0
        units: dict[int, Fraction] = defaultdict(Fraction)
        packs: dict[int, int] = defaultdict(int)
        for p in rows:
            if p.id == exclude:
                continue
            spent += p.total
            n += 1
            for ln in p.lines:
                idx = ln.get("item")
                if idx is not None:
                    desc = items[idx]["description"] if 0 <= idx < len(items) else ""
                    units[idx] += line_units(desc, ln.get("title", ""), ln["qty"]).requested
                    packs[idx] += ln["qty"]
        return SpendState(spent=spent, purchases=n, qty_by_item=dict(units),
                          packs_by_item=dict(packs))

    async def request_purchase(
        self,
        intent_id: str,
        merchant_id: str,
        lines: list[CartLineRequest],
        context_texts: list[str] | None = None,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        context_texts = [t for t in (context_texts or []) if t and t.strip()]
        async with self.sessions() as s:
            it = await self._intent(s, intent_id)
            if it.status != "active":
                raise GatewayError(
                    f"intent {intent_id} is {it.status}; the user must approve it first"
                )
            if not it.signature or not self.signer.verify(self._intent_signed_body(it), it.signature):
                raise GatewayError(f"intent {intent_id} failed signature verification")
            mandate = Mandate.model_validate(it.mandate)
            state = await self._spend_state(s, intent_id)
            owner = it.agent_id

        # The merchant prices and signs the cart; we check its signature against its pinned key.
        signed_offer, offer_check, resolved, unknown = await self._merchant_offer(
            merchant_id, lines)
        known = CheckResult(
            name="known_products",
            passed=not unknown,
            detail=f"not in {merchant_id}'s catalog: {unknown}" if unknown else "",
        )
        async with self.sessions() as s:
            agent_checks = await self._agent_checks(
                s, agent_id, owner, cart_total(resolved), mandate.max_total.currency)
        verdict, exchanges = await recorded(evaluate_cart(
            mandate, merchant_id, resolved, state, now(), self.verifier, self.injection,
            context_texts, self.thresholds, pre_checks=[*agent_checks, offer_check, known],
        ))
        total = cart_total(resolved)

        purchase_id = new_id("pur")
        cart_hash = compute_cart_hash(intent_id, merchant_id, resolved,
                                      (signed_offer or {}).get("offer", {}).get("offer_id"))
        status = {"block": "blocked", "escalate": "pending_approval", "approve": "authorized"}[
            verdict.decision
        ]
        async with self.sessions.begin() as s:
            p = Purchase(
                id=purchase_id,
                intent_id=intent_id,
                agent_id=agent_id or owner,
                merchant_id=merchant_id,
                lines=[ln.model_dump(mode="json") for ln in resolved],
                merchant_offer=signed_offer,
                total=total,
                currency=mandate.max_total.currency,
                cart_hash=cart_hash,
                context_texts=context_texts,
                verdict=verdict.model_dump(mode="json"),
                model_exchanges=exchanges or None,
                decision=verdict.decision,
                status=status,
                approval_expires_at=now() + self.approval_ttl
                if verdict.decision == "escalate"
                else None,
            )
            s.add(p)
            await s.flush()
            await chain.append(
                s, "purchase_decision",
                {
                    "agent_id": p.agent_id,
                    "merchant_id": merchant_id,
                    "cart_hash": cart_hash,
                    "lines": p.lines,
                    "total": f"{total:.2f}",
                    "currency": p.currency,
                    "context_text_hashes": [sha256_hex(t.encode()) for t in context_texts],
                    "verdict": p.verdict,
                    "model_exchanges_sha256": (sha256_hex(canonical_json(p.model_exchanges))
                                               if p.model_exchanges else None),
                },
                intent_id=intent_id, purchase_id=purchase_id,
            )

        if verdict.decision == "approve":
            await self._authorize(purchase_id, approver="policy", approval=None)
        return await self.get_purchase(purchase_id, for_agent=True)

    async def request_page_purchase(self, intent_id: str, cart: PageCart,
                                    agent_id: str | None = None,
                                    context_texts: list[str] | None = None) -> dict[str, Any]:
        """A cart the checkout guard read from a normal shop's checkout (no shop signature).
        Same policy and semantic checks as any cart, shipping and tax included in the budget,
        and it is always held for the user's passkey, whatever the amount."""
        context_texts = [t for t in (context_texts or []) if t and t.strip()]
        async with self.sessions() as s:
            if agent_id:
                row = await s.get(Agent, agent_id)
                if row is None or row.kind != "browser":
                    raise GatewayError("only the checkout guard of a browser agent can submit "
                                       "carts read from a shop's page")
            it = await self._intent(s, intent_id)
            if it.status != "active":
                raise GatewayError(
                    f"intent {intent_id} is {it.status}; the user must approve it first")
            if not it.signature or not self.signer.verify(self._intent_signed_body(it),
                                                          it.signature):
                raise GatewayError(f"intent {intent_id} failed signature verification")
            mandate = Mandate.model_validate(it.mandate)
            state = await self._spend_state(s, intent_id)
            owner = it.agent_id

        merchant_id = shop_host(cart.shop_url)
        resolved = [ResolvedLine(sku=ln.product_url or f"line-{i + 1}", title=ln.title
                                 + (f" ({ln.variant})" if ln.variant else ""),
                                 unit_price=ln.unit_price, qty=ln.qty, item=ln.item)
                    for i, ln in enumerate(cart.lines)]
        charges = cart.charges()
        total = cart_total(resolved) + charges
        page_checks = page_cart_checks(cart, merchant_id, total)
        async with self.sessions() as s:
            agent_checks = await self._agent_checks(
                s, agent_id, owner, total, mandate.max_total.currency)
        verdict, exchanges = await recorded(evaluate_cart(
            mandate, merchant_id, resolved, state, now(), self.verifier, self.injection,
            context_texts, self.thresholds, pre_checks=[*agent_checks, *page_checks],
            charges=charges,
        ))
        if verdict.decision != "block":  # unsigned carts are never approved by policy alone
            reasons = [r for r in verdict.reasons if r != "all checks passed"]
            verdict = verdict.model_copy(update={"decision": "escalate",
                                                 "reasons": [UNSIGNED_REASON, *reasons]})

        screenshot = cart.screenshot_png_b64
        evidence = cart.model_dump(mode="json", exclude={"screenshot_png_b64"})
        evidence["screenshot_sha256"] = (
            sha256_hex(base64.b64decode(screenshot)) if screenshot else None)
        purchase_id = new_id("pur")
        cart_hash = sha256_hex(canonical_json({
            "intent_id": intent_id, "merchant_id": merchant_id, "source": "page",
            "lines": [ln.model_dump(mode="json") for ln in resolved], "page_cart": evidence}))
        status = "blocked" if verdict.decision == "block" else "pending_approval"
        async with self.sessions.begin() as s:
            p = Purchase(
                id=purchase_id, intent_id=intent_id, agent_id=agent_id or owner,
                merchant_id=merchant_id, lines=[ln.model_dump(mode="json") for ln in resolved],
                cart_source="page", page_cart=evidence, page_screenshot=screenshot,
                total=total, currency=mandate.max_total.currency, cart_hash=cart_hash,
                context_texts=context_texts, verdict=verdict.model_dump(mode="json"),
                model_exchanges=exchanges or None, decision=verdict.decision, status=status,
                approval_expires_at=now() + self.approval_ttl if status == "pending_approval"
                else None,
            )
            s.add(p)
            await s.flush()
            await chain.append(
                s, "purchase_decision",
                {"agent_id": p.agent_id, "merchant_id": merchant_id, "cart_source": "page",
                 "cart_hash": cart_hash, "lines": p.lines, "total": f"{total:.2f}",
                 "currency": p.currency, "page_cart": evidence,
                 "context_text_hashes": [sha256_hex(t.encode()) for t in context_texts],
                 "verdict": p.verdict,
                 "model_exchanges_sha256": (sha256_hex(canonical_json(p.model_exchanges))
                                            if p.model_exchanges else None)},
                intent_id=intent_id, purchase_id=purchase_id,
            )
        return await self.get_purchase(purchase_id, for_agent=True)

    async def confirm_page_payment(self, purchase_id: str, paypal_order_id: str,
                                   page_url: str, agent_id: str | None = None,
                                   page_text_sha256: str | None = None) -> dict[str, Any]:
        """After a guarded payment: the guard reports the PayPal order PayPal sent the person
        back with. The gateway checks it with PayPal when its PayPal app can see the order
        (amount, currency, completed), then signs and logs a payment confirmation."""
        async with self.sessions() as s:
            p = await self._purchase(s, purchase_id)
            if p.cart_source != "page" or not p.checkout_permit:
                raise GatewayError("only approved carts read from a shop's page take a payment "
                                   "confirmation")
            if agent_id and p.agent_id != agent_id:
                raise GatewayError("this purchase belongs to a different agent")
            if p.payment_confirmation:
                return await self.get_purchase(purchase_id, for_agent=agent_id is not None)
            permit = p.checkout_permit["permit"]
        if shop_host(page_url) != permit["shop"] and not shop_host(page_url).endswith(
                "." + permit["shop"]):
            raise GatewayError(f"the confirmation page is not on {permit['shop']}")
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{7,40}", paypal_order_id):  # e.g. 5O19..., EC-8X...
            raise GatewayError("that does not look like a PayPal order id")

        check: dict[str, Any] = {"checked_with_paypal": False}
        try:
            order = await self.paypal.get_order(paypal_order_id)
            amount = (order.get("purchase_units") or [{}])[0].get("amount") or {}
            captures = [c for pu in order.get("purchase_units", [])
                        for c in (pu.get("payments") or {}).get("captures", [])]
            check = {"checked_with_paypal": True, "status": order.get("status"),
                     "amount": amount.get("value"), "currency": amount.get("currency_code"),
                     "capture_id": captures[0]["id"] if captures else None}
        except Exception as e:  # noqa: BLE001 - the shop's order may be invisible to our app
            check["detail"] = f"PayPal lookup not possible from the gateway's app ({e})"[:200]
        if check["checked_with_paypal"]:
            if (check["amount"] is not None and
                    (Decimal(str(check["amount"])) != Decimal(permit["total"])
                     or check["currency"] != permit["currency"])):
                raise GatewayError(f"PayPal order {paypal_order_id} is for {check['amount']} "
                                   f"{check['currency']}, not the approved {permit['total']} "
                                   f"{permit['currency']}")
            if check["status"] != "COMPLETED":
                raise GatewayError(f"PayPal order {paypal_order_id} is {check['status']}, "
                                   "not completed yet")

        body = {"purchase_id": purchase_id, "receipt_id": p.receipt_id,
                "paypal_order_id": paypal_order_id, "page_url": page_url,
                "page_text_sha256": page_text_sha256, "permit_total": permit["total"],
                "currency": permit["currency"], **check, "confirmed_at": now().isoformat(),
                "key_id": self.signer.key_id}
        record = {"confirmation": body, "signature": self.signer.sign(body)}
        async with self.sessions.begin() as s:
            p = await self._purchase(s, purchase_id, for_update=True)
            p.payment_confirmation = record
            p.paypal_order_id = paypal_order_id
            p.paypal_status = check.get("status") or "REPORTED"
            p.status = "captured" if check.get("status") == "COMPLETED" else "payment_reported"
            await chain.append(s, "payment_confirmed",
                               {"confirmation_hash": sha256_hex(canonical_json(record)),
                                "paypal_order_id": paypal_order_id,
                                "checked_with_paypal": check["checked_with_paypal"]},
                               intent_id=p.intent_id, purchase_id=p.id)
        return await self.get_purchase(purchase_id, for_agent=agent_id is not None)

    def _page_evidence(self, p: Purchase) -> dict[str, Any]:
        return {**(p.page_cart or {}),
                "note": "read from the shop's own checkout by the checkout guard; "
                        "not signed by the shop"}

    async def decide_purchase(
        self, purchase_id: str, user: str, approve: bool, cart_hash: str,
        credential: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Human decision on an escalated cart. `cart_hash` must be the hash the user was shown,
        so an approval can never be applied to a different cart."""
        async with self.sessions.begin() as s:
            p = await self._purchase(s, purchase_id, for_update=True)
            if p.status != "pending_approval":
                raise GatewayError(f"purchase is {p.status}, not pending_approval")
            if cart_hash != p.cart_hash:
                raise GatewayError("cart hash mismatch: the cart changed since it was displayed")
            if p.approval_expires_at and now() > p.approval_expires_at:
                p.status = "approval_expired"
                await chain.append(s, "approval_expired", {}, intent_id=p.intent_id,
                                   purchase_id=p.id)
                return self._purchase_view(p)
            if not approve:
                p.status = "rejected"
                await chain.append(s, "purchase_rejected_by_human", {"by": user},
                                   intent_id=p.intent_id, purchase_id=p.id)
                return self._purchase_view(p)

            # Limits may have moved since the decision (another cart was paid): re-check.
            it = await self._intent(s, p.intent_id)
            mandate = Mandate.model_validate(it.mandate)
            state = await self._spend_state(s, p.intent_id, exclude=p.id)
            lines = [ResolvedLine.model_validate(x) for x in p.lines]
            charges = (PageCart.model_validate(p.page_cart).charges()
                       if p.cart_source == "page" else Decimal(0))
            recheck = run_hard_checks(mandate, p.merchant_id, lines, state, now(), charges)
            recheck += await self._agent_checks(s, p.agent_id, it.agent_id, p.total, p.currency,
                                                exclude=p.id)
            if p.merchant_offer:  # the merchant's prices must still be on offer
                expires = datetime.fromisoformat(p.merchant_offer["offer"]["expires_at"])
                recheck.append(CheckResult(
                    name="merchant_offer_current", passed=now() < expires,
                    detail="" if now() < expires else
                    "the merchant's signed offer expired; ask the agent to request the cart again"))
            failed = [c for c in recheck if not c.passed]
            if failed:
                p.status = "blocked"
                p.error = "; ".join(f"{c.name}: {c.detail}" for c in failed)
                await chain.append(s, "purchase_blocked_on_recheck",
                                   {"failed": [c.model_dump() for c in failed]},
                                   intent_id=p.intent_id, purchase_id=p.id)
                return self._purchase_view(p)

            record = await self._user_signature(
                s, user, f"purchase:{p.id}", credential,
                expected={"purchase_id": p.id, "cart_hash": p.cart_hash},
                force=p.cart_source == "page")
            token_body = {
                "purchase_id": p.id,
                "intent_id": p.intent_id,
                "cart_hash": p.cart_hash,
                "approved_by": user,
                "approved_at": now().isoformat(),
                "expires_at": p.approval_expires_at.astimezone(UTC).isoformat()
                if p.approval_expires_at else None,
                "key_id": self.signer.key_id,
                **({"user_signature_hash": sha256_hex(canonical_json(record))} if record else {}),
            }
            approval = {"token": token_body, "signature": self.signer.sign(token_body),
                        "user_signature": record}
            p.approval = approval
            await chain.append(
                s, "purchase_approved_by_human",
                {"approved_by": user, "token_hash": sha256_hex(canonical_json(approval)),
                 "user_signed": record is not None},
                intent_id=p.intent_id, purchase_id=p.id,
            )
        await self._authorize(purchase_id, approver=user, approval=approval)
        return await self.get_purchase(purchase_id)

    async def _pinned_key(self, merchant_id: str) -> tuple[dict[str, Any], str]:
        """The merchant's key (a JWK): pinned on first sight; a different key later is refused."""
        async with self.sessions() as s:
            pinned = await s.get(MerchantKey, merchant_id)
            if pinned and pinned.public_key.startswith("{"):
                return json.loads(pinned.public_key), "pinned"
        # First sight, or a pin in the earlier Ed25519 format: (re)pin from the shop's profile.
        profile = await self.catalog.profile(merchant_id)
        async with self.sessions.begin() as s:
            row = await s.get(MerchantKey, merchant_id)
            event = "merchant_key_pinned" if row is None else "merchant_key_format_upgraded"
            if row is None:
                row = MerchantKey(merchant_id=merchant_id, public_key="", key_id="")
                s.add(row)
            row.public_key = json.dumps(profile["jwk"], sort_keys=True)
            row.key_id = profile["key_id"]
            row.source = f"/{merchant_id}/.well-known/agent-merchant.json"
            await chain.append(s, event, {"merchant_id": merchant_id, "key_id": profile["key_id"],
                                          "jwk": profile["jwk"]})
        return profile["jwk"], "pinned on first use"

    async def _merchant_offer(self, merchant_id: str, lines: list[CartLineRequest]):
        """Ask the merchant for a signed offer and check it. Returns
        (signed offer or None, check result, resolved lines, unavailable skus)."""
        def fail(detail: str):
            return None, CheckResult(name="merchant_signed_cart", passed=False, detail=detail), [], []
        try:
            signed = await self.catalog.offer(merchant_id, lines)
            public_key, how = await self._pinned_key(merchant_id)
        except UnknownMerchant:
            return fail(f"{merchant_id} is not a known merchant")
        except (OSError, ValueError, KeyError) as e:
            return fail(f"could not get a signed offer from {merchant_id} ({type(e).__name__})")
        ok, detail = verify_offer(signed, public_key, merchant_id,
                                  [(ln.sku, ln.qty) for ln in lines], now())
        if not ok and not same_key(signed.get("jwk"), public_key):
            detail += " (the merchant's key changed since it was pinned)"
        check = CheckResult(name="merchant_signed_cart", passed=ok,
                            detail=f"{detail}; key {signed.get('key_id')} ({how})")
        if not ok:
            return None, check, [], []
        offer = signed["offer"]
        return signed, check, lines_from_offer(offer, lines), list(offer["unavailable"])

    async def purchase_approval_options(self, purchase_id: str, user: str) -> dict[str, Any]:
        async with self.sessions() as s:
            p = await self._purchase(s, purchase_id)
            if p.status != "pending_approval":
                raise GatewayError(f"purchase is {p.status}, not pending_approval")
            statement = new_statement(
                "cart_mandate_approval", purchase_id=p.id, intent_id=p.intent_id,
                cart_hash=p.cart_hash, merchant_id=p.merchant_id, total=f"{p.total:.2f}",
                merchant_offer_id=((p.merchant_offer or {}).get("offer") or {}).get("offer_id"),
                currency=p.currency,
                lines=[f"{ln['qty']} x {ln['title']} @ {ln['unit_price']['amount']}"
                       for ln in p.lines],
                **({"cart_source": "page", "page_url": p.page_cart.get("page_url"),
                    "charges": {k: (p.page_cart.get(k) or {}).get("amount")
                                for k in ("shipping", "tax", "discount")},
                    "screenshot_sha256": p.page_cart.get("screenshot_sha256")}
                   if p.cart_source == "page" and p.page_cart else {}),
            )
            return await self._approval_options(s, user, f"purchase:{p.id}", statement,
                                                force=p.cart_source == "page")

    async def _authorize(self, purchase_id: str, approver: str, approval: dict | None) -> None:
        receipt_id = new_id("rcp")
        async with self.sessions.begin() as s:
            p = await self._purchase(s, purchase_id, for_update=True)
            p.status = "authorized"
            p.receipt_id = receipt_id
            auth_entry = await chain.append(
                s, "purchase_authorized",
                {"by": approver, "cart_hash": p.cart_hash, "receipt_id": receipt_id},
                intent_id=p.intent_id, purchase_id=p.id,
            )
            lines = [ResolvedLine.model_validate(x) for x in p.lines]
            merchant, currency, intent_id = p.merchant_id, p.currency, p.intent_id
            auth_seq, auth_hash = auth_entry.seq, auth_entry.hash
            if p.cart_source == "page":
                # The shop's own checkout takes the payment. The gateway's part ends with a
                # signed permit: the checkout guard unlocks payment only for this cart.
                body = {
                    "purchase_id": p.id, "intent_id": p.intent_id, "cart_hash": p.cart_hash,
                    "shop": p.merchant_id, "total": f"{p.total:.2f}", "currency": p.currency,
                    "expires_at": (now() + PERMIT_TTL).isoformat(), "key_id": self.signer.key_id,
                }
                p.checkout_permit = {"permit": body, "signature": self.signer.sign(body)}
                await chain.append(s, "checkout_permit_issued",
                                   {"permit_hash": sha256_hex(canonical_json(p.checkout_permit)),
                                    "expires_at": body["expires_at"]},
                                   intent_id=p.intent_id, purchase_id=p.id)
                it = await self._intent(s, intent_id)
                await self._issue_receipt(s, p, it, approver, approval, None, auth_seq, auth_hash)
                return

        order: dict | None = None
        error: str | None = None
        if self.paypal is None:
            error = "PayPal is not configured"
        else:
            try:
                order = await self.paypal.create_order(
                    lines=lines,
                    currency=currency,
                    merchant_name=self.merchant_names.get(merchant, merchant),
                    custom_id=receipt_id,
                    invoice_id=purchase_id,
                    return_url=f"{self.public_base_url}/paypal/return",
                    cancel_url=f"{self.public_base_url}/paypal/cancel",
                    vault_id=self.vault_id,
                )
                if order.get("status") == "APPROVED":
                    order = await self.paypal.capture_order(order["id"])
            except (PayPalError, OSError) as e:
                error = str(e)

        async with self.sessions.begin() as s:
            p = await self._purchase(s, purchase_id, for_update=True)
            if error or not order:
                p.status = "failed"
                p.error = error
                await chain.append(s, "order_failed", {"error": error},
                                   intent_id=intent_id, purchase_id=purchase_id)
                return
            p.paypal_order_id = order["id"]
            p.paypal_status = order.get("status")
            p.payer_action_url = payer_action_url(order)
            p.status = "captured" if order.get("status") == "COMPLETED" else "ordered"
            await chain.append(
                s, "order_created",
                {"paypal_order_id": order["id"], "paypal_status": p.paypal_status},
                intent_id=intent_id, purchase_id=purchase_id,
            )
            it = await self._intent(s, intent_id)
            await self._issue_receipt(s, p, it, approver, approval, order, auth_seq, auth_hash)

    async def _issue_receipt(self, s: AsyncSession, p: Purchase, it: Intent, approver: str,
                             approval: dict | None, order: dict | None, auth_seq: int,
                             auth_hash: str) -> None:
        """Sign the authorization receipt. `order` is None for page carts: the shop's own
        checkout takes the payment, after the gateway's checkout permit."""
        receipt_id, purchase_id, intent_id = p.receipt_id, p.id, p.intent_id
        merchant, currency = p.merchant_id, p.currency
        body = {
            "receipt_id": receipt_id,
            "purchase_id": purchase_id,
            "intent_id": intent_id,
            "intent_signature": it.signature,
            "user_text": it.user_text,
            "merchant_id": merchant,
            "lines": p.lines,
            "total": f"{p.total:.2f}",
            "currency": currency,
            "cart_hash": p.cart_hash,
            "decision": p.decision,
            "approved_by_human": approval is not None,
            "approved_by": approver,
            "approval_token_hash": sha256_hex(canonical_json(approval)) if approval else None,
            "verdict_summary": {
                "reasons": p.verdict.get("reasons"),
                "semantic": p.verdict.get("semantic"),
                "injection_score": (p.verdict.get("injection") or {}).get("score"),
            },
            # AP2-aligned sections: what the user authorised, and the cart that was approved.
            "format": "agent-spend-receipt/2",
            "intent_mandate": {
                "intent_id": intent_id,
                "user_text": it.user_text,
                "terms": it.mandate,
                "approved_by": it.approved_by,
                "approved_at": it.approved_at.isoformat() if it.approved_at else None,
                "signature": it.signature,
                "user_signature": it.user_signature,
            },
            "cart_mandate": {
                "cart_hash": p.cart_hash,
                "merchant_id": merchant,
                "lines": p.lines,
                "total": f"{p.total:.2f}",
                "currency": currency,
                "approved_by": approver,
                "approved_by_human": approval is not None,
                "user_signature": (approval or {}).get("user_signature"),
                "merchant_signature": p.merchant_offer,
                # "merchant_signed": the shop signed the cart; "page": the checkout guard read it
                # from the shop's own checkout page (weaker; always passkey-approved).
                "cart_source": p.cart_source or ("merchant_signed" if p.merchant_offer
                                                 else "catalog"),
                **({"page_evidence": self._page_evidence(p)} if p.cart_source == "page" else {}),
            },
            # How the gateway matched the cart to the intent before paying.
            "gateway_checks": {
                "rules": [
                    {"name": c["name"], "passed": c["passed"]}
                    for c in p.verdict.get("hard_checks", [])
                ],
                "semantic": p.verdict.get("semantic"),
                "injection_score": (p.verdict.get("injection") or {}).get("score"),
                "thresholds": p.verdict.get("thresholds"),
                "decision": p.decision,
            },
            "ap2": self._ap2_bundle(it, p, order, merchant) if order else None,
            "log_seq": auth_seq,
            "log_hash": auth_hash,
            "paypal_order_id": order["id"] if order else None,
            **({"payment": {"status": "pending", "by": "the shop's own checkout",
                            "checkout_permit_hash":
                                sha256_hex(canonical_json(p.checkout_permit))}}
               if order is None else {}),
            "issued_at": now().isoformat(),
            "key_id": self.signer.key_id,
        }
        sig = self.signer.sign(body)
        s.add(Receipt(id=receipt_id, purchase_id=purchase_id, body=body, signature=sig,
                      key_id=self.signer.key_id))
        await chain.append(
            s, "receipt_issued",
            {"receipt_id": receipt_id, "receipt_hash": sha256_hex(canonical_json(body))},
            intent_id=intent_id, purchase_id=purchase_id,
        )

    def _ap2_bundle(self, it: Intent, p: Purchase, order: dict, merchant: str) -> dict | None:
        """AP2 v0.2 mandates for this purchase (needs a merchant Checkout JWT)."""
        offer = p.merchant_offer or {}
        if "checkout_jwt" not in offer:
            return None
        captures = [c for pu in order.get("purchase_units", [])
                    for c in (pu.get("payments") or {}).get("captures", [])]
        return build_ap2_bundle(
            self.ap2_signer, intent_id=it.id, mandate=it.mandate,
            intent_approved_at=int(it.approved_at.timestamp()) if it.approved_at else 0,
            user_signature=it.user_signature, merchant_id=merchant,
            merchant_names=self.merchant_names, checkout_jwt=offer["checkout_jwt"],
            total=f"{p.total:.2f}", currency=p.currency, paypal_order_id=order["id"],
            paypal_capture_id=captures[0]["id"] if captures else None,
            gateway_url=self.public_base_url,
        )

    def jwks(self) -> dict[str, Any]:
        return {"keys": [self.ap2_signer.jwk]}

    async def on_paypal_return(self, order_id: str) -> dict[str, Any]:
        """Buyer came back from PayPal after approving: capture the payment."""
        async with self.sessions() as s:
            p = (
                await s.execute(select(Purchase).where(Purchase.paypal_order_id == order_id))
            ).scalar_one_or_none()
            if not p:
                raise NotFound(f"no purchase for PayPal order {order_id}")
            if p.status == "captured":
                return self._purchase_view(p)
        try:
            cap = await self.paypal.capture_order(order_id)
            status, err = cap.get("status"), None
        except PayPalError as e:
            status, err = None, str(e)
        async with self.sessions.begin() as s:
            p = await self._purchase(s, p.id, for_update=True)
            if err:
                p.error = err
                await chain.append(s, "capture_failed", {"error": err},
                                   intent_id=p.intent_id, purchase_id=p.id)
            else:
                p.paypal_status = status
                p.status = "captured" if status == "COMPLETED" else p.status
                await chain.append(s, "payment_captured",
                                   {"paypal_order_id": order_id, "paypal_status": status},
                                   intent_id=p.intent_id, purchase_id=p.id)
            return self._purchase_view(p)

    async def get_purchase(self, purchase_id: str, for_agent: bool = False) -> dict[str, Any]:
        async with self.sessions() as s:
            p = await self._purchase(s, purchase_id)
            if p.status == "pending_approval" and p.approval_expires_at and now() > p.approval_expires_at:
                return {**self._purchase_view(p), "status": "approval_expired"}
            view = self._purchase_view(p)
            if not for_agent and p.page_screenshot:
                view["page_screenshot_png_b64"] = p.page_screenshot
            if not for_agent:
                view["model_exchanges"] = p.model_exchanges or []
        if for_agent:
            # The agent sees the decision and reasons, not the PayPal payer link.
            view.pop("payer_action_url", None)
            if view["status"] == "pending_approval":
                view["next"] = ("The user must review this cart at approval_url. Call "
                                "get_purchase_status to wait for their decision.")
            elif view["status"] == "authorized" and view["checkout_permit"]:
                view["next"] = ("Approved. The checkout guard now unlocks payment for this exact "
                                "cart at this shop; the person completes payment in PayPal.")
        return view

    async def wait_purchase(self, purchase_id: str, wait_seconds: float = 0,
                            agent_id: str | None = None) -> dict[str, Any]:
        await self._check_owner(Purchase, purchase_id, agent_id)
        async def get(pid: str) -> dict[str, Any]:
            return await self.get_purchase(pid, for_agent=True)
        return await self._wait(get, purchase_id, wait_seconds, "pending_approval")

    async def waiting(self) -> list[dict[str, Any]]:
        """What is waiting for the person right now (small, for frequent polling)."""
        async with self.sessions() as s:
            intents = (await s.execute(select(Intent).where(Intent.status == "pending_user")
                                       .order_by(Intent.created_at.desc()))).scalars()
            carts = (await s.execute(select(Purchase).where(
                Purchase.status == "pending_approval", Purchase.approval_expires_at > now())
                .order_by(Purchase.created_at.desc()))).scalars()
            return [
                *({"kind": "intent", "id": i.id, "path": f"/intents/{i.id}",
                   "title": "Permission to review", "text": i.user_text[:140],
                   "created_at": i.created_at.isoformat()} for i in intents),
                *({"kind": "cart", "id": p.id, "path": f"/purchases/{p.id}",
                   "title": f"Cart held: {p.total:.2f} {p.currency} at {p.merchant_id}",
                   "text": ((p.verdict or {}).get("reasons") or [""])[0][:140],
                   "created_at": p.created_at.isoformat()} for p in carts),
            ]

    async def list_purchases(self, intent_id: str | None = None, limit: int = 100):
        async with self.sessions() as s:
            q = select(Purchase).order_by(Purchase.created_at.desc()).limit(limit)
            if intent_id:
                q = q.where(Purchase.intent_id == intent_id)
            out = []
            for p in (await s.execute(q)).scalars():
                view = self._purchase_view(p)
                # Same rule as get_purchase: a held cart past its approval window is expired.
                if p.status == "pending_approval" and p.approval_expires_at and now() > p.approval_expires_at:
                    view["status"] = "approval_expired"
                out.append(view)
            return out

    def _purchase_view(self, p: Purchase) -> dict[str, Any]:
        return {
            "purchase_id": p.id,
            "intent_id": p.intent_id,
            "agent_id": p.agent_id,
            "merchant_id": p.merchant_id,
            "cart_source": p.cart_source or ("merchant_signed" if p.merchant_offer else "catalog"),
            "page_cart": p.page_cart,
            "checkout_permit": p.checkout_permit,
            "payment_confirmation": p.payment_confirmation,
            "lines": p.lines,
            "total": f"{p.total:.2f}",
            "currency": p.currency,
            "cart_hash": p.cart_hash,
            "decision": p.decision,
            "status": p.status,
            "reasons": p.verdict.get("reasons", []),
            "verdict": p.verdict,
            "context_texts": p.context_texts,
            "approval_url": f"{self.web_base_url}/purchases/{p.id}"
            if p.status == "pending_approval" else None,
            "approval_expires_at": p.approval_expires_at.isoformat()
            if p.approval_expires_at else None,
            "approved_by": (p.approval or {}).get("token", {}).get("approved_by"),
            "user_signed": bool((p.approval or {}).get("user_signature")),
            "receipt_id": p.receipt_id,
            "paypal_order_id": p.paypal_order_id,
            "paypal_status": p.paypal_status,
            "payer_action_url": p.payer_action_url,
            "error": p.error,
            "created_at": p.created_at.isoformat(),
        }

    async def _purchase(self, s: AsyncSession, pid: str, for_update: bool = False) -> Purchase:
        q = select(Purchase).where(Purchase.id == pid)
        if for_update:
            q = q.with_for_update()
        p = (await s.execute(q)).scalar_one_or_none()
        if not p:
            raise NotFound(f"unknown purchase {pid}")
        return p

    # ------------------------------------------------------------- anchoring

    async def anchor_now(self) -> dict[str, Any] | None:
        """Timestamp the current chain head with an outside authority (no-op if nothing new)."""
        async with self.sessions() as s:
            head = (await s.execute(select(LogEntry).order_by(LogEntry.seq.desc()).limit(1))
                    ).scalar_one_or_none()
            if head is None or head.event == "log_anchored":
                return None
            last = (await s.execute(select(Anchor).order_by(Anchor.seq.desc()).limit(1))
                    ).scalar_one_or_none()
            if last and last.seq >= head.seq:
                return None
            seq, head_hash = head.seq, head.hash
        ts = await self.timestamper(bytes.fromhex(head_hash))
        async with self.sessions.begin() as s:
            a = Anchor(seq=seq, head_hash=head_hash, tsa_url=ts.tsa_url, tsa_name=ts.tsa_name,
                       gen_time=ts.gen_time, token=base64.b64encode(ts.token).decode())
            s.add(a)
            await s.flush()
            await chain.append(s, "log_anchored",
                               {"anchor_id": a.id, "seq": seq, "head_hash": head_hash,
                                "tsa": ts.tsa_url, "gen_time": ts.gen_time.isoformat()})
            return self._anchor_view(a)

    def _anchor_view(self, a: Anchor) -> dict[str, Any]:
        return {"id": a.id, "seq": a.seq, "head_hash": a.head_hash, "tsa_url": a.tsa_url,
                "tsa_name": a.tsa_name, "gen_time": a.gen_time.isoformat(),
                "token_url": f"{self.public_base_url}/api/anchors/{a.id}.tsr"}

    async def list_anchors(self, limit: int = 50) -> list[dict[str, Any]]:
        async with self.sessions() as s:
            rows = (await s.execute(select(Anchor).order_by(Anchor.seq.desc()).limit(limit))
                    ).scalars()
            return [self._anchor_view(a) for a in rows]

    async def anchor_token(self, anchor_id: int) -> bytes:
        async with self.sessions() as s:
            a = await s.get(Anchor, anchor_id)
            if not a:
                raise NotFound(f"unknown anchor {anchor_id}")
            return base64.b64decode(a.token)

    async def receipt_proof(self, receipt_id: str) -> dict[str, Any]:
        """Public proof that a receipt's log entry is covered by an outside timestamp."""
        async with self.sessions() as s:
            r = await s.get(Receipt, receipt_id)
            if not r:
                raise NotFound(f"unknown receipt {receipt_id}")
            seq = r.body.get("log_seq")
            a = (await s.execute(select(Anchor).where(Anchor.seq >= seq)
                                 .order_by(Anchor.seq).limit(1))).scalar_one_or_none()
            if a is None:
                return {"anchored": False, "reason": "not witnessed yet (anchors run every few minutes)"}
            entries = list((await s.execute(
                select(LogEntry).where(LogEntry.seq >= seq, LogEntry.seq <= a.seq)
                .order_by(LogEntry.seq))).scalars())
            if any(e.hash_version != 2 for e in entries):
                return {"anchored": False,
                        "reason": "receipt predates privacy-preserving log links (Oct 21)"}
            return {"anchored": True, "links": [chain.link(e) for e in entries],
                    "anchor": {**self._anchor_view(a), "token": a.token}}

    # ------------------------------------------------------------ receipts, log

    async def get_receipt(self, receipt_id: str) -> dict[str, Any]:
        async with self.sessions() as s:
            r = await s.get(Receipt, receipt_id)
            if not r:
                raise NotFound(f"unknown receipt {receipt_id}")
            return {"receipt": r.body, "signature": r.signature, "key_id": r.key_id,
                    "public_key": self.signer.public_key_b64}

    async def verify_receipt(self, receipt_id: str) -> dict[str, Any]:
        """Checks a seller or dispute analyst can run: signature, log anchor, whole chain."""
        async with self.sessions() as s:
            r = await s.get(Receipt, receipt_id)
            if not r:
                return {"receipt_id": receipt_id, "valid": False, "reason": "unknown receipt"}
            sig_ok = self.signer.verify(r.body, r.signature)
            entry = await s.get(LogEntry, r.body.get("log_seq"))
            anchor_ok = bool(
                entry
                and entry.hash == r.body.get("log_hash")
                and entry.purchase_id == r.body.get("purchase_id")
                and entry.hash
                == chain.recompute(entry)
            )
            chain_ok, bad_seq, n = await chain.verify_chain(s)
            purchase = await s.get(Purchase, r.purchase_id)
            confirmation = purchase.payment_confirmation if purchase else None
        payment = None
        if confirmation:
            body = confirmation["confirmation"]
            payment = {**body, "signature": confirmation["signature"],
                       "signature_valid": self.signer.verify(body, confirmation["signature"])}
        merchant = merchant_signature_check(r.body)
        ap2 = verify_ap2_bundle(r.body.get("ap2"), self.ap2_signer.jwk,
                                (merchant.get("jwk") or {}))
        users = user_signature_checks(r.body)
        users_ok = all(v["valid"] for v in users.values())
        witness = verify_receipt_proof(await self.receipt_proof(receipt_id),
                                       r.body.get("log_hash"), self.token_verifier)
        witness_ok = witness["valid"] is not False  # not-yet-anchored is fine, a bad anchor is not
        return {
            "receipt_id": receipt_id,
            "valid": (sig_ok and anchor_ok and chain_ok and users_ok and witness_ok
                      and (payment is None or payment["signature_valid"])
                      and merchant.get("valid") is not False and ap2.get("valid") is not False),
            "merchant_signature": merchant,
            "ap2": ap2,
            "user_signatures": users,
            "witness": witness,
            "payment_confirmation": payment,
            "signature_valid": sig_ok,
            "log_anchor_valid": anchor_ok,
            "chain_valid": chain_ok,
            "chain_first_bad_seq": bad_seq,
            "chain_entries": n,
            "key_id": r.key_id,
            "receipt": r.body,
        }

    async def get_log(
        self, intent_id: str | None = None, purchase_id: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        async with self.sessions() as s:
            q = select(LogEntry).order_by(LogEntry.seq.desc()).limit(limit)
            if intent_id:
                q = q.where(LogEntry.intent_id == intent_id)
            if purchase_id:
                q = q.where(LogEntry.purchase_id == purchase_id)
            return [
                {"seq": e.seq, "at": e.at.isoformat(), "event": e.event, "intent_id": e.intent_id,
                 "purchase_id": e.purchase_id, "payload": e.payload, "prev_hash": e.prev_hash,
                 "hash": e.hash}
                for e in (await s.execute(q)).scalars()
            ]

    async def agent_log(self, intent_id: str, agent_id: str | None) -> list[dict[str, Any]]:
        await self._check_owner(Intent, intent_id, agent_id)
        return await self.get_log(intent_id=intent_id)

    async def verify_log(self) -> dict[str, Any]:
        async with self.sessions() as s:
            ok, bad, n = await chain.verify_chain(s)
        return {"valid": ok, "first_bad_seq": bad, "entries": n}

    # ------------------------------------------------------------------ agents

    async def ensure_default_agent(self, key: str) -> None:
        """The AGENT_API_KEY from .env keeps working as the "default" agent. Records from before
        per-agent keys are attributed to it. A revoked default agent stays revoked."""
        if not key:
            return
        async with self.sessions.begin() as s:
            row = (await s.execute(select(Agent).where(Agent.key_hash == agent_key_hash(key)))
                   ).scalar_one_or_none()
            if row is None:
                row = Agent(id=new_id("agt"), name="default (AGENT_API_KEY)",
                            key_hash=agent_key_hash(key), key_hint=key[:4], created_by="config")
                s.add(row)
                await s.flush()
                await chain.append(s, "agent_registered",
                                   {"agent_id": row.id, "name": row.name, "source": "config"})
            # A changed AGENT_API_KEY retires the old one: it must not keep working.
            old = (await s.execute(select(Agent).where(
                Agent.created_by == "config", Agent.status == "active", Agent.id != row.id))
            ).scalars()
            for a in old:
                a.status, a.revoked_at = "revoked", now()
                await chain.append(s, "agent_revoked",
                                   {"agent_id": a.id, "by": "config",
                                    "reason": "AGENT_API_KEY was changed"})
            for model in (Intent, Purchase):
                await s.execute(update(model).where(model.agent_id.is_(None))
                                .values(agent_id=row.id))

    async def authenticate_agent(self, key: str) -> str:
        """Agent id for an MCP key, or AgentDenied. Checked on every MCP call."""
        async with self.sessions.begin() as s:
            row = (await s.execute(select(Agent).where(Agent.key_hash == agent_key_hash(key)))
                   ).scalar_one_or_none()
            if row is None:
                raise AgentDenied("unknown agent key")
            stopped = await self._agent_stopped(s, row.id, row)
            if stopped:
                raise AgentDenied(stopped)
            if not row.last_used_at or now() - row.last_used_at > timedelta(minutes=1):
                row.last_used_at = now()
            return row.id

    async def _paused(self, s: AsyncSession) -> dict[str, Any] | None:
        c = await s.get(Control, PAUSE_KEY)
        return c.value if c and c.value.get("paused") else None

    async def _agent_stopped(self, s: AsyncSession, agent_id: str | None,
                             row: Agent | None = None) -> str | None:
        """Why this agent may not act right now, or None."""
        if await self._paused(s):
            return "all agents are stopped by the user"
        if agent_id:
            row = row or await s.get(Agent, agent_id)
            if row is None:
                return f"unknown agent {agent_id}"
            if row.status != "active":
                return f"agent {row.name} is {row.status}"
        return None

    async def _agent_spent(self, s: AsyncSession, agent_id: str, currency: str,
                           exclude: str | None = None) -> Decimal:
        q = select(func.coalesce(func.sum(Purchase.total), 0)).where(
            Purchase.agent_id == agent_id, Purchase.currency == currency,
            Purchase.status.in_(SPENDING_STATUSES),
            Purchase.created_at >= now() - AGENT_CAP_WINDOW)
        if exclude:
            q = q.where(Purchase.id != exclude)
        return Decimal((await s.execute(q)).scalar_one())

    async def _agent_checks(self, s: AsyncSession, agent_id: str | None, owner: str | None,
                            total: Decimal, currency: str, exclude: str | None = None,
                            ) -> list[CheckResult]:
        """Checks about who is buying: the agent is allowed to act, owns the permission, and
        stays under its own spending cap."""
        acting = agent_id or owner
        stopped = await self._agent_stopped(s, acting)
        checks = [CheckResult(name="agent_active", passed=stopped is None, detail=stopped or "")]
        if agent_id and owner and agent_id != owner:
            checks.append(CheckResult(
                name="intent_agent_match", passed=False,
                detail="this permission was given to a different agent"))
        row = await s.get(Agent, acting) if acting else None
        if row is not None and row.spend_cap is not None:
            spent = await self._agent_spent(s, row.id, currency, exclude)
            ok = currency == row.currency and spent + total <= row.spend_cap
            checks.append(CheckResult(
                name="agent_spend_cap", passed=ok,
                detail=f"{row.name} spent {spent:.2f} of its {row.spend_cap:.2f} {row.currency} "
                       f"30-day cap; this cart is {total:.2f} {currency}"))
        return checks

    async def _check_owner(self, model, ident: str, agent_id: str | None) -> None:
        """An agent can only read its own intents and purchases."""
        if agent_id is None:
            return
        async with self.sessions() as s:
            row = await s.get(model, ident)
            if row is None or (row.agent_id and row.agent_id != agent_id):
                raise NotFound(f"unknown {model.__tablename__[:-1]} {ident}")

    def _agent_view(self, a: Agent, spent: Decimal) -> dict[str, Any]:
        return {
            "agent_id": a.id, "name": a.name, "key_hint": a.key_hint + "…", "status": a.status,
            "kind": a.kind or "mcp",
            "spend_cap": f"{a.spend_cap:.2f}" if a.spend_cap is not None else None,
            "currency": a.currency, "spent_30d": f"{spent:.2f}",
            "created_by": a.created_by, "user_signed": a.user_signature is not None,
            "created_at": a.created_at.isoformat(),
            "last_used_at": a.last_used_at.isoformat() if a.last_used_at else None,
            "revoked_at": a.revoked_at.isoformat() if a.revoked_at else None,
        }

    async def list_agents(self) -> dict[str, Any]:
        async with self.sessions() as s:
            rows = list((await s.execute(select(Agent).order_by(Agent.created_at))).scalars())
            agents = [self._agent_view(a, await self._agent_spent(s, a.id, a.currency))
                      for a in rows]
            paused = await self._paused(s)
        return {"agents": agents, "paused": paused is not None,
                "paused_at": (paused or {}).get("at"), "paused_by": (paused or {}).get("by")}

    @staticmethod
    def _agent_terms(name: str, spend_cap: Decimal | None, currency: str,
                     kind: str = "mcp") -> dict[str, Any]:
        if kind not in ("mcp", "browser"):
            raise GatewayError("kind must be mcp or browser")
        name = name.strip()
        if not name or len(name) > 100:
            raise GatewayError("give the agent a name (up to 100 characters)")
        if spend_cap is not None and spend_cap <= 0:
            raise GatewayError("the spending cap must be positive")
        return {"name": name, "spend_cap": f"{spend_cap:.2f}" if spend_cap is not None else None,
                "currency": currency.upper(), "agent_kind": kind}

    async def agent_creation_options(self, user: str, name: str, spend_cap: Decimal | None,
                                     currency: str = "USD", kind: str = "mcp") -> dict[str, Any]:
        """Giving a new agent power to shop is signed with the user's passkey."""
        terms = self._agent_terms(name, spend_cap, currency, kind)
        async with self.sessions() as s:
            return await self._approval_options(s, user, f"agent_create:{user}",
                                                new_statement("agent_creation", **terms))

    async def create_agent(self, user: str, name: str, spend_cap: Decimal | None,
                           currency: str = "USD",
                           credential: dict[str, Any] | None = None,
                           kind: str = "mcp") -> dict[str, Any]:
        terms = self._agent_terms(name, spend_cap, currency, kind)
        key = "agk_" + secrets.token_urlsafe(32)
        async with self.sessions.begin() as s:
            record = await self._user_signature(s, user, f"agent_create:{user}", credential,
                                                expected={"kind": "agent_creation", **terms})
            a = Agent(id=new_id("agt"), name=terms["name"], key_hash=agent_key_hash(key),
                      key_hint=key[:8], spend_cap=spend_cap, currency=terms["currency"],
                      kind=terms["agent_kind"], created_by=user, user_signature=record)
            s.add(a)
            await s.flush()
            await chain.append(s, "agent_created",
                               {"agent_id": a.id, **terms, "by": user,
                                "user_signed": record is not None,
                                "user_signature_hash":
                                    sha256_hex(canonical_json(record)) if record else None})
            view = self._agent_view(a, Decimal(0))
        return {**view, "key": key,
                "note": "Copy this key now: it is shown once. Give it to the agent as its "
                        "Bearer token for /mcp."}

    async def revoke_agent(self, agent_id: str, user: str) -> dict[str, Any]:
        """Cut one agent off. Permanent; takes effect on its next call. No passkey needed:
        taking power away should never be harder than giving it."""
        async with self.sessions.begin() as s:
            a = await s.get(Agent, agent_id, with_for_update=True)
            if a is None:
                raise NotFound(f"unknown agent {agent_id}")
            if a.status == "revoked":
                raise GatewayError("agent is already revoked")
            a.status, a.revoked_at = "revoked", now()
            held = await self._expire_held(s, agent_id, f"agent {a.name} was revoked")
            await chain.append(s, "agent_revoked",
                               {"agent_id": a.id, "by": user, "held_carts_closed": held})
            return self._agent_view(a, await self._agent_spent(s, a.id, a.currency))

    async def _expire_held(self, s: AsyncSession, agent_id: str | None, why: str) -> list[str]:
        """Close the carts waiting for approval so a stopped agent's cart can't be paid."""
        q = select(Purchase).where(Purchase.status == "pending_approval").with_for_update()
        if agent_id:
            q = q.where(Purchase.agent_id == agent_id)
        closed = []
        for p in (await s.execute(q)).scalars():
            p.status, p.error = "blocked", why
            closed.append(p.id)
        return closed

    async def stop_all_agents(self, user: str) -> dict[str, Any]:
        """The kill switch: every agent is refused from its next call; held carts are closed."""
        async with self.sessions.begin() as s:
            c = await s.get(Control, PAUSE_KEY, with_for_update=True)
            value = {"paused": True, "at": now().isoformat(), "by": user}
            if c is None:
                s.add(Control(key=PAUSE_KEY, value=value, updated_by=user))
            else:
                c.value, c.updated_by, c.updated_at = value, user, now()
            held = await self._expire_held(s, None, "all agents were stopped by the user")
            await chain.append(s, "agents_stopped", {"by": user, "held_carts_closed": held})
        return await self.list_agents()

    async def resume_options(self, user: str) -> dict[str, Any]:
        async with self.sessions() as s:
            return await self._approval_options(s, user, f"agents_resume:{user}",
                                                new_statement("agents_resume"))

    async def resume_agents(self, user: str,
                            credential: dict[str, Any] | None = None) -> dict[str, Any]:
        """Turning agents back on gives power back, so it is signed like an approval."""
        async with self.sessions.begin() as s:
            record = await self._user_signature(s, user, f"agents_resume:{user}", credential,
                                                expected={"kind": "agents_resume"})
            c = await s.get(Control, PAUSE_KEY, with_for_update=True)
            if c is None or not c.value.get("paused"):
                raise GatewayError("agents are not stopped")
            c.value, c.updated_by, c.updated_at = {"paused": False}, user, now()
            await chain.append(s, "agents_resumed", {"by": user, "user_signed": record is not None})
        return await self.list_agents()

    # ------------------------------------------------------------------ helpers

    async def _wait(self, getter, ident: str, wait_seconds: float, pending: str):
        deadline = asyncio.get_running_loop().time() + max(0.0, min(wait_seconds, 60.0))
        while True:
            view = await getter(ident)
            if view["status"] != pending or asyncio.get_running_loop().time() >= deadline:
                return view
            await asyncio.sleep(1.0)


def user_signature_checks(receipt: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Re-check the user's passkey signatures carried by a receipt (only those present)."""
    out: dict[str, dict[str, Any]] = {}
    intent_sig = (receipt.get("intent_mandate") or {}).get("user_signature")
    if intent_sig:
        ok, why = verify_user_signature(intent_sig, {"intent_id": receipt.get("intent_id")})
        out["intent_mandate"] = {"valid": ok, "detail": why}
    cart_sig = (receipt.get("cart_mandate") or {}).get("user_signature")
    if cart_sig:
        ok, why = verify_user_signature(cart_sig, {"cart_hash": receipt.get("cart_hash"),
                                                   "purchase_id": receipt.get("purchase_id")})
        out["cart_mandate"] = {"valid": ok, "detail": why}
    return out
