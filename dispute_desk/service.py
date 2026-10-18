"""Seller-side dispute handling: intake (fetch by id, webhook), packet building, and submission
to PayPal after a human approves. Talks to PayPal (seller credentials) and to the buyer's gateway
public receipt endpoint; never to the gateway's database."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dispute_desk.models import Action, Dispute, WebhookEvent
from dispute_desk.packet import build_packet
from dispute_desk.paypal import Disputes, PayPalError
from dispute_desk.receipts import Receipts

ACTIONS = ("provide_evidence", "make_offer", "accept_claim")


class DeskError(Exception):
    pass


class NotFound(DeskError):
    pass


def _parse_time(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


class Desk:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        paypal: Disputes | None,
        receipts: Receipts,
        *,
        webhook_id: str = "",
        allow_unverified_webhooks: bool = False,
    ):
        self.sessions = sessions
        self.paypal = paypal
        self.receipts = receipts
        self.webhook_id = webhook_id
        self.allow_unverified = allow_unverified_webhooks

    # ----------------------------------------------------------------- intake

    async def import_dispute(self, dispute_id: str) -> dict[str, Any]:
        """Seller pastes a case id (needed because PayPal's list can miss sandbox cases)."""
        dispute = await self._fetch(dispute_id)
        return await self._store(dispute, source="import", verified=True)

    async def handle_webhook(self, headers: dict[str, str], event: dict) -> dict[str, Any]:
        verified = bool(
            self.webhook_id and self.paypal
            and await self.paypal.verify_webhook(headers, event, self.webhook_id)
        )
        if not verified and not self.allow_unverified:
            raise DeskError("webhook signature could not be verified")
        resource = event.get("resource") or {}
        dispute_id = resource.get("dispute_id")
        async with self.sessions.begin() as s:
            if not await s.get(WebhookEvent, event.get("id", "")):
                s.add(WebhookEvent(id=event.get("id") or f"evt-{dispute_id}",
                                   event_type=event.get("event_type", ""), verified=verified,
                                   dispute_id=dispute_id, body=event))
        if not (event.get("event_type", "").startswith("CUSTOMER.DISPUTE") and dispute_id):
            return {"ignored": event.get("event_type")}
        try:
            dispute = await self._fetch(dispute_id)
            source = "webhook"
        except DeskError:
            # PayPal's simulate-event sends a sample dispute that does not exist: keep it, labelled.
            dispute, source = resource, "simulated"
        view = await self._store(dispute, source=source, verified=verified)
        return {"dispute_id": view["dispute_id"], "source": source, "verified": verified}

    async def refresh(self, dispute_id: str, fulfillment: dict | None = None) -> dict[str, Any]:
        async with self.sessions() as s:
            row = await s.get(Dispute, dispute_id)
            if not row:
                raise NotFound(f"unknown dispute {dispute_id}")
            source, verified, raw = row.source, row.source_verified, row.raw
            if fulfillment is None:
                fulfillment = row.fulfillment
        dispute = raw if source == "simulated" else await self._fetch(dispute_id)
        return await self._store(dispute, source=source, verified=verified,
                                 fulfillment=fulfillment)

    async def _fetch(self, dispute_id: str) -> dict:
        if not self.paypal:
            raise DeskError("PayPal is not configured")
        try:
            return await self.paypal.get_dispute(dispute_id)
        except PayPalError as e:
            raise DeskError(f"PayPal could not return dispute {dispute_id}: {e}") from e

    async def _store(self, dispute: dict, source: str, verified: bool,
                     fulfillment: dict | None = None) -> dict[str, Any]:
        txn = (dispute.get("disputed_transactions") or [{}])[0]
        receipt_id = txn.get("custom")
        receipt_check = (await self.receipts.check(receipt_id)
                         if receipt_id and receipt_id.startswith("rcp_") else None)
        packet = build_packet(dispute, receipt_check, fulfillment)
        amount = dispute.get("dispute_amount") or {}
        async with self.sessions.begin() as s:
            row = await s.get(Dispute, dispute["dispute_id"])
            if not row:
                row = Dispute(id=dispute["dispute_id"], source=source, source_verified=verified)
                s.add(row)
            row.status = dispute.get("status")
            row.reason = dispute.get("reason")
            row.stage = dispute.get("dispute_life_cycle_stage")
            row.amount = Decimal(amount["value"]) if amount.get("value") else None
            row.currency = amount.get("currency_code")
            row.due_at = _parse_time(dispute.get("seller_response_due_date"))
            row.capture_id = txn.get("seller_transaction_id")
            row.receipt_id = receipt_id
            row.receipt_check = receipt_check
            row.fulfillment = fulfillment
            row.packet = packet
            row.raw = dispute
        return await self.get(dispute["dispute_id"])

    # ------------------------------------------------------------ submission

    async def submit(self, dispute_id: str, by: str, action: str, note: str,
                     offer_amount: str | None = None) -> dict[str, Any]:
        """Send the seller's approved response to PayPal. Only actions PayPal offers are allowed."""
        if action not in ACTIONS:
            raise DeskError(f"unknown action {action}")
        async with self.sessions() as s:
            row = await s.get(Dispute, dispute_id)
            if not row:
                raise NotFound(f"unknown dispute {dispute_id}")
            packet, fulfillment, currency = row.packet, row.fulfillment, row.currency
            amount = row.amount
            simulated = row.source == "simulated"
        if simulated:
            raise DeskError("simulated disputes cannot be submitted to PayPal")
        if action not in packet.get("available_actions", []):
            raise DeskError(f"PayPal does not currently allow {action} on this case")
        if not note.strip():
            raise DeskError("a note is required")

        if action == "provide_evidence":
            evidences = [{"evidence_type": "OTHER", "notes": note[:2000]}]
            if fulfillment and fulfillment.get("tracking_number"):
                evidences.insert(0, {
                    "evidence_type": "PROOF_OF_FULFILLMENT",
                    "evidence_info": {"tracking_info": [{
                        "carrier_name": fulfillment.get("carrier") or "OTHER",
                        "tracking_number": fulfillment["tracking_number"]}]},
                    "notes": "Proof of delivery.",
                })
            request = {"evidences": evidences}
            call = self.paypal.provide_evidence(dispute_id, request)
        elif action == "make_offer":
            value = Decimal(offer_amount) if offer_amount else amount
            request = {"note": note[:2000], "offer_type": "REFUND",
                       "offer_amount": {"currency_code": currency, "value": f"{value:.2f}"}}
            call = self.paypal.make_offer(dispute_id, request)
        else:
            request = {"note": note[:2000], "accept_claim_type": "REFUND"}
            call = self.paypal.accept_claim(dispute_id, request)

        try:
            response, ok = await call, True
        except PayPalError as e:
            response, ok = {"status": e.status, **e.body}, False
        async with self.sessions.begin() as s:
            s.add(Action(dispute_id=dispute_id, kind=action, by=by, request=request,
                         response=response, ok=ok))
        if ok:
            try:
                return await self.refresh(dispute_id)
            except DeskError:
                pass
        view = await self.get(dispute_id)
        if not ok:
            raise DeskError(f"PayPal rejected {action}: {response.get('message') or response}")
        return view

    # ------------------------------------------------------------------ reads

    async def list(self) -> list[dict[str, Any]]:
        async with self.sessions() as s:
            rows = (await s.execute(select(Dispute).order_by(Dispute.updated_at.desc()))).scalars()
            return [self._summary(r) for r in rows]

    async def get(self, dispute_id: str) -> dict[str, Any]:
        async with self.sessions() as s:
            row = await s.get(Dispute, dispute_id)
            if not row:
                raise NotFound(f"unknown dispute {dispute_id}")
            actions = (await s.execute(select(Action).where(Action.dispute_id == dispute_id)
                                       .order_by(Action.at))).scalars()
            return {
                **self._summary(row),
                "packet": row.packet,
                "receipt_check": row.receipt_check,
                "fulfillment": row.fulfillment,
                "actions": [{"kind": a.kind, "by": a.by, "ok": a.ok, "at": a.at.isoformat(),
                             "request": a.request, "response": a.response} for a in actions],
            }

    def _summary(self, r: Dispute) -> dict[str, Any]:
        rec = (r.packet or {}).get("recommendation") or {}
        return {
            "dispute_id": r.id,
            "source": r.source,
            "source_verified": r.source_verified,
            "status": r.status,
            "reason": r.reason,
            "reason_label": (r.packet or {}).get("facts", {}).get("reason_label"),
            "stage": r.stage,
            "amount": f"{r.amount:.2f}" if r.amount is not None else None,
            "currency": r.currency,
            "due_at": r.due_at.isoformat() if r.due_at else None,
            "receipt_id": r.receipt_id,
            "has_valid_receipt": any(e["kind"] == "authorization_receipt" and e["have"]
                                     for e in (r.packet or {}).get("evidence", [])),
            "recommendation": rec.get("action"),
            "updated_at": r.updated_at.isoformat(),
        }
