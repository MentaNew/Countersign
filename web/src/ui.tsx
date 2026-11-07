import { Icon } from './icons'
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import type { CheckResult, Line, Money, Verdict } from './api'

export function usePoll<T>(load: () => Promise<T>, deps: unknown[], intervalMs = 2500) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const loadRef = useRef(load)
  loadRef.current = load
  const refresh = useCallback(async () => {
    try {
      setData(await loadRef.current())
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])
  useEffect(() => {
    setData(null)
    refresh()
    const t = setInterval(refresh, intervalMs)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return { data, error, refresh }
}

export const pageCharges = (e: { shipping?: Money | null; tax?: Money | null; discount?: Money | null } | null | undefined) =>
  e ? [{ label: 'Shipping', amount: e.shipping }, { label: 'Tax', amount: e.tax }, { label: 'Discount', amount: e.discount }] : []

export const money = (m: Money | { amount: string | number; currency: string }) =>
  `${Number(m.amount).toFixed(2)} ${m.currency}`

export const when = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '—'

const STATUS_TONE: Record<string, string> = {
  approve: 'ok', active: 'ok', revoked: 'neutral', stopped: 'bad', ordered: 'ok', captured: 'ok', authorized: 'ok',
  escalate: 'warn', pending_user: 'warn', pending_approval: 'warn',
  block: 'bad', blocked: 'bad', rejected: 'bad', failed: 'bad', approval_expired: 'bad', cancelled: 'neutral',
  payment_reported: 'warn',
  // seller side
  verified: 'ok', contest: 'ok', 'evidence strong': 'ok', RESOLVED: 'ok',
  WAITING_FOR_SELLER_RESPONSE: 'warn', add_fulfillment: 'warn', 'evidence medium': 'warn',
  UNDER_REVIEW: 'neutral', WAITING_FOR_BUYER_RESPONSE: 'neutral', refund: 'neutral', review: 'neutral', wait: 'neutral', evidence_not_accepted: 'warn',
  simulated: 'warn', unverified: 'bad', 'evidence weak': 'bad',
}

const STATUS_LABEL: Record<string, string> = {
  pending_user: 'awaiting you', pending_approval: 'awaiting you', approve: 'approved',
  escalate: 'escalated', block: 'blocked', ordered: 'order created', captured: 'paid',
  approval_expired: 'approval expired', revoked: 'cancelled', payment_reported: 'paid (reported)',
  WAITING_FOR_SELLER_RESPONSE: 'your response needed', WAITING_FOR_BUYER_RESPONSE: 'waiting for buyer',
  UNDER_REVIEW: 'PayPal reviewing', RESOLVED: 'resolved', add_fulfillment: 'add delivery proof',
  contest: 'contest', refund: 'refund',
}

export function Badge({ value }: { value: string }) {
  return <span className={`badge ${STATUS_TONE[value] ?? 'neutral'}`}>{STATUS_LABEL[value] ?? value}</span>
}

export function Page({ title, kicker, actions, children }: {
  title: ReactNode; kicker?: ReactNode; actions?: ReactNode; children: ReactNode
}) {
  return (
    <main className="page">
      <header className="page-head">
        <div>
          {kicker && <div className="kicker">{kicker}</div>}
          <h1>{title}</h1>
        </div>
        {actions && <div className="actions">{actions}</div>}
      </header>
      {children}
    </main>
  )
}

export function Card({ title, children, tone }: { title?: ReactNode; children: ReactNode; tone?: string }) {
  return (
    <section className={`card ${tone ?? ''}`}>
      {title && <h2>{title}</h2>}
      {children}
    </section>
  )
}

export function Notice({ tone, children }: { tone: 'ok' | 'warn' | 'bad' | 'neutral'; children: ReactNode }) {
  return <div className={`notice ${tone}`}>{children}</div>
}

export function Hash({ value, n = 12 }: { value: string | null | undefined; n?: number }) {
  if (!value) return <span className="muted">—</span>
  return <code className="hash" title={value}>{value.slice(0, n)}…</code>
}

export function LineTable({ lines, currency, total, flagged = [], charges = [] }: {
  lines: Line[]; currency: string; total: string; flagged?: string[]
  /** Shipping, tax, discount added by the shop at checkout. */
  charges?: { label: string; amount: Money | null | undefined }[]
}) {
  return (
    <div className="table-wrap">
      <table className="lines">
        <thead>
          <tr><th>Item</th><th>SKU</th><th className="num">Qty</th><th className="num">Unit</th><th className="num">Line total</th></tr>
        </thead>
        <tbody>
          {lines.map((l, i) => (
            <tr key={i} className={l.item === null || flagged.includes(l.sku) ? 'flag' : ''}>
              <td>
                {l.title}
                {l.item === null && <div className="flag-note">Not part of what you asked for</div>}
                {l.item !== null && flagged.includes(l.sku) && (
                  <div className="flag-note">Claimed as item #{l.item}, but does not look like it (verifier)</div>
                )}
              </td>
              <td><code>{l.sku}</code></td>
              <td className="num">{l.qty}</td>
              <td className="num">{money(l.unit_price)}</td>
              <td className="num">{(Number(l.unit_price.amount) * l.qty).toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          {charges.filter((c) => c.amount).map((c) => (
            <tr key={c.label}><td>{c.label}</td><td /><td /><td /><td className="num">{c.label === 'Discount' ? '-' : ''}{Number(c.amount!.amount).toFixed(2)}</td></tr>
          ))}
          <tr><td>Total</td><td /><td /><td /><td className="num strong">{Number(total).toFixed(2)} {currency}</td></tr>
        </tfoot>
      </table>
    </div>
  )
}

const label = (name: string) => {
  const s = name.replaceAll('_', ' ')
  return s.charAt(0).toUpperCase() + s.slice(1)
}

export function Checks({ checks }: { checks: CheckResult[] }) {
  return (
    <ul className="checks">
      {checks.map((c) => (
        <li key={c.name} className={c.passed ? 'pass' : 'fail'}>
          <span className="mark" aria-hidden>{c.passed ? '✓' : '✕'}</span>
          <span className="name">{label(c.name)}</span>
          {c.detail && <span className="detail">{c.detail}</span>}
        </li>
      ))}
    </ul>
  )
}

function Bar({ label, p, tone, threshold }: { label: string; p: number; tone: string; threshold?: number }) {
  return (
    <div className="bar-row">
      <span className="bar-label">{label}</span>
      <span className="bar"><span className={`fill ${tone}`} style={{ width: `${Math.round(p * 100)}%` }} />
        {threshold !== undefined && <span className="tick" style={{ left: `${threshold * 100}%` }} title={`threshold ${threshold}`} />}
      </span>
      <span className="bar-value">{p.toFixed(2)}</span>
    </div>
  )
}

export function VerdictPanel({ v, texts }: { v: Verdict; texts: string[] }) {
  const t = v.thresholds
  return (
    <div className="verdict">
      <h3>Policy checks <span className="muted small">deterministic code</span></h3>
      <Checks checks={v.hard_checks} />
      <h3>Does the cart match what you asked for? {v.semantic && <span className="muted small">{v.semantic.model}</span>}</h3>
      {v.semantic ? (
        <>
          <Bar label="matches" p={v.semantic.match.matches} tone="ok" threshold={t.min_match} />
          <Bar label="partial" p={v.semantic.match.partial} tone="warn" />
          <Bar label="mismatch" p={v.semantic.match.mismatch} tone="bad" />
          <Bar label="unrequested items" p={v.semantic.extra_items} tone="bad" threshold={t.max_extra_items} />
          {v.semantic.rationale && <p className="rationale">“{v.semantic.rationale}”</p>}
          {v.semantic.model.startsWith('groq') && (
            <p className="muted small">LLM fallback verifier: probabilities are self-reported, not calibrated.</p>
          )}
        </>
      ) : (
        <p className="muted">Not run: a policy check already failed.</p>
      )}
      {v.injection && (
        <>
          <h3>Merchant text the agent relied on <span className="muted small">{v.injection.model}</span></h3>
          {texts.map((txt, i) => (
            <div key={i} className="ctx">
              <Bar label="instructions to the agent" p={v.injection!.per_text[i] ?? 0} tone="bad" threshold={t.max_injection} />
              <pre className="ctx-text">{txt}</pre>
            </div>
          ))}
        </>
      )}
    </div>
  )
}

/** A shop's initials on a neutral tint: identity only, never data. */
export function Monogram({ name, size }: { name: string; size?: 'lg' }) {
  const base = name.replace(/^https?:\/\//, '').replace(/^www\./, '').split(/[./]/)[0]
  const parts = base.split(/[-_\s]+/).filter(Boolean)
  const text = (parts.length > 1 ? parts[0][0] + parts[1][0] : base.slice(0, 2)).toUpperCase()
  return <span className={`mono-tile ${size ?? ''}`} aria-hidden>{text}</span>
}

/** How an approval was made: signed on the person's device, a password session, or automatically by policy. */
export function Seal({ signed, human, short }: { signed?: boolean; human: boolean; short?: boolean }) {
  if (!human) return <span className="seal auto" title="Approved automatically: inside your permission and below your ask-me amount"><Icon name="bolt" />{short ? 'auto' : 'Automatic, within your rules'}</span>
  if (signed) return <span className="seal signed" title="Signed on your own device with your passkey"><Icon name="fingerprint" />{short ? 'passkey' : 'Signed with your passkey'}</span>
  return <span className="seal password" title="Approved in a password session: no device signature"><Icon name="key" />{short ? 'password' : 'Password only, not device-signed'}</span>
}

/** Short shop name for display: host without www. */
export const shopName = (id: string) => id.replace(/^https?:\/\//, '').replace(/^www\./, '')

export const ago = (iso: string | null | undefined) => {
  if (!iso) return '—'
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

const CHECK_PLAIN: Record<string, string> = {
  lines_in_intent: 'not part of your permission',
  budget: 'over your budget',
  quantity: 'more than you allowed',
  merchant_allowed: 'shop not in your permission',
  unit_price: 'price above your limit',
  currency: 'wrong currency',
  not_expired: 'permission expired',
  purchase_count: 'too many purchases under this permission',
  agent_spend_cap: "agent's 30-day cap reached",
  agent_active: 'agent switched off',
  intent_agent_match: 'permission belongs to another agent',
  merchant_signed_cart: "shop's signature check failed",
  known_products: 'product not in the shop catalog',
  page_cart_adds_up: "the page's totals don't add up",
  checkout_on_shop_site: "checkout page is not on the shop's site",
  non_empty_cart: 'empty cart',
}

/** A gateway reason in plain words (the exact text stays available next to it). */
export function plainReason(r: string): string {
  const s = r.trim()
  if (/^cart may not match the intent/.test(s)) return "may not match what you asked for"
  if (/^cart may contain unrequested items/.test(s)) return "may include items you didn't ask for"
  if (/^lines that do not look like what was requested: /.test(s)) return `doesn't look like what you asked for: ${s.split(': ').slice(1).join(': ')}`
  if (/^merchant text the agent used looks like instructions/.test(s)) return 'the shop page tried to give the agent instructions'
  if (/is above the ask-me threshold/.test(s)) return `above your ask-me amount (${s.split('threshold ').pop()})`
  if (/^cart read from the shop's page/.test(s)) return "this shop doesn't sign carts: needs your passkey"
  if (/^all checks passed$/.test(s)) return 'within your permission'
  if (/^semantic check unavailable/.test(s)) return 'AI check unavailable: held for you'
  const m = s.match(/^([a-z_]+): /)
  if (m && CHECK_PLAIN[m[1]]) return CHECK_PLAIN[m[1]]
  return s
}
