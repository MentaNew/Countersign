// One cart, as the drawer and the full page show it. Tabs answer one question each:
// Decision (what you allowed vs what the agent asked, and your call), Lines, Checks, AI replies, Timeline.
import { useEffect, useState, type ReactNode } from 'react'
import { api, type CheckResult, type Intent, type LogEntry, type ModelExchange, type Purchase } from '../api'
import { Icon } from '../icons'
import { describeError, signApproval } from '../passkey'
import { Link } from '../router'
import {
  Badge, Card, Hash, LineTable, Monogram, Notice, Seal, VerdictPanel, money, pageCharges, plainReason, shopName, usePoll, when,
} from '../ui'

export type CartTab = 'decision' | 'lines' | 'checks' | 'ai' | 'timeline'

function useCountdown(iso: string | null) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])
  if (!iso) return null
  const s = Math.max(0, Math.floor((new Date(iso).getTime() - now) / 1000))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

const check = (checks: CheckResult[] | undefined, name: string) => checks?.find((c) => c.name === name)

type Mark = 'ok' | 'no' | 'warn' | 'none'
const MARK: Record<Mark, string> = { ok: '✓', no: '✕', warn: '!', none: '–' }

/** Signature: what the permission allows, line by line next to what the agent asked for. */
function AllowedVsAsked({ p, intent }: { p: Purchase; intent: Intent }) {
  const m = intent.mandate
  const hc = p.verdict.hard_checks
  const flagged = p.verdict.semantic?.flagged_skus ?? []
  const qty = check(hc, 'quantity')
  const unit = check(hc, 'unit_price')
  const rows: { allowed: ReactNode; asked: ReactNode; mark: Mark; why?: string }[] = []

  m.items.forEach((it, i) => {
    const lines = p.lines.filter((l) => l.item === i)
    const bad = lines.filter((l) => flagged.includes(l.sku) || (unit && !unit.passed && unit.detail.includes(l.sku)))
    const overQty = qty && !qty.passed && qty.detail.includes(`item ${i} `)
    const mark: Mark = !lines.length ? 'none' : bad.length || overQty ? 'no' : 'ok'
    rows.push({
      allowed: <><span className="what">{it.qty} × {it.description}</span>{it.max_unit_price && <div className="muted small">at most {money(it.max_unit_price)} each</div>}</>,
      asked: lines.length
        ? lines.map((l, j) => <div key={j}>{l.qty} × {l.title} <span className="muted">@ {money(l.unit_price)}</span>
            {flagged.includes(l.sku) && <div className="small">does not look like what you allowed</div>}</div>)
        : <span className="muted">nothing for this item in this cart</span>,
      mark,
      why: overQty ? qty!.detail : undefined,
    })
  })
  p.lines.filter((l) => l.item === null || l.item >= m.items.length).forEach((l) => rows.push({
    allowed: <span className="muted">not in your permission</span>,
    asked: <>{l.qty} × {l.title} <span className="muted">@ {money(l.unit_price)}</span></>,
    mark: 'no',
  }))
  const shopOk = check(hc, 'merchant_allowed')?.passed !== false
  rows.push({
    allowed: <><span className="what">Shops</span><div className="muted small">{m.merchants.map(shopName).join(', ')}</div></>,
    asked: <>{shopName(p.merchant_id)}</>,
    mark: shopOk ? 'ok' : 'no',
  })
  const budget = check(hc, 'budget')
  rows.push({
    allowed: <><span className="what">Budget {money(m.max_total)}</span><div className="muted small">in total, over at most {m.max_purchases} purchases</div></>,
    asked: <>{Number(p.total).toFixed(2)} {p.currency}{budget?.detail && <div className="muted small">{budget.detail}</div>}</>,
    mark: budget ? (budget.passed ? 'ok' : 'no') : 'none',
  })
  if (m.approval_above) {
    const above = Number(p.total) > Number(m.approval_above.amount)
    rows.push({
      allowed: <><span className="what">Ask me above {money(m.approval_above)}</span></>,
      asked: <>{above ? 'above it: held for you' : 'below it'}</>,
      mark: above ? 'warn' : 'ok',
    })
  }
  return (
    <>
      <table className="ava">
        <thead><tr><th className="col">You allowed</th><th className="col">The agent asked for</th><th /></tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className={r.mark === 'no' ? 'no' : r.mark === 'ok' ? 'ok' : ''} title={r.why}>
              <td>{r.allowed}</td><td className="asked">{r.asked}</td>
              <td className={`mark ${r.mark}`} aria-label={r.mark}>{MARK[r.mark]}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="ava-legend">✓ within your permission · ✕ outside it · ! needs your OK · from the gateway's own checks and the shop's prices, not from the agent.</p>
    </>
  )
}

const EVENT: Record<string, { label: string; icon: string; cls?: string }> = {
  purchase_decision: { label: 'Agent asked; gateway checked the cart', icon: 'cart' },
  purchase_approved_by_human: { label: 'You approved this exact cart', icon: 'fingerprint', cls: 'done' },
  purchase_rejected_by_human: { label: 'You rejected the cart', icon: 'x', cls: 'stop' },
  purchase_blocked_on_recheck: { label: 'Blocked when re-checked before paying', icon: 'x', cls: 'stop' },
  approval_expired: { label: 'Your approval window expired', icon: 'clock', cls: 'stop' },
  purchase_authorized: { label: 'Authorized by the gateway', icon: 'check', cls: 'done' },
  checkout_permit_issued: { label: 'Payment unlocked for this exact cart', icon: 'unlock', cls: 'lock' },
  order_created: { label: 'PayPal order created', icon: 'receipt', cls: 'done' },
  order_failed: { label: 'PayPal order failed', icon: 'x', cls: 'stop' },
  receipt_issued: { label: 'Receipt signed', icon: 'receipt', cls: 'done' },
  payment_captured: { label: 'Paid', icon: 'check', cls: 'done' },
  capture_failed: { label: 'Payment capture failed', icon: 'x', cls: 'stop' },
  payment_confirmed: { label: 'Payment recorded', icon: 'check', cls: 'done' },
}
const human = (e: string) => (e.charAt(0).toUpperCase() + e.slice(1)).replaceAll('_', ' ')

/** Signature: the cart's story from the audit log; for ordinary-shop carts, where the payment lock opened. */
function Timeline({ p, log }: { p: Purchase; log: LogEntry[] }) {
  const events = [...log].sort((a, b) => a.seq - b.seq)
  const fromPage = p.cart_source === 'page'
  const decision = events.find((e) => e.event === 'purchase_decision')
  return (
    <ul className="track">
      {events.map((e) => {
        const ev = EVENT[e.event] ?? { label: human(e.event), icon: 'log' }
        let sub: ReactNode = null
        if (e === decision) sub = <>decision: <Badge value={p.decision} />{fromPage && ' · cart read from the shop’s own checkout by the checkout guard'}</>
        if (e.event === 'checkout_permit_issued') sub = <>until {when(String(e.payload.expires_at ?? ''))} · PayPal opens only for this cart</>
        if (e.event === 'payment_confirmed') sub = <>PayPal order <code>{String(e.payload.paypal_order_id ?? '')}</code>{e.payload.checked_with_paypal ? ' · checked with PayPal' : ' · reported by the checkout guard'}</>
        return (
          <li key={e.seq} className={ev.cls ?? ''}>
            <span className="dot"><Icon name={ev.icon} /></span>
            <span><div className="what">{ev.label}</div>{sub && <div className="sub">{sub}</div>}</span>
            <span className="at" title={e.at}>#{e.seq} · {new Date(e.at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}</span>
          </li>
        )
      })}
      {p.status === 'pending_approval' && (
        <li className="lock">
          <span className="dot"><Icon name="lock" /></span>
          <span><div className="what">{fromPage ? 'Payment locked until you approve' : 'Waiting for your decision'}</div>
            <div className="sub">{fromPage ? 'the checkout guard blocks PayPal until the gateway issues a permit for this exact cart' : 'nothing is paid until you approve'}</div></span>
          <span className="at">now</span>
        </li>
      )}
    </ul>
  )
}

function parseReply(text: string): { reasoning?: string; content?: string } {
  try {
    const m = JSON.parse(text).choices[0].message
    return { reasoning: m.reasoning, content: m.content }
  } catch {
    return { content: text }
  }
}

function ModelReplies({ exchanges }: { exchanges: ModelExchange[] }) {
  if (!exchanges.length) return <div className="empty"><strong>No AI replies kept for this cart</strong>Either a rule decided before any AI check, the offline checker was used, or the cart predates Nov 1.</div>
  return (
    <>
      <p className="muted small" style={{ marginTop: 0 }}>Exact requests and replies, kept with this decision. Their fingerprint is in the audit log, so they cannot be changed later without it showing.</p>
      {exchanges.map((ex, i) => {
        const r = parseReply(ex.response_text)
        const user = ex.request.messages?.find((m) => m.role === 'user')?.content
        return (
          <div key={i} className="exchange">
            <div className="exchange-head"><code>{ex.request.model}</code> · HTTP {ex.status} · {ex.latency_ms} ms{ex.attempt > 1 ? ` · attempt ${ex.attempt}` : ''}</div>
            {user && <><div className="label">Asked about</div><pre>{user}</pre></>}
            {r.reasoning && <><div className="label">Model's reasoning</div><pre>{r.reasoning}</pre></>}
            <div className="label">Answer</div>
            <pre>{r.content}</pre>
          </div>
        )
      })}
    </>
  )
}

export function usePurchase(id: string) {
  const q = usePoll(() => api.purchase(id), [id])
  const intentId = q.data?.intent_id
  const intent = usePoll(() => (intentId ? api.intent(intentId) : Promise.resolve(null)), [intentId], 15000)
  const log = usePoll(() => api.log({ purchase_id: id }), [id], 5000)
  return { q, intent: intent.data, log: log.data ?? [] }
}

/** Header facts shared by the drawer and the full page. */
export function CartHeader({ p, agentName }: { p: Purchase; agentName?: string }) {
  return (
    <>
      <div className="row1">
        <Monogram name={p.merchant_id} size="lg" />
        <div>
          <div className="title">{Number(p.total).toFixed(2)} {p.currency} at {shopName(p.merchant_id)}</div>
          <div className="muted small"><code>{p.purchase_id}</code></div>
        </div>
        <Badge value={p.status} />
      </div>
      <div className="meta">
        <span><Icon name="agent" /> {agentName ?? p.agent_id ?? 'agent'}</span>
        <span><Icon name="clock" /> {when(p.created_at)}</span>
        {(p.status === 'ordered' || p.status === 'captured' || p.status === 'authorized' || p.status === 'payment_reported')
          && <Seal human={!!p.approved_by} signed={p.user_signed} />}
        {p.cart_source === 'page' && <span><Icon name="page" /> read from the shop's page</span>}
        {p.cart_source === 'merchant_signed' && <span><Icon name="store" /> signed by the shop</span>}
      </div>
    </>
  )
}

export default function PurchaseDetail({ id, tab, onTab }: { id: string; tab: CartTab; onTab: (t: CartTab) => void }) {
  const { q, intent, log } = usePurchase(id)
  const keys = usePoll(() => api.passkeys(), [], 30000)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const left = useCountdown(q.data?.status === 'pending_approval' ? q.data.approval_expires_at : null)
  const p = q.data

  const decide = async (approve: boolean) => {
    if (!p) return
    setBusy(true)
    setError(null)
    try {
      // The cart hash binds the approval to exactly this cart; approving, your device signs it.
      const webauthn = approve ? await signApproval(() => api.purchaseApprovalOptions(id)) : undefined
      await api.decidePurchase(id, approve, p.cart_hash, webauthn)
      await q.refresh()
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusy(false)
    }
  }

  if (!p) return <div className="dbody">{q.error ? <Notice tone="bad">{q.error}</Notice> : <p className="muted">Loading…</p>}</div>
  const fromPage = p.cart_source === 'page'
  const exchanges = p.model_exchanges ?? []
  const failed = p.verdict.hard_checks.filter((c) => !c.passed).length
  const tabs: [CartTab, string, number | null][] = [
    ['decision', 'Decision', null], ['lines', 'Lines', p.lines.length], ['checks', 'Checks', failed || null],
    ['ai', 'AI replies', exchanges.length], ['timeline', 'Timeline', log.length],
  ]

  return (
    <>
      <div className="dtabs" role="tablist">
        {tabs.map(([k, label, n]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? 'on' : ''} onClick={() => onTab(k)}>
            {label}{n !== null && <span className="n">{n}</span>}
          </button>
        ))}
      </div>
      <div className="dbody">
        {tab === 'decision' && (
          <>
            {p.status === 'pending_approval' && (
              <Card title={<>Your decision is needed <span className="meta">expires in {left}</span></>} tone="focus">
                <ul className="reasons">{p.reasons.map((r, i) => <li key={i}>{plainReason(r)}{plainReason(r) !== r && <div className="muted small">{r}</div>}</li>)}</ul>
                {fromPage && (
                  <Notice tone="warn"><strong>This shop does not sign its carts.</strong> The checkout guard read it from the
                    shop's own checkout page (see Lines for the screenshot). Your passkey signs exactly these lines,
                    shipping, tax and total, and payment opens only for this cart.</Notice>
                )}
                {error && <Notice tone="bad">{error}</Notice>}
                <div className="decide-bar">
                  <button className="primary" disabled={busy} onClick={() => decide(true)}>
                    <Icon name="fingerprint" />{keys.data?.required || fromPage ? 'Approve with your passkey' : 'Approve this cart'}
                  </button>
                  <button className="danger" disabled={busy} onClick={() => decide(false)}>Reject</button>
                  <span className="countdown">signs cart <Hash value={p.cart_hash} n={10} /></span>
                </div>
              </Card>
            )}
            {p.status === 'blocked' && <Notice tone="bad"><strong>Blocked by the gateway.</strong> {p.error ?? p.reasons.join('; ')}</Notice>}
            {p.status === 'failed' && <Notice tone="bad"><strong>Order failed at PayPal.</strong> {p.error}</Notice>}
            {p.payment_confirmation && (() => {
              const c = p.payment_confirmation.confirmation
              return <Notice tone={c.checked_with_paypal ? 'ok' : 'warn'}>
                <strong>{c.checked_with_paypal ? 'Paid, checked with PayPal' : 'Payment reported by the checkout guard'}</strong>:
                PayPal order <code>{c.paypal_order_id}</code>{c.checked_with_paypal ? ` (${c.status}, ${c.amount} ${c.currency})` : '. Check it in your PayPal account.'}
              </Notice>
            })()}
            {p.status === 'authorized' && p.checkout_permit && !p.payment_confirmation && (
              <Notice tone="ok"><strong>Payment unlocked for this exact cart</strong> until {when(p.checkout_permit.permit.expires_at)}.
                You confirm the payment in PayPal; if the cart changes, payment locks again.</Notice>
            )}
            {(p.status === 'ordered' || p.status === 'captured') && (
              <Notice tone="ok">PayPal order <code>{p.paypal_order_id}</code> ({p.paypal_status}).
                {p.status === 'ordered' && p.payer_action_url && <> <a href={p.payer_action_url}>Pay with PayPal (sandbox) →</a></>}</Notice>
            )}
            <Card title={<>You allowed vs the agent asked <span className="proof-tag">Cart Mandate</span></>}>
              {intent ? <AllowedVsAsked p={p} intent={intent} /> : <p className="muted">Loading the permission…</p>}
            </Card>
            {intent && (
              <Card title={<>The permission <span className="meta"><Link href={`/intents/${intent.intent_id}`}>open →</Link></span></>}>
                <blockquote className="quote">{intent.user_text}</blockquote>
              </Card>
            )}
            <Card title="Record">
              <dl className="facts">
                <div><dt>Receipt</dt><dd>{p.receipt_id ? <Link href={`/receipts/${p.receipt_id}`}>{p.receipt_id}</Link> : '—'}</dd></div>
                <div><dt>PayPal order</dt><dd>{p.paypal_order_id ?? '—'}</dd></div>
                <div><dt>Cart fingerprint</dt><dd><Hash value={p.cart_hash} n={16} /></dd></div>
              </dl>
            </Card>
          </>
        )}
        {tab === 'lines' && (
          <>
            <Card title="Every line in the cart">
              <LineTable lines={p.lines} currency={p.currency} total={p.total} flagged={p.verdict.semantic?.flagged_skus ?? []}
                charges={fromPage ? pageCharges(p.page_cart) : []} />
              <p className="muted small">
                {fromPage ? <>Read by the checkout guard from the shop's cart and checkout page ({p.page_cart?.reader}), not from the agent. Not signed by the shop.</>
                  : p.cart_source === 'merchant_signed' ? 'Titles and prices come from the offer the shop signed; the gateway checked its signature. Not from the agent.'
                    : 'Titles and prices come from the shop catalog, looked up by the gateway, not from the agent.'}
              </p>
            </Card>
            {fromPage && p.page_cart && (
              <Card title={<>The checkout page, as the guard saw it <span className="meta"><Hash value={p.page_cart.screenshot_sha256} /></span></>}>
                <p className="muted small" style={{ marginTop: 0 }}><code>{p.page_cart.page_url}</code></p>
                {p.page_screenshot_png_b64
                  ? <img className="page-shot" alt="Checkout page screenshot taken by the checkout guard" src={`data:image/png;base64,${p.page_screenshot_png_b64}`} />
                  : <p className="muted">No screenshot.</p>}
              </Card>
            )}
          </>
        )}
        {tab === 'checks' && <Card title="Why the gateway decided this"><VerdictPanel v={p.verdict} texts={p.context_texts} /></Card>}
        {tab === 'ai' && <ModelReplies exchanges={exchanges} />}
        {tab === 'timeline' && (
          <Card title={<>What happened <span className="meta"><Link href={`/log?purchase_id=${p.purchase_id}`}>audit log →</Link></span></>}>
            {log.length ? <Timeline p={p} log={log} /> : <p className="muted">Loading…</p>}
          </Card>
        )}
      </div>
    </>
  )
}
