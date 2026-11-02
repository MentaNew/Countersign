# Next steps

**Nov 1:** security and interface pass done (sessions, passkey sign-in, headers, notifications, setup checklist, repeat-order rule, raw model replies on live decisions, cancel a permission). **Your list: `docs/your-todo.md`.**

Status on **October 18, 2026** (submission deadline **November 12, 2026**, 25 days). Design: `Agent spend guardrail plan and architecture.md`. How to run: `docs/run-locally.md`. Week-1 results: `docs/week1-findings.md`.

## Current status (Oct 21)

P1.1 passkeys, P1.2 outside witness, P1.3 shop-signed carts, P1.4 AP2 v0.2 subset and P1.5 fail-closed + per-agent keys + kill switch are **done** (details in `docs/production-plan.md`). **P1.6 hosting in progress (free Render, user's choice)**: code, `render.yaml`, `scripts/render_secrets.py` and the guide `docs/deploy-render.md` are written; still to do: full test run (blocked Oct 21: disk full broke Docker), then you push and create the Blueprint, then the PayPal webhook. P2.1 checkout guard for browser agents **done Oct 21** (gateway page carts + `checkout_guard/` + demo shop `demo/webshop`; 92 tests, guard e2e 12/12). P2.3 payment confirmation **done Oct 22** (96 tests, guard e2e 14/14). P2.4 eval done Oct 22 (154 cases, 0/110 unsafe approvals; see `docs/eval.md`). P3.2 is ready (`docs/live-purchase.md`) but on hold until the user decides to spend real money. Then: P3.2 a real purchase on a Shopify shop that accepts PayPal. Tests: 78 pytest; browser e2e `tests/e2e/browser_flow.py` 19/19 (reset the test DB first with `--reset-db`).

Your to-do: your `.env` still has the placeholder `AGENT_API_KEY=change-me-agent`. Either set a long random value there (the old key stops working on the next start), or create agents on the new **Agents** page and revoke "default".

## New direction (Oct 20): production-grade and a real purchase

The project owner raised the bar: a gateway they would personally trust, proven by an agent buying something real with a real PayPal account. The plan, phases P1–P3, cut list and decisions are in **`docs/production-plan.md`**; it supersedes the remaining build items below (webhook tunnel becomes part of P1.6 hosting). Not started yet.

## Where we are

The whole demo story works end to end: allow, block, injection, escalate, signed receipt, and a dispute answered from the seller side. What is left: three demo gaps, proof for the judges, then polish and recording.

| Area | State |
|---|---|
| Gateway (buyer side) | Signed intents, catalog-priced carts, 10 hard checks, decision rules, cart-hash-bound approvals with re-check, PayPal Orders v2 + capture, signed receipts, hash-chained log, MCP tools (agent), REST (user). Tables in the `gateway` schema |
| Verifier | GroqCloud: `gpt-oss-20b` (typed JSON) for cart match; injection = max(Prompt Guard 2, `gpt-oss-20b`). Offline `stub` mode |
| Buyer UI | Inbox, permission review, line-item cart approval, audit log with chain badge, public receipt page |
| Dispute desk (seller side) | Separate service on :8200, own `desk` schema and `/seller` screens; fetch-by-id + webhook intake; verifies receipts via the gateway's public endpoint; per-reason evidence types; refund offer / accept / contest after human review |
| Claude adapter | `guarded-purchase` skill, guard hooks, Agent SDK runner, `demo/agent-workspace` |
| Verified live | Sandbox orders; buyer payment + capture (Oct 17); real sandbox dispute; refund offer from the desk accepted (Oct 18); Groq catches A4 vs Letter; real Claude agent run (Oct 16) |
| Tests | 46 pytest, no network |
| Evaluation | 38 labelled carts; GroqCloud verifier 38/38, 0 unsafe approvals, 0 false alarms (`docs/eval.md`, cached, reproducible without a key) |

## Now: you, in a browser (Oct 18–21)

1. ~~**Vault check**~~ **passed Oct 19**: no-click payment works; `PAYPAL_VAULT_ID` is set. Still to do: one purchase through the gateway with the vault (see your test list). Original step: Enable *Vault* on the sandbox app, then `uv run python spikes/paypal_spikes.py vault`; approve with the sandbox Personal account (use "Log in", not the card form). If the second order completes without a link, put the vault id in `PAYPAL_VAULT_ID`. Decides whether the demo payment needs a click.
2. **Disputes (Oct 19):** `PP-R-CGO-10193580` used for a second live refund offer (works). `PP-R-FER-10193581` (not as described, refund/accept only) kept for the recording. New sandbox cases need ~5 min before the seller can respond. Original step: **Fresh dispute for the recording**, ideally reason **"unauthorized"**. Steps: run the gateway with `VERIFIER=llm`, make an in-policy purchase, pay it as the sandbox buyer, then open the dispute from sandbox.paypal.com. (`PP-R-ELT-10193319` is used up.) Send me the case id; do not respond to it.
3. **Devpost rules page**: judging weights, video and repo requirements, sponsor-prize conditions.
4. *(Optional)* **Jev access** request.

## Now: build (in this order)

1. ~~**Compromised-agent demo step.**~~ **Done Oct 18** (`demo/compromised_agent.py`; verifier now names mismatched lines, shown in red; verified live with GroqCloud, 5 calls). Claude refuses the poisoned listing, so the video cannot rely on the agent being tricked. A scripted agent that falls for it (adds the gift card, passes the poisoned text) shows the gateway blocking it on screen. Pitch line: *a good agent helps; the gateway does not depend on it.*
2. ~~**Evaluation set + runner**~~ **Done Oct 18**: 38 cases, results in `docs/eval.md` (GroqCloud 38/38 after one injection-prompt fix; offline stub 27/38, misses all correct-but-wrong cases). Original plan: (`eval/run_eval.py`): 30–50 labelled carts (correct, hard fail, correct-but-wrong, extra item, injection, benign text), precision/recall per decision, thresholds shown, Groq answers cached so re-runs cost nothing. Proof for "Technological Implementation".
3. ~~**Live test of "contest"**~~ **Done Oct 19** (`PP-R-TMO-10193618`, evidence accepted). Original: (`provide_evidence` with the per-reason types) on the fresh dispute.
4. **Webhook path**: `cloudflared` tunnel, webhook registered on the app (`PAYPAL_WEBHOOK_ID`), so disputes arrive without pasting the id.
5. ~~**AP2 naming**~~ **Done Oct 19**: receipt format 2 (`intent_mandate`, `cart_mandate`, `gateway_checks`), four-step proof panel on the seller and receipt pages, `docs/dispute-playbook.md`. Original:: label the signed permission and approved cart as Intent Mandate / Cart Mandate in the receipt and docs.

Smaller items if time allows: attach a PDF of the packet to `provide_evidence`; intent revocation; approval-expiry sweep; capture status from `PAYMENT.CAPTURE.COMPLETED` webhooks; Alembic migrations; Jev verifier when access arrives (compare with Groq on the eval set).

## Submission (Nov 5–9), then buffer (Nov 10–12)

- README: problem, architecture diagram, "real vs simulated" table, tools-used statement (GroqCloud models, Claude Agent SDK, PayPal Orders and Disputes, Jev if used).
- Fresh-clone test using only `docs/run-locally.md`.
- Video under 3 minutes following the beat sheet in `docs/manual-test-plan.md`; public on YouTube.
- Public repo with the license visible (you push).
- Stretch, only if ahead: Render hosting → AG Grid fleet view → LangChain adapter.

## Known limits to state honestly

- LLM fallback probabilities are self-reported, not calibrated; Prompt Guard alone missed agent-directed text (0.0016).
- The sandbox has no real adjudication: show the dispute workflow and time saved, not win rates.
- The authorization receipt proves what the buyer's side authorised, not delivery; for "not received" disputes it is supporting evidence only.
- In the sandbox one PayPal business account plays every seller (production design: architecture doc 3.2).
- `SELLER_PASSWORD` in the local `.env` is still the template value; set a real one before hosting anything.
