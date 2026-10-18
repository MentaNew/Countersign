"""The gateway's core flow. MCP tools (agent side) and the REST API (user side) are thin
wrappers around this class; all enforcement happens here."""

import asyncio
import secrets
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.canonical import canonical_json, sha256_hex
from gateway.catalog.client import Catalog, UnknownProduct, resolve_lines
from gateway.db.models import Intent, LogEntry, Purchase, Receipt
from gateway.domain import CartLineRequest, CheckResult, Mandate, ResolvedLine
from gateway.ledger import chain
from gateway.paypal.client import PayPal, PayPalError, payer_action_url
from gateway.policy.decision import Thresholds, decide
from gateway.policy.hard_checks import SpendState, cart_total, run_hard_checks
from gateway.signing.keys import Signer
from gateway.verifier.base import InjectionDetector, Verifier

SPENDING_STATUSES = ("authorized", "ordered", "captured")


class GatewayError(Exception):
    """An error the caller should see verbatim (wrong state, tampered record)."""


class NotFound(GatewayError):
    pass


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def now() -> datetime:
    return datetime.now(UTC)


def compute_cart_hash(intent_id: str, merchant_id: str, lines: list[ResolvedLine]) -> str:
    return sha256_hex(
        canonical_json(
            {
                "intent_id": intent_id,
                "merchant_id": merchant_id,
                "lines": [ln.model_dump(mode="json") for ln in lines],
            }
        )
    )


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

    # ------------------------------------------------------------------ intents

    def _intent_signed_body(self, it: Intent) -> dict[str, Any]:
        return {
            "intent_id": it.id,
            "user_text": it.user_text,
            "mandate": it.mandate,
            "approved_by": it.approved_by,
            "approved_at": it.approved_at.astimezone(UTC).isoformat() if it.approved_at else None,
            "key_id": it.key_id,
        }

    async def confirm_intent(self, user_text: str, mandate: Mandate) -> dict[str, Any]:
        intent_id = new_id("int")
        async with self.sessions.begin() as s:
            it = Intent(id=intent_id, user_text=user_text, mandate=mandate.model_dump(mode="json"))
            s.add(it)
            await s.flush()
            await chain.append(
                s, "intent_proposed",
                {"user_text": user_text, "mandate": it.mandate}, intent_id=intent_id,
            )
        return {
            "intent_id": intent_id,
            "status": "pending_user",
            "summary": mandate.summary(),
            "review_url": f"{self.web_base_url}/intents/{intent_id}",
            "next": "Ask the user to review and approve this intent at review_url, then call "
            "get_intent_status. Do not shop with payment until it is active.",
        }

    async def decide_intent(self, intent_id: str, user: str, approve: bool) -> dict[str, Any]:
        async with self.sessions.begin() as s:
            it = await self._intent(s, intent_id, for_update=True)
            if it.status != "pending_user":
                raise GatewayError(f"intent is {it.status}, not pending_user")
            if approve:
                it.status = "active"
                it.approved_by = user
                it.approved_at = now()
                it.key_id = self.signer.key_id
                it.signature = self.signer.sign(self._intent_signed_body(it))
                await chain.append(
                    s, "intent_approved",
                    {"approved_by": user, "signature": it.signature, "key_id": it.key_id},
                    intent_id=it.id,
                )
            else:
                it.status = "rejected"
                await chain.append(s, "intent_rejected", {"by": user}, intent_id=it.id)
            return self._intent_view(it)

    async def get_intent(self, intent_id: str) -> dict[str, Any]:
        async with self.sessions() as s:
            return self._intent_view(await self._intent(s, intent_id))

    async def wait_intent(self, intent_id: str, wait_seconds: float = 0) -> dict[str, Any]:
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
        }

    async def _intent(self, s: AsyncSession, intent_id: str, for_update: bool = False) -> Intent:
        q = select(Intent).where(Intent.id == intent_id)
        if for_update:
            q = q.with_for_update()
        it = (await s.execute(q)).scalar_one_or_none()
        if not it:
            raise NotFound(f"unknown intent {intent_id}")
        return it

    # ---------------------------------------------------------------- purchases

    async def _spend_state(self, s: AsyncSession, intent_id: str, exclude: str | None = None):
        rows = (
            await s.execute(
                select(Purchase).where(
                    Purchase.intent_id == intent_id, Purchase.status.in_(SPENDING_STATUSES)
                )
            )
        ).scalars()
        spent, n, qty = Decimal(0), 0, defaultdict(int)
        for p in rows:
            if p.id == exclude:
                continue
            spent += p.total
            n += 1
            for ln in p.lines:
                if ln.get("item") is not None:
                    qty[ln["item"]] += ln["qty"]
        return SpendState(spent=spent, purchases=n, qty_by_item=dict(qty))

    async def request_purchase(
        self,
        intent_id: str,
        merchant_id: str,
        lines: list[CartLineRequest],
        context_texts: list[str] | None = None,
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

        unknown: list[str] = []
        resolved: list[ResolvedLine] = []
        for ln in lines:
            try:
                resolved += await resolve_lines(self.catalog, merchant_id, [ln])
            except UnknownProduct:
                unknown.append(ln.sku)

        hard = run_hard_checks(mandate, merchant_id, resolved, state, now())
        hard.insert(
            0,
            CheckResult(
                name="known_products",
                passed=not unknown,
                detail=f"not in {merchant_id}'s catalog: {unknown}" if unknown else "",
            ),
        )
        hard_ok = all(c.passed for c in hard)
        # Injection screening runs even on blocked carts: it is part of the evidence.
        injection = await self.injection.score(context_texts) if context_texts else None
        semantic = await self.verifier.evaluate(mandate, resolved) if hard_ok else None
        total = cart_total(resolved)
        verdict = decide(mandate, total, hard, semantic, injection, self.thresholds)

        purchase_id = new_id("pur")
        cart_hash = compute_cart_hash(intent_id, merchant_id, resolved)
        status = {"block": "blocked", "escalate": "pending_approval", "approve": "authorized"}[
            verdict.decision
        ]
        async with self.sessions.begin() as s:
            p = Purchase(
                id=purchase_id,
                intent_id=intent_id,
                merchant_id=merchant_id,
                lines=[ln.model_dump(mode="json") for ln in resolved],
                total=total,
                currency=mandate.max_total.currency,
                cart_hash=cart_hash,
                context_texts=context_texts,
                verdict=verdict.model_dump(mode="json"),
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
                    "merchant_id": merchant_id,
                    "cart_hash": cart_hash,
                    "lines": p.lines,
                    "total": f"{total:.2f}",
                    "currency": p.currency,
                    "context_text_hashes": [sha256_hex(t.encode()) for t in context_texts],
                    "verdict": p.verdict,
                },
                intent_id=intent_id, purchase_id=purchase_id,
            )

        if verdict.decision == "approve":
            await self._authorize(purchase_id, approver="policy", approval=None)
        return await self.get_purchase(purchase_id, for_agent=True)

    async def decide_purchase(
        self, purchase_id: str, user: str, approve: bool, cart_hash: str
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
            recheck = run_hard_checks(mandate, p.merchant_id, lines, state, now())
            failed = [c for c in recheck if not c.passed]
            if failed:
                p.status = "blocked"
                p.error = "; ".join(f"{c.name}: {c.detail}" for c in failed)
                await chain.append(s, "purchase_blocked_on_recheck",
                                   {"failed": [c.model_dump() for c in failed]},
                                   intent_id=p.intent_id, purchase_id=p.id)
                return self._purchase_view(p)

            token_body = {
                "purchase_id": p.id,
                "intent_id": p.intent_id,
                "cart_hash": p.cart_hash,
                "approved_by": user,
                "approved_at": now().isoformat(),
                "expires_at": p.approval_expires_at.astimezone(UTC).isoformat()
                if p.approval_expires_at else None,
                "key_id": self.signer.key_id,
            }
            approval = {"token": token_body, "signature": self.signer.sign(token_body)}
            p.approval = approval
            await chain.append(
                s, "purchase_approved_by_human",
                {"approved_by": user, "token_hash": sha256_hex(canonical_json(approval))},
                intent_id=p.intent_id, purchase_id=p.id,
            )
        await self._authorize(purchase_id, approver=user, approval=approval)
        return await self.get_purchase(purchase_id)

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
                "log_seq": auth_seq,
                "log_hash": auth_hash,
                "paypal_order_id": order["id"],
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
        if for_agent:
            # The agent sees the decision and reasons, not the PayPal payer link.
            view.pop("payer_action_url", None)
            if view["status"] == "pending_approval":
                view["next"] = ("The user must review this cart at approval_url. Call "
                                "get_purchase_status to wait for their decision.")
        return view

    async def wait_purchase(self, purchase_id: str, wait_seconds: float = 0) -> dict[str, Any]:
        async def get(pid: str) -> dict[str, Any]:
            return await self.get_purchase(pid, for_agent=True)
        return await self._wait(get, purchase_id, wait_seconds, "pending_approval")

    async def list_purchases(self, intent_id: str | None = None, limit: int = 100):
        async with self.sessions() as s:
            q = select(Purchase).order_by(Purchase.created_at.desc()).limit(limit)
            if intent_id:
                q = q.where(Purchase.intent_id == intent_id)
            return [self._purchase_view(p) for p in (await s.execute(q)).scalars()]

    def _purchase_view(self, p: Purchase) -> dict[str, Any]:
        return {
            "purchase_id": p.id,
            "intent_id": p.intent_id,
            "merchant_id": p.merchant_id,
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
                == chain.entry_hash(entry.seq, entry.at, entry.event, entry.payload, entry.prev_hash)
            )
            chain_ok, bad_seq, n = await chain.verify_chain(s)
        return {
            "receipt_id": receipt_id,
            "valid": sig_ok and anchor_ok and chain_ok,
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

    async def verify_log(self) -> dict[str, Any]:
        async with self.sessions() as s:
            ok, bad, n = await chain.verify_chain(s)
        return {"valid": ok, "first_bad_seq": bad, "entries": n}

    # ------------------------------------------------------------------ helpers

    async def _wait(self, getter, ident: str, wait_seconds: float, pending: str):
        deadline = asyncio.get_running_loop().time() + max(0.0, min(wait_seconds, 60.0))
        while True:
            view = await getter(ident)
            if view["status"] != pending or asyncio.get_running_loop().time() >= deadline:
                return view
            await asyncio.sleep(1.0)
