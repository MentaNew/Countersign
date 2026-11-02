# Dispute playbook: who can do what, for every dispute

What a buyer can claim on a PayPal payment, what the seller and PayPal can do at each step, and what the buyer's payment gateway (this project) adds as evidence. Built from PayPal's developer documentation (sources at the end, read October 19, 2026) and from real sandbox cases we ran (October 17–19). Where PayPal's pages say nothing, this file says so instead of guessing.

## 1. How a dispute moves

```
 Buyer reports a problem
          │
          ▼
 ┌─────────────────┐  not settled in 20 days, or    ┌──────────────────────┐   seller appeals   ┌────────────────┐
 │ INQUIRY         │  either side escalates          │ CLAIM ("CHARGEBACK") │ ─────────────────► │ PRE_ARBITRATION │──► ARBITRATION
 │ buyer ↔ seller  │ ──────────────────────────────► │ PayPal decides       │  (if lost)         │ 1st appeal      │    2nd appeal
 └─────────────────┘                                 └──────────────────────┘                    └────────────────┘
          │                                                     │
          └────────────── settled (refund, offer accepted, ───┴──► RESOLVED
                           buyer cancels, PayPal decides)
```

- **Inquiry** (stage `INQUIRY`): buyer and seller try to settle it themselves for up to **20 days**. PayPal's docs say inquiries only cover *item not received* and *not as described*.
- **Claim** (stage `CHARGEBACK`; a PayPal stage, not a card chargeback): PayPal investigates and decides for the buyer or the seller.
- **Appeals** (`PRE_ARBITRATION`, then `ARBITRATION`): only the **seller** can appeal a decision it lost. If it does not appeal in time, the case stays resolved.

### Status → who can act

| Status | Seller can | Buyer can | PayPal |
|---|---|---|---|
| `OPEN` | send a message, make an offer, accept the claim, escalate | send a message, escalate, cancel, change the reason; accept or deny a seller offer | waits |
| `WAITING_FOR_SELLER_RESPONSE` | **provide evidence**, accept the claim, acknowledge a returned item | — | has asked the seller for something (observed: proof of fulfillment) |
| `WAITING_FOR_BUYER_RESPONSE` | — | provide evidence, cancel | has asked the buyer for something |
| `UNDER_REVIEW` | nothing | nothing | reviewing, will decide |
| `RESOLVED` | appeal, if lost and within the appeal period | — | decision made and communicated |

The exact actions on a given case are the links PayPal returns with it. The dispute desk only ever offers those (`dispute_desk/packet.py`).

### What PayPal itself can do

| Action | Source |
|---|---|
| Ask either side for evidence (puts the case in `WAITING_FOR_*_RESPONSE`) | docs + observed: PayPal requested `PROOF_OF_FULFILLMENT` from the seller ~3 minutes after a case opened |
| Hold the disputed amount from the seller's balance | observed: `fund_movements` showed a `HOLD_PLACED` debit of 3.50 USD on the seller |
| Review the case itself (`UNDER_PAYPAL_REVIEW`) with no seller actions | observed: every new sandbox case starts there for a few minutes; cases also return there after the seller responds |
| Decide for the buyer or the seller and settle | docs |
| Ask for the item to be returned | docs mention only the seller's "acknowledge return item" action; no rules given |

Not stated in the pages we read: the exact consequence when the seller lets the response date pass, the length of appeal periods, and the claim-stage timeline.

## 2. Every reason a buyer can give

"Evidence PayPal accepts" is the official list per reason; sending another type is refused (`EVIDENCE_TYPE_IS_NOT_ALLOWED`, which we hit on October 18). "Gateway receipt" says how much the buyer's authorization receipt helps the seller.

| Buyer's claim (`reason`) | Evidence PayPal accepts | Seller's realistic options | Gateway receipt | Desk suggestion today |
|---|---|---|---|---|
| **I did not authorise this** (`UNAUTHORISED`) | `OTHER` (notes / documents), `PROOF_OF_FULFILLMENT` (carrier + tracking **required**), `PROOF_OF_REFUND` | Contest with the receipt; refund if no record | **Strong. The core case for agent purchases**: the buyer's own gateway signed an Intent Mandate, checked the cart against it, and approved this exact cart before paying | Contest (`OTHER`, + tracking if saved) |
| **Not as described** (`MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED`) | `OTHER` (description, item URL, return policy, contract), `PROOF_OF_REFUND` | Show the item matches its listing; offer a refund; accept a return | **Medium**: proves the buyer's side approved exactly this item and variant (for example US Letter, not A4); does not prove the item's condition | Contest, *if PayPal offers evidence*; at inquiry stage it may only offer refund / accept / message / escalate (observed Oct 19), then the desk says so |
| **Not received** (`MERCHANDISE_OR_SERVICE_NOT_RECEIVED`) | `PROOF_OF_FULFILLMENT` (tracking, or notes / documents), `PROOF_OF_REFUND` | Send tracking; refund if none | **Supporting only**: proves the purchase was authorised, not that it arrived | Refund if small and no tracking; contest once tracking is saved |
| **Wrong amount** (`INCORRECT_AMOUNT`) | `PROOF_OF_REFUND` (for the difference), `OTHER` | Show the charged amount was agreed; refund the difference | **Strong**: the Cart Mandate states the exact approved total and lines | Review manually (not automated yet) |
| **Charged twice** (`DUPLICATE_TRANSACTION`) | `PROOF_OF_REFUND`, `OTHER` | Show two separate orders; refund the duplicate | **Useful**: each receipt is bound to one cart hash and one purchase id, so two receipts mean two separately authorised carts | Review manually (not automated yet) |
| **Refund not received** (`CREDIT_NOT_PROCESSED`) | `PROOF_OF_REFUND`, `OTHER` (why no credit is due) | Show the refund id or explain | None | Review manually |
| **Paid another way** (`PAYMENT_BY_OTHER_MEANS`) | `PROOF_OF_REFUND`, `OTHER` | Show only one payment exists | Weak | Review manually |
| **Subscription was cancelled** (`CANCELED_RECURRING_BILLING`) | `PROOF_OF_REFUND`, `OTHER` (subscription agreement) | Show the agreement was active | **Medium, future**: an Intent Mandate's expiry and revocation would show whether the permission was still live | Review manually |
| **Other** (`OTHER`) | `PROOF_OF_REFUND`, `OTHER` | Depends | Depends | Review manually |

## 3. What the seller can send from the desk

| Seller action | PayPal action | In the desk | Tested live |
|---|---|---|---|
| Contest with evidence | `provide-evidence` | yes; evidence type chosen per reason, tracking attached when saved | yes, Oct 19 (`PP-R-TMO-10193618`, accepted) |
| Offer a refund | `make-offer` (type `REFUND`) | yes | yes, Oct 18 and 19 (accepted twice) |
| Accept the claim | `accept-claim` | yes | no |
| Message the buyer | `send-message` | not yet | — |
| Escalate to a claim | `escalate` | not yet (do it in PayPal) | — |
| Acknowledge a returned item | `acknowledge-return-item` | not yet | — |
| Appeal a lost claim | pre-arbitration / arbitration | not yet | — |

## 4. Where the gateway changes the outcome

Without the gateway, a seller facing "my assistant bought this without asking me" has a payment record and nothing that shows what the buyer actually allowed. With it, the desk fetches, from the buyer's gateway, a receipt the gateway signed **before** paying:

1. **Intent Mandate**: the buyer's own words and the limits they signed (items, budget, merchants, ask-me amount, expiry).
2. **Gateway checks**: every spending rule that passed, the cart-to-request match score, and the injection screen.
3. **Cart Mandate**: the exact lines and total approved, and whether a person approved this exact cart or the policy did.
4. **Proof**: the PayPal order, the signature (checked by the desk with the gateway's public key), and the anchor in a tamper-evident log.

The seller sees only this receipt (found through the receipt id PayPal carries on the payment), never the buyer's other data. The desk shows these four steps on every case that has a receipt.

## 5. Real sandbox cases (October 17–19)

| Case | Reason | What happened | Lesson |
|---|---|---|---|
| `PP-R-ELT-10193319` | not received (buyer wrote "asked for A4 and received US Letter") | `provide_evidence` with `OTHER` refused; refund offer 3.50 USD accepted → PayPal review | Evidence types are per reason |
| `PP-R-CGO-10193580` | not received | Started under PayPal review, answerable a few minutes later; refund offer 3.89 USD accepted | Wait ~5 min before acting on a new case |
| `PP-R-FER-10193581` | not as described | Offered refund / accept / message / escalate, but **no** evidence at inquiry stage | Desk must not suggest an action PayPal does not offer. Kept untouched for the recording |
| `PP-R-TMO-10193618` | not received (vault test payment, no receipt) | Evidence (`PROOF_OF_FULFILLMENT` + notes) accepted → PayPal review | Contest path works; non-agent payments work without a receipt |

The sandbox does not adjudicate like production, so these show the workflow, not win rates.

## Sources

- [PayPal: dispute reasons and evidence](https://developer.paypal.com/disputes/reasons-evidence)
- [PayPal: disputes lifecycle stages reference](https://developer.paypal.com/disputes/disputes-lifecycle)
- [PayPal: use the Disputes API](https://developer.paypal.com/platforms/disputes/handle-disputes/use-disputes-api)
- [PayPal: Disputes API reference](https://developer.paypal.com/docs/api/customer-disputes/v1/)
- Our sandbox results: `docs/week1-findings.md` (checks 2a–2e)
