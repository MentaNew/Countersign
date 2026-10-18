# Next steps

Status on **October 17, 2026** (submission deadline **November 12, 2026**). Phase numbers follow the build plan; design: `Agent spend guardrail plan and architecture.md`.

## Done

| Area | State |
|---|---|
| Gateway core (Phase 1) | Signed intents, catalog-priced carts, 10 hard checks, decision rules, cart-hash-bound approvals with re-check, PayPal Orders v2, signed receipts, hash-chained log, MCP tools for the agent, REST for the user |
| Verifier | GroqCloud fallback: `gpt-oss-20b` (typed JSON) for cart match; injection = max(Prompt Guard 2, `gpt-oss-20b`). Offline `stub` mode |
| Approval UI | Inbox, permission review, line-item cart approval, audit log with chain badge, public receipt verification. Light/dark, phone width |
| Claude adapter | `guarded-purchase` skill, guard hooks (Claude Code command hook + Agent SDK callbacks), `demo/agent-workspace`, `adapters/claude/run_agent.py` |
| Verified live | PayPal sandbox orders created; Groq verifier catches A4 vs Letter; real Claude agent bought in-policy paper end to end (Oct 16) |
| Tests | 26 pytest (policy, signing, ledger tamper, full flow, HTTP auth split, guard hooks). No network |

## Decided Oct 17 (see architecture doc 3.2)

- Production: PayPal runs the gateway as a wallet feature; each seller's own checkout creates the order; the gateway releases the buyer's saved consent only for approved carts. Hackathon: open-source server, one sandbox account plays every seller.
- Buyer side (gateway) and seller side (dispute desk) are separate services with separate schemas.
- Align naming with AP2 (Intent Mandate / Cart Mandate) in docs and receipt.

## Open from Week 1 (needs you in a browser)

1. **Vault / autonomous payment** (check 1b): `uv run python spikes/paypal_spikes.py vault`. If it works, put the vault id in `PAYPAL_VAULT_ID` and orders complete without the buyer redirect. If not, keep the "Pay with PayPal" step on the purchase page and say so in the pitch.
2. ~~Buyer approval → capture (1a)~~ done Oct 17. ~~Sandbox disputes (2a)~~ done Oct 17: case `PP-R-ELT-10193319`, readable by id only.
3. **Jev access** (check 3), **Agent Toolkit tool names** (check 5), **Devpost rules** (check 6).

## Phase 2 (Oct 29 – Nov 4): in priority order

1. **"Fooled agent" demo path.** Claude refused the planted injection on its own, so the demo cannot rely on the agent being tricked. Add a scripted "compromised agent" scenario (raw MCP calls that add the gift card and pass the poisoned text, as in `demo/smoke.py` scenario 3) and show the gateway catching it. Pitch line: *a good agent helps; the gateway does not depend on it*.
2. ~~**Dispute desk**~~ **built Oct 18** (see below for what is left), as a separate **seller-side** service (`dispute_desk/`, port 8200, own `desk` schema, own seller screens). Intake: webhook (`/webhooks/paypal`) and fetch-by-case-id (the list endpoint misses real cases). Gets the buyer's proof only via the public receipt endpoint, using the order's `custom_id`. Packet builder, fight-or-refund economics (industry figures labelled), human approval, `provide-evidence` / `make-offer`. Demo case: `PP-R-ELT-10193319` (due Nov 6, leave open).
3. **Eval runner** (`eval/run_eval.py`): add `item` indices to `eval/cases/seed.yaml`, run cases through `decide()` with the Groq verifier, report precision/recall per decision and thresholds; grow to 30–50 cases. Cache verifier outputs to keep Groq calls low.
4. **Jev verifier** behind the same interface, when access arrives; compare with Groq on the eval set.
5. **Webhook tunnel** for local dev (`cloudflared tunnel --url http://localhost:8000`) and PayPal webhook registration.
6. Small gateway items: intent revocation, approval-expiry sweep, capture status from webhooks (`PAYMENT.CAPTURE.COMPLETED`), Alembic migration once the schema settles.

### Dispute desk: what is left
- **Real submission untested.** `provide_evidence` / `make_offer` / `accept_claim` are built and tested against a fake PayPal only. Test on a *second* sandbox dispute before the demo; keep `PP-R-ELT-10193319` for the recording.
- **Webhook untested live:** needs a tunnel and a webhook registered on the app (`PAYPAL_WEBHOOK_ID`); `simulate-event` works with `DESK_ALLOW_UNVERIFIED_WEBHOOKS=true` (labelled "simulated").
- **Attach a document** (PDF of the packet) to `provide_evidence`; today the evidence is the note text.
- **Demo case caveat:** the paid order was approved by the *stub* verifier, so the receipt shows the buyer asked for A4 while Letter was authorised. For the video, use an "unauthorized" dispute on an order approved with the real verifier, where the receipt is the strongest evidence.

## Phase 3 (Nov 5 – 9): submission

- README: problem, architecture diagram, quickstart, "real vs simulated" table, tools-used statement (GroqCloud models, Claude Agent SDK, PayPal Orders/Disputes, Jev if used).
- Fresh-clone run test; record the < 3 min video (terminal agent + browser side by side, see `docs/manual-test-plan.md`).
- Stretch, only if ahead: Render deploy → AG Grid log/fleet view → LangChain adapter.

## Known limits to state honestly

- LLM fallback probabilities are self-reported, not calibrated; Prompt Guard alone missed agent-directed text (0.0016).
- The sandbox has no real adjudication: show dispute workflow and time saved, not win rates.
- PayPal's evidence types are narrow; the receipt goes in as supporting evidence (`OTHER`).
