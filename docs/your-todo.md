# Your to-do (as of Nov 1)

Everything only you can do, in order. Deadline: **Nov 12** (Devpost). Times are rough.

---

## 1. Example secrets in `.env` (optional for now)

`AGENT_API_KEY`, `USER_PASSWORD` and `SELLER_PASSWORD` still contain `change-me`. **You can keep them while you work locally**, because:

- the gateway, desk and web app only listen on your own machine (`localhost`), so nobody else can reach them;
- `.env` is never committed (`.gitignore`);
- once you add a passkey, the password alone cannot approve anything.

Change them **before** any of these: you expose your laptop to the internet (e.g. a `cloudflared` tunnel for PayPal webhooks), you share your screen with `.env` open, or you put real money through it. Online on Render this is enforced: the services refuse to start with example values, and `scripts/render_secrets.py` generates separate strong ones.

How (2 min): `python3 -c "import secrets; print(secrets.token_urlsafe(24))"` three times, paste into `.env`, restart the gateway (8000) and the desk (8200). The demo agent then needs the new `AGENT_API_KEY` exported again.

---

## 2. Try the new things locally (15 min)

Start everything as in `docs/run-locally.md`, open http://localhost:5173 and sign in (you will be signed in again: the old login token no longer works).

1. **Security → Add a passkey** (Touch ID). Then sign out and use **Sign in with your passkey**.
2. **Security → Where you are signed in**: check the list; try ending a session.
3. **Inbox → Set up for real use → Turn on** (notifications; allow in the browser prompt).
4. Ask the demo agent for something that gets held, switch to another tab: you should get a notification and see `(1) Spend Guardrail` in the tab title.
5. On a held cart: open **"What the AI checks said, word for word"** under *Why the gateway decided this*.
6. On a permission: **Cancel this permission**.

Tell me anything that feels wrong or confusing.

**Keep the "default" agent until the video is recorded.** The demo scripts (`demo/smoke.py`, `demo/compromised_agent.py`) and `demo/agent-workspace` use `AGENT_API_KEY` from `.env`, which is the "default" agent. Revoke it after recording, or switch the demo agent to a key from the Agents page.

---

## 3. Create the GitHub repo and push (20 min)

There is no remote yet, and ~185 new files plus many changes are not committed.

1. Check nothing secret is staged: `git status` should not list `.env`, any `keys/` folder or `*.pem` (they are ignored).
2. Commit: `git add -A && git commit -m "Checkout guard, hosting, eval, sessions and UI hardening"`.
3. On github.com: **New repository**, public, no README/license (the repo has them).
4. `git remote add origin https://github.com/<you>/<repo>.git` then `git push -u origin main`.
5. On GitHub, check that the license shows and `.env` is not there.

---

## 4. Put it online on Render, free plan (45–60 min)

Full guide: `docs/deploy-render.md`. Summary:

1. `uv run python scripts/render_secrets.py` → writes `keys/render-secrets.env` (not committed, not printed).
2. dashboard.render.com → sign in with GitHub → **New → Blueprint** → pick the repo.
3. Paste the secrets it asks for: from `keys/render-secrets.env`, plus `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET` from `.env` (gateway and desk). Leave `PAYPAL_WEBHOOK_ID` and `GROQ_API_KEY` empty for now.
4. **Apply**, wait for the builds. If a service name was taken, Render adds a suffix: then fix the URLs in `render.yaml` (env values and the rewrite rules), push, and it redeploys.
5. Open the web app address, sign in with the hosted `USER_PASSWORD`, **add a passkey there** (passkeys are tied to one address; your local one does not work online).
6. PayPal webhook: developer.paypal.com → your sandbox app → **Webhooks → Add webhook** → `https://<desk address>/webhooks/paypal`, events *Customer dispute created / updated / resolved* → copy the **Webhook ID** into the desk's `PAYPAL_WEBHOOK_ID` on Render.

Free plan: services sleep after 15 min (first click then waits ~1 min; open the four addresses before a demo). The free database is deleted 30 days after creation.

---

## 5. A fresh sandbox dispute for the video (20 min + ~5 min waiting)

1. Have the agent make a small purchase that is paid as the sandbox **buyer** (cart page → *Pay with PayPal (sandbox)*).
2. sandbox.paypal.com → sign in as the sandbox **personal** account → Activity → that payment → **Report a problem** (e.g. "item not received" or "unauthorised").
3. Wait ~5 minutes (new sandbox disputes start under PayPal review).
4. http://localhost:5173/seller (or the online desk) → sign in with `SELLER_PASSWORD` → **Import** the dispute id (`PP-R-…`). With the webhook set up online, it arrives by itself.
5. Don't answer it before recording: the video shows the evidence packet and the response choice.

---

## 6. The real purchase (optional, real money)

`docs/live-purchase.md`. You choose a Shopify shop that accepts PayPal, a cheap item and a small cap (e.g. 15 USD with shipping and tax). Create a **browser** agent on the Agents page, run Claude in `demo/browser-agent-workspace` with its key, approve with your passkey, pay in PayPal yourself. Screen-record it.

---

## 7. Video and submission (Nov 5–11)

1. Rehearse with `docs/manual-test-plan.md` (its last table is the beat sheet; trim to under 3 minutes).
2. Record: problem → signed permission → allowed purchase + receipt → blocked → injection caught → held cart with passkey → dispute desk using the receipt → (real purchase clip if done) → "real vs simulated".
3. Upload to YouTube (public or unlisted, as the rules require).
4. Devpost: paste from `docs/devpost.md` (draft; check it against the form's fields), add the repo, video and live links.
5. Fresh-clone check: clone the public repo in a new folder and follow `docs/run-locally.md` once.

---

## Things I can still do while you work on these

- Fix anything you find in step 2.
