# From interesting to winner: final plan (Nov 7 → Nov 12)

## Official rules (read Nov 7 from Devpost)

- **Deadline: Thu Nov 12, 12:00 pm Pacific (3 pm EST).** Judging **Dec 1 → Dec 15**; winners ≈ Dec 21.
- **Stage 1 (pass/fail):** fits the theme and applies the PayPal developer platform (sandbox) + an AI tool.
- **Stage 2, five equally weighted criteria** (tie-break in this order):
  1. *Technological Implementation:* how thoroughly and skillfully the project uses the PayPal Developer Platform and
     AI tools; genuine, working, non-trivial.
  2. *Design:* a complete, coherent product experience, not just a technical proof of concept.
  3. *Potential Impact:* a credible, specific case for a real problem and audience, backed by what is demonstrated.
  4. *Innovation/Idea:* creative, novel, different from existing concepts.
  5. *Presentation:* the video shows it working end to end; who it's for, what it solves, why it matters.
- Judges "may judge based solely on the text description, images, and video"; judging may use AI-driven analysis.
- **Submission:** text description; a functional demo (hosted URL **or** complete run instructions); **public repo with
  an open-source license visible in the About section**; video **< 3 min, public on YouTube**, showing the project
  working, **no third-party trademarks or copyrighted music/material without permission**; English.
- **Testing:** if the site is private, include login credentials; the project must stay available free of charge
  **until the Judging Period ends (Dec 15)**.
- **Prizes:** Grand 12k/8k/5k; Honorable Mentions 5k each (Most Creative, Most Impactful, Best Demo Delivery, Best Use
  of PayPal + AI, Best Use of Agentic Commerce); sponsor prizes for **any AG Grid tools** (5k/2k/3×1k), **any Render
  tools** (1000/750/500 credits), APIMatic, Bryntum, Channel3. A project can win **one Grand or one Honorable Mention
  plus one Sponsor prize**.

### Consequences for the plan
- **Free database vs judging:** it expires ≈ Dec 5, inside judging. Delete it and create a fresh free one on
  **Nov 30 / Dec 1**, then run the re-seed script: 30 days of life covers Dec 1–15.
- **Judges and passkeys:** judges log in with a password from the submission form; a passkey registered online would
  block their approvals. Film the Face ID moment in November; the Dec 1 reset gives judges a clean instance
  (their approvals show "password only", honestly labelled).
- **Real purchase:** keep the real shop's name and logo out of the video (crop/blur, or keep it off camera and
  mention it in the text). Fictional demo shops are fine.
- **AG Grid now clearly qualifies with the free library** and stacks on a Grand Prize: recommended back in scope
  (½ day: the Inbox queue or Audit log as an AG Grid table).
- **Render qualifies already** (Blueprint deployment): say so in the submission.

## Our edge (say it in the first 10 seconds)

PayPal launched Agent Ready and Store Sync so agents can pay merchants, with "buyer protection and dispute
resolution" — but nothing public explains **how a dispute is decided when an agent bought without the person
present**. We are the missing half: the **buyer's side**. You give an agent a permission, not your wallet; every cart
is checked against it; doubtful carts wait for your fingerprint; and every payment carries a **signed receipt the
seller can verify on their own** to win (or fairly settle) the dispute.

Proof we already have: real sandbox orders and disputes answered from the desk, passkey-signed approvals, an audit log
witnessed by DigiCert, AP2 mandates, a checkout guard for ordinary shops, 167 evaluated carts with 0/114 unsafe
approvals (55 held out), deployed on Render.

## What makes judges' jaws drop (the plan)

Ranked by impact per hour. **Must** = do before submitting. **Strong** = big gain, do if on schedule. **Stretch** = only
if ahead.

### 1. MUST — A "try it in 60 seconds" judge mode on the live site (≈ 1 day)
Judges rarely install anything. Today the online app needs an agent running on a laptop to show anything.
- A **Scenario panel** on the live web app (behind a judge password given in the Devpost form): buttons that run the
  scripted agent server-side against the real gateway: *Allowed purchase · Over budget · Wrong variant · Poisoned
  product page (the fooled agent) · Ordinary web shop with the checkout guard*.
- Each run lands in the Inbox in real time; the judge opens the drawer, sees *You allowed vs the agent asked*, the
  AI's own words, and approves or rejects (password session for judges; your passkey for your own demo).
- A **Reset demo** button and a seeded dispute so the seller desk is never empty.
- Why it wins: *Best Demo Delivery* and every criterion at once — the judge feels the product instead of reading about it.

### 2. MUST — The phone moment (≈ 2 hours, mostly setup)
- Register your passkey online from the iPhone (or iCloud Keychain sync), open the agent's approval link on the phone,
  approve with **Face ID**. Film it next to the laptop: the agent waits, the phone buzzes, Face ID, the agent
  continues and PayPal is paid.
- Small code: make the approval link + cart page perfect on a phone (already responsive), and add a "waiting for you"
  e-mail-free nudge: the tab title + desktop notification already exist; for phone, the link the agent posts is enough.

### 3. MUST — The video (½ day) and the Devpost story (½ day)
Structure (≈ 2:50):
1. **Cold open (15 s):** the agent on a real-looking shop gets a hidden instruction to buy a gift card; split screen:
   the gateway catches it, the cart turns red: "The agent was fooled. The gateway was not."
2. **The problem (15 s):** agents can fill carts; giving one your wallet is all or nothing; and when it goes wrong,
   nobody can prove what you allowed.
3. **Permission, not wallet (25 s):** Touch ID on a permission; the agent shops inside it; an in-policy cart is paid
   automatically with a receipt.
4. **The phone moment (25 s):** a doubtful cart → phone buzzes → Face ID → PayPal.
5. **Ordinary shop (25 s):** the checkout guard keeps PayPal locked until you approve the exact cart it read.
6. **The dispute (30 s):** the seller's desk receives the PayPal dispute by webhook, verifies the buyer's receipt on its
   own, and answers PayPal with evidence in one click.
7. **Trust you can check (20 s):** tamper with the database → the log turns red; the outside timestamp still proves
   the original. 0 of 114 unsafe approvals on 167 tested carts.
8. **Close (15 s):** "PayPal could run this as a wallet feature: agents get permissions, people keep control, sellers
   get proof." Real vs simulated in one line.
- Devpost text from `docs/devpost.md`, plus: one architecture image, three GIFs (held cart in the drawer, checkout
  guard lock, dispute desk), the live link with the judge password, and the eval table.

### 4. STRONG — A real purchase on a real shop (½ day incl. finding the shop)
`docs/live-purchase.md` is ready. 10 seconds of a real Claude agent buying a real item with real PayPal, approved by
Face ID, turns "demo" into "works". Highest credibility per second of video.

### 5. STRONG — A guardrail in front of PayPal's own agent tools (≈ 1 day, risk: medium)
PayPal ships an official MCP server / Agent Toolkit (orders, refunds, invoices, disputes; sandbox endpoint and
`@paypal/mcp`). Agents connected to it can move money with no per-purchase limit. We add **"Guarded PayPal tools"**:
our MCP exposes the same PayPal tools to the agent, but money-moving ones (create/capture order, refund, send
invoice) go through the permission + checks + passkey, while read-only ones pass straight through. One slide, one
demo beat: *"PayPal's own agent tools, now with a permission and an off switch."* Strongest *Best Use of PayPal + AI*
and *Agentic Commerce* story; verify tool names against the current package before building.

### 6. STRONG — Personality pass + a 30-second landing page (½ day)
- Run the **product-personality** skill on the approved backbone: type, colour, voice, a hero.
- Public landing page at `/` for signed-out visitors: the one-line promise, a 3-step diagram (permission → check →
  proof), live counters from the public log ("N carts checked, M stopped, 0 paid outside a permission"), buttons
  "Try the demo" (judge mode) and "Verify a receipt".

### 7. STRETCH — Sponsor prizes (2–4 hours each)
- **Render:** a Render Cron Job (or Workflow) that witnesses the log every 5 minutes even while the free web service
  sleeps, plus the one-click Blueprint in the README. Fixes a real free-plan gap and qualifies cleanly.
- **AG Grid:** the Audit log as an AG Grid table (filter, sort, export, row details). Only if the rules confirm plain
  AG Grid counts (the AG Grid blog talks about "AG Studio").

## Must not break during judging (Nov 12 → Dec 21)

- **The free Render database expires 30 days after creation (≈ Dec 5)** and is deleted ≈ Dec 19: **upgrade the
  database** (a few USD/month) before Dec 5, or the live demo dies mid-judging.
- **Sleeping services:** a judge's first click waits ~1 min. Recommend upgrading the gateway (and the web app is static,
  already instant) to an always-on instance for Nov 12 → Dec 21, or show a friendly "waking up" screen.
- **Secrets:** the judge password is a separate low-power account (password session; no passkey) — never your own.
- **Make the repo public** and re-check from outside that `.env` / notes are absent.

## Schedule

| Day | Work | Owner |
|---|---|---|
| Fri Nov 7 | Judge mode (scenario panel, reset, seeded dispute) | Claude |
| Fri Nov 7 | Confirm Devpost rules (criteria, video length, time of deadline); passkey on the phone; upgrade Render DB | you |
| Sat Nov 8 | Guarded PayPal tools (or personality + landing if you prefer less risk) | Claude |
| Sat Nov 8 | Real purchase on a real shop; fresh sandbox dispute via webhook | you (+ Claude on call) |
| Sun Nov 9 | Personality pass + landing page; README image + GIFs | Claude |
| Mon Nov 10 | Record the video (2 takes), cut to < 3 min | you |
| Tue Nov 11 | Devpost text, final tests, repo public, live-site check from a clean browser | both |
| Wed Nov 12 | Submit in the morning (buffer for the cutoff time) | you |

## Decisions (Nov 7, user)

- **Scope:** the three musts (judge mode, phone Face ID moment, video + Devpost) **+ 5. Guarded PayPal tools + 4. a real
  purchase on a real shop.** Not now: personality/landing page, sponsor prizes.
- **Hosting: stay on the free plan.** Consequences and mitigations:
  - *Sleeping:* the web app is static (instant); add a friendly "waking the gateway up (≈ 1 min)" state to the web app
    instead of an error, and say it in the Devpost text.
  - *Database expiry (≈ Dec 5, deleted ≈ Dec 19):* a one-command **re-seed script** so the demo can be restored on a
    fresh free database in minutes (create new free DB → update `DATABASE_URL` → run the script); put a reminder in
    your calendar for Dec 4. The video and repo stay the primary evidence.

## Updated schedule

| Day | Work | Owner |
|---|---|---|
| Fri Nov 7 | Judge mode on the live site (scenario panel, reset, seeded dispute, waking-up state, re-seed script) | Claude |
| Fri Nov 7 | Confirm Devpost rules (criteria, video length, cutoff time); passkey on the phone | you |
| Sat Nov 8 | Guarded PayPal tools (verify the official tool names first) | Claude |
| Sat Nov 8 | Find the real shop + item; real purchase run with Claude on call; fresh sandbox dispute via webhook | you |
| Sun Nov 9 | README image + GIFs; Devpost text updated for judge mode and guarded PayPal tools | Claude |
| Mon Nov 10 | Record the video (2 takes), cut to < 3 min | you |
| Tue Nov 11 | Final tests, repo public, live-site check from a clean browser, submit draft | both |
| Wed Nov 12 | Submit in the morning (buffer for the cutoff time) | you |

## Status (Nov 7, evening)

- ✅ Judge mode: demo agent card on the Inbox, six scenarios, "waking up" screen, `scripts/seed_demo.py`,
  `PASSKEY_REGISTRATION_OPEN=false` for the shared judge login (set it after the Dec 1 reset).
- ✅ Guarded PayPal tools: `paypal_guard/` in front of `@paypal/mcp` 1.8.1, held actions in the Inbox with exact
  arguments, approve once / reject, logged; checked against the real sandbox (`create_product` held → approved → run once).
- ✅ README, Devpost draft (testing instructions), test plan scene 9 and new beat sheet.
- You: commit + push, Render redeploy, seed, phone passkey, real purchase, video, submit (`docs/your-todo.md`).
- Not done: AG Grid (optional sponsor prize), personality pass, seeded dispute in judge mode (the desk uses real
  sandbox disputes instead).
