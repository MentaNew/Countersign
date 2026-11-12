# Recording script (laptop only, ≈ 2:25)

Everything runs on your laptop, approvals with **Touch ID**. Record each clip separately, then cut them together.

## 0. Before you record (≈ 20 min, once)

**Start the local servers** (`docs/run-locally.md`, one terminal tab each). Start the gateway with judge mode on:

```bash
docker compose up -d
uv run uvicorn demo.merchant.app:app --port 8100
DEMO_MODE=true VERIFIER=stub uv run uvicorn gateway.main:app --port 8000
uv run uvicorn dispute_desk.app:app --port 8200
uv run uvicorn demo.webshop.app:app --port 8300
cd web && npm run dev
```

**In http://localhost:5173** (exactly `localhost`):
1. **Security**: if no passkey is listed, **Add a passkey** → Touch ID.
2. **Agents → New agent** "PayPal assistant" → copy the key.
3. **Agents → New agent**, kind *Shops on websites*, cap 100 USD → copy the key.
4. Practise judge mode once so you know where things appear.

**Two terminals ready** (type these *before* recording, then `clear`, so no key is ever on screen):

```bash
# Terminal A
cd demo/browser-agent-workspace && export GUARD_AGENT_KEY=<shop agent key> && clear
# Terminal B
cd demo/paypal-agent-workspace && export GUARD_AGENT_KEY=<PayPal assistant key> && clear
```

**Chrome order matters:** open your **normal Chrome** with the Inbox *before* starting the clip 5 agent. The agent
opens its own Chrome (bar: "controlled by automated test software") with no passkeys: never approve in that window.
If you quit your Chrome while the agent's is open, the Dock icon opens the agent's one; `/exit` the agent first.

**Screen:** Do Not Disturb on, Chrome zoom 110–125 %, bookmarks bar hidden. Record with **Cmd+Shift+5**.

---

## The clips

| # | Time | What you do | What you say |
|---|---|---|---|
| 1 | 10 s | Show the Inbox. | "AI agents can fill carts and call PayPal's own tools. Giving one your wallet is all or nothing." |
| 2 | 15 s | Judge mode card → **Ask for a new permission** → open it → **Approve** → Touch ID. | "Instead, I give it a permission: what, how much, which shops. Signed with my fingerprint." |
| 3 | 30 s | Click **Buy 2 reams, in policy**, **Recycled paper, over budget**, **A shop you did not allow**, **Follow a poisoned product page**. Each result appears under the buttons (✅ / ✋ / ⏸) and its cart opens: the red "Stopped" panel (your shops vs the shop it tried), then the poisoned page with the **hidden text highlighted** and the gift card it caused. | "In policy: paid. Over budget, or a shop I never allowed: stopped by plain code. And this page hid orders for my agent, invisible to shoppers. The gateway caught it." |
| 4 | 20 s | **Premium paper, above your ask-me line** → open → *AI replies* tab → *Decision* → **Approve** → Touch ID. | "When in doubt, it asks me, shows exactly why, and I approve this exact cart." |
| 5 | 25 s | Terminal A: `claude` → *"Buy 10 reams of A4 copy paper from http://greenleaf.localhost:8300, max 60 USD including shipping and tax, valid today."* Approve the permission. In the agent's Chrome: the purple banner, each click highlighted; it tries **Pay with PayPal** → **"Payment locked"** page and red banner → it goes back → banner "⏳ Waiting for your approval" → approve the cart in your Chrome (Touch ID) → the banner turns green **"🔓 Payment unlocked"** by itself → PayPal opens. **Speed up the browsing ×4.** | "It shops on a normal site, but payment stays locked. The moment I approve this exact cart, it unlocks. Nothing else." |
| 6 | 25 s | Terminal B: `claude` → *"Create a product called Desk lamp, type PHYSICAL."* → held → Inbox **PayPal · create_product** → **Approve** → Touch ID → tell Claude *"approved, go on"* → drawer: **Done in PayPal**. | "Even PayPal's official agent tools: anything that changes my account waits for my approval, then runs once, exactly as approved." |
| 7 | 15 s | http://localhost:5173/seller → open a dispute → green receipt checks → suggested response. | "If the buyer disputes, the seller verifies the receipt on their own and answers PayPal with evidence." |
| 8 | 5 s | README *Real vs simulated* table. | "PayPal sandbox, open source, and judges can try it live." |
|   | **≈ 2:25** | | |

Rules of thumb so it stays under 3:00:
- Say each line once, then move; don't wait on screen for loading (cut it).
- If the cut is over 2:50, drop clip 7 first.

---

## After recording

1. Export, check the length (< 3:00), no music unless royalty-free, no real shop logos.
2. Upload to YouTube as **Public**.
3. Put the link in `docs/devpost.md` and submit (`docs/your-todo.md`, step 8).
