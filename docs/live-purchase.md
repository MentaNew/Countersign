# P3.2: a real purchase on a real shop

A browser agent buys one cheap item on a real online shop that accepts PayPal, through the
checkout guard. Real money, so you are present for the passkey and the PayPal step.

## What is real and what is not

- **Real:** the shop, its cart and checkout, the PayPal payment (your own PayPal account), the
  item delivered.
- **Unchanged:** the gateway runs on your laptop as usual. For a shop like this it never creates
  the PayPal order itself: the shop does, after the gateway's signed checkout permit.
- **Expected label:** the payment shows as *reported by the checkout guard*, not *checked with
  PayPal*, because the shop's PayPal order belongs to the shop's account and the gateway's
  (sandbox) PayPal app cannot look it up. Check it in your PayPal activity.

## Pick the shop and item

- A **Shopify** store (the guard reads Shopify carts): its pages answer at `<shop>/cart.js`.
- Accepts **PayPal** at checkout.
- A cheap item (a few dollars), shipping to you, and a small cap (e.g. 15 USD with shipping and
  tax).
- Its terms do not forbid automated or agent purchases.

## Before

1. Database, gateway and web app running (see `docs/run-locally.md`). The demo shops are not
   needed.
2. In the web app, **Agents → New agent**: kind *Shops on websites (checkout guard)*, a spend
   cap (e.g. 20 USD). Copy the key shown once.
3. A passkey registered on the **Security** page (required: carts read from a page always need
   it).

## Run

```bash
cd demo/browser-agent-workspace
export GUARD_AGENT_KEY=<the browser agent's key>
claude
```

The workspace gives the agent only the guard's tools: no shell, no direct web access, no file
access outside the workspace. A visible Chrome window opens: you can watch every step.

Ask, for example: *"Buy one <item> from <shop address>, budget 15 USD including shipping and
tax, ship to <your address>. The permission ends tonight."*

1. Approve the permission in the web app (passkey).
2. The agent shops; if it tries the PayPal button early, the guard blocks it.
3. On the checkout page it calls `checkout_ready`. Open the cart in the web app: compare the
   lines, shipping, tax and total with the screenshot, then approve with your passkey.
4. The agent clicks PayPal; **you** log in and confirm in the PayPal window.
5. Back on the shop's thank-you page, the agent calls `confirm_payment`. Check the receipt.

## Record for the submission

Screen-record the run. Afterwards: the receipt page, the audit log entries, PayPal's activity
entry for the payment, and the shop's order email.

## If something goes wrong

| Symptom | What to do |
|---|---|
| "could not read a cart from this shop" | Not a Shopify store, or the cart is empty |
| "no order total on this page" | Enter the shipping address first, so shipping and tax are shown |
| The shop shows a captcha | Solve it yourself in the Chrome window; the agent waits |
| PayPal button stays blocked after approval | The cart changed after approval: call `checkout_ready` again |
| `confirm_payment` says no payment seen | PayPal did not return to the shop (closed window?); check PayPal and the shop's email |
