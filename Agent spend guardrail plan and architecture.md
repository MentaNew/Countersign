# Agent Spend Guardrail + Dispute Desk: plan, architecture and next steps

Prepared October 15, 2026 for the "Build What's Next with PayPal and AI" hackathon (submissions close **November 12, 2026**; winners announced December 21, per PayPal's announcement).

---

## 1. The project in one paragraph

AI agents are starting to buy things on behalf of people and companies, but nothing checks that a purchase matches what the user actually asked for, and nothing records that agreement in a form that holds up in a dispute. This project is a **payment gateway for agents**. The user states and approves an intent (what to buy, budget, limits). The agent can only pay through the gateway, which runs hard checks and a typed semantic check (Jev) of the cart against the signed intent, asks the user for approval when needed, creates the PayPal order itself, and writes a tamper-evident log. That log produces a **verifiable authorization receipt**, which a **dispute desk** uses to assemble the evidence packet when a dispute arrives. The same desk also handles ordinary, non-agent disputes.

**Priority:** buyer-side guardrail first; the dispute desk is the payoff at the end of the demo.

## 2. Decisions made so far

| Decision | Choice | Note |
|---|---|---|
| First problem to build | Spend governance for AI-agent purchases | Dispute desk follows, built on the same log |
| Primary audience | Enterprise (procurement, finance, risk, audit); individuals as a secondary use case | Enterprise is the stronger business story |
| Agent runtime | Claude Code / Claude Agent SDK first | LangChain or another open-source harness is a stretch goal |
| Semantic verifier | Jev (TypeSafe AI typed decision model), behind an interface with an LLM structured-output fallback | Access not yet confirmed |
| Where trust lives | In the gateway, not in agent-side hooks | Hooks are a second layer |
| Rail | PayPal sandbox, with the gateway designed to be rail-agnostic | Enterprise PayPal usage is unproven (see risks) |
| Who runs the gateway (Oct 17) | **Product: PayPal**, as a feature of the buyer's wallet. **Hackathon: us**, as an open-source server standing in for it | PayPal already holds the buyer's consent and reaches every seller; see 3.2 |
| How many sellers get paid (Oct 17) | Each seller's own checkout creates the order for its own PayPal account; the gateway only decides whether the buyer's saved consent may pay it | The gateway never holds seller credentials. In the sandbox, one business account plays every seller |
| Payment consent (Oct 17) | Buyer consents once; PayPal saves it as a token (vault); the gateway releases it per approved cart | Same pattern as Stripe Shared Payment Tokens and Mastercard Agentic Tokens. Fallback: the "Pay with PayPal" click per order |
| Dispute desk placement (Oct 17) | Separate **seller-side** service with its own data; reads the buyer's proof only through the public receipt endpoint | Buyer and seller are different organisations and do not share a database |
| Standards (Oct 17) | Align naming with AP2 (Intent Mandate ≈ signed permission, Cart Mandate ≈ approved cart) | AP2 defines the format; this project is the enforcement plus the dispute side, on PayPal |

## 3. Architecture

### 3.1 Who talks to the gateway (three doors)

The agent and the human never share a door. The **web app** is the human's screen (inbox, permission review, cart approval, audit log), not the agent.

```
                         ┌─────────────────────────────── GATEWAY ───────────────────────────────┐
AGENT (Claude, untrusted)│                                                                        │
  badge: AGENT_API_KEY ──┼─▶ /mcp      propose intent, request cart, read status. NEVER approves  │
                         │                                                                        │
USER in the WEB APP      │                                                                        │──▶ PAYPAL
  (localhost:5173)       │                                                                        │    (orders,
  badge: login token  ───┼─▶ /api      approve/reject intents and held carts, read the log        │     capture)
                         │                                                                        │
USER's BROWSER, sent     │                                                                        │
  back by PayPal         │                                                                        │
  badge: none         ───┼─▶ /paypal   return after paying → gateway captures the payment        │
                         │                                                                        │
ANYONE with a receipt id─┼─▶ /api/receipts/{id}/verify   public: seller, dispute analyst          │
                         └────────────────────────────────────────────────────────────────────────┘
```

| Door | Caller | Credential | Why it is safe |
|---|---|---|---|
| `/mcp` | Agent | `AGENT_API_KEY` (Bearer) | No tool approves anything; a test confirms the agent key gets 401 on `/api` |
| `/api` | Human, through the web app | Login token from `USER_PASSWORD` | The agent never has it; approvals are signed by the gateway and bound to the cart hash |
| `/paypal/return`, `/paypal/cancel` | Human's browser, redirected by PayPal | None (a redirect cannot carry the web app's token) | Only captures an order the buyer already approved at PayPal; PayPal refuses otherwise; idempotent |
| `/api/receipts/...` | Anyone holding a receipt id | None, by design | Read-only; verification uses the gateway's public key |
| `/webhooks/paypal` (planned) | PayPal servers | PayPal webhook signature | For the dispute desk |

Code: `/mcp` in `gateway/mcp_tools.py` (guarded by `AgentKeyMiddleware`), `/api` in `build_router` and `/paypal` in `build_paypal_router` (`gateway/api/routes.py`). The PayPal routes are a separate router because their caller and authentication differ.

### 3.2 Who runs it, and how sellers get paid

**The gateway decides; it does not pay.** It sits at the moment an agent's request is about to become a payment and decides whether the buyer's saved consent may be used for this cart.

A PayPal payment needs two things from two parties: the **seller's API keys** create the order ("collect 10.80 USD for me"), and the **buyer's consent** approves it (a click at PayPal, or a consent saved in advance). API keys alone cannot take money from a buyer.

**Production design (the pitch):**

```
 BUYER SIDE                                PAYPAL                              SELLER SIDE (one of many)
 ──────────                                ──────                              ─────────────────────────
 Agent ──/mcp──┐                                                               Seller's checkout
               ▼                                                                  │ creates the order for
 Human ─/api─► GATEWAY (a PayPal wallet    buyer's saved consent (token)          │ the seller's own account
 (web app)      feature)                   ◄── released only for carts ──────────┤ (receipt id attached)
               + buyer data: intents,          the gateway approved               │
                 carts, receipts, log      order paid ──► seller's account        ▼
                     ▲                     dispute ──── webhook ───────────►  DISPUTE DESK
                     │                                                         + seller data: disputes,
                     └──── public "verify receipt rcp_…" ◄─────────────────────  packets, submissions
```

1. The buyer connects their PayPal wallet once; PayPal saves the consent as a token.
2. The agent finds a product; the **seller's own checkout** creates the order, so money goes to the right seller automatically.
3. The gateway runs its checks against the signed permission. Only if they pass does it let the token pay that order.
4. The receipt id travels with the order (`custom_id`). The seller's dispute desk verifies it through the gateway's public endpoint and never sees the buyer's other data.

**Who runs it:**

| | Production (pitch) | Hackathon (demo) |
|---|---|---|
| Runs the gateway | **PayPal**, inside the buyer's wallet | **Us**, open-source server |
| Why | PayPal already holds the buyer's consent and reaches every seller | We cannot build inside PayPal |
| Sellers | Each seller's own PayPal account and checkout | The gateway creates orders with the sandbox app's keys, so **one sandbox business account plays all four demo shops** (stated in the video) |
| No-click payment | PayPal's saved consent | Vault check (Week-1 check 1b); fallback: "Pay with PayPal" click per order |
| Data | Buyer data with PayPal; seller data with each seller | One Postgres, **two schemas**: `gateway` (buyer side) and `desk` (seller side); the desk uses only the public receipt endpoint |

A self-hosted gateway run by the buyer's company alone would still need a way to pay arbitrary sellers without their keys: the seller's checkout plus the buyer's saved consent (as above), PayPal's partner `payee` feature (permissions unverified), or Payouts (not a purchase). PayPal is the natural host.

**Positioning against PayPal's own agent products.** PayPal Agent Ready (October 2025) is **merchant-side**: it lets PayPal/Braintree merchants accept payments from ChatGPT (ACP) and Google AI Mode/Gemini (UCP). Its public pages do not cover the buyer side: limits, intent checks, approvals and authorization evidence for disputes. Pitch line: *"Agent Ready lets shops accept agents. This is the other half: it lets people and companies control what their agents may buy, with proof for disputes."* (Based on public pages; absence of evidence, not proof.)

### 3.3 Flow

`[door]` marks which door each step uses.

```
USER ──(1) states need──▶ AGENT (Claude Code / Agent SDK, with skill)
                              │
        (2) skill makes agent gather: item, budget, constraints
                              │
                              ▼
          [/mcp]  GATEWAY  tool: confirm_intent
                  - compiles constraints into a structured mandate
                  - returns intent_id + review_url, status pending_user
                              │
          [/api]  USER opens review_url in the WEB APP, reads the plain-language summary
                  (generated by code, outside agent context) → approves
                  → gateway SIGNS and STORES the intent
                              │
          [/mcp]  agent polls get_intent_status until "active"
                              │
        (3) agent shops (browsing, comparing) – no payment ability
                              │
          [/mcp]  (4) agent calls request_purchase(intent_id, lines=[{sku, qty, item}])
                              ▼
                  GATEWAY
                  a. load signed intent by intent_id, verify signature (agent never restates it)
                  b. resolve SKUs in the merchant catalog: titles and prices never come from the agent
                  c. HARD CHECKS (plain code)
                  d. SEMANTIC CHECK (Jev; fallback LLM) on structured intent + resolved cart only
                  e. INJECTION CHECK on any merchant-supplied text the agent used
                  f. decision: approve / block / escalate-to-human
                              │
          [/api]  g. if escalate: USER sees every line item in the WEB APP and approves;
                     the gateway signs a token bound to the cart hash, re-checks limits
                              │
                  h. gateway builds the PayPal order itself and calls PayPal
                  i. append to hash-chained log; issue signed authorization receipt
                              │
       [/paypal]  j. without a vaulted wallet: USER pays at PayPal, the browser returns to
                     /paypal/return, the gateway captures the payment
                              │
                              ▼
                  PAYPAL (sandbox) ──webhooks──▶ DISPUTE DESK
                                                  - CUSTOMER.DISPUTE.CREATED/UPDATED/RESOLVED
                                                  - look up order → receipt + log
                                                  - build evidence packet, human approves
                                                  - submit via Disputes API
```

### 3.4 Trust model (the important part)

- **The agent is untrusted.** It can be confused, manipulated by merchant text, or compromised. It never holds PayPal credentials, never writes the intent store, and never restates the intent.
- **The gateway is the trust anchor.** It owns the signing key, the intent store, the log and the PayPal credentials. Everything is enforced there.
- **Hooks are defense in depth.** In Claude Code / Agent SDK, hooks and the skill keep the agent on the happy path (for example, blocking any direct PayPal tool call, and refusing `request_purchase` before `confirm_intent`). A runtime without hooks is less convenient, not unsafe.
- **The agent's cart is a request, not the order.** The gateway builds the PayPal order from verified SKUs and prices.
- **Approvals are bound to the exact cart.** The human approval token is a signature over the cart hash, with an expiry, so it cannot be reused for a different cart.
- **The PayPal MCP server must not be connected to the agent directly.** It accepts a client ID and secret as a Bearer token; if the agent has it, it can bypass the gateway.

### 3.5 Gateway interfaces

**Agent side: MCP tools at `/mcp`** (deliberately no approval tool)

| Tool | Purpose |
|---|---|
| `confirm_intent` | Propose a permission; returns `intent_id`, plain-language summary and `review_url` for the user |
| `get_intent_status` | Wait (up to 60 s) for the user's decision: pending_user / active / rejected |
| `request_purchase` | Submit a cart against `intent_id`; returns approve / block / escalate with reasons |
| `get_purchase_status` | Wait for the user's decision on an escalated cart |
| `verify_receipt` | Check a receipt's signature and its anchor in the log |
| `get_log` | Read-only log entries for one intent |

**User side: REST at `/api`** (web app, login required unless marked public)

| Endpoint | Purpose |
|---|---|
| `POST /api/login` | Exchange `USER_PASSWORD` for the session token |
| `POST /api/intents/{id}/decision` | Approve (gateway signs) or reject a permission |
| `POST /api/purchases/{id}/decision` | Approve or reject a held cart; must send the `cart_hash` the user was shown |
| `GET /api/intents`, `/api/purchases`, `/api/log`, `/api/log/verify` | Inbox, history, audit log, chain check |
| `GET /api/receipts/{id}`, `/verify`, `/api/public-key` | **Public** receipt verification |

**PayPal redirects at `/paypal`**: `return` (capture after the buyer pays), `cancel`.

### 3.6 Hard checks (deterministic code, never an LLM)

Budget cap (per purchase and cumulative), merchant allowlist or new-merchant rule, category, quantity, currency, expiry, velocity limits, approval threshold, and "every cart line must map to an item in the intent." Anything not in the intent is a block.

### 3.7 Semantic verifier (Jev, with a fallback)

Jev is a non-generative model: you give it a state and typed questions and it returns typed answers with probabilities. Input is text only; send only the structured intent and cart.

| Check | Jev question type | Output |
|---|---|---|
| Does the cart match what the user asked for? | Choice: matches / partial / mismatch | option + probabilities + confidence |
| Anything in the cart the user did not ask for? | Noul (yes/no probability) | 0–1 |
| Does merchant text contain instructions aimed at the agent? | Noul on page text | 0–1 |
| Overall risk level (optional) | Score | score + distribution |

Rules for using it:
- **Keep arithmetic, counting and date checks in code.** Jev is not a calculator and reads dates as text.
- **Pin the version** (Jev-1.13.0 at time of writing); a silent upgrade can shift your thresholds.
- **Tune thresholds on your own labelled examples** (see section 6). Calibration claims are vendor-stated and untested independently.
- **Log the probabilities** with every decision. "Cart-to-intent match: 0.94" is part of the evidence.
- **Hide it behind an interface** (`Verifier.evaluate(intent, cart) → Verdict`) with an LLM structured-output implementation as the fallback, so a waitlist cannot block the build.

### 3.8 Prompt-injection design

Filtering alone will not be enough, so the design limits what a successful injection can achieve:
1. The gateway builds the order, so injected cart items are not purchased unless they map to the intent.
2. The verifier never sees the agent's reasoning or raw merchant text (the injection check sees page text in isolation and only returns a probability).
3. Any item not in the intent is a hard block.
4. The approval screen lists **every line item**, not only a total (the gift-card scenario in Unit 42's write-up relies on a total-only UI).
5. A spending ceiling and approval threshold bound the damage from any residual miss.
6. Outbound actions by the agent (for example, submitting personal data to a link) are outside the payment path; note this as a limit and keep sensitive data out of the agent's context.

Evidence it is real: in F-Secure's test (one model, 100 runs) a planted fake-discount link in a review led the agent to submit personal data in 12% of runs. Unit 42's attacks are hypothetical, with no real incidents reported.

### 3.9 Data model (sketch)

```jsonc
// Intent (signed by gateway)
{
  "intent_id": "int_01",
  "user_text": "Buy 10 reams of A4 paper, max $60 total, from approved suppliers",
  "mandate": {
    "items": [{"description": "A4 paper, 500 sheets", "qty": 10}],
    "max_total": {"amount": "60.00", "currency": "USD"},
    "merchants": ["allowlist ids"],
    "approval_above": {"amount": "40.00", "currency": "USD"},
    "expires_at": "2026-11-01T00:00:00Z"
  },
  "approved_by": "user_id", "approved_at": "…", "signature": "…"
}

// Verdict
{
  "decision": "approve|block|escalate",
  "hard_checks": [{"name": "budget", "pass": true}],
  "semantic": {"match": {"matches": 0.94, "partial": 0.05, "mismatch": 0.01}, "extra_items": 0.02},
  "injection": 0.01,
  "model": "Jev-1.13.0", "reasons": ["…"]
}

// Log entry (hash-chained)
{"seq": 17, "prev_hash": "…", "event": "purchase_decision",
 "intent_id": "int_01", "cart_hash": "…", "verdict": {…},
 "approval": {"token_hash": "…", "at": "…"}, "paypal_order_id": "…", "hash": "…"}

// Authorization receipt (what a seller / dispute desk checks)
{"receipt_id": "rcp_01", "intent_id": "int_01", "cart_hash": "…",
 "approved_by_human": true, "log_seq": 17, "paypal_order_id": "…", "signature": "…"}
```

The receipt ID is attached to the PayPal order as `custom_id` (accepted at order creation, verified Oct 16) and the purchase id as `invoice_id`.

**Receipt format 2 (Oct 19), AP2-aligned naming.** Receipts now carry three named sections, so a seller or a judge can map them to Google's AP2 vocabulary:

| Receipt section | Meaning | AP2 counterpart |
|---|---|---|
| `intent_mandate` | the buyer's words, the signed terms (items, budget, merchants, ask-me amount, expiry), who approved and when, the gateway signature | Intent Mandate |
| `cart_mandate` | cart hash, merchant, exact lines and total, approved by policy or by a person for this exact cart | Cart Mandate |
| `gateway_checks` | each spending rule and its result, cart-to-request match, injection score, thresholds, decision | (no AP2 counterpart: this is the enforcement) |
| `paypal_order_id`, `log_seq`, `log_hash`, signature | the payment and its tamper-evident anchor | close to a Payment Mandate |

Update Oct 21: receipts now also carry **real AP2 v0.2 objects** (AP2 v0.2 replaced Intent/Cart Mandates with SD-JWT Checkout and Payment Mandates): open and closed Checkout/Payment Mandates, the merchant's ES256 Checkout JWT and a Payment Receipt, verified with AP2's dispute rules by the gateway and the seller's desk. Not implemented: delegate SD-JWT key binding, selective disclosure, merchant-issued Checkout Receipts (see `docs/production-plan.md`). The flat fields of format 1 are kept, so older receipts still verify and display. The seller's dispute page and the public receipt page show these sections as a four-step story ("what the buyer asked, what the gateway checked, what was approved, payment and proof"). Every dispute reason and the seller and PayPal options for each: `docs/dispute-playbook.md`.

### 3.10 Dispute desk

- **Where it runs:** a separate **seller-side** service (`dispute_desk/`, its own port, its own `desk` schema and its own seller screens). It never reads the gateway's tables; it gets the buyer's proof through the public receipt endpoint, using the receipt id found in the order's `custom_id`.
- **Intake:** webhooks `CUSTOMER.DISPUTE.CREATED`, `.UPDATED`, `.RESOLVED` at `/webhooks/paypal` (needs a tunnel locally), plus **fetch by case id** (paste a case id in the UI). Verified Oct 17: `GET /v1/customer/disputes/{id}` returns a real sandbox case, but **the list endpoint returned nothing** for it, so the list is only a fallback.
- **Real sandbox case for the demo:** `PP-R-ELT-10193319` on capture `07145137L9934133Y` (order `83U552177A737101D`): `WAITING_FOR_SELLER_RESPONSE`, `MERCHANDISE_OR_SERVICE_NOT_RECEIVED`, stage `INQUIRY`, 3.50 USD, seller response due **2026-11-06**; actions offered: `provide_evidence`, `make_offer`, `accept_claim`, `send_message`, `escalate`. Leave it open until the demo.
- **Context:** dispute → capture id → PayPal order → `custom_id` receipt id → verified receipt (permission, approved cart, who approved, log anchor), plus order and transaction records (Transaction Search is not enabled on the app; limited to 31-day windows and 10,000 records) and shipping or tracking data.
- **Decision support:** fight-or-refund economics (average chargeback costs about $128, of which roughly $82 is internal labour), draft response, human approval gate.
- **Submit:** provide-evidence or make-offer endpoints.
- **Non-agent purchases:** same desk, no signed intent. The packet uses order, tracking, delivery and policy evidence instead. This covers the "extend beyond agents" goal.
- **Two honest limits to state in the pitch:**
  1. PayPal's named evidence types are narrow (from the research notes: `PROOF_OF_FULFILLMENT`, `PROOF_OF_REFUND`, `OTHER`; not re-checked against current docs). The receipt is submitted as supporting evidence, not as something PayPal's adjudication is known to recognise.
  2. The sandbox has no real adjudication, so win rates cannot be shown. Demonstrate workflow and time saved, not "wins more."

### 3.11 Repo layout

```
/gateway          buyer side: MCP tools for the agent (/mcp), REST for the user (/api), PayPal redirects (/paypal)
  /policy         hard checks and decision rules
  /verifier       interface, GroqCloud LLM fallback, stub; Jev planned
  /catalog        SKU lookup in the merchant catalog
  /paypal         Orders v2 client
  /ledger         append-only, hash-chained log
  /signing        Ed25519 keys, intent / approval / receipt signatures
/dispute_desk     seller side (separate service): webhook + fetch-by-id intake, evidence packet builder, Disputes API submission
/web              React UI: buyer approval screens (+ seller dispute screens, planned)
/adapters
  /claude         skill + hooks (Claude Code / Agent SDK)
  /langchain      stretch goal
/eval             labelled intent/cart cases incl. correct-but-wrong and injected pages
/demo             rigged merchant site + scripted scenarios + approval UI
/docs             README, run instructions, architecture, tools-used statement
LICENSE           open-source license (required, visible at top of repo)
```

## 4. How it maps to the judging criteria and prizes

| Criterion | How this project addresses it |
|---|---|
| Technological Implementation | Real MCP gateway, signed mandates, typed verifier, PayPal Orders and Disputes integration, hash-chained log, eval set |
| Design | Plain-language mandate review, line-item approval screen, evidence-packet view; AG Grid for the log and fleet budget view if used |
| Potential Impact | Control layer for agent purchasing; tiered autonomy for enterprises; evidence record insurers and auditors can use; dispute handling for all purchases |
| Innovation | Ties a user-signed intent to a PayPal order and a dispute response; typed (not prose) verification. No PayPal-native product like this was found (absence of evidence, not proof) |
| Presentation | Clear three-act demo: allow, block, inject, then dispute |

Prizes to target: overall podium; Best Use of Agentic Commerce; Best Use of PayPal + AI; Most Impactful; Best Demo Delivery ($5,000 each for the honorable mentions). Sponsor options if they fit naturally: AG Grid (log/fleet view), Render (hosting the gateway and workflows), Channel3 (product data for a buyer agent). Eligibility conditions for sponsor prizes were **not verified**.

## 5. Week-1 checks (do these first; everything depends on them)

| # | Question | Why it matters | If the answer is no |
|---|---|---|---|
| 1 | Can an agent complete a PayPal sandbox order without the buyer approving each one (for example, authorize/capture after a one-time buyer approval, or a stored payment method)? | Determines whether the demo purchase is autonomous | Reframe: gateway controls when the agent may request approval and what it presents; say so openly |
| 2 | Can you create a dispute in the sandbox (buyer account opens a dispute, webhook fires)? | The dispute desk depends on it | Use a clearly labelled simulated webhook payload |
| 3 | Do you have Jev access, and what are the endpoint and SDK shape? | Verifier implementation | Ship the LLM structured-output fallback behind the same interface |
| 4 | Can an order carry custom metadata (receipt ID)? Can authorizations be voided? | Linking receipts to orders | Map by order ID in your own store |
| 5 | Exact tool names in the PayPal Agent Toolkit / MCP server; can calls be wrapped or restricted? | Gateway integration | Call the REST API directly from the gateway |
| 6 | Devpost rules: judging weights, sponsor-prize conditions, video and repo requirements | Eligibility | Read paypalaihackathon.devpost.com and the rules page directly |

## 6. Evaluation set (build early; it doubles as proof for judges)

About 30–50 labelled intent/cart pairs covering:
- clearly correct purchases;
- over-budget and wrong-merchant purchases (hard-check cases);
- **correct-but-wrong** purchases (within limits but not what the user meant, such as a wrong variant or an added warranty);
- carts with an item the user never asked for;
- merchant pages with planted instructions (injection cases).

Report precision and recall for block/escalate decisions and the threshold you chose. Re-run after any model version change.

## 7. Timeline (October 15 → November 12)

| Dates | Focus | Done when |
|---|---|---|
| Oct 15–21 | Week-1 checks; repo skeleton, license, MCP server running; Jev access request; eval set started | Checks 1–6 answered; hello-world gateway call creates a sandbox order |
| Oct 22–28 | Gateway core: confirm_intent, hard checks, verifier interface (+ fallback), order creation, log, approvals | An in-policy purchase and a blocked purchase both work end to end |
| Oct 29–Nov 4 | Injection demo (rigged merchant site), receipt, dispute desk (intake, packet, submit), minimal UI | Full demo script runs without manual intervention |
| Nov 5–9 | Polish, README and run instructions, tools-used write-up, eval report, record and edit the video | Fresh-clone run test passes; video under 3 minutes |
| Nov 10–12 | Buffer; submit with time to spare | Submission form complete before the deadline |

Stretch (only if ahead of schedule): LangChain or open-source-harness adapter; AG Grid fleet view; non-agent dispute scenarios.

## 8. Demo script (under 3 minutes)

1. **Setup (20s):** state the problem; user signs a plain-language mandate ("up to $60, office paper, approved suppliers, ask me above $40").
2. **Allow (25s):** in-policy purchase goes through; show the verdict with probabilities and the receipt.
3. **Block (25s):** over-budget or wrong-item purchase is blocked with a clear reason.
4. **Inject (40s):** a product page contains hidden instructions; show the gateway catching the extra item or flag.
5. **Escalate (20s):** ambiguous purchase held; user sees every line item and approves in one click.
6. **Dispute (40s):** a dispute webhook arrives; the desk builds the evidence packet from the receipt and log; human approves; submitted.
7. **Close (10s):** one line on impact and what is real versus simulated.

The video must show the project running on the device it was built for, be uploaded publicly to YouTube, and avoid third-party trademarks and copyrighted music.

## 9. Submission checklist (from the hackathon brief)

- [ ] Working project using PayPal and AI meaningfully
- [ ] Text description of features and functionality
- [ ] Functional demo: hosted URL **or** clear setup and run instructions in the repo (a mockup does not count)
- [ ] Tools used (models and AI services) and how each is used
- [ ] Public repo (GitHub, GitLab or Bitbucket) with all source, assets and instructions, open-source license visible in the About section
- [ ] Demo video under 3 minutes on YouTube, public, link on the form
- [ ] Compliance with the Official Rules (read them)

## 10. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Sandbox cannot do autonomous orders | Week-1 check 1b (vault); fallback is the "Pay with PayPal" click per order, stated honestly |
| Sandbox disputes | Resolved Oct 17: a real case exists and is readable by id, but the list endpoint misses it. Use webhook or fetch-by-id; simulated webhook only as labelled fallback |
| One sandbox account plays every seller | Say so in the video; production design in 3.2 (seller's own checkout + buyer's saved consent) |
| "PayPal already has this" (Agent Ready) | Agent Ready is merchant-side (accepting agent payments); this is the buyer side plus dispute evidence. Use the pitch line in 3.2 |
| Jev access unavailable or API unclear | Verifier interface with LLM fallback (GroqCloud, built) |
| Looks like "just AP2 / Mastercard Verifiable Intent" | AP2 defines signed mandates (format); this is the working enforcement (checks, approvals, tamper-evident log) plus the dispute side, on PayPal. Align naming with AP2 so the relationship is visible |
| Weak enterprise-PayPal story | Rail-agnostic design; PayPal as the demonstrated rail; do not claim large-enterprise PayPal adoption |
| Demand for agent buying is uncertain (forecasts large, observed volume small) | Frame as the control layer needed before it scales; use dated, sourced figures; avoid headline forecasts |
| Over-scoping | Cut the LangChain adapter and fleet view first; protect the demo path |
| Overclaiming dispute outcomes | Say "faster, complete evidence," never "wins more" |
| Secrets in repo | Credentials only in local env files; never commit or paste them |

## 11. Immediate next steps

Superseded: the living roadmap is `docs/next-steps.md`, and Week-1 results are in `docs/week1-findings.md`. Done as of Oct 17: PayPal sandbox accounts and app, public-repo layout with license, gateway core, approval UI, Claude adapter, buyer payment and capture (check 1a), real sandbox dispute (check 2a). Open: vault (1b), Jev access (3), dispute desk.
5. Read the Devpost rules page.
6. Start the eval set.
7. Decide whether to have Claude scaffold the repo and the Week-1 check scripts (credentials go in a local env file, not in chat).
