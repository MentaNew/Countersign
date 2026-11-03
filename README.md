# Agent Spend Guardrail + Dispute Desk

A payment gateway for AI agents. The user approves a signed permission ("intent"); the agent can only pay through the gateway, which prices the cart from the merchant catalog, runs deterministic policy checks and a typed semantic check of the cart against the intent, asks for line-item approval when needed, creates the PayPal order itself and writes a tamper-evident log. The log yields a verifiable authorization receipt that a dispute desk uses to build evidence packets.

**A fleet of agents, each in its own lane.** Several agents can shop for the same person at the same time (say, one restocking office paper, another booking travel), and each one has its own key, its own permissions, its own spending cap and its own off switch. An agent can only use the permissions approved for it, so a hacked or confused agent cannot spend another agent's budget. Every cart and receipt names the agent that asked for it. One click on **Stop all agents** cuts the whole fleet off on its next call.

Built for the "Build What's Next with PayPal and AI" hackathon. Design: `Agent spend guardrail plan and architecture.md`. Status and roadmap: `docs/next-steps.md`.

## Run it locally

Step-by-step guide with checks and troubleshooting: **`docs/run-locally.md`** (four elements for the buyer side, a fifth for the seller side). Short version:

Prerequisites: Docker, [uv](https://docs.astral.sh/uv/), Node 20+, a PayPal developer sandbox app, a GroqCloud API key (or `VERIFIER=stub` to run offline).

```bash
cp .env.example .env              # fill in PayPal sandbox + GROQ_API_KEY, set AGENT_API_KEY and USER_PASSWORD
docker compose up -d              # Postgres on localhost:5433
uv sync --extra agent --extra guard   # guard: checkout guard (uses your installed Google Chrome)
(cd web && npm install)

uv run uvicorn demo.merchant.app:app --port 8100    # demo merchants
uv run uvicorn gateway.main:app --port 8000         # buyer side: MCP at /mcp, REST at /api
uv run uvicorn dispute_desk.app:app --port 8200     # seller side: dispute desk + PayPal webhook
(cd web && npm run dev)                             # http://localhost:5173 (buyer) and /seller (seller)
```

Drive it:

```bash
uv run python demo/smoke.py                         # scripted agent + user: allow, wrong variant, injection
uv run python adapters/claude/run_agent.py "Restock 10 reams of A4 paper, max 60 USD, ask me above 40"
# or interactively: cd demo/agent-workspace && export AGENT_API_KEY=... && claude
```

Hands-on walkthrough: `docs/manual-test-plan.md`. Tests: `uv run pytest` (needs the Postgres container; no network calls).

## How trust is split

| | Agent (untrusted) | Gateway (trust anchor) | User, in the web app |
|---|---|---|---|
| Door | `/mcp` | — | `/api` (and `/paypal/return` when coming back from PayPal) |
| Credential | its own agent key (Agents page; `AGENT_API_KEY` is the "default" agent) | PayPal secret, Ed25519 signing key | a sign-in session (HttpOnly cookie; password or passkey) plus a passkey for every approval |
| Can | propose an intent, request a cart, read status | sign intents/receipts, price carts, decide, create PayPal orders, log | approve or reject intents and held carts |
| Cannot | approve anything, see PayPal links or credentials | — | — |

Approvals are **signed on the user's own device with a passkey** (Touch ID / Face ID), so the gateway cannot approve anything in the user's name. The audit log is **witnessed every few minutes by a public timestamp authority** (DigiCert, RFC 3161), so not even the gateway's operator can rewrite history without it showing. Each shop **signs its cart offer** (titles and prices) with its own key, which the gateway pins. Every receipt carries an **Intent Mandate** (what the buyer allowed), a **Cart Mandate** (the exact cart approved) and the **gateway's checks**, plus **AP2 v0.2 Checkout and Payment Mandates** (SD-JWTs, the shop's ES256 Checkout JWT, AP2 dispute verification; focused subset); the seller's dispute desk shows them as a four-step story. What a seller and PayPal can do for each dispute reason: `docs/dispute-playbook.md`.

Each agent has **its own key and optional 30-day spending cap**; you can revoke one agent or press **Stop all agents**, effective on its next call (Agents page). If a model check fails or times out, the cart is held for you, never approved.

Sign-in sessions use a random token stored only as a hash, in an HttpOnly SameSite=Strict cookie, with an app header required on every change; they end after 2 hours idle or 12 hours at most, and the Security page lists them with "end" and "sign out everywhere else". The seller desk has its own sessions. Every live decision keeps the AI checks' exact requests and replies (their hash is in the audit log), shown word for word on the cart page.

The web app (`localhost:5173`) is the human's screen, not the agent. Diagram: section 3.1 of the architecture doc. Guard hooks for Claude Code / Agent SDK (`adapters/claude/guard.py`) keep the agent on this path, but the gateway does not rely on them.

## Agents that shop on ordinary websites

Most shops do not sign carts or know about agents. For those, a **checkout guard** (`checkout_guard/`) gives the agent a browser it drives through tools, while the guard keeps the agent's key and the payment step:

- payment pages (PayPal, Stripe, Shop Pay…) are blocked until you approve the exact cart;
- the guard reads the cart, shipping, tax and total from the shop itself (Shopify-style carts), with a screenshot, never from the agent;
- such carts always need your passkey, whatever the amount; the gateway then signs a 20-minute **checkout permit** for that cart only, and any change to the cart locks payment again;
- card, bank and password fields are refused; the browser identifies itself as an AI shopping agent;
- after you pay in PayPal, the guard reports the order and the gateway records a signed payment confirmation on the receipt.

Try it on the demo web shop (`demo/webshop`, http://greenleaf.localhost:8300) or a real one (`docs/live-purchase.md`).

## How well the checks work

167 labelled carts (`docs/eval.md`), including 55 **held out** (written before any run and never used for tuning): wrong variants, disguised extras, subscriptions, pack sizes, and hidden instructions in other languages, fake system messages, base64 and role play. With the GroqCloud verifier: **0 of 114 carts that should have been stopped were paid without asking** (95% CI 0–3%); 6 of 53 good carts were held for a tap. Pack sizes ("12 Pack", "48 Count (Pack of 2)") are counted in code, not left to the model. Answers are cached, so the numbers reproduce without an API key.

## Real vs simulated

| Part | Real | Simulated / limits |
|---|---|---|
| PayPal | Orders v2 (create, capture), Disputes API (read, refund offer, evidence) called live in the sandbox; webhook signature verification | Sandbox money; one sandbox business account plays every seller |
| Shops | Real Shopify-style checkouts supported by the checkout guard (`docs/live-purchase.md`) | Demo merchants and the Greenleaf web shop are fictional |
| AI checks | GroqCloud `openai/gpt-oss-20b` and `meta-llama/llama-prompt-guard-2-86m`; probabilities are self-reported, not calibrated | Offline word-overlap stub for tests |
| Agent | Claude (Claude Code / Agent SDK) through MCP | The "fooled agent" demo is scripted: a real Claude refuses the planted injection |
| Approvals | WebAuthn passkeys (Touch ID / Face ID) | Tests use Chrome's virtual authenticator |
| Outside witness | RFC 3161 timestamps from DigiCert and FreeTSA | — |
| AP2 | v0.2 Checkout and Payment Mandates (SD-JWT, ES256), focused subset | Not full protocol compliance |
| Evaluation | 167 cases, 55 held out, Wilson intervals | Written by the project author |

## Layout

| Path | What |
|---|---|
| `gateway/` | MCP tools, REST API, policy checks, verifier (GroqCloud / stub; Jev planned), PayPal client, ledger, signing |
| `web/` | React UI. Buyer: inbox, permission review, line-item cart approval, audit log, receipt verification. Seller (`/seller`): dispute list and case page |
| `adapters/claude/` | `guarded-purchase` skill, guard hooks, Agent SDK runner |
| `demo/` | Fictional merchants (one listing has a planted injection), agent workspace, smoke script, fooled-agent script |
| `dispute_desk/` | Seller-side service: dispute intake (fetch by id, webhook), evidence packets, PayPal responses after human review. Own `desk` schema; reads buyer receipts only through the gateway's public endpoint |
| `checkout_guard/` | Guarded browser for agents on ordinary shops (stdio MCP server, Playwright + Chrome) |
| `eval/` | Labelled carts (`cases/` development, `heldout/` never tuned on), runner, cached model replies, viewer `show_case.py` |
| `demo/webshop/`, `demo/browser-agent-workspace/` | Ordinary demo shop for browser agents; Claude workspace that only has the guard's tools |
| `render.yaml`, `scripts/` | Free Render deployment (`docs/deploy-render.md`) and secret generation |
| `spikes/` | Week-1 PayPal sandbox checks |

## License

MIT, see `LICENSE`.
