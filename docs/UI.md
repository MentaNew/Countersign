# UI design (web app)

Method: the product-ui skill (`.claude/skills/product-ui`), applied to the existing React app (lesson 26: port the
design system into the app, don't build a static copy next to it).

## Discovery

- **Who works in it every day:** the person agents spend for (the buyer). They are interrupted by agents asking for
  approval and want to clear that quickly and safely. A seller's staff use the separate `/seller` desk.
- **Their loop:** *get notified → see what is waiting → check one (what I allowed vs what the agent asked) →
  approve with my device or reject → it is paid and proven → manage my agents.*
- **Queue row:** a request waiting for or decided by the person: a **cart** (purchase) or a **permission**
  (intent). A cart's children: its lines, the checks, the AI replies, its audit-log entries, its receipt.
- **Status vocabulary → 4 meanings:**

  | Meaning | Statuses |
  |---|---|
  | red: stopped | blocked, rejected, failed, approval expired |
  | amber: needs you / in progress | awaiting you (pending), escalated, payment reported |
  | green: done | approved, authorized, order created, paid, active |
  | grey: out of scope | cancelled, revoked (permission) |

- **Core story:** *what you allowed vs what the agent asked for.* It becomes the cart's main view.
- **AI output:** shown as a proposal with proof: scores with the threshold, the model's own words (exact replies),
  and the code's verdict. Never as "AI insights".

## Decision log

| Date | Decision | Options | Why | Decided by |
|---|---|---|---|---|
| Nov 6 | Product shell + work queue landing, cart in a side drawer (j/k triage), full page for deep work | shell + queue / keep top bar and polish | a tool used daily wants a queue, density and keyboard | user |
| Nov 6 | Inter + one indigo accent; JetBrains Mono only for ids and hashes; four tinted status colours | Inter+indigo / Plex+teal / keep blue | calm, familiar SaaS base; accent never encodes data | user |
| Nov 6 | Shop monograms (initials on a neutral tint), no third-party logos | monograms / favicons / none | identity without using brand assets | user |
| Nov 6 | Signatures: allowed vs asked; device seal; payment lock timeline | (all three chosen) | details only this product can have | user |
| Nov 6 | Fonts self-hosted via @fontsource (no Google Fonts) | self-host / CDN | the online CSP only allows the app's own files | Claude (CSP constraint) |
| Nov 6 | Seller desk keeps its own teal accent and "Seller side" tag on the same system | same / separate look | the two sides must never be confused | Claude (existing rule) |

## Honesty rules applied

- All values come from the gateway's API; nothing is invented. Agent names come from the Agents list.
- Derived values say how they are computed (e.g. "budget left = cap − spent under this permission").
- Notifications and saved views are browser-local and say so.

## What was built (Nov 6–7)

- **Shell:** sidebar (Inbox with a waiting count, Agents, Audit log, Security; Seller desk and Sign out at the
  bottom), top bar with breadcrumb and a global search (`/`). Narrow screens: the sidebar becomes a menu.
- **Inbox = work queue:** carts and permissions in one grid, five tabs with counts (Needs you · Approved · Stopped ·
  Permissions · All carts), a filter, keyboard triage (`j`/`k`, `Enter`, `Esc`), plain-language reasons
  (`plainReason()` in `ui.tsx`; the exact gateway text stays visible in the cart). Lands on "Needs you" when
  something waits, otherwise "All carts". The setup checklist is one slim card with the next step.
- **Drawer** (`?open=<id>`, shareable): cart header (shop monogram, amount, status, agent, time, device seal, source),
  tabs Decision · Lines · Checks · AI replies · Timeline; "open full page" for `/purchases/:id`. Next to the drawer the
  queue collapses to status, shop (+ reason) and amount.
- **Signatures:** *You allowed vs the agent asked* (each permission item, shops, budget and ask-me line next to the
  cart, ✓/✕/! from the gateway's own checks); the **device seal** (signed with passkey / password only / automatic)
  on carts, permissions and in the queue; the **timeline** from the cart's audit-log entries, showing where the
  payment lock opened for ordinary-shop carts.
- **Design system:** Inter + JetBrains Mono self-hosted (@fontsource), indigo accent, four tinted status pills,
  16px stroke icons (`icons.tsx`), seller desk on the same system with its teal accent.
- **Server fix found by the review:** the cart list now marks held carts past their approval window as expired, like
  the cart page did (the queue and the drawer disagreed).

## Verification

- Before/after screenshots of every screen, desktop 1440 and phone 390, on the same seeded test data (smoke agent,
  fooled agent, checkout guard, two simulated disputes); side-by-side comparisons kept outside the repo.
- Scripted interaction test: 12/13 (the miss was the test's selector matching the "Approved" tab).
- `tests/e2e/browser_flow.py` 19/19 and `tests/e2e/guard_flow.py` 14/14 through the new screens; 128 pytest.

## Lessons from this pass

- **CSS class names collide across a growing stylesheet:** a table header `th.side` picked up the sidebar's
  `.side` rules, and earlier `.tick` (match bars) broke the checklist. Prefix new component classes.
- **A list and a detail must apply the same rules:** the cart list ignored approval expiry that the cart page applied.
- **Developer text leaks into product copy:** check names (`lines_in_intent`) and probabilities in the queue; map
  them to plain words and keep the exact text one level down.

## Still to do

- Personality pass (`.claude/skills/product-personality`) once this backbone is approved.
- Dark mode (token swap).
- Agents, Audit log and Security pages are restyled but keep their earlier layout.
