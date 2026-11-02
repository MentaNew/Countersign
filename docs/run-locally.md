# Run the project locally

Four things run the buyer side. A fifth, the dispute desk, is only needed for the seller screens (`/seller`). Each server keeps its terminal busy, so **each one needs its own terminal tab**. Run every command from the repo root unless it says `cd`.

| # | Element | Port | Needed for | Command (own terminal) |
|---|---|---|---|---|
| 1 | Database (Postgres in Docker) | 5433 | everything | `docker compose up -d` (returns; no tab kept busy) |
| 2 | Demo merchant (fictional shops) | 8100 | buying | `uv run uvicorn demo.merchant.app:app --port 8100` |
| 3 | Gateway (buyer side: agent MCP + approval API) | 8000 | buying | `uv run uvicorn gateway.main:app --port 8000` |
| 4 | Web app (screens in the browser) | 5173 | buyer and seller screens | `cd web && npm run dev` |
| 5 | Dispute desk (seller side) | 8200 | `/seller` screens only | `uv run uvicorn dispute_desk.app:app --port 8200` |
| 6 | Ordinary demo web shop | 8300 | browser agents only (checkout guard) | `uv run uvicorn demo.webshop.app:app --port 8300`, open http://greenleaf.localhost:8300 |

Then open **http://localhost:5173** (buyer, log in with `USER_PASSWORD`) or **http://localhost:5173/seller** (seller, log in with `SELLER_PASSWORD`).

## One-time setup

```bash
cp .env.example .env        # then fill in PayPal sandbox keys, GROQ_API_KEY, AGENT_API_KEY,
                            # USER_PASSWORD and SELLER_PASSWORD (two different passwords)
uv sync --extra agent --extra guard   # Python dependencies (uv installs Python 3.12 if needed);
                            # `guard` = the checkout guard, which drives your installed Google Chrome
(cd web && npm install)     # web dependencies
```

## Start everything, step by step

In every new terminal, first run:

```bash
cd ~/Projects/online_hackathons/paypal_hackathon
unset VIRTUAL_ENV           # your shell points at another project's virtualenv
```

**1. Database.** Docker Desktop must be fully started first; this waits for it:

```bash
open -a Docker; until docker info >/dev/null 2>&1; do sleep 2; done; docker compose up -d
```

Ready when you see `Container paypal_hackathon-db-1 Started` (or `Running`).

**2. Demo merchant** (new terminal):

```bash
uv run uvicorn demo.merchant.app:app --port 8100
```

Ready at `Uvicorn running on http://127.0.0.1:8100`.

**3. Gateway** (new terminal). Pick the verifier: `stub` makes no GroqCloud calls; `llm` uses GroqCloud (about 1 call per cart, plus 2 per merchant text).

```bash
VERIFIER=stub uv run uvicorn gateway.main:app --port 8000
```

Ready at `Application startup complete`.

**4. Web app** (new terminal):

```bash
cd web && npm run dev
```

Ready at `Local: http://localhost:5173/`.

**5. Dispute desk** (new terminal, only for the seller side):

```bash
uv run uvicorn dispute_desk.app:app --port 8200
```

Ready at `Application startup complete`.

## Check that everything is up

```bash
for p in 5433 8100 8000 5173 8200; do printf "port %s: " $p; lsof -nP -iTCP:$p -sTCP:LISTEN >/dev/null && echo up || echo DOWN; done
```

## Drive it

| What | Command |
|---|---|
| Scripted agent + user (allow, wrong variant, injection) | `VERIFIER=stub uv run python demo/smoke.py` |
| Fooled agent vs the gateway (demo beat) | `uv run python demo/compromised_agent.py` (add `--auto-approve` to rehearse) |
| Real Claude agent, one shot (uses your Claude login, ~$0.25 per run) | `uv run python adapters/claude/run_agent.py "Restock 10 reams of A4 paper, max 60 USD, ask me above 40"` |
| Real Claude agent, interactive | `cd demo/agent-workspace && export AGENT_API_KEY=$(grep '^AGENT_API_KEY=' ../../.env \| cut -d= -f2) && claude` |
| Tests (database must be up; no network) | `uv run pytest` |
| Browser end-to-end (passkeys + outside witness, on the test DB) | see the header of `tests/e2e/browser_flow.py` |
| Ordinary web shop for browser agents (port 8300, open http://greenleaf.localhost:8300) | `uv run uvicorn demo.webshop.app:app --port 8300` |
| Checkout guard end-to-end (browser agent on that shop, test DB) | see the header of `tests/e2e/guard_flow.py` |
| Checkout guard for a real agent (stdio MCP) | create a **browser** agent on the Agents page, then `GUARD_GATEWAY_URL=http://localhost:8000 GUARD_AGENT_KEY=<its key> uv run --extra guard python -m checkout_guard` |
| Evaluation (cached GroqCloud answers, no key needed) | `uv run python eval/run_eval.py --verifier llm` |

Scenarios to try: `docs/manual-test-plan.md`.

## Sign-in

The web app keeps your sign-in in an HttpOnly cookie (2 h idle, 12 h at most). After adding a passkey you can sign in with it. Scripts sign in with `POST /api/login` and send the returned `token` as `Authorization: Bearer`.

## Stop

`Ctrl+C` in each server terminal, then `docker compose stop` (keeps the data) or `docker compose down -v` (deletes it).

Reset the buyer data without touching Docker: `uv run python -m gateway.db.init --reset`, then restart the gateway.

## When something goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| `failed to connect to the docker API` | Docker Desktop still starting | Use the waiting command in step 1 |
| `role "guardrail" does not exist` | Something hit your Homebrew Postgres on 5432 | The project database is on **5433**; check `DATABASE_URL` in `.env` |
| `Connection refused` from `smoke.py` | Gateway (8000) not running | Start step 3; run the port check above |
| `404 Not Found` when opening `localhost:8000` | Normal: the gateway has no web page | Open `localhost:5173` instead |
| Gateway errors about missing tables after an update | Tables moved to the `gateway` schema (Oct 18) | Restart the gateway; it creates or moves them on startup |
| `/seller` shows errors or keeps logging out | Dispute desk (8200) not running, or wrong password | Start step 5; use `SELLER_PASSWORD`, not `USER_PASSWORD` |
| A `keys/` folder appears inside `web/` | A server was started from `web/` (before Oct 18) | Fixed: `.env` and keys now resolve from the repo root; delete the stray folder |
| `smoke.py` / `--auto-approve` fails with "must be signed with your passkey" | You registered a passkey, so password approvals are refused (by design) | Approve in the web app with Touch ID, or run the gateway with `REQUIRE_PASSKEY=never` for scripted rehearsals (receipts then say "no device signature") |
| `VIRTUAL_ENV ... does not match` warnings | Shell points at another project | `unset VIRTUAL_ENV` |
| PayPal checkout asks for a real card | Sandbox page offers guest card checkout too | Choose **Log in** and use the sandbox **Personal** account |
