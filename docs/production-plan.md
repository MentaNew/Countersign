# Road to production and a real purchase

Written **October 20, 2026** (submission deadline **November 12**, 23 days). Goal set by the project owner: make the gateway something they would personally trust, and prove it by **having an agent buy something real on the internet with a real PayPal account**. Nothing here is built yet; this is the plan.

## 1. The trust test

> *If the gateway operator were dishonest or hacked, could the evidence be faked?*

Today: yes. The user's approval is a password login asserted by our key, the operator can rebuild the hash chain, the merchant never commits to the cart, and "AP2" is only naming. Production-grade means the answer becomes no.

## 2. What buying for real implies

### How agents buy today (October 2026)

| Way | How it works | Who controls payment | Can our gateway sit in it? |
|---|---|---|---|
| **Browser checkout** (Claude in Chrome / computer use, Operator-style agents, Playwright) | The agent fills the shop's normal checkout and clicks Pay (card, PayPal button, Shop Pay) | The person, through the payment page (e.g. PayPal login + approve) | **Yes, as a checkout guard**: the agent may not reach the payment step until the gateway approves the cart |
| **Shopify agent checkout** (WebMCP, rolled out late Sept 2026, Chromium) | Browser agents read, update and submit the checkout as structured data, with the buyer's authorisation; PayPal is one of the payment options | Buyer authorisation at submit | **Yes, and better**: the gateway checks a structured cart instead of scraped text |
| **In-platform protocols** (ACP in ChatGPT, UCP in Google, Agent Ready) | The AI platform and the merchant integrate; scoped payment tokens | Platform + payment provider | Not open to us; the long-term home of this logic (inside PayPal) |
| **Single-use virtual cards** (e.g. Stripe Link, agent card issuers) | A card number scoped to one merchant and amount | Card issuer | Possible but not PayPal; out of scope |
| **Merchant API** (our demo store) | The gateway creates the order itself | Gateway, with the buyer's consent | What we built |

### Hard facts that shape the design

1. **A third party cannot charge your PayPal at an arbitrary shop.** PayPal saved payment methods are scoped to the merchant that saved them (`usage_type: MERCHANT`, "accessible by you only"). Cross-merchant use needs platform-level vaulting (`PLATFORM`), which is for marketplaces/partners and is a limited-release pilot. So for real third-party shops, **PayPal's own checkout (you log in and approve) stays the payment step**, and the gateway's job is to decide *before* the agent may reach it.
2. **Saving a PayPal wallet on a live account requires vetting** (Developer Dashboard → Save payment methods → eligibility review) **and Risk Data Acquisition** for every customer-initiated vaulted payment. A per-order PayPal-approval checkout needs neither.
3. **For a real third-party shop, the seller never sees our receipt id.** The shop creates its own PayPal order; we cannot put `custom_id` on it. The receipt then protects the **buyer** (and their company or insurer); seller-side use needs merchants to adopt the verification step (what ACP/UCP-style integration, or PayPal itself, would provide).
4. **Some retailers block agents** (Amazon; reportedly others). Real tests must use shops that allow agent checkout (Shopify stores with agent checkout enabled are the natural choice) and respect their terms.
5. **Real money and real personal data** mean: HTTPS hosting, secrets out of `.env` on a laptop, fail-closed behaviour, a kill switch, small amounts, and the user's own approval on every real payment at first.

### So the end-goal test looks like this

```
You: "Buy me X, under N USD, from shop S"            (in Claude / your agent)
  → agent proposes the Intent Mandate               → you sign it with Face ID / Touch ID (passkey)
  → agent shops the real store (browser, or Shopify agent checkout)
  → at checkout, the agent must submit the cart to the gateway; the browser tool refuses the payment step until then
  → gateway checks cart vs your signed intent (rules + verifier + injection screen)
  → you approve the exact cart with your passkey (held carts) or policy approves (small, in-policy)
  → the agent may now click "Pay with PayPal" → you log in to your real PayPal and pay
  → gateway records the PayPal confirmation against the receipt; tamper-evident, externally anchored
```

Two real-money tests. **Decision (Oct 20): do R2 first; R1 is deferred** (needs a live PayPal business account; if done later, no live dispute: a PayPal dispute costs the seller ~15 USD, and disputes are already proven in the sandbox).

| Test | Merchant | Money | What it proves |
|---|---|---|---|
| **R1: our own live shop** | Your own live PayPal **business** account selling a ~1 USD digital item through the gateway's API flow | ~1 USD from your personal PayPal to your business PayPal | The whole system on live PayPal: real order, receipt, real dispute, dispute desk; we control both ends, so nothing can go wrong for a third party |
| **R2: a real third-party shop** | A Shopify store (or another store that accepts PayPal and allows agents) | A few USD | Integration with how agents actually buy today; the gateway as checkout guard; your passkey + PayPal approval |

## 3. Work plan

Progress: **P1.1 done (Oct 20)**: WebAuthn enrolment on a Security page; approving an Intent Mandate or a held cart signs a statement (intent id + terms hash, or purchase id + cart hash + total) on the user's device; receipts carry the signature and device public key; the gateway, the public receipt page and the seller's desk re-verify it from the receipt alone; once a passkey exists a password alone cannot approve. Tested with a software authenticator (unit) and Chrome's virtual authenticator against the real web app (end to end).

**P1.2 done (Oct 21)**: every 5 minutes (and on demand) the chain head is timestamped by DigiCert's public RFC 3161 authority (FreeTSA fallback, pinned root); new log entries hash the payload's digest so public proofs reveal no contents; `GET /api/receipts/{id}/proof` links a receipt to the witnessed head; the gateway and the seller's desk verify the authority's CMS signature and certificate chain themselves (the desk keeps its own copy of the proof); tokens also verify with `openssl ts -verify`. A test shows an operator who rewrites an entry and recomputes every hash passes the internal chain check but fails the outside witness. Real anchor #1 on the live DB: DigiCert, Oct 21 16:11 UTC, entry #110 (receipts before Oct 21 use the older hash format and are covered server-side only). Browser end-to-end check saved as `tests/e2e/browser_flow.py`.

**P1.3 done (Oct 21)**: each shop has its own Ed25519 key and publishes it at `/{shop}/.well-known/agent-merchant.json`; on every purchase request the gateway asks the shop for a signed cart offer (titles, prices, total, 30-minute expiry) and verifies it against the key it pinned the first time it saw the shop (a changed key blocks the purchase); resolved lines now come from the signed offer; the cart hash and the user's passkey statement include the offer id; an expired offer cannot be approved later; receipts carry the signed offer (`cart_mandate.merchant_signature`); the gateway re-checks it and that the paid lines equal the offer; the seller's desk also compares the key with the shop's own published key. Limitation: keys are trusted on first use (production: a merchant registry, DNS or PayPal's merchant identity).

**P1.4 done (Oct 21), focused subset of AP2 v0.2** (spec at google-agentic-commerce/AP2 e1ea56d, 2026-04-29; v0.2 replaced Intent/Cart Mandates with Checkout + Payment Mandates as SD-JWT VCs). Shops now sign **ES256 Checkout JWTs** (AP2 forbids deterministic Ed25519 there; old pins upgrade automatically). Each receipt carries an `ap2` bundle signed by the gateway as Trusted Surface (ES256 key published at `/.well-known/jwks.json`): open Checkout (`checkout.allowed_merchants`) and open Payment (`payment.amount_range`, `payment.allowed_payees`) Mandates with `cnf`, closed `mandate.checkout.1` (merchant `checkout_jwt`, `checkout_hash`) and `mandate.payment.1` (`transaction_id` = checkout_hash, amount in minor units), and a Payment Receipt once PayPal has captured. The gateway and the seller's desk (with keys it fetches itself) run AP2's dispute verification: 12 checks. Not implemented, and stated in the UI: delegate SD-JWT key binding, selective disclosure, merchant-issued Checkout Receipts. Note: AP2 v0.2 calls automated retrieval of mandates for disputes valuable but out of scope; our dispute desk does exactly that.


**P1.5 done (Oct 21), without approval chains (on the cut list).** *Fail closed:* if the semantic check or the injection screen errors or takes over 30 s, the cart is held for the human with the reason shown, never approved (a rule block stays a block). *Per-agent keys:* table `gateway.agents`; each agent has its own MCP key, created on the new **Agents** page and shown once (only its SHA-256 is stored). Creating an agent is signed with the passkey (statement `agent_creation` with name and cap). The `.env` `AGENT_API_KEY` is registered as the "default" agent; changing it retires the old key; older intents and purchases are attributed to it. Every intent and purchase records its `agent_id`; an agent can only read and use its own permissions (check `intent_agent_match`); an optional per-agent **spending cap over a rolling 30 days** (check `agent_spend_cap`). *Kill switches:* revoke one agent (permanent) or **Stop all agents** (table `gateway.controls`); the key is looked up on every MCP call, so either takes effect on the next call; waiting carts are closed, and the approval re-check (`agent_active`) refuses carts of a stopped agent. Stopping needs no passkey; turning agents back on is signed. Log events `agent_registered/created/revoked`, `agents_stopped/resumed`. Tests: `tests/test_fail_closed.py`, `tests/test_agents.py` (78 total); browser e2e 19/19 (create agent with passkey, use its key, stop, resume).


**P2.1 done (Oct 21).** *Gateway:* a second, labelled trust level. `submit_page_cart` (only for agents created as kind "browser") takes a cart the guard read from a normal shop's checkout: lines, shipping, tax, discount, total, page address, screenshot. Extra checks: the money adds up, the checkout is on the shop's own site, shipping and tax count toward the budget. Such carts are never approved by policy alone: they always need the user's passkey, even with no ask-me amount. On approval the gateway creates no PayPal order; it signs a 20-minute **checkout permit** for that cart and issues a receipt marked `cart_source: page` ("not signed by the shop"). *Guard* (`checkout_guard/`, Playwright driving the installed Chrome, stdio MCP for the agent): the agent browses with open_page / click / type_text / choose; payment sites (PayPal, Stripe, Shop Pay...) are blocked until a valid permit, including server redirects to them; card, bank and password fields are refused; the guard reads the cart itself (Shopify-style `/cart.js` plus the checkout page's totals and a screenshot); after approval it checks the permit's signature and that the browser's cart is unchanged; any cart change locks payment again; the user agent says it is an AI shopping agent. *Demo shop:* `demo/webshop` (Greenleaf Paper Co., http://greenleaf.localhost:8300) with a Shopify-style cart, shipping, tax and its own PayPal order. Tests: `tests/test_page_carts.py`, `tests/test_guard_rules.py` (92 total), `tests/e2e/guard_flow.py` 12/12. Limits: the guard reads Shopify-style carts only; it must run where the agent cannot read its key (no shell access for the agent).

**Nov 1: ready for real use (security + interface).** *Sign-in:* random session tokens stored only as hashes, HttpOnly SameSite=Strict cookie (page scripts never see it), CSRF header required with the cookie, sessions end after 2 h idle / 12 h at most, sign-out, a sessions list with "end" and "sign out everywhere else", passwordless sign-in with a passkey (`gateway/logins.py`). *Headers:* nosniff, no framing, no referrer, `no-store` on API answers, HSTS online; strict Content-Security-Policy for the web app in `render.yaml` (checked against the built app). *Policy:* deterministic repeat-order rule (autoship, subscription, auto-renew, every N weeks) holds such lines unless asked for; eval now 0/110 unsafe. *Audit:* every live decision keeps the exact model requests and replies, with their hash in the audit log; shown word for word on the cart page. *Permissions:* "Cancel this permission" (no passkey needed; cancels held carts). *Interface:* waiting count on the Inbox link and tab title, desktop notifications, a "set up for real use" checklist, phone layout fixes. 107 tests, browser e2e 19/19 and guard e2e 14/14. Nov 2: the seller desk uses the same session system (own `desk.seller_sessions` table, HttpOnly cookie limited to `/desk`, CSRF header, sign-out).

**Nov 3: pack sizes counted in code** (`gateway/policy/units.py`): requests and titles are read for pack sizes; the quantity rule counts requested items (two 6-roll packs fill a 12-roll request; prior purchases count in the same units); more units than asked, or the same number of smaller packs, is held; the AI is told packs are checked and to stay strict about the product. Eval: 167 cases, 0/114 unsafe, 6/53 false alarms; new held-out pack set 13/13. Full history in `docs/eval.md`.

### Phase P1: trust foundations (sandbox), Oct 21–28

| # | Item | Done when |
|---|---|---|
| P1.1 | ✅ **Done Oct 20.** **Passkey signatures.** WebAuthn registration for the user; approving an Intent Mandate or a held Cart Mandate = a passkey signature over its hash; receipts carry the user's signature and public key; the desk and receipt page verify it | A receipt shows "signed by the user's device"; the gateway cannot produce an approval without the device |
| P1.2 | ✅ **Done Oct 21.** **External anchoring of the log.** Periodically publish the chain head to an outside witness (RFC 3161 timestamp authority and/or Sigstore's public transparency log); receipts reference the next anchor | Rewriting history after an anchor is detectable by anyone |
| P1.3 | ✅ **Done Oct 21.** **Merchant-signed carts.** Demo merchants get keys and sign cart offers; the gateway verifies before checking; receipts carry the merchant signature | Prices and lines are attested by the merchant, not just looked up |
| P1.4 | ✅ **Done Oct 21 (focused subset, user's choice).** **AP2 data structures.** Read AP2's published schema; emit Intent/Cart/Payment Mandates in that format alongside ours | An AP2 tool could parse our mandates (verified against the spec's examples) |
| P1.5 | ✅ **Done Oct 21 (no approval chains).** **Fail closed + agent controls.** Verifier or PayPal outage → hold for the human, never error or approve; per-agent keys, budgets and a kill switch; optional approval chain ("manager above N") | Tests for each failure mode; revoking an agent stops it immediately |
| P1.6 | 🟡 **Ready (free Render), tested Oct 21–22; waiting for your deploy** (push, Blueprint, secrets, PayPal webhook: `docs/deploy-render.md`). **Production hygiene.** Hosted (Render) over HTTPS, secrets in the host's secret store, signing key not on disk in plain text, idempotency and retries reviewed, rate limits, structured logs | Judges can click a link; a fresh deploy works from the README |

### Phase P2: work with today's agent tools, Oct 29 – Nov 3

| # | Item | Done when |
|---|---|---|
| P2.1 | ✅ **Done Oct 21 (sandbox demo shop; real-shop test is P3.2).** **Checkout guard for browser agents.** Adds a second, labelled trust level: **unsigned carts** read from a normal shop's checkout page always need the user's passkey approval (even small amounts), and the receipt says "cart read from the shop's page; not signed by the shop". The agent identifies itself openly (no disguised browser). The agent's browser actions go through gateway-controlled tools (Playwright-based); navigation to payment steps (PayPal, card forms) is refused until the gateway has approved a cart for that shop; the cart is read from the checkout page and compared with what the agent claims | A browser agent on a real store cannot reach "Pay" without an approved Cart Mandate |
| P2.2 | **Shopify agent checkout** where available: read the structured checkout instead of scraping | Cart lines and totals come from the shop's own data |
| P2.3 | ✅ **Done Oct 22.** When PayPal returns the browser to the shop (`?token=<order>&PayerID=`) while a permit is active, the guard notes the order; its `confirm_payment` tool sends it to the gateway, which looks the order up with PayPal when its app can see it (completed, same amount and currency as the permit; otherwise refused), or records it as "reported by the guard" when it cannot. The gateway signs and logs a payment confirmation (status `captured` or `payment_reported`), shown on the cart page, the receipt and the seller's dispute page (the desk re-checks its signature). Payment locks again afterwards. **Payment confirmation.** After payment, record the PayPal transaction id and the shop's confirmation against the receipt | The receipt links to the real payment |
| P2.4 | ✅ **Done Oct 22 (Jev not available): 154 cases (112 development incl. 74 real-world style, 42 held-out), Wilson 95% CIs. LLM verifier (refreshed Oct 23, raw replies stored): 1/110 unsafe approvals overall (CI 0–5%; an Autoship subscription); held-out 0/29 unsafe, 5/13 false alarms; development 1/81 unsafe, 2/31 false alarms. Re-asking the same prompts changed 2 answers.** **Bigger, honest evaluation.** 150+ cases with real-world product titles, a held-out attack set, error rates with confidence intervals; Jev if access arrives | `docs/eval.md` reports measured, not just demonstrated, accuracy |
| P2.5 | **Two sandbox merchants** (two business accounts) if PayPal's sandbox allows directing payment to each; otherwise documented | Money visibly reaches the right merchant, or the limit is stated |

### Phase P3: real money, Nov 4–6

| # | Item | Needs from you |
|---|---|---|
| P3.1 | **(Deferred) R1: own live shop.** Live PayPal business account + live REST app; per-order PayPal approval (no vetting needed); buy a ~1 USD item through the gateway; open a real dispute from your personal account and answer it in the desk; refund afterwards | A live PayPal business account (verification can take days: **start now**), live app credentials kept out of the repo |
| P3.2 | 🟡 **Ready Oct 22, waiting for the user (not run: real money):** runbook `docs/live-purchase.md`, workspace `demo/browser-agent-workspace` (guard tools only, no shell/web/file access), guard hardened for real Shopify checkouts (PayPal popups, `EC-` order ids, checkout subdomains, screenshot size). **R2: real third-party shop.** Agent buys a cheap item on a store that allows agent checkout and accepts PayPal; passkey approvals; you approve in PayPal | Choice of store and item, budget cap, your presence for the PayPal step |
| P3.3 | *(only if vetting comes through)* live vault for no-click payments at your own shop | PayPal's eligibility approval; Risk Data Acquisition |

### Submission: Nov 7–12

README, "real vs simulated" table (now with real-money results), tools-used statement, video (passkey moment, fooled agent stopped, real purchase, dispute answered with verified evidence), fresh-clone test, buffer.

## 4. Honest cut list (if time runs short, drop in this order)

1. P3.3 live vault (depends on PayPal's review timeline anyway)
2. P2.5 two sandbox merchants
3. P1.5 approval chains (keep fail-closed and kill switch)
4. P1.4 full AP2 structures (keep the naming)
5. P2.2 Shopify structured checkout (keep the generic checkout guard)

Do not cut: P1.1 passkeys, P1.2 anchoring, P1.3 merchant-signed carts, P2.1 checkout guard, P3.1 real purchase at your own shop.

## 5. Risks and decisions for you

| Topic | Decision or risk |
|---|---|
| **Live PayPal business account** | Needed for R1; verification time unknown, so start this week |
| **Money** | Suggest a hard cap (e.g. 20 USD total) enforced by the gateway itself during real tests |
| **Which real store for R2** | Must allow agent checkout and accept PayPal; avoid stores that block agents |
| **Agent-hostile shops** | Amazon blocks AI shopping agents, does not generally take PayPal, and won a March 2026 preliminary injunction against Perplexity's Comet agent ("with the user's permission, but without authorization by Amazon"; a reported appellate reversal is unconfirmed). Never use such shops; only shops that explicitly allow agents |
| **Terms of service** | Automated purchasing must respect each store's terms; keep a human approving the actual payment |
| **Legal** | Stay non-custodial: money always moves inside PayPal between buyer and merchant; the gateway never holds funds |
| **Personal data** | Real addresses and emails will flow through the agent and logs: minimise what the gateway stores; never log full payment details |
| **Scope vs deadline** | This is ~16 working days of build for 23 calendar days; the cut list protects the submission |

## Sources

- [PayPal: save payment methods (usage types, vetting)](https://developer.paypal.com/platforms/checkout/save-payment-methods)
- [PayPal: save PayPal for purchase later (live vetting, RDA)](https://developer.paypal.com/docs/checkout/save-payment-methods/purchase-later/js-sdk/paypal/)
- [PayPal: vault with the Orders API (limited release)](https://developer.paypal.com/limited-release/vault-payment-methods/orders-api/)
- [PayPal: platform-level saved payments](https://developer.paypal.com/platforms/checkout/save-payment-methods/onboarding/platform)
- [TechCrunch: Shopify opens checkout to browser-based AI agents (Sept 28, 2026)](https://techcrunch.com/2026/09/28/shopify-opens-checkout-to-browser-based-ai-agents/)
- [PYMNTS: Shopify opens store checkouts to AI agents](https://www.pymnts.com/news/artificial-intelligence/2026/shopify-opens-store-checkouts-to-ai-agents/)
- [The AI Insider: Shopify opens checkout as rivals block agentic shopping](https://theaiinsider.tech/2026/09/30/shopify-opens-checkout-to-browser-based-ai-agents-as-rivals-block-agentic-shopping/)
- [Nile: AI shopping agents, the three kinds and how each buys](https://nile.app/blog/ai-shopping-agents/)
