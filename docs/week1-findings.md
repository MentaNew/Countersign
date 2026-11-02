# Week-1 findings

Fill in as each check runs (see architecture doc §5). Raw responses land in `spikes/out/` (gitignored).

| # | Check | How | Result | Date |
|---|---|---|---|---|
| 1a | Buyer-approved sandbox order works | UI "Pay with PayPal" → sandbox Personal account → `/paypal/return` | **Pass.** Order `83U552177A737101D` (10.80 USD) captured, status COMPLETED; `payment_captured` logged; receipt `rcp_d1a2e481ab34ecca` still valid. The sandbox checkout page also offers a guest card form; use "Log in" with the sandbox account | Oct 17 |
| 1b | Agent can pay without per-order buyer approval (vault) | `spikes/paypal_spikes.py vault` | **Pass.** First order with `store_in_vault` approved once by the sandbox buyer → vault token `3cl0…122v` (`VAULTED`). Second order with only `vault_id`: `COMPLETED` at creation, capture `10896589KD600921U` 9.99 USD, no approval link. `PAYPAL_VAULT_ID` set in `.env`; gateway path not yet re-tested with it | Oct 19 |
| 2a | Real dispute can be opened in sandbox | buyer opens dispute, then `disputes` | **Pass, with a catch.** Buyer "Report a problem" on capture `07145137L9934133Y` (order `83U552177A737101D`) created case **`PP-R-ELT-10193319`**: `WAITING_FOR_SELLER_RESPONSE`, `MERCHANDISE_OR_SERVICE_NOT_RECEIVED`, stage `INQUIRY`, 3.50 USD, seller response due 2026-11-06; links offer `provide_evidence`, `make_offer`, `accept_claim`, `send_message`, `escalate`. **`GET /v1/customer/disputes/{id}` works, but the list endpoint returns no items** (default, `start_time`, all states, `disputed_transaction_id`). Intake must use webhooks or fetch-by-id, list only as fallback. Keep this case open for the demo | Oct 17 |
| 2c | Seller can respond from the desk | dispute desk `/seller` | **Pass (refund offer).** Oct 18: `make_offer` REFUND 3.50 USD on `PP-R-ELT-10193319` accepted; case moved to `UNDER_REVIEW` / `UNDER_PAYPAL_REVIEW`, no further seller actions offered. Earlier `provide_evidence` with `OTHER` refused (`EVIDENCE_TYPE_IS_NOT_ALLOWED`); fixed to use per-reason types, **not yet re-tested live**. This case is now used up for the demo. Oct 19: second live `make_offer` (3.89 USD on `PP-R-CGO-10193580`) also accepted. **Oct 19: `provide_evidence` accepted live** on `PP-R-TMO-10193618` (not received, `PROOF_OF_FULFILLMENT` with notes, per-reason fix confirmed); case moved to PayPal review. That payment came from the vault spike (no receipt), so it also exercised the non-agent path | Oct 19 |
| 2d | A second dispute is answerable by the seller | buyer "Report a problem" on a vaulted payment | **Yes, after a delay (see 2e).** First looked unanswerable: `PP-R-CGO-10193580` (Oct 19, 3.89 of 15.56 USD, reason recorded as `MERCHANDISE_OR_SERVICE_NOT_RECEIVED`) went straight to `UNDER_REVIEW` / `UNDER_PAYPAL_REVIEW`: no seller due date, no response options. Receipt `rcp_a112f3d5e4e81316` linked and verified (5/5 checks). Desk now suggests "wait" in this state. Became answerable a few minutes later (see 2e) | Oct 19 |
| 2e | What makes a sandbox case answerable by the seller | re-check after a delay | **Timing.** New cases start as `OPEN` / `UNDER_PAYPAL_REVIEW` with only a `self` link, then move to `WAITING_FOR_SELLER_RESPONSE` / `REQUIRED_ACTION` a few minutes later (`PP-R-FER-10193581`: created 18:34:12, actionable 18:37:34). My earlier `merchant_contacted` guess was wrong. Wait ~5 min before importing, or click Refresh. `PP-R-FER` (not as described) offers no `provide_evidence`; `PP-R-CGO` (not received) does | Oct 19 |
| 2b | Dispute webhook arrives / simulate-event works | `simulate --url …` via tunnel | | |
| 3 | Jev access, endpoint, SDK shape | | | |
| 4a | `custom_id` / `invoice_id` round-trip on order | `order` output | **Pass.** Accepted at order creation; after capture they are still on the capture (`custom_id`, `invoice_id`) and the dispute (`custom`, `invoice_number`), which is how the desk finds the receipt | Oct 18 |
| 4b | Authorization can be voided | `void` | | |
| 5 | Agent Toolkit / MCP tool names (for the deny-list hook) | read toolkit README | | |
| 6 | Devpost rules: weights, sponsor-prize conditions, video/repo rules | read rules page | | |

## Other findings (Oct 16)
- OAuth scopes on the sandbox app include payment-tokens/vault, disputes (read-seller, update-seller), invoicing, webhooks. Transaction Search (reporting) is not enabled.
- `PAYPAL_BASE_URL` must be the API host `https://api-m.sandbox.paypal.com`, not `sandbox.paypal.com`.
- Orders created without a vault id return `PAYER_ACTION_REQUIRED` with a `payer-action` link.
- GroqCloud: `openai/gpt-oss-20b` honours strict `json_schema`. `llama-prompt-guard-2-86m` returns a bare probability string and scored an agent-directed hidden instruction at 0.0016, so it is combined with an LLM check.
- A real Claude agent (Agent SDK) refused the planted injection on its own; the demo needs a scripted "compromised agent" path to show the gateway catching it.

## Decisions that follow
- Payment path (vault vs approve-link-in-approval-step): **vault** (Oct 19). Approved carts are paid without a click; the "Pay with PayPal" button remains the fallback when `PAYPAL_VAULT_ID` is empty.
- Dispute source (real sandbox vs labelled simulation):
- Verifier for the demo (Jev vs LLM fallback):
