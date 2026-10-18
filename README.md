# Agent Spend Guardrail + Dispute Desk

A payment gateway for AI agents. The user approves a signed permission ("intent"); the agent can only pay through the gateway, which prices the cart from the merchant catalog, runs deterministic policy checks and a typed semantic check of the cart against the intent, asks for line-item approval when needed, creates the PayPal order itself and writes a tamper-evident log. The log yields a verifiable authorization receipt that a dispute desk uses to build evidence packets.

Built for the "Build What's Next with PayPal and AI" hackathon. Design: `Agent spend guardrail plan and architecture.md`. Status and roadmap: `docs/next-steps.md`.

## Run it locally

Step-by-step guide with checks and troubleshooting: **`docs/run-locally.md`** (four elements for the buyer side, a fifth for the seller side). Short version:

Prerequisites: Docker, [uv](https://docs.astral.sh/uv/), Node 20+, a PayPal developer sandbox app, a GroqCloud API key (or `VERIFIER=stub` to run offline).

```bash
cp .env.example .env              # fill in PayPal sandbox + GROQ_API_KEY, set AGENT_API_KEY and USER_PASSWORD
docker compose up -d              # Postgres on localhost:5433
uv sync --extra agent
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
| Credential | `AGENT_API_KEY` | PayPal secret, Ed25519 signing key | login token from `USER_PASSWORD` |
| Can | propose an intent, request a cart, read status | sign intents/receipts, price carts, decide, create PayPal orders, log | approve or reject intents and held carts |
| Cannot | approve anything, see PayPal links or credentials | — | — |

The web app (`localhost:5173`) is the human's screen, not the agent. Diagram: section 3.1 of the architecture doc. Guard hooks for Claude Code / Agent SDK (`adapters/claude/guard.py`) keep the agent on this path, but the gateway does not rely on them.

## Layout

| Path | What |
|---|---|
| `gateway/` | MCP tools, REST API, policy checks, verifier (GroqCloud / stub; Jev planned), PayPal client, ledger, signing |
| `web/` | React UI. Buyer: inbox, permission review, line-item cart approval, audit log, receipt verification. Seller (`/seller`): dispute list and case page |
| `adapters/claude/` | `guarded-purchase` skill, guard hooks, Agent SDK runner |
| `demo/` | Fictional merchants (one listing has a planted injection), agent workspace, smoke script |
| `dispute_desk/` | Seller-side service: dispute intake (fetch by id, webhook), evidence packets, PayPal responses after human review. Own `desk` schema; reads buyer receipts only through the gateway's public endpoint |
| `eval/` | Labelled intent/cart cases |
| `spikes/` | Week-1 PayPal sandbox checks |

## License

MIT, see `LICENSE`.
