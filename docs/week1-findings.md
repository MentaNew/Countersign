# Week-1 findings

Fill in as each check runs (see architecture doc §5). Raw responses land in `spikes/out/` (gitignored).

| # | Check | How | Result | Date |
|---|---|---|---|---|
| 1a | Buyer-approved sandbox order works | UI "Pay with PayPal" → sandbox Personal account → `/paypal/return` | **Pass.** Order `83U552177A737101D` (10.80 USD) captured, status COMPLETED; `payment_captured` logged; receipt `rcp_d1a2e481ab34ecca` still valid. The sandbox checkout page also offers a guest card form; use "Log in" with the sandbox account | Oct 17 |
| 1b | Agent can pay without per-order buyer approval (vault) | `spikes/paypal_spikes.py vault` | | |
| 2a | Real dispute can be opened in sandbox | buyer opens dispute, then `disputes` | **Pass, with a catch.** Buyer "Report a problem" on capture `07145137L9934133Y` (order `83U552177A737101D`) created case **`PP-R-ELT-10193319`**: `WAITING_FOR_SELLER_RESPONSE`, `MERCHANDISE_OR_SERVICE_NOT_RECEIVED`, stage `INQUIRY`, 3.50 USD, seller response due 2026-11-06; links offer `provide_evidence`, `make_offer`, `accept_claim`, `send_message`, `escalate`. **`GET /v1/customer/disputes/{id}` works, but the list endpoint returns no items** (default, `start_time`, all states, `disputed_transaction_id`). Intake must use webhooks or fetch-by-id, list only as fallback. Keep this case open for the demo | Oct 17 |
| 2b | Dispute webhook arrives / simulate-event works | `simulate --url …` via tunnel | | |
| 3 | Jev access, endpoint, SDK shape | | | |
| 4a | `custom_id` / `invoice_id` round-trip on order | `order` output | Accepted at order creation (`custom_id` = receipt id); read-back after capture not yet checked | Oct 16 |
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
- Payment path (vault vs approve-link-in-approval-step):
- Dispute source (real sandbox vs labelled simulation):
- Verifier for the demo (Jev vs LLM fallback):
