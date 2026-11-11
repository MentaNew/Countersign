# Your to-do (final, as of Nov 7)

Everything only you can do, in order. Deadline: **Thu Nov 12, 12:00 pm Pacific (3 pm EST)**. Submit on Nov 11 or the
morning of Nov 12 to leave a buffer.

| # | Step | Time | When |
|---|---|---|---|
| 1 | Commit and push | 5 min | today |
| 2 | Redeploy on Render and check judge mode online | 15 min | today |
| 3 | Passkey on your phone (the Face ID moment) | 10 min | today |
| 4 | Try the guarded PayPal tools locally | 15 min | today |
| 5 | Real purchase on a real shop (optional, real money) | 30 min | Nov 8 |
| 6 | Fresh sandbox dispute for the video | 25 min | Nov 8 |
| 7 | Record the video | half a day | Nov 9–10 |
| 8 | Repo public, submit on Devpost | 30 min | Nov 11 |
| 9 | Reset the database for judging | 20 min | **Nov 30 / Dec 1** (put it in your calendar) |

---

## 1. Commit and push (5 min)

```bash
cd ~/Projects/online_hackathons/paypal_hackathon
git status        # check: no .env, no keys/, no .pem, no notes file
git add -A
git commit -m "Judge mode, guarded PayPal tools, waking-up screen, seed script, docs"
git push
```

---

## 2. Redeploy on Render and check judge mode online (15 min)

1. Render dashboard → your Blueprint (**spend-guardrail**) → **Sync** if it does not sync by itself after the push.
   This adds two new settings to the gateway: `DEMO_MODE=true` and `PASSKEY_REGISTRATION_OPEN=true`.
   Check: gateway service → *Environment* shows both.
2. Wait until the gateway and the web app say **Live**.
3. Open https://spend-guardrail-ttdy.onrender.com in a private window → sign in with the live `USER_PASSWORD`
   (in `keys/render-secrets.env`).
4. Inbox → **Try it: a demo agent shops for you** → *Ask for a new permission* → approve it → click each cart button.
   Expected: 1 approved (PayPal sandbox order), 2 waiting for you, 2 blocked. (The 6th, "US Letter instead of A4",
   is only caught with `VERIFIER=llm`; online it runs the offline checker, and the card says so.)
5. Optional, fills the Inbox in one go instead of step 4:
   `uv run python scripts/seed_demo.py --gateway https://spend-guardrail-gw.onrender.com` (asks for the password).

---

## 3. Passkey on your phone (10 min)

On the live site: **Security → Add a passkey** → choose *Use a phone or tablet* → scan the QR code with your
phone → Face ID. Then approve a held cart from the judge mode: the browser shows a QR code, the phone does Face ID.
Film this for the video (phone and laptop in one shot).

From now until the Dec 1 reset, every approval online needs your phone. That is fine: judging starts Dec 1.

---

## 4. Try the guarded PayPal tools locally (15 min)

Needs the local stack running (`docs/run-locally.md`) and Node (for `npx`).

1. http://localhost:5173 → **Agents** → *New agent* "PayPal assistant" → copy its key.
2. In a terminal:
   ```bash
   cd demo/paypal-agent-workspace
   export GUARD_AGENT_KEY=<that key>
   claude
   ```
3. Ask *"List my invoices"* (runs directly), then *"Create a product called Desk lamp, type PHYSICAL"*.
   Claude says it is held → Inbox → **PayPal · create_product** → Approve with your passkey → tell Claude
   "approved, go on" → the drawer shows **Done in PayPal**.
4. Ask for a refund and **Reject** it. Full script: `docs/manual-test-plan.md`, scene 9.

---

## 5. Real purchase (optional, real money, 30 min)

`docs/live-purchase.md`. Keep the shop's name and logo **out of the video** (rules: no third-party trademarks);
crop or blur, or mention it only in the text. I can help live while you do it.

---

## 6. Fresh sandbox dispute for the video (25 min)

As before (`docs/manual-test-plan.md`, scene 5b): buy something with the sandbox personal account, open a dispute
in the sandbox, wait for the webhook, answer it from the seller desk.

---

## 7. Record the video (Nov 9–10)

Beat sheet: `docs/manual-test-plan.md`, last table (under 3 minutes). Upload to **YouTube, public**. No copyrighted
music; no real shop logos.

---

## 8. Repo public and submit (Nov 11)

1. GitHub → repo → *Settings* → **Change visibility → Public**. On the repo page → *About* (gear icon) → check the
   **MIT license** shows. Tell me and I will check from outside that nothing private shows.
2. Delete the old backup: `rm ../paypal_hackathon-backup.bundle`.
3. Devpost: paste from `docs/devpost.md`, fill in the two passwords in *Testing instructions*, add repo, video and
   live links. Submit.

---

## 9. Reset the database for judging (Nov 30 / Dec 1)

The free database expires 30 days after creation (≈ Dec 5), and judges must not be blocked by your phone's passkey.

1. Render → database **spend-guardrail-db** → *Settings* → **Delete database**.
2. Blueprint → **Sync** (it recreates the database with the same name and links it to the gateway and desk).
3. Gateway → *Environment* → `PASSKEY_REGISTRATION_OPEN` = **false** → *Save* (redeploys). Judges share one
   password, so nobody can add a passkey that locks the others out.
4. `uv run python scripts/seed_demo.py --gateway https://spend-guardrail-gw.onrender.com`
5. Open the site in a private window and click through judge mode once.

The new database lives 30 days: enough for judging (Dec 1–15).
