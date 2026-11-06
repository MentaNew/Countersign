# Deploy on Render (free plan)

Everything in this repo runs on Render's free plan: one Postgres, three small Python services, and the web app as a static site. The file `render.yaml` describes all of it (a "Blueprint").

| Piece | Render service | Address (if the names are free) |
|---|---|---|
| Web app (your screen, and `/seller`) | `spend-guardrail` (static) | https://spend-guardrail.onrender.com |
| Gateway (agents connect to `/mcp`) | `spend-guardrail-gw` | https://spend-guardrail-gw.onrender.com |
| Dispute desk (seller side, PayPal webhooks) | `spend-guardrail-desk` | https://spend-guardrail-desk.onrender.com |
| Demo shops | `spend-guardrail-shops` | https://spend-guardrail-shops.onrender.com |
| Database (schemas `gateway` and `desk`) | `spend-guardrail-db` | internal only |

The web app forwards `/api` and `/paypal` to the gateway and `/desk` to the desk, so the browser only ever talks to one address (needed for passkeys).

## What the free plan means

- **Sleeping.** A service with no traffic for 15 minutes goes to sleep; the next request waits about a minute while it wakes. A first purchase can wake the gateway and then the shops (up to ~2 minutes). Before a demo, open the four addresses once.
- **Outside witness.** The log is timestamped every 5 minutes only while the gateway is awake. It stays awake 15 minutes after the last request, so recent activity still gets anchored. Use "Anchor now" on the Audit log page if needed.
- **Database expiry.** A free Render database **expires 30 days after it is created**. Created around Oct 22, it expires around Nov 21. If judging runs past that, upgrade the database alone before it expires (a few USD per month).
- **Hours.** 750 free instance hours per month for the whole account. Sleeping services use none. Do not keep all three awake with an uptime pinger.
- **No disk.** Keys come from secret settings, not files (the step below creates them).

## Steps

### 1. Make the secrets (on your laptop)

```bash
uv run python scripts/render_secrets.py
```

This writes `keys/render-secrets.env` (gitignored, not printed). The hosted gateway gets **its own keys**, separate from your laptop's.

### 2. Push the repo to GitHub (you do this)

Render deploys from your GitHub repo. Commit `render.yaml`, `scripts/`, and the code changes, then push.

### 3. Create the Blueprint

1. Sign in at https://dashboard.render.com (free account; GitHub login is easiest).
2. **New → Blueprint**, pick this repo. Render reads `render.yaml` and lists the five pieces.
3. It asks for every value marked as secret. Paste from `keys/render-secrets.env`, and from your `.env`:
   - gateway: `SIGNING_KEY_PEM`, `AP2_SIGNING_KEY_PEM`, `AGENT_API_KEY`, `USER_PASSWORD`, `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`; optional `PAYPAL_VAULT_ID`, `GROQ_API_KEY` (leave empty for now)
   - desk: `SELLER_PASSWORD`, `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`; `PAYPAL_WEBHOOK_ID` empty for now
   - shops: `MERCHANT_KEY_SEED`
4. **Apply.** The first build takes a few minutes.

The gateway and desk **refuse to start on a public address with an example password** (anything containing `change-me` or shorter than 16 characters); the log says which one.

### 4. Check the addresses

If a name was taken, Render adds a suffix (e.g. `spend-guardrail-gw-x1y2.onrender.com`). Then edit the URLs in `render.yaml` (env values and the four rewrite rules) to the real ones, push, and Render redeploys.

Open each: gateway `/health`, desk `/health`, shops `/`, web app `/`. Log in to the web app with the hosted `USER_PASSWORD`.

### 5. Register a passkey on the hosted site

Passkeys belong to one address: the one from `localhost` does not work here. Web app → **Security** → Add a passkey.

### 6. PayPal webhook (disputes arrive by themselves)

1. developer.paypal.com → Apps & Credentials → your sandbox app → **Webhooks → Add webhook**.
2. URL: `https://spend-guardrail-desk.onrender.com/webhooks/paypal`
3. Events: `Customer dispute created`, `Customer dispute updated`, `Customer dispute resolved`.
4. Copy the **Webhook ID** into the desk's `PAYPAL_WEBHOOK_ID` on Render (the desk restarts).

The desk verifies each webhook's PayPal signature with that id; unverified ones are refused.

### 7. Point an agent at it

The agent's MCP address is `https://spend-guardrail-gw.onrender.com/mcp/` with `Authorization: Bearer <key>`. Use the hosted `AGENT_API_KEY`, or better, create an agent on the hosted **Agents** page and use its key.

## Rehearsed locally (Nov 5)

The exact committed code was built and started with the commands in `render.yaml`, fresh secrets from `scripts/render_secrets.py`, a `postgresql://` database URL and HTTPS public addresses: all services started, and a full purchase passed (sign-in with the generated password, agent key, sandbox PayPal order, receipt, shop signatures from `MERCHANT_KEY_SEED`, AP2 mandates, desk sign-in, HSTS, example password refused).

Not testable before deploying: whether Render's rewrite passes the sign-in cookie through. The web app handles both: it uses the HttpOnly cookie when it works, and otherwise keeps the session token for the open tab only (tested with a proxy that drops cookies).

## What the hosted version adds (P1.6)

- Keys from secret settings (`SIGNING_KEY_PEM`, `AP2_SIGNING_KEY_PEM`, `MERCHANT_KEY_SEED`), never written to disk there.
- Rate limits per client and overall: login 10 per minute, MCP 120 per minute, other API calls 300 per minute; too many → 429.
- Example secrets refused on a public address.
- One JSON log line per event (`LOG_FORMAT=json`), readable in Render's Logs tab.
- No double payments on retries: PayPal order creation and capture carry a request id (`PayPal-Request-Id`), and approvals lock the cart row.
- Service-to-service timeouts of 70 s, so a sleeping service has time to wake.
