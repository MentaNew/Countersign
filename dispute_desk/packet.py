"""Build the evidence packet for one dispute. Pure function, no I/O, no LLM: what goes to PayPal
must be traceable to records, so the narrative is a template filled from verified facts.

The key judgement is matching the dispute *reason* to the evidence we actually hold. An
authorization receipt proves what the buyer's side authorised; it does not prove delivery."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from gateway.signing.passkeys import verify_user_signature  # signature math only

# Industry figures, labelled as such wherever shown (Javelin 2026 via chargeback.io).
INDUSTRY_COST = {
    "avg_total_cost_usd": 128,
    "avg_internal_labour_usd": 82,
    "source": "Javelin 2026 via chargeback.io; industry average, not measured by this desk",
}
SMALL_CLAIM_USD = Decimal(25)  # below this, refunding usually costs less than contesting

REASON_LABELS = {
    "UNAUTHORISED": "Buyer says they did not authorise the payment",
    "MERCHANDISE_OR_SERVICE_NOT_RECEIVED": "Buyer says the item was not received",
    "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED": "Buyer says the item is not as described",
    "DUPLICATE_TRANSACTION": "Buyer says they were charged twice",
    "INCORRECT_AMOUNT": "Buyer says the amount is wrong",
    "CREDIT_NOT_PROCESSED": "Buyer says a refund was not processed",
    "PAYMENT_BY_OTHER_MEANS": "Buyer says they paid another way",
    "CANCELED_RECURRING_BILLING": "Buyer says a subscription was cancelled",
}


# Evidence types PayPal accepts per dispute reason (developer.paypal.com/disputes/reasons-evidence,
# read Oct 18, 2026). Sending a type not listed fails with EVIDENCE_TYPE_IS_NOT_ALLOWED.
#   NOT_RECEIVED:     PROOF_OF_FULFILLMENT (tracking, or notes/documents), PROOF_OF_REFUND
#   NOT_AS_DESCRIBED: OTHER (notes/documents), PROOF_OF_REFUND
#   UNAUTHORISED:     OTHER (notes/documents), PROOF_OF_FULFILLMENT (tracking required),
#                     PROOF_OF_REFUND
NOTES_TYPE = {
    "MERCHANDISE_OR_SERVICE_NOT_RECEIVED": "PROOF_OF_FULFILLMENT",
    "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED": "OTHER",
    "UNAUTHORISED": "OTHER",
}
TRACKING_OK = {"MERCHANDISE_OR_SERVICE_NOT_RECEIVED", "UNAUTHORISED"}


def evidence_plan(reason: str, requested: list[str], fulfillment: dict | None,
                  note: str) -> list[dict[str, Any]]:
    """The `evidences` list for provide-evidence, using only types PayPal accepts for the reason.
    The note (receipt narrative) rides on the reason's notes-capable type."""
    tracking = None
    if fulfillment and fulfillment.get("tracking_number") and reason in TRACKING_OK:
        tracking = {"tracking_info": [{"carrier_name": fulfillment.get("carrier") or "OTHER",
                                       "tracking_number": fulfillment["tracking_number"]}]}
    notes_type = NOTES_TYPE.get(reason) or (requested[0] if requested else "OTHER")
    if tracking and notes_type == "PROOF_OF_FULFILLMENT":
        return [{"evidence_type": "PROOF_OF_FULFILLMENT", "evidence_info": tracking,
                 "notes": note[:2000]}]
    plan = [{"evidence_type": notes_type, "notes": note[:2000]}]
    if tracking:
        plan.insert(0, {"evidence_type": "PROOF_OF_FULFILLMENT", "evidence_info": tracking,
                        "notes": "Proof of delivery."})
    return plan


def _money(m: dict | None) -> tuple[Decimal | None, str | None]:
    if not m:
        return None, None
    return Decimal(m["value"]), m["currency_code"]


def _parse_time(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def build_packet(
    dispute: dict[str, Any],
    receipt_check: dict[str, Any] | None,
    fulfillment: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    txn = (dispute.get("disputed_transactions") or [{}])[0]
    reason = dispute.get("reason") or "OTHER"
    amount, currency = _money(dispute.get("dispute_amount"))
    gross, _ = _money(txn.get("gross_amount"))
    due = _parse_time(dispute.get("seller_response_due_date"))
    facts = {
        "dispute_id": dispute.get("dispute_id"),
        "reason": reason,
        "reason_label": REASON_LABELS.get(reason, reason.replace("_", " ").capitalize()),
        "stage": dispute.get("dispute_life_cycle_stage"),
        "status": dispute.get("status"),
        "disputed_amount": f"{amount:.2f}" if amount is not None else None,
        "payment_amount": f"{gross:.2f}" if gross is not None else None,
        "currency": currency,
        "due_at": due.isoformat() if due else None,
        "days_left": (due - now).days if due else None,
        "capture_id": txn.get("seller_transaction_id"),
        "invoice_number": txn.get("invoice_number"),
        "receipt_id": txn.get("custom"),
        "buyer": (txn.get("buyer") or {}).get("email"),
        "items": [
            {"sku": i.get("item_id"), "name": i.get("item_name"), "qty": i.get("item_quantity")}
            for i in txn.get("items") or []
        ],
    }
    buyer_messages = [m.get("content", "").strip() for m in dispute.get("messages") or []
                      if m.get("posted_by") == "BUYER"]
    requested = sorted({e.get("evidence_type") for e in dispute.get("evidences") or []
                        if e.get("source") == "REQUESTED_FROM_SELLER" and e.get("evidence_type")})
    available = [link["rel"] for link in dispute.get("links") or []
                 if link.get("rel") in ("provide_evidence", "make_offer", "accept_claim")]

    # --- authorization receipt: present, authentic, and about this payment?
    checks: list[dict[str, Any]] = []
    authorization = None
    receipt_ok = False
    if receipt_check and receipt_check.get("found"):
        r = receipt_check["receipt"]
        sig = bool(receipt_check.get("signature_valid"))
        log_ok = bool((receipt_check.get("gateway_report") or {}).get("valid"))
        same_purchase = r.get("purchase_id") == facts["invoice_number"]
        same_amount = gross is not None and Decimal(r.get("total", "-1")) == gross
        skus_r = {ln.get("sku") for ln in r.get("lines") or []}
        skus_d = {i["sku"] for i in facts["items"] if i.get("sku")}
        same_items = bool(skus_d) and skus_d <= skus_r
        checks = [
            {"name": "Receipt signature (checked by this desk with the gateway's public key)",
             "ok": sig},
            {"name": "Buyer gateway reports its audit log intact and the receipt anchored in it",
             "ok": log_ok},
            {"name": "Receipt is for this payment (purchase id matches the invoice number)",
             "ok": same_purchase},
            {"name": "Receipt total equals the payment amount", "ok": same_amount},
            {"name": "Disputed items are items the buyer's side authorised", "ok": same_items},
        ]
        # The buyer's own device signatures (passkeys), re-checked here from the receipt alone.
        intent_sig = (r.get("intent_mandate") or {}).get("user_signature")
        cart_sig = (r.get("cart_mandate") or {}).get("user_signature")
        if intent_sig:
            ok, _ = verify_user_signature(intent_sig, {"intent_id": r.get("intent_id")})
            checks.append({"name": "The buyer's own device signed the Intent Mandate (passkey)",
                           "ok": ok})
        if cart_sig:
            ok, _ = verify_user_signature(cart_sig, {"cart_hash": r.get("cart_hash"),
                                                     "purchase_id": r.get("purchase_id")})
            checks.append({"name": "The buyer's own device signed this exact cart (passkey)",
                           "ok": ok})
        merchant = receipt_check.get("merchant") or {}
        if merchant.get("present"):
            checks.append({
                "name": f"Prices and lines were signed by the shop (key {merchant.get('key_id')})",
                "ok": bool(merchant.get("valid"))})
            if merchant.get("matches_published_key") is not None:
                checks.append({"name": "That key is the shop's own published key",
                               "ok": bool(merchant["matches_published_key"])})
        ap2 = receipt_check.get("ap2") or {}
        if ap2.get("present"):
            n_ok = sum(c["ok"] for c in ap2.get("checks", []))
            checks.append({"name": f"AP2 v0.2 Checkout and Payment Mandates verify "
                                   f"({n_ok}/{len(ap2.get('checks', []))} dispute checks)",
                           "ok": bool(ap2.get("valid"))})
        witness = receipt_check.get("witness") or {}
        if witness.get("anchored"):
            checks.append({
                "name": f"Witnessed by an outside timestamp authority ({witness.get('tsa_name')}, "
                        f"{(witness.get('gen_time') or '')[:16].replace('T', ' ')} UTC)",
                "ok": bool(witness.get("valid"))})
        receipt_ok = all(c["ok"] for c in checks)
        authorization = {
            "receipt_id": r.get("receipt_id"),
            "user_text": r.get("user_text"),
            "lines": r.get("lines"),
            "total": r.get("total"),
            "currency": r.get("currency"),
            "approved_by_human": r.get("approved_by_human"),
            "approved_by": r.get("approved_by"),
            "issued_at": r.get("issued_at"),
            "intent_id": r.get("intent_id"),
            "verify_url": receipt_check.get("verify_url"),
            "key_id": receipt_check.get("key_id"),
            # Present on receipts from Oct 19 on (format agent-spend-receipt/2).
            "paypal_order_id": r.get("paypal_order_id"),
            "intent_terms": (r.get("intent_mandate") or {}).get("terms"),
            "intent_approved_at": (r.get("intent_mandate") or {}).get("approved_at"),
            "gateway_checks": r.get("gateway_checks"),
            "semantic": (r.get("verdict_summary") or {}).get("semantic"),
            "witness": receipt_check.get("witness"),
            "merchant_signature": receipt_check.get("merchant"),
            "payment_confirmation": receipt_check.get("payment_confirmation"),
            "page_evidence": (r.get("cart_mandate") or {}).get("page_evidence"),
            "ap2": receipt_check.get("ap2"),
            "intent_user_signed": bool(intent_sig),
            "cart_user_signed": bool(cart_sig),
        }
    elif facts["receipt_id"]:
        checks = [{"name": "Authorization receipt retrievable from the buyer's gateway", "ok": False,
                   "detail": (receipt_check or {}).get("error", "not checked")}]

    has_tracking = bool(fulfillment and fulfillment.get("tracking_number"))
    evidence = [
        {"kind": "order_record", "label": "PayPal payment record (amount, items, buyer, time)",
         "have": True},
        {"kind": "authorization_receipt",
         "label": "Buyer-side authorization receipt (signed permission + approved cart)",
         "have": receipt_ok,
         "detail": None if receipt_ok else ("no receipt on this payment" if not facts["receipt_id"]
                                            else "receipt present but a check failed")},
        {"kind": "proof_of_fulfillment", "label": "Proof of delivery (carrier + tracking number)",
         "have": has_tracking, "detail": None if has_tracking else "add tracking to include it"},
    ]

    if available:
        rec = _recommend(reason, receipt_ok, has_tracking, amount, authorization)
        if rec["paypal_call"] and rec["paypal_call"] not in available:
            # Observed Oct 19 (PP-R-FER-10193581, not as described, inquiry stage): PayPal offered
            # refund / accept / message / escalate, but not provide_evidence.
            offered = ", ".join(a.replace("_", " ") for a in available)
            rec = {
                "action": "evidence_not_accepted",
                "paypal_call": None,
                "strength": rec["strength"],
                "why": [
                    *rec["why"],
                    (f"PayPal does not accept evidence on this case at this stage (it offers: "
                    f"{offered}). To contest, escalate the case in PayPal so it becomes a claim; "
                    "otherwise offer a refund."),
                ],
            }
    else:
        # Observed Oct 19 (PP-R-CGO-10193580): the case can go straight to PayPal's own review,
        # with no seller deadline and no response options. Do not suggest an action PayPal refuses.
        rec = {"action": "wait", "paypal_call": None, "strength": "n/a",
               "why": [(f"PayPal is handling this case itself (status {dispute.get('status')}); "
                       "it currently accepts no response from the seller."),
                       "The packet is ready if PayPal asks for evidence later."]}
    narrative = _narrative(facts, authorization, receipt_ok, fulfillment, rec)
    return {
        "version": 1,
        "built_at": now.isoformat(),
        "facts": facts,
        "buyer_messages": buyer_messages,
        "requested_by_paypal": requested,
        "authorization": authorization,
        "checks": checks,
        "evidence": evidence,
        "recommendation": rec,
        "economics": {**INDUSTRY_COST, "disputed_amount": facts["disputed_amount"],
                      "small_claim_threshold_usd": str(SMALL_CLAIM_USD)},
        "narrative": narrative,
        "available_actions": available,
        "allowed_response_options": dispute.get("allowed_response_options") or {},
        # What "contest" would send, shown to the seller before they confirm.
        "evidence_types": [e["evidence_type"] for e in
                           evidence_plan(reason, requested, fulfillment, narrative)],
    }


def _recommend(reason: str, receipt_ok: bool, has_tracking: bool, amount: Decimal | None,
               authorization: dict | None) -> dict[str, Any]:
    small = amount is not None and amount <= SMALL_CLAIM_USD
    who = None
    if authorization:
        who = ("a person approved this exact cart" if authorization.get("approved_by_human")
               else "it was inside the buyer's signed spending permission")
    if reason == "UNAUTHORISED":
        if receipt_ok:
            return {"action": "contest", "paypal_call": "provide_evidence", "strength": "strong",
                    "why": [("The buyer's own payment gateway signed an authorization for this exact "
                            f"cart: {who}."), "The signature and log anchor verify."]}
        return {"action": "refund" if small else "review", "paypal_call":
                "accept_claim" if small else None, "strength": "weak",
                "why": ["No verifiable authorization record for this payment."]}
    if reason == "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED" and receipt_ok:
        return {"action": "contest", "paypal_call": "provide_evidence", "strength": "medium",
                "why": [("The items sold are exactly the items the buyer's side authorised "
                        f"({who})."), ("Authorization does not prove the item matched its "
                        "description; add photos or a description if you have them.")]}
    if reason == "MERCHANDISE_OR_SERVICE_NOT_RECEIVED":
        why = []
        if receipt_ok:
            why.append("The authorization receipt shows the purchase was authorised, but it does "
                       "not prove delivery.")
        if has_tracking:
            return {"action": "contest", "paypal_call": "provide_evidence", "strength": "medium",
                    "why": [*why, "Tracking information is available as proof of fulfillment."]}
        if small:
            return {"action": "refund", "paypal_call": "make_offer", "strength": "n/a",
                    "why": [*why, ("No proof of delivery, and the amount is small: refunding usually "
                            "costs less than contesting (see economics).")]}
        return {"action": "add_fulfillment", "paypal_call": None, "strength": "weak",
                "why": [*why, ("PayPal needs proof of delivery. Add carrier and tracking, then "
                        "contest.")]}
    return {"action": "review", "paypal_call": None, "strength": "unknown",
            "why": ["No automatic recommendation for this reason; review manually."]}


def _narrative(facts: dict, auth: dict | None, receipt_ok: bool, fulfillment: dict | None,
               rec: dict) -> str:
    items = ", ".join(f"{i['qty']} x {i['name']}" + (f" ({i['sku']})" if i.get("sku") else "")
                      for i in facts["items"]) or "n/a"
    parts = [
        (f"Payment {facts['capture_id']} of {facts['payment_amount']} {facts['currency']} "
        f"(invoice {facts['invoice_number']}) for: {items}."),
    ]
    if auth and receipt_ok:
        approver = (f"approved by {auth['approved_by']} for this exact cart"
                    if auth.get("approved_by_human")
                    else "approved automatically inside the buyer's signed spending permission")
        if auth.get("cart_user_signed"):
            approver += ", signed on the buyer's own device (passkey)"
        elif auth.get("intent_user_signed"):
            approver += "; the permission itself was signed on the buyer's own device (passkey)"
        lines = "; ".join(f"{ln['qty']} x {ln['title']}" for ln in auth.get("lines") or [])
        parts.append(
            f"The buyer's payment gateway issued authorization receipt {auth['receipt_id']} on "
            f"{auth['issued_at'][:10]}: the buyer's request was \"{auth['user_text']}\"; the cart "
            f"({lines}; total {auth['total']} {auth['currency']}) was {approver}. The receipt is "
            f"signed (key {auth['key_id']}) and anchored in the gateway's tamper-evident log; it "
            f"can be verified at {auth['verify_url']}."
        )
        sem = auth.get("semantic") or {}
        if sem.get("match"):
            parts.append(
                "Before paying, the gateway checked the cart against the buyer's signed request: "
                f"all spending rules passed and the cart matched the request with probability "
                f"{sem['match'].get('matches', 0):.2f} ({sem.get('model', 'verifier')})."
            )
    if fulfillment and fulfillment.get("tracking_number"):
        parts.append(f"Shipped with {fulfillment.get('carrier', 'carrier')} under tracking number "
                     f"{fulfillment['tracking_number']}.")
    if rec["action"] == "contest":
        parts.append("We ask that the case be decided in the seller's favour.")
    return " ".join(parts)[:2000]
