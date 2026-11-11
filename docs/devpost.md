# Devpost submission draft

Draft text for the Devpost form. Check the field names and length limits on the hackathon's submission page, then paste section by section. Replace the bracketed parts.

---

## Name

Agent Spend Guardrail + Dispute Desk

## Tagline (one line)

Let AI agents pay with PayPal, only for the exact cart you approved, with proof a seller can use in a dispute.

## Inspiration

AI agents can now browse shops and fill carts, but handing one a payment method is all or nothing. One poisoned product page, a wrong variant or a sneaky subscription, and the agent spends money you never meant to spend. When that happens, nobody can prove afterwards what you actually authorised: not you, not the seller, not PayPal.

## What it does

- **You give an agent a signed permission**, not your wallet: what to buy, the budget, which shops, until when. You approve it with your passkey (Touch ID).
- **The agent can only pay through the gateway.** The gateway prices the cart itself, runs plain-code rules (budget, quantity, shops, expiry), then AI checks: does the cart match what you asked for, and did the shop's page try to give the agent instructions?
- **Good carts go through; doubtful ones wait for you.** You see every line, why it was held, and the AI's own words, and approve with your passkey. Carts that break a rule are blocked.
- **It works on ordinary websites too.** A checkout guard drives the browser for the agent, keeps PayPal locked until you approve the exact cart it read from the shop, and relocks if the cart changes.
- **Every purchase gets a signed receipt**, anchored in a tamper-evident log witnessed by a public timestamp authority. A **seller-side dispute desk** imports PayPal disputes, verifies the buyer's receipt on its own, and builds the evidence packet; staff choose the response.
- **It guards PayPal's own agent tools.** In front of PayPal's official MCP server, read-only tools pass through; a refund, invoice or order waits in your Inbox with its exact arguments, and runs once, only after your passkey approval.
- **A fleet of agents, each in its own lane:** separate keys, 30-day caps, and a one-click "stop all agents".
- **Judges can try it in 60 seconds:** the live site has a judge mode where a demo agent tries six carts and you decide.

## How we built it

- Gateway: Python, FastAPI, MCP (the agent's only door), PostgreSQL; PayPal Orders v2 and Disputes APIs in the sandbox.
- AI checks: GroqCloud `openai/gpt-oss-20b` (typed JSON cart matching) and `meta-llama/llama-prompt-guard-2-86m`; if a check fails or times out, the cart is held, never approved.
- Trust: Ed25519-signed intents and receipts, WebAuthn passkey signatures over what you saw, a hash-chained audit log witnessed by RFC 3161 timestamp authorities (DigiCert, FreeTSA), shop-signed cart offers, AP2 v0.2 Checkout and Payment Mandates (focused subset).
- Checkout guard: Playwright driving Chrome, as an MCP server for the agent.
- PayPal guard: an MCP proxy in front of PayPal's official MCP server (`@paypal/mcp`, Agent Toolkit), approvals bound to a hash of the exact arguments.
- Agent: Claude through Claude Code and the Claude Agent SDK.
- Web app: React + TypeScript (buyer inbox, approvals, audit log, receipts; seller dispute desk).
- Hosting: Render (one Blueprint: gateway, dispute desk, demo shops, static web app, PostgreSQL).
- Evaluation: 167 labelled carts, 55 held out, cached model replies.

## Challenges

- A real Claude agent refused our poisoned product page, so the "fooled agent" scene had to be scripted; the gateway still has to stop a fooled agent, and it does.
- PayPal vault tokens belong to one merchant, so a third-party gateway cannot pay at any shop. We made PayPal's own checkout the payment step and the gateway the gatekeeper in front of it.
- Asking the same model the same question twice can give different answers. We store every reply word for word and measure on a held-out set.

## Accomplishments

- 0 of 114 carts that should have been stopped were paid without asking (95% CI 0–3%); all 15 held-out hidden-instruction attacks were caught; pack sizes are counted in code (13 of 13 new held-out pack cases right).
- Real sandbox disputes answered from the desk (refund offer, evidence accepted).
- Receipts anyone can check with public keys only: gateway signature, the buyer's device signature, the shop's signature, the outside timestamp.

## What we learned

- The model is the weakest link, so it never has the last word: money rules are plain code, and doubt means "ask the person".
- Proof is worth more when it is checkable by the other side: the seller verifies the buyer's receipt on its own, without trusting the buyer's gateway.

## What's next

- Run inside PayPal as a wallet feature, so the saved consent is released only for approved carts at any merchant.
- Structured agent checkouts (e.g. Shopify) instead of reading pages; more shops; calibrated checks.

## Built with

python, fastapi, postgresql, react, typescript, paypal-orders-api, paypal-disputes-api, paypal-agent-toolkit, paypal-mcp, groqcloud, gpt-oss, llama-prompt-guard, claude, claude-agent-sdk, mcp, webauthn, playwright, render

## Links

- Code: [GitHub URL]
- Video: [YouTube URL]
- Live demo: https://spend-guardrail-ttdy.onrender.com

## Testing instructions (for judges)

1. Open https://spend-guardrail-ttdy.onrender.com. It runs on free hosting: the first visit can take up to a minute to wake up.
2. Sign in with the password: [the live USER_PASSWORD].
3. On the Inbox, the **judge mode** card: *Ask for a new permission* → open it → approve. Then click the cart buttons one by one and open each result in the Inbox (why it was decided, the AI's replies, the receipt).
4. Seller side: https://spend-guardrail-ttdy.onrender.com/seller, password [the live SELLER_PASSWORD].
5. Everything uses the PayPal **sandbox**; no real money moves. The repo README explains how to run it locally, including the PayPal guard with Claude Code.
