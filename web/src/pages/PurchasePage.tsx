import { useEffect, useState } from 'react'
import { api, type ModelExchange } from '../api'
import { describeError, signApproval } from '../passkey'
import { Link } from '../router'
import { Badge, Card, Hash, LineTable, Notice, Page, VerdictPanel, pageCharges, usePoll, when } from '../ui'

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

function parseReply(text: string): { reasoning?: string; content?: string } {
  try {
    const m = JSON.parse(text).choices[0].message
    return { reasoning: m.reasoning, content: m.content }
  } catch {
    return { content: text }
  }
}

/** What each AI check was asked and answered, word for word. */
function ModelReplies({ exchanges }: { exchanges: ModelExchange[] }) {
  if (!exchanges.length) return null
  return (
    <details className="model-replies">
      <summary>What the AI checks said, word for word ({exchanges.length} {exchanges.length === 1 ? 'reply' : 'replies'})</summary>
      <p className="muted small">
        Exact requests and replies, kept with this decision. Their fingerprint is in the audit log, so they cannot be
        changed later without it showing.
      </p>
      {exchanges.map((ex, i) => {
        const r = parseReply(ex.response_text)
        const user = ex.request.messages?.find((m) => m.role === 'user')?.content
        return (
          <div key={i} className="exchange">
            <div className="exchange-head">
              <code>{ex.request.model}</code> · HTTP {ex.status} · {ex.latency_ms} ms{ex.attempt > 1 ? ` · attempt ${ex.attempt}` : ''}
            </div>
            {user && <><div className="label">Asked about</div><pre>{user}</pre></>}
            {r.reasoning && <><div className="label">Model's reasoning</div><pre>{r.reasoning}</pre></>}
            <div className="label">Answer</div>
            <pre>{r.content}</pre>
          </div>
        )
      })}
    </details>
  )
}

export default function PurchasePage({ id }: { id: string }) {
  const q = usePoll(() => api.purchase(id), [id])
  const keys = usePoll(() => api.passkeys(), [], 30000)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const left = useCountdown(q.data?.status === 'pending_approval' ? q.data.approval_expires_at : null)

  const decide = async (approve: boolean) => {
    if (!q.data) return
    setBusy(true)
    setError(null)
    try {
      // The cart hash shown on this screen binds the approval to exactly this cart.
      // Approving: your device signs this exact cart (if you have a passkey).
      const webauthn = approve ? await signApproval(() => api.purchaseApprovalOptions(id)) : undefined
      await api.decidePurchase(id, approve, q.data.cart_hash, webauthn)
      await q.refresh()
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusy(false)
    }
  }

  const p = q.data
  const fromPage = p?.cart_source === 'page'
  if (!p) return <Page title="Cart">{q.error ? <Notice tone="bad">{q.error}</Notice> : <p className="muted">Loading…</p>}</Page>

  return (
    <Page
      title={`Cart at ${p.merchant_id}`}
      kicker={<>Purchase <code>{p.purchase_id}</code> {p.status !== 'blocked' && <Badge value={p.decision} />} <Badge value={p.status} /></>}
      actions={<Link href={`/intents/${p.intent_id}`}>Permission →</Link>}
    >
      {p.status === 'pending_approval' && (
        <Card title="Your decision is needed" tone="focus">
          <ul className="reasons">{p.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
          {fromPage && (
            <Notice tone="warn">
              <strong>This shop does not sign its carts.</strong> The checkout guard read this cart from the shop's own
              checkout page (screenshot below). Compare it with what you expect before approving: your passkey signs
              exactly these lines, shipping, tax and total, and payment opens only for this cart.
            </Notice>
          )}
          <p className="muted small">
            Check every line below. Approving signs this exact cart (hash <Hash value={p.cart_hash} />); any change
            to it would need a new approval. Expires in {left}.
          </p>
          {error && <Notice tone="bad">{error}</Notice>}
          <div className="actions">
            <button className="primary" disabled={busy} onClick={() => decide(true)}>{keys.data?.required || fromPage ? 'Approve with your passkey' : 'Approve this cart'}</button>
            <button className="danger" disabled={busy} onClick={() => decide(false)}>Reject</button>
          </div>
        </Card>
      )}
      {p.status === 'blocked' && (
        <Notice tone="bad"><strong>Blocked by the gateway.</strong> {p.error ?? p.reasons.join('; ')}</Notice>
      )}
      {p.payment_confirmation && (() => {
        const c = p.payment_confirmation.confirmation
        return (
          <Notice tone={c.checked_with_paypal ? 'ok' : 'warn'}>
            <strong>{c.checked_with_paypal ? 'Paid, checked with PayPal' : 'Payment reported by the checkout guard'}</strong>:
            PayPal order <code>{c.paypal_order_id}</code>
            {c.checked_with_paypal ? <> ({c.status}, {c.amount} {c.currency}{c.capture_id && <>, transaction <code>{c.capture_id}</code></>})</>
              : <>. The gateway could not look this order up with its own PayPal app (the shop's order); check it in your PayPal account.</>}
            {' '}Recorded {when(c.confirmed_at)}.
            {p.receipt_id && <> Receipt <Link href={`/receipts/${p.receipt_id}`}>{p.receipt_id}</Link>.</>}
          </Notice>
        )
      })()}
      {p.status === 'authorized' && p.checkout_permit && (
        <Notice tone="ok">
          <strong>Payment unlocked for this exact cart</strong>{p.user_signed ? ', signed on your own device (passkey)' : ''}.
          The checkout guard may now open the shop's PayPal step for {p.checkout_permit.permit.total} {p.checkout_permit.permit.currency} until{' '}
          {when(p.checkout_permit.permit.expires_at)}; you confirm the payment in PayPal. If the cart changes, payment locks again.
          {p.receipt_id && <> Receipt <Link href={`/receipts/${p.receipt_id}`}>{p.receipt_id}</Link>.</>}
        </Notice>
      )}
      {p.status === 'failed' && <Notice tone="bad"><strong>Order failed at PayPal.</strong> {p.error}</Notice>}
      {(p.status === 'ordered' || p.status === 'captured') && (
        <Notice tone="ok">
          {p.approved_by ? `Approved by ${p.approved_by}${p.user_signed ? ', signed on your own device (passkey)' : ''}. ` : 'Approved automatically by policy. '}
          PayPal order <code>{p.paypal_order_id}</code> ({p.paypal_status}).
          {p.receipt_id && <> Receipt <Link href={`/receipts/${p.receipt_id}`}>{p.receipt_id}</Link>.</>}
        </Notice>
      )}
      {p.status === 'ordered' && p.payer_action_url && (
        <Card title="Pay with PayPal">
          <p>The gateway created the order. Complete payment as the buyer (sandbox personal account).
            You will come back here when it is captured.</p>
          <a className="button primary" href={p.payer_action_url}>Pay with PayPal (sandbox)</a>
        </Card>
      )}

      <Card title={<>Every line in the cart {(p.status === 'ordered' || p.status === 'captured') && <span className="proof-tag">Cart Mandate</span>}</>}>
        <LineTable lines={p.lines} currency={p.currency} total={p.total} flagged={p.verdict.semantic?.flagged_skus ?? []}
          charges={fromPage ? pageCharges(p.page_cart) : []} />
        <p className="muted small">
          {fromPage
            ? <>Read by the checkout guard from the shop's cart and checkout page ({p.page_cart?.reader}), not from the agent. Not signed by the shop.</>
            : p.cart_source === 'merchant_signed'
              ? 'Titles and prices come from the offer the shop signed; the gateway checked its signature. Not from the agent.'
              : 'Titles and prices come from the merchant catalog, looked up by the gateway, not from the agent.'}
        </p>
      </Card>

      {fromPage && p.page_cart && (
        <Card title="The shop's checkout page, as the guard saw it">
          <p className="muted small"><code>{p.page_cart.page_url}</code>{p.page_cart.screenshot_sha256 && <> · screenshot hash <Hash value={p.page_cart.screenshot_sha256} /></>}</p>
          {p.page_screenshot_png_b64
            ? <img className="page-shot" alt="Checkout page screenshot taken by the checkout guard" src={`data:image/png;base64,${p.page_screenshot_png_b64}`} />
            : <p className="muted">No screenshot.</p>}
        </Card>
      )}

      <Card title="Why the gateway decided this">
        <VerdictPanel v={p.verdict} texts={p.context_texts} />
        <ModelReplies exchanges={p.model_exchanges ?? []} />
      </Card>

      <Card title="Record">
        <dl className="facts">
          <div><dt>Requested</dt><dd>{when(p.created_at)}</dd></div>
          <div><dt>Cart hash</dt><dd><Hash value={p.cart_hash} n={24} /></dd></div>
          <div><dt>Receipt</dt><dd>{p.receipt_id ? <Link href={`/receipts/${p.receipt_id}`}>{p.receipt_id}</Link> : '—'}</dd></div>
          <div><dt>PayPal order</dt><dd>{p.paypal_order_id ?? '—'}</dd></div>
        </dl>
        <p><Link href={`/log?purchase_id=${p.purchase_id}`}>Audit log for this cart →</Link></p>
      </Card>
    </Page>
  )
}
