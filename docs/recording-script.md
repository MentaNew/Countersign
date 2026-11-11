# Recording script (under 3 minutes)

Part A is filmed on the **live site** (so the phone's Face ID works). Part B is filmed **locally** (the ordinary web
shop and the dispute desk need your laptop's servers). Record each scene as its own clip, then cut them together.

## 0. Before you record (≈ 45 min, once)

**Put the new version online** (the live site still runs the old one):

```bash
cd ~/Projects/online_hackathons/paypal_hackathon
git add -A && git commit -m "Judge mode, guarded PayPal tools, seed script, docs" && git push
```
Render → Blueprint **spend-guardrail** → **Sync** → wait until the gateway and web app say *Live*.
Check: gateway → *Environment* shows `DEMO_MODE=true`.

**Live site, in Chrome** (https://spend-guardrail-ttdy.onrender.com):
1. Sign in with the live `USER_PASSWORD` (`keys/render-secrets.env`).
2. **Security → Add a passkey → "Use a phone or tablet"** → scan the QR with your iPhone → Face ID.
3. **Agents → New agent** "PayPal assistant" → copy the key (shown once) into a note.
4. Do one dry run of judge mode (permission + the six buttons) so you know where things appear.

**Local** (`docs/run-locally.md`, one terminal tab each): database, merchant 8100, gateway 8000, web 5173,
desk 8200, web shop 8300. In http://localhost:5173: **Agents → New agent**, kind *Shops on websites*, cap 100 USD →
copy the key.

**Screen:**
- Close Slack, mail and notifications (macOS: Focus → Do Not Disturb). Hide the bookmarks bar. Zoom Chrome to 110–125 %.
- Never show `.env`, `keys/` or the terminal history that holds keys: run `export ...` before you start recording, then `clear`.
- Recording: **Cmd+Shift+5** → *Record Selected Portion* (1920×1080 area).
- Phone on screen: plug the iPhone in → **QuickTime → File → New Movie Recording → arrow next to the red button → your iPhone**. Put that window next to Chrome.
  The Face ID prompt then shows in the recording.

**Wake the free servers 2 minutes before each live take:** open the live site and reload once.

---

## Part A: live site

### Clip 1: the problem (10 s)
Show the Inbox, empty or calm.
> "AI agents can now fill carts and call PayPal's own tools. But giving one your wallet is all or nothing. Spend
> Guardrail gives it a permission instead."

### Clip 2: signed permission with Face ID (15 s)
Inbox → judge mode card → **Ask for a new permission** → open it in the drawer → show the plain summary (10 reams,
max 60 USD, two shops, ask me above 40) → **Approve** → the QR code shows → **Face ID on the phone** (QuickTime window).
> "I approve what it may buy, the budget and the shops. Signed on my own phone, so not even the server can approve
> in my name."

### Clip 3: in policy → paid, with a receipt (15 s)
Click **Buy 2 reams, in policy** → open the new row → status *ordered*, the *Allowed vs asked* panel → open the receipt.
> "In policy: it goes straight through, PayPal sandbox order created, and a signed receipt anyone can verify."

### Clip 4: blocked and fooled (25 s)
Click **Recycled paper, over budget** → open it: *blocked*, the budget rule.
> "Over budget: blocked by plain code. No AI involved."

Click **Follow a poisoned product page** → open it: held; the gift card line flagged; *Checks* tab shows the hidden
instructions found on the shop page.
> "Here the shop page hid instructions to add a gift card. The agent fell for it. The gateway didn't."

### Clip 5: held for me, the AI's own words, approve (20 s)
Click **Premium paper, above your ask-me line** → open it → *AI replies* tab (the word-for-word answer) →
*Decision* tab → **Approve** → QR → **Face ID** → status changes, the device seal shows.
> "When in doubt, it asks me, shows exactly why, and my phone signs this exact cart."

### Clip 6: PayPal's own agent tools, guarded (25 s)
Terminal next to Chrome (set up before you start recording):
```bash
cd demo/paypal-agent-workspace
export GUARD_GATEWAY_URL=https://spend-guardrail-gw.onrender.com
export GUARD_AGENT_KEY=<the "PayPal assistant" key>
clear && claude
```
Ask Claude: *"List my invoices."* (runs directly). Then: *"Create a product called Desk lamp, type PHYSICAL."*
→ Claude says it is held → Chrome Inbox shows **PayPal · create_product** with the exact arguments → **Approve** →
**Face ID** → tell Claude *"approved, go on"* → the drawer shows **Done in PayPal** with the `PROD-…` id.
> "Even PayPal's official agent tools: reading is free, but anything that moves money waits for my Face ID, then
> runs once, exactly as approved."

If this fails online, film it locally instead (`GUARD_GATEWAY_URL=http://localhost:8000`, an agent key from
localhost, Touch ID instead of the phone).

---

## Part B: local

### Clip 7: an ordinary web shop (25 s)
```bash
cd demo/browser-agent-workspace
export GUARD_AGENT_KEY=<the "Shops on websites" key>
clear && claude
```
Ask: *"Buy 10 reams of A4 copy paper from greenleaf.localhost, max 60 USD including shipping and tax, valid today."*
Approve the permission (Touch ID). Speed up the browsing in the edit. Show: the agent tries **Pay with PayPal** →
blocked → held cart in http://localhost:5173 with the guard's screenshot and 42.79 USD → **Approve** → PayPal opens.
> "It works on normal shops too: payment stays locked until I approve the exact cart the guard read from the shop."

### Clip 8: dispute desk (20 s)
http://localhost:5173/seller → sign in with `SELLER_PASSWORD` → open the dispute (a fresh one, or the kept case
`PP-R-FER-10193581`) → the receipt checks turn green (the desk verifies them itself) → the suggested response.
> "If the buyer later disputes, the seller checks the receipt on their own and answers PayPal with evidence."

### Clip 9: close (5 s)
README's *Real vs simulated* table, or the live site.
> "Everything runs on PayPal's sandbox, open source, and judges can try it live in judge mode."

---

## After recording

1. Cut to **under 3:00** (aim for 2:40). If you are over, cut clip 4's first half, then clip 8.
2. No music unless it is royalty-free. No real shop names or logos.
3. Upload to YouTube as **Public**, title e.g. *"Spend Guardrail: let AI agents pay with PayPal, only for what you approved"*.
4. Put the link in `docs/devpost.md` and submit (`docs/your-todo.md`, step 8).
5. Remember **Dec 1**: reset the database (`docs/your-todo.md`, step 9), which also removes your phone's passkey so
   judges can approve.
