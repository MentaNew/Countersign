# Your to-do (as of Nov 3)

Everything only you can do, in order. Deadline: **Nov 12** (Devpost). Times are rough.

| # | Step | Time | Status |
|---|---|---|---|
| 1 | Commit and push the Nov 2–3 changes | 5 min | to do |
| 2 | Try the new things locally | 20 min | to do |
| 3 | Put it online on Render (free) | 45–60 min | to do |
| 4 | Fresh sandbox dispute for the video | 25 min | to do |
| 5 | Real purchase (optional, real money) | 30 min | optional |
| 6 | Video | half a day | Nov 5–10 |
| 7 | Make the repo public, submit on Devpost | 30 min | by Nov 11 |
| — | Example secrets in `.env` | 2 min | only before going online with your laptop |

---

## 1. Commit and push today's changes (5 min)

About 200 files changed since your push: the seller desk sign-in, the README and Devpost draft, pack sizes counted in code, and the re-measured evaluation (most of the files are the stored AI answers in `eval/cache/`).

```bash
cd ~/Projects/online_hackathons/paypal_hackathon
git status                     # check: no .env, no keys/, no .pem, no notes file
git add -A
git commit -m "Seller desk sessions, pack sizes counted in code, README and eval update"
git push
```

Commits now use your private GitHub address automatically (set for this repo).

---

## 2. Try the new things locally (20 min)

Start everything as in `docs/run-locally.md` (database, demo merchant, gateway, web app, and the desk for `/seller`). If the database does not start, Docker Desktop is not running: open it first.

Open **http://localhost:5173** (exactly `localhost`, not `127.0.0.1`: passkeys are tied to the name). Password: the `USER_PASSWORD` from `.env` (currently `change-me-user`).

1. **Inbox → "Set up for real use"** checklist:
   - **Add a passkey** → Security page → *Add a passkey* → Touch ID.
   - **Turn on notifications** → allow in the browser prompt.
   - Leave the two agent steps for after the video (see below).
2. **Sign out**, then **Sign in with your passkey**.
3. **Security → Where you are signed in**: one line per browser or script; try *End* on an old one.
4. In another terminal, start the demo agent (`docs/run-locally.md`, "Real Claude agent, interactive") and ask for something that gets held, e.g. *"Buy 10 reams of premium A4 paper from northside-office, max 60 USD, ask me above 40."* Switch to another tab: you should get a notification and see `(1) Spend Guardrail` in the tab title.
5. Open the held cart → *Why the gateway decided this* → **"What the AI checks said, word for word"**. (Only on carts the AI checked after Nov 1; a cart blocked by a rule never reaches the AI.)
6. Approve it with your passkey. Then on the permission page try **Cancel this permission**.
7. **http://localhost:5173/seller** → sign in with `SELLER_PASSWORD` (`change-me-seller`): the seller desk now has its own sign-in; *Sign out* works.

Tell me anything that feels wrong or confusing.

**Keep the "default" agent until the video is recorded.** The demo scripts and `demo/agent-workspace` use `AGENT_API_KEY` from `.env`, which is the "default" agent. After recording: Agents page → create your own agents with caps → revoke "default".

---

## 3. Put it online on Render, free plan (45–60 min)

Full guide with screenshots-level detail: `docs/deploy-render.md`. Render can read your private repo once you connect GitHub; the repo does not need to be public for this.

1. **Secrets** (on your Mac):
   ```bash
   uv run python scripts/render_secrets.py
   ```
   This writes `keys/render-secrets.env` (never committed, not printed). Open it in your editor; you will copy values from it.
2. **Render account**: https://dashboard.render.com → sign up with GitHub → allow access to `MentaNew/paypal-hackathon`.
3. **New → Blueprint** → pick the repo → Render reads `render.yaml` and lists 1 database + 4 services.
4. **Fill in the secret values it asks for:**
   - `spend-guardrail-gw` (gateway): `SIGNING_KEY_PEM`, `AP2_SIGNING_KEY_PEM`, `AGENT_API_KEY`, `USER_PASSWORD` from `render-secrets.env`; `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` from your `.env`; leave `PAYPAL_VAULT_ID` and `GROQ_API_KEY` empty for now.
   - `spend-guardrail-desk`: `SELLER_PASSWORD` from `render-secrets.env`; `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` from `.env`; `PAYPAL_WEBHOOK_ID` empty for now.
   - `spend-guardrail-shops`: `MERCHANT_KEY_SEED` from `render-secrets.env`.
5. **Apply.** First builds take ~5 minutes. Each service shows *Live* when ready.
6. **Check the addresses.** If Render added a suffix to a name (e.g. `spend-guardrail-gw-ab12`), tell me the real addresses: I will update `render.yaml` and you push.
7. **Open the web app** (`https://spend-guardrail.onrender.com` or with suffix) → sign in with the hosted `USER_PASSWORD` → Security → **add a passkey there** (your local one does not work on another address).
8. **AI checks online (optional):** the online gateway uses the offline checker (`VERIFIER=stub`, no Groq calls). For the real AI checks: gateway → *Environment* → set `VERIFIER=llm` and `GROQ_API_KEY` (from `.env`). Each held cart then costs about one Groq call.
9. **PayPal webhook** (disputes arrive by themselves): https://developer.paypal.com → *Apps & Credentials* → your sandbox app → *Webhooks* → *Add webhook*:
   - URL: `https://spend-guardrail-desk.onrender.com/webhooks/paypal` (or with suffix)
   - Events: *Customer dispute created*, *Customer dispute updated*, *Customer dispute resolved*
   - Save → copy the **Webhook ID** → Render → `spend-guardrail-desk` → *Environment* → `PAYPAL_WEBHOOK_ID` → save (it restarts).

Free plan: services sleep after 15 minutes without visits; the first click then waits about a minute. Before a demo, open the four addresses once. The free database is deleted 30 days after creation (after the deadline).

---

## 4. Fresh sandbox dispute for the video (25 min, with ~5 min waiting)

The earlier sandbox disputes are used up.

1. Locally (or online), have the agent make a small purchase that gets approved.
2. Open the cart → **Pay with PayPal (sandbox)** → log in as your **sandbox personal (buyer) account** (developer.paypal.com → *Sandbox accounts* shows its email and password) → pay. The cart shows *paid*.
3. https://www.sandbox.paypal.com → log in as that buyer → *Activity* → the payment → **Report a problem** → e.g. *I didn't receive it* or *I didn't authorise it* → submit.
4. Wait about 5 minutes (new sandbox disputes start under PayPal review).
5. `/seller` → sign in → **Import** the dispute id (`PP-R-…`, shown in the buyer's resolution center). Online with the webhook, it appears by itself.
6. **Don't answer it before recording**: the video shows the evidence and the choice of response.

---

## 5. Real purchase (optional, real money, 30 min)

`docs/live-purchase.md`: a Shopify shop that accepts PayPal, a cheap item, a small cap (e.g. 15 USD with shipping and tax). Agents page → new agent, kind *Shops on websites*, with a cap → run Claude in `demo/browser-agent-workspace` with that key → approve with your passkey → pay in PayPal yourself. Screen-record it: it is the strongest proof in the video.

---

## 6. Video (Nov 5–10)

1. Rehearse with `docs/manual-test-plan.md`; its last table is the beat sheet (aim for under 3 minutes; trim Block and Inject if over).
2. Record: the problem (10 s) → signed permission (passkey) → allowed purchase + receipt → blocked cart → injection caught (`demo/compromised_agent.py`) → held cart, the AI's word-for-word reply, approve with passkey → dispute desk using the receipt → real purchase clip if done → "real vs simulated" (README table).
3. Upload to YouTube (public or unlisted, as the rules require).

---

## 7. Make the repo public and submit (by Nov 11)

1. GitHub → repo → *Settings* → *Danger Zone* → **Change visibility → Public**. Tell me and I will check from outside that README and license show and nothing private does.
2. Delete the backup: `rm ../paypal_hackathon-backup.bundle` (it still holds the notes and your Gmail).
3. Devpost: paste from `docs/devpost.md` (check its sections against the form), add the repo, video and live links, submit.

---

## Example secrets in `.env` (only before exposing your laptop)

`AGENT_API_KEY`, `USER_PASSWORD` and `SELLER_PASSWORD` still contain `change-me`. Fine locally (everything listens only on your Mac, `.env` is never committed, approvals need your passkey). Change them before a tunnel to your laptop, showing `.env` on screen, or real money from your laptop: `python3 -c "import secrets; print(secrets.token_urlsafe(24))"` three times → `.env` → restart gateway and desk → re-export the agent key. Online, Render uses the strong values from step 3.
